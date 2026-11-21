from __future__ import annotations

import asyncio
import time
from typing import Any

try:
    from google import genai
    from google.genai import types
except Exception:  # noqa: BLE001 — a build without google-genai must
    # still import: the local brain keeps working, and cloud paths
    # report the missing library honestly instead of crashing.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from ..config import config
from ..funnel.tools_gate import _READ_ONLY_TOOLS
from ..funnel.tools_handlers import (
    deadline_for,
    note_tool_timeout,
    timeout_cooldown_active,
)
from ..funnel.tools_registry import tools
from ..memory.memory import memory_store

# INTENTIONAL (skills split B47): receive/tool-loop mixin. Each sibling
# import is exactly a name its methods resolve by bare name (B45 P3
# lesson, per-mixin): resume/interaction/helpers families + the B46
# error-cost names (_log_line, _cost_record_live — _receive_loop
# resolves both — and _short_exc for the field-check fix #2 loss
# notes).
from .live_client_error_cost import (
    _cost_record_live,
    _log_line,
    _short_exc,
)
from collections import deque

from .live_client_helpers import (
    _AUDIBLE_RMS_FLOOR,
    _chunk_rms,
    _SEND_REALTIME_TIMEOUT_S,
    _VOICE_EPISODE_END_GAP_S,
    _VOICE_EPISODE_MAX_SAMPLES,
    _VOICE_RMS_FLOOR,
    _voice_episode_is_stationary,
)
from .live_client_interaction import (
    _call_id_of,
    _interaction_status_of,
    _turn_status_notification,
)
from .live_client_resume import (
    _clear_resume_handle,
    _is_stale_handle_close,
    _save_resume_handle,
)
# P2 speed pass (2026-11-16): per-turn latency recorder + filler
# bridge. Both are stdlib/light by construction (live_latency is
# stdlib-only; live_filler's tree imports are lazy inside the
# render function), so neither can create an import cycle here.
from .live_filler import FILLER_TICK_S, FillerBridge
from .live_latency import LatencyLedger, TurnLatency


# Mic field check (2026-11-16, report §9): the send loop had no
# starvation signal — an empty mic_queue made it sleep silently
# forever, so a deaf session looked connected. After this many
# seconds with no mic frames at all and no voice heard, the loop
# logs (throttled) and exposes the starved seconds on the
# session, so the UI can show it. 20 s matches the deaf hint.
# Silence frames still queue by design, so an empty queue means
# the mic is delivering nothing — never a quiet user.
_MIC_STARVED_AFTER_S = 20.0
_MIC_STARVED_LOG_EVERY_S = 20.0


# Round-3 fix #2 (#55): the live loop's hard round cap — one batch
# is one round, 8 rounds per model turn (parity with the cloud text
# loop's MAX_TOOL_ROUNDS = 8; the local loop caps at 6). The live
# value is config.live_tool_rounds (env LIVE_TOOL_ROUNDS); this
# constant is the fallback when the field is absent.
LIVE_MAX_TOOL_ROUNDS = 8


def _max_live_tool_rounds() -> int:
    """Tool rounds allowed in one live model turn: config
    .live_tool_rounds (default 8, env LIVE_TOOL_ROUNDS), the module
    constant as fallback. Never non-positive."""
    try:
        value = int(getattr(config, "live_tool_rounds",
                            LIVE_MAX_TOOL_ROUNDS))
    except (TypeError, ValueError):
        value = LIVE_MAX_TOOL_ROUNDS
    return value if value > 0 else LIVE_MAX_TOOL_ROUNDS


def _mirror_starved_seconds(audio_manager, seconds):
    """Mirror the starvation signal onto the audio manager, so
    mic_status() can carry it to the GUI's deaf hint (issue-hunt
    #5: the signal lived only on the session, with no consumer).
    Never raises: a diagnostic write must never break the send
    loop. A test fake driving the loop gets the attribute too —
    SimpleNamespace accepts setattr."""
    try:
        audio_manager._mic_starved_seconds = float(seconds)
    except Exception:  # noqa: BLE001 — diagnostic only
        pass


class GeminiLiveSessionReceiveMixin:
    # INTENTIONAL (skills split B47): receive/tool loop — transcript
    # flush/handle, audio-send loop, receive loop, tool execution +
    # delivery, tool batch + worker loop. Cross-mixin self-calls
    # (notably _receive_loop -> _notify_status in lifecycle) resolve
    # through the composed class MRO.

    def _ensure_speed_pass(self, audio_manager=None):
        """P2 speed pass: the per-session latency recorder, the
        recent-turns ledger, and the filler bridge — created
        lazily so a test fake driving _receive_loop directly
        (without _run_session) still gets them on first use.
        _run_session pre-creates them so the filler watchdog
        task exists from session birth. The getattr guards are
        the file's usual shape for mixin use on partial fakes."""
        if getattr(self, "_turn_latency", None) is None:
            self._turn_latency = TurnLatency()
        if getattr(self, "_latency_ledger", None) is None:
            self._latency_ledger = LatencyLedger()
        if getattr(self, "_filler_bridge", None) is None:
            self._filler_bridge = FillerBridge(audio_manager)
        elif audio_manager is not None:
            self._filler_bridge.audio_manager = audio_manager
        return self._turn_latency, self._filler_bridge

    def _finalize_turn_latency(self) -> None:
        """P2 speed pass: close the current turn's latency
        record (idempotent — a straggler turn_complete and the
        interaction-end note both call this, and only the first
        emits), file the summary on the ledger, reset the
        filler for the next turn, and write the numbers to the
        session log where the field test can read them."""
        _lat = getattr(self, "_turn_latency", None)
        if _lat is None:
            return
        _summary = _lat.note_turn_complete()
        if _summary is None:
            return
        _ledger = getattr(self, "_latency_ledger", None)
        if _ledger is not None:
            _ledger.add(_summary)
        _bridge = getattr(self, "_filler_bridge", None)
        if _bridge is not None:
            _bridge.reset()
        try:
            _fs = _summary["first_sound_ms"]
            _fs_txt = f"{_fs:.0f} ms" if _fs is not None else "n/a"
            _local = _summary["local_ms"]
            _local_txt = f"{_local:.1f} ms" if _local is not None else "n/a"
            _tools = _summary["tool_wait_ms"]
            _tools_txt = f"{_tools:.0f} ms" if _tools is not None else "n/a"
            _log_line(
                f"Live turn latency: first sound {_fs_txt} "
                f"(budget 500 ms, within={_summary['within_budget']}), "
                f"local {_local_txt}, tools {_tools_txt}, "
                f"filler {_summary['filler']!r}, "
                f"interrupted={_summary['interrupted']}")
        except Exception:  # noqa: BLE001 — logging never breaks a turn
            pass

    async def _filler_watchdog_loop(self):
        """P2 speed pass: the filler bridge's tick. Every
        FILLER_TICK_S while the session is live, the bridge may
        speak one bridge phrase into a wait past the gap. Never
        raises; a cancelled watchdog is a normal session end.
        It never ends the session itself — it is not in the
        FIRST_COMPLETED set, and _run_session cancels it at
        teardown."""
        try:
            while self._running and getattr(self, "is_connected", True):
                await asyncio.sleep(FILLER_TICK_S)
                _bridge = getattr(self, "_filler_bridge", None)
                if _bridge is None:
                    continue
                try:
                    _spoken = await _bridge.tick()
                except Exception:  # noqa: BLE001 — a tick fault
                    continue  # never kills the watchdog
                if _spoken is not None:
                    _lat = getattr(self, "_turn_latency", None)
                    if _lat is not None:
                        _lat.note_filler(_spoken)
                    _log_line(f"Live filler spoken: {_spoken!r} "
                              "(bridge while the turn waited past "
                              "the 0.5 s budget)")
        except asyncio.CancelledError:
            pass

    def _loss_note(self, text: str) -> None:
        """Surface a silent-loss note in the Activity feed and,
        best-effort, as a chat line. Never raises: a note about
        a lost record must not itself be lost to an exception,
        and must never break the receive loop or a flush. The
        feed line is the durable record; the chat line is the
        one the user sees in the moment.
        """
        try:
            _log_line(text)
        except Exception:  # noqa: BLE001 - the feed write
            pass  # itself failed; nothing else can carry it
        try:
            if self.on_transcript:
                self.on_transcript("assistant", text)
        except Exception:  # noqa: BLE001 - the chat line is
            pass  # best-effort; the feed line already landed

    def _release_held_output_chunks(self) -> None:
        """Emit the output-transcription chunks held while the
        user's words for the turn were still unflushed (see
        _handle_output_transcription). The chunks are a prefix
        of the buffered reply, so the live bubble they create
        is finalised by the flush that follows with the same
        full text. A chunk consumer that raises is surfaced
        with the same once-per-run loss note as the live
        stream (see _handle_output_transcription); the
        remaining held chunks are dropped — they are already
        in the buffer, so the full reply still lands at the
        flush. Never raises; the flush must not lose the reply
        to a display fault."""
        held = getattr(self, "_held_output_chunks", None)
        if not held:
            return
        self._held_output_chunks = []
        _cb = getattr(self, "on_transcript_chunk", None)
        if _cb is None:
            return
        for _chunk in held:
            if getattr(self, "_reply_stop_requested", False):
                return
            try:
                _cb("assistant", _chunk)
            except Exception as exc:  # noqa: BLE001
                if not getattr(self, "_chunk_loss_noted", False):
                    self._chunk_loss_noted = True
                    self._loss_note(
                        "The live reply text could not be shown "
                        "as it arrived: %s — the full reply will "
                        "still appear when it finishes."
                        % _short_exc(exc))
                return
            else:
                self._chunk_loss_noted = False

    def _flush_output_transcript(
            self, *, record_on_failure: bool = False) -> None:
        """Emit one accumulated assistant-transcription reply.

        Text-replies (v5.94): called when a transcribed reply
        ends (transcription finished, turn complete, or
        interruption). Emits exactly ONE on_transcript
        ("assistant", ...) for the whole accumulated text, so
        the reply lands as a single bubble through the existing
        transcript path, plus one memory-log entry — mirroring
        the model-turn text handling. Empty buffers emit
        nothing.

        record_on_failure (round-3 section C fix queue item
        13): a model_turn text part is a turn already spoken,
        so a bubble that cannot be shown is still recorded to
        memory and surfaced ("could not be shown in the
        chat"), not kept for a retry — the field-check fix
        #17 guarantee. The transcription path keeps the
        default: a failed bubble stays in the buffer for the
        next flush.

        Field-check fix #2 (T6): the buffer is cleared only
        once the text has actually been delivered. A failed
        bubble keeps the text for the next flush (so a later
        flush can still show it); a bubble that landed but
        whose memory row failed is gone from the buffer (the
        bubble IS the record) and the missing memory row is
        surfaced with a loss note. A stopped reply's memory
        failure is likewise surfaced, never swallowed.
        """
        # Round-3 section C fix queue item 9 (C1-F2): the answer
        # must never render above its own question. When the
        # user's words for this turn have not flushed yet
        # (_input_transcript_open), the reply is held in the
        # buffer — the input flush releases it (see
        # _flush_input_transcript). The stop flag is NOT cleared
        # on this path: the deferred flush is still this
        # reply's flush, and a stopped reply must stay stopped
        # when it eventually runs.
        if getattr(self, "_input_transcript_open", False):
            self._output_flush_deferred = True
            return
        if getattr(self, "_input_transcript_buf", "").strip():
            # The input finished but its flush did not land (a
            # raising consumer keeps the text) — retry it now
            # so the question still precedes the answer, then
            # proceed either way: the input is finished, so
            # holding the reply for it would strand the answer
            # on a broken consumer.
            self._flush_input_transcript()
        # Field-check fix #19: a reply ends here, so the
        # chunk-loss note arms again for the next reply.
        self._chunk_loss_noted = False
        text = self._output_transcript_buf
        stopped = bool(getattr(self, "_reply_stop_requested", False))
        # A flush is always a turn end: the stop flag clears
        # here, whatever the outcome below.
        self._reply_stop_requested = False
        if not text.strip():
            self._output_transcript_buf = ""
            return
        if stopped:
            # The user stopped this reply: the words already
            # shown stay shown (the GUI finalised its live
            # bubble as stopped at stop time), so no second
            # bubble is emitted — but the partial text is
            # still the record, and it is logged to memory.
            try:
                memory_store.log_message("assistant", text)
            except Exception as exc:  # noqa: BLE001 - the
                # stopped reply is already shown; only the
                # memory row is lost, and that is surfaced
                self._loss_note(
                    "The stopped reply was shown but could not "
                    "be saved to memory: %s"
                    % _short_exc(exc))
            self._output_transcript_buf = ""
            return
        # Round-3 section C fix queue item 9 (C1-F2): the held
        # live-stream chunks go out first — they create the
        # live assistant bubble AFTER the user's bubble has
        # landed (the input flush above released this reply),
        # and the bubble below finalises it with the full text.
        self._release_held_output_chunks()
        try:
            if self.on_transcript:
                self.on_transcript("assistant", text)
        except Exception as exc:  # noqa: BLE001 - the reply
            # is kept for the next flush, and the loss is
            # surfaced rather than swallowed — unless the
            # caller asked for record_on_failure (round-3
            # section C fix queue item 13, field-check fix
            # #17): a model_turn text part is a turn already
            # spoken, so its memory row is still the record
            # and the lost bubble is surfaced, not kept for
            # a retry that may never come.
            if record_on_failure:
                try:
                    memory_store.log_message("assistant", text)
                except Exception as exc2:  # noqa: BLE001
                    self._loss_note(
                        "The assistant reply was shown but "
                        "could not be saved to memory: %s"
                        % _short_exc(exc2))
                self._output_transcript_buf = ""
                self._loss_note(
                    "The assistant reply could not be shown "
                    "in the chat: %s" % _short_exc(exc))
                return
            self._loss_note(
                "The assistant reply could not be shown and "
                "was kept in the transcript buffer (%s); it "
                "will be retried on the next reply."
                % _short_exc(exc))
            return
        self._output_transcript_buf = ""
        try:
            memory_store.log_message("assistant", text)
        except Exception as exc:  # noqa: BLE001 - the bubble
            # landed, so the text is not lost; only the
            # memory row is, and that is surfaced
            self._loss_note(
                "The assistant reply was shown but could not "
                "be saved to memory: %s" % _short_exc(exc))

    def _flush_input_transcript(self) -> None:
        """Emit one accumulated user input-transcription utterance.

        Round-3 fix #5 (#56): the mirror of
        _flush_output_transcript for the user's own words. Emits
        exactly ONE on_transcript("user", ...) for the whole
        accumulated text, verbatim as STT heard — never cleaned
        up — so the utterance lands as a single "You" bubble
        through the existing transcript path (the same channel
        the local path and the typed echo use), plus one
        memory-log entry. Empty buffers emit nothing.

        Unlike the assistant flush, a stop request does NOT
        suppress this: Stop applies to the assistant's reply,
        not to the user's words, and a genuine new spoken turn
        is exactly what clears the stopped marker (fix #7 U4
        #29). A failed bubble keeps the text for the next flush
        (field-check fix #2 pattern); a bubble that landed but
        whose memory row failed is gone from the buffer (the
        bubble IS the record) and the missing memory row is
        surfaced with a loss note.
        """
        text = self._input_transcript_buf
        if not text.strip():
            self._input_transcript_buf = ""
            self._input_transcript_open = False
            return
        try:
            if self.on_transcript:
                self.on_transcript("user", text)
        except Exception as exc:  # noqa: BLE001 - the words
            # are kept for the next flush, and the loss is
            # surfaced rather than swallowed
            self._loss_note(
                "The user's spoken words could not be shown "
                "and were kept in the transcript buffer (%s); "
                "they will be retried on the next utterance."
                % _short_exc(exc))
            return
        self._input_transcript_buf = ""
        self._input_transcript_open = False
        try:
            memory_store.log_message("user", text)
        except Exception as exc:  # noqa: BLE001 - the bubble
            # landed, so the words are not lost; only the
            # memory row is, and that is surfaced
            self._loss_note(
                "The user's spoken words were shown but could "
                "not be saved to memory: %s" % _short_exc(exc))
        # Round-3 section C fix queue item 9 (C1-F2): the
        # question has landed, so a reply held for it is
        # released now — the answer follows its question, in
        # the transcript and in memory (the release flush
        # logs the assistant row after this user row). Only
        # on a landed bubble: a failed bubble keeps its text,
        # and releasing the answer above an unlanded question
        # would be the bug again.
        if getattr(self, "_output_flush_deferred", False):
            self._output_flush_deferred = False
            self._flush_output_transcript()

    def stop_reply(self) -> bool:
        """Stop the reply in flight (chat UI, 2026-11-16).

        Sets the stop flag: further transcription chunks are
        accumulated but not emitted, model-turn text parts are
        dropped, and the turn-end flush logs without emitting.
        The audio cut is the caller's job — the GUI calls
        ``audio_manager.interrupt_playback()`` (the P2-verified
        hard cut) alongside this; this method only owns the
        text side, so it is safe to call from any thread (a
        plain attribute set, no loop or queue touched).
        Returns True: the flag is set for the current turn
        whether or not any text has arrived yet.
        """
        self._reply_stop_requested = True
        return True

    def _handle_output_transcription(self, server_content: Any) -> None:
        """Accumulate one server message's output transcription.

        Text-replies (v5.94): the server streams transcribed
        reply text in small chunks alongside the audio. Each
        chunk appends to the buffer; the buffer flushes once
        per reply when the transcription finishes or the turn
        completes (see _flush_output_transcript), so a reply
        lands as a single bubble. A chunk consumer that
        raises is surfaced with a loss note (once per failure
        run — see below); any other failure here is
        swallowed — transcription never breaks audio, the
        session, or the receive loop.
        """
        try:
            _out_tr = getattr(
                server_content, "output_transcription", None)
            if _out_tr is not None:
                _chunk = getattr(_out_tr, "text", None) or ""
                if _chunk:
                    if not self._output_transcript_buf:
                        # Field-check fix #19: a chunk arriving
                        # on an empty buffer starts a new reply,
                        # so the chunk-loss note arms again —
                        # this also covers a reconnect, where
                        # _run_session clears the buffer without
                        # a flush (the session object itself is
                        # a reused singleton).
                        self._chunk_loss_noted = False
                    self._output_transcript_buf += _chunk
                    # Chat UI (2026-11-16): stream the chunk to
                    # the transcript in real time — the same
                    # words, at the same moment, as the voice.
                    # While a stop is requested the chunk is
                    # accumulated only: the GUI has already
                    # finalised the reply as stopped, and a
                    # late chunk must not grow it again.
                    # Round-3 section C fix queue item 9 (C1-F2):
                    # while the user's words for this turn are
                    # still unflushed, the chunk is HELD, not
                    # streamed: streaming it would create the
                    # live assistant bubble above the question.
                    # Held chunks are released by the flush that
                    # follows the question (see
                    # _release_held_output_chunks) — the hold
                    # lasts ~0.08 s in the field, the gap
                    # between the output finishing and the input
                    # finishing. A turn with no input
                    # transcription at all streams exactly as
                    # before (nothing is held).
                    if not getattr(self, "_reply_stop_requested",
                                   False):
                        _cb = getattr(self, "on_transcript_chunk",
                                      None)
                        if _cb is not None:
                            if getattr(self, "_input_transcript_open",
                                       False):
                                _held = getattr(
                                    self, "_held_output_chunks", None)
                                if _held is None:
                                    _held = \
                                        self._held_output_chunks = []
                                _held.append(_chunk)
                            else:
                                try:
                                    _cb("assistant", _chunk)
                                except Exception as exc:  # noqa: BLE001
                                    # Field-check fix #19 (F2
                                    # follow-up): a raising chunk
                                    # consumer must not swallow
                                    # silently — the live words were
                                    # not shown as they arrived. The
                                    # chunk itself is already in the
                                    # buffer, so the full reply still
                                    # lands at the flush; only the
                                    # streaming is lost, and the loss
                                    # is noted once per failure run —
                                    # a note per chunk would flood
                                    # the feed and the chat, a reply
                                    # streams dozens of them.
                                    if not getattr(
                                            self, "_chunk_loss_noted",
                                            False):
                                        self._chunk_loss_noted = True
                                        self._loss_note(
                                            "The live reply text "
                                            "could not be shown as it "
                                            "arrived: %s — the full "
                                            "reply will still appear "
                                            "when it finishes."
                                            % _short_exc(exc))
                                else:
                                    self._chunk_loss_noted = False
                if getattr(_out_tr, "finished", False):
                    self._flush_output_transcript()
            if getattr(server_content, "turn_complete", False):
                self._flush_output_transcript()
        except Exception:  # noqa: BLE001 — transcription never breaks audio
            pass

    def _handle_input_transcription(self, server_content: Any) -> None:
        """Accumulate one server message's input transcription.

        Round-3 fix #5 (#56): the mirror of
        _handle_output_transcription for the user's own words.
        The server streams a transcription of the user's spoken
        audio in small chunks alongside the turn. Each chunk
        appends to the buffer; the buffer flushes once per
        utterance when the transcription finishes or the turn
        completes (see _flush_input_transcript), so the words
        land as a single "You" bubble — verbatim as STT heard,
        fillers and all, never cleaned up — through the same
        on_transcript("user", ...) channel the local path and
        the typed echo use. Any failure here is swallowed —
        transcription never breaks audio, the session, or the
        receive loop.

        Deliberately no live chunk streaming: the GUI streams
        assistant chunks only (its _on_transcript_chunk ignores
        non-assistant roles), and the local path likewise emits
        one bubble per utterance — the finished transcription
        is the ruling's "transcribed on screen", and interim
        text would need GUI work the ruling does not require.
        """
        try:
            _in_tr = getattr(
                server_content, "input_transcription", None)
            if _in_tr is not None:
                _chunk = getattr(_in_tr, "text", None) or ""
                if _chunk:
                    self._input_transcript_buf += _chunk
                    # Round-3 section C fix queue item 9 (C1-F2):
                    # the utterance is open — its words are
                    # pending, not final. The reply side holds
                    # its flush and its live chunks until this
                    # clears (see _flush_output_transcript).
                    self._input_transcript_open = True
                if getattr(_in_tr, "finished", False):
                    self._input_transcript_open = False
                    self._flush_input_transcript()
            if getattr(server_content, "turn_complete", False):
                self._flush_input_transcript()
        except Exception:  # noqa: BLE001 — transcription never breaks audio
            pass

    def _voice_episode_note_chunk(self, rms: float, now: float) -> None:
        """Record one voice-level chunk in the current voice
        episode (round-3 section C fix queue item 9, C1-F1).
        An episode opens on the first voice-level chunk after a
        gap and remembers the watchdog stamps as they stood
        before it, so the episode-end classification can roll
        them back when the episode turns out to be stationary
        loudness, not speech. Lazily created (getattr pattern):
        test fakes driving this loop without _run_session get
        the state here, exactly like the speed pass below."""
        rms_hist = getattr(self, "_voice_episode_rms", None)
        if rms_hist is None:
            rms_hist = deque(maxlen=_VOICE_EPISODE_MAX_SAMPLES)
            self._voice_episode_rms = rms_hist
        if getattr(self, "_voice_episode_started_at", None) is None:
            self._voice_episode_started_at = now
            self._voice_episode_prev_voice_at = getattr(
                self, "_last_voice_at", None)
            self._voice_episode_prev_audible_at = getattr(
                self, "_last_audible_at", None)
        rms_hist.append(rms)
        self._voice_episode_last_chunk_at = now

    def _voice_episode_close_if_ended(self, now: float) -> None:
        """Close the open voice episode once no voice-level
        chunk has arrived for _VOICE_EPISODE_END_GAP_S, and
        classify it. A stationary episode (sustained loud
        noise, not speech) has its watchdog stamps rolled back
        to before the episode — the server's VAD correctly
        ignored it, so the stamps are a false turn and must
        not end the session (C1-8a). A speech episode keeps
        its stamps: the turn was real and the silence rules
        still apply to it on the same envelope. Never raises:
        classification must not break the send loop."""
        started = getattr(self, "_voice_episode_started_at", None)
        if started is None:
            return
        last = getattr(self, "_voice_episode_last_chunk_at", None)
        if last is None or now - last <= _VOICE_EPISODE_END_GAP_S:
            return
        # The episode has ended: classify it, then clear the
        # state. (The clearing MUST live here, on the close
        # path only — an early return above must leave the open
        # episode untouched, or the next iteration would wipe
        # an episode that only just opened.)
        try:
            rms_hist = getattr(self, "_voice_episode_rms", None) or []
            duration = last - started
            if _voice_episode_is_stationary(list(rms_hist),
                                            duration):
                self._last_voice_at = getattr(
                    self, "_voice_episode_prev_voice_at", None)
                cur_aud = getattr(self, "_last_audible_at", None)
                # Roll the audible stamp back too, unless a
                # LATER audible stamp exists: the episode's own
                # tail (a sub-voice chunk inside the end gap)
                # belongs to the episode, but audio after the
                # gap is a separate, later sound and its stamp
                # must survive.
                if (cur_aud is None
                        or cur_aud <= last
                        + _VOICE_EPISODE_END_GAP_S):
                    self._last_audible_at = getattr(
                        self, "_voice_episode_prev_audible_at", None)
                _log_line(
                    "[Live] stationary loudness, not speech "
                    f"({duration:.1f}s, RMS variation below the "
                    "speech floor) — watchdog stamps rolled back "
                    "to before the episode.")
        except Exception:  # noqa: BLE001 — never break the loop
            pass
        self._voice_episode_started_at = None
        self._voice_episode_last_chunk_at = None
        self._voice_episode_prev_voice_at = None
        self._voice_episode_prev_audible_at = None
        rms_hist = getattr(self, "_voice_episode_rms", None)
        if rms_hist is not None:
            rms_hist.clear()

    async def _send_audio_loop(self, audio_manager):
        """Continuously pulls recorded microphone chunks and streams to Gemini Live."""
        import queue
        # v5.32: stream mic audio the way the current Google
        # library documents — send_realtime_input(audio=...),
        # resolved once per session. The old code used the
        # deprecated unified send() carrying a realtime input
        # built on the legacy media_chunks field.
        send_realtime = getattr(self._session, "send_realtime_input", None)
        # Voice-control batch (2026-11-16, B3): the loop also
        # stops when the session is gone or disconnected — the
        # old condition checked _session alone, so a stale
        # reference kept the loop feeding a dead session.
        # getattr: the micfix send tests drive this loop on a
        # SimpleNamespace fake without is_connected — a missing
        # attribute reads as connected, exactly as before.
        # Mic field check (2026-11-16, report §9): starvation
        # state is per run, reset here so a reused session
        # object never carries a stale starved-since stamp into
        # a fresh run. The fields are plain attributes — the
        # session status the UI reads (getattr with a 0 default
        # for older callers).
        self._mic_starved_since = None
        self._mic_starved_seconds = 0.0
        self._mic_starved_logged_at = None
        # Round-3 section C fix queue item 9 (C1-F1): the voice
        # episode tracker is per run, reset here like the
        # starvation state above, so a reused session object
        # never carries a stale open episode into a fresh run.
        self._voice_episode_started_at = None
        self._voice_episode_last_chunk_at = None
        self._voice_episode_prev_voice_at = None
        self._voice_episode_prev_audible_at = None
        _rms_hist = getattr(self, "_voice_episode_rms", None)
        if _rms_hist is not None:
            _rms_hist.clear()
        # Issue-hunt #5: the audio manager's copy resets here
        # too, so a reused session object never carries a stale
        # starved value into a fresh run (the GUI keys the deaf
        # hint on mic_status(), which reads this mirror).
        _mirror_starved_seconds(audio_manager, 0.0)
        while self._running and self._session \
                and getattr(self, "is_connected", True):
            try:
                # Round-3 section C fix queue item 9 (C1-F1):
                # once per iteration, close a voice episode
                # whose last voice-level chunk is older than
                # the end gap — the classification (and any
                # stamp rollback) lands here, on every path
                # through the loop, including the empty-queue
                # path below that otherwise continues early.
                # getattr: the micfix send tests drive this
                # loop on a SimpleNamespace fake without the
                # method — a missing attribute must read as
                # "nothing to close", never raise before the
                # chunk is even pulled (an unguarded call
                # would spin the loop without consuming).
                _close_ep = getattr(
                    self, "_voice_episode_close_if_ended", None)
                if callable(_close_ep):
                    _close_ep(time.monotonic())
                try:
                    chunk = audio_manager.mic_queue.get_nowait()
                    # A frame arrived — the mic is delivering,
                    # whatever the frame holds (silence frames
                    # queue by design). Starvation ends here.
                    self._mic_starved_since = None
                    self._mic_starved_seconds = 0.0
                    self._mic_starved_logged_at = None
                    _mirror_starved_seconds(audio_manager, 0.0)
                except queue.Empty:
                    # Mic field check (2026-11-16, report §9):
                    # no frames at all — track how long the
                    # queue has been empty. Voice stamps come
                    # only from chunks, so after 20 s empty the
                    # voice stamp is stale by construction; the
                    # stale check is the guard anyway. Log
                    # throttled (first crossing, then every
                    # 20 s) and expose the starved seconds on
                    # the session — a deaf session must not
                    # look like a connected one whose user
                    # simply has not spoken yet.
                    _now = time.monotonic()
                    _since = getattr(
                        self, "_mic_starved_since", None)
                    if _since is None:
                        self._mic_starved_since = _now
                        _since = _now
                    _empty_s = _now - _since
                    _last_voice = getattr(
                        self, "_last_voice_at", None)
                    _stale = (_last_voice is None
                              or (_now - _last_voice)
                              >= _MIC_STARVED_AFTER_S)
                    if (_empty_s >= _MIC_STARVED_AFTER_S
                            and _stale):
                        self._mic_starved_seconds = _empty_s
                        _mirror_starved_seconds(
                            audio_manager, _empty_s)
                        _logged_at = getattr(
                            self, "_mic_starved_logged_at", None)
                        if (_logged_at is None
                                or (_now - _logged_at)
                                >= _MIC_STARVED_LOG_EVERY_S):
                            self._mic_starved_logged_at = _now
                            _log_line(
                                "[Send Audio] no mic audio for "
                                f"{int(_empty_s)}s")
                    await asyncio.sleep(0.01)
                    continue

                if not chunk:
                    continue

                # Stream audio PCM frame (v5.95 micfix: the
                # send is bounded — a hung send drops the chunk
                # instead of stalling the sender forever).
                if callable(send_realtime):
                    await asyncio.wait_for(send_realtime(audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={config.sample_rate_in}"
                    )), timeout=_SEND_REALTIME_TIMEOUT_S)
                else:
                    realtime_input = types.LiveClientRealtimeInput(
                        media_chunks=[
                            types.Blob(
                                data=chunk,
                                mime_type=f"audio/pcm;rate={config.sample_rate_in}"
                            )
                        ]
                    )
                    await asyncio.wait_for(
                        self._session.send(input=realtime_input),
                        timeout=_SEND_REALTIME_TIMEOUT_S)
                # Voice-control batch (2026-11-16, B1): the send
                # streak stamps — a success clears the failure
                # streak, so the watchdog's third rule only fires
                # on CONTINUOUS send failure, never on a transient.
                self._last_send_success_at = time.monotonic()
                self._last_send_failure_at = None
                # v5.54.1: the watchdog's voice stamp — only
                # voice-level audio counts, so room noise and
                # silence frames never arm the silence clock.
                # Mic remake (v6.3.x): the audible stamp —
                # sound above the audible floor (0.002) arms
                # the watchdog's second rule even when it never
                # crosses the voice floor, so a quiet voice
                # does not leave a half-open session
                # "connected" forever. Audible includes voice:
                # both stamps fire on a loud chunk.
                _rms = _chunk_rms(chunk)
                if _rms > _VOICE_RMS_FLOOR:
                    # Round-3 section C fix queue item 9 (C1-F1):
                    # the chunk joins the current voice episode
                    # BEFORE its stamp lands (see
                    # _voice_episode_note_chunk): the episode
                    # opener must capture the stamps as they
                    # stood before the episode, so a stationary
                    # episode rolls this stamp back to before
                    # itself — capturing after the stamp would
                    # roll back to the episode's own first
                    # chunk and the watchdog would still trip.
                    _now_voice = time.monotonic()
                    # getattr, as above: a SimpleNamespace fake
                    # driving this loop has no episode tracker —
                    # the chunk still stamps, exactly as before.
                    _note_ep = getattr(
                        self, "_voice_episode_note_chunk", None)
                    if callable(_note_ep):
                        _note_ep(_rms, _now_voice)
                    self._last_voice_at = _now_voice
                    # P2 speed pass: the turn's clock starts at
                    # the user's voice — the recorder and the
                    # filler both key on it. Guarded: test fakes
                    # driving this loop without _run_session get
                    # the state lazily (see _ensure_speed_pass).
                    self._ensure_speed_pass()
                    _lat = getattr(self, "_turn_latency", None)
                    if _lat is not None:
                        _lat.note_voice()
                    _bridge = getattr(self, "_filler_bridge", None)
                    if _bridge is not None:
                        _bridge.note_voice()
                if _rms > _AUDIBLE_RMS_FLOOR:
                    self._last_audible_at = time.monotonic()

            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                # Mic remake (v6.3.x): a failed send used to
                # drop one chunk silently and keep going — a
                # 15 s stall left ~8 s of speech lost with no
                # count, no log, no resync. Count it, drain
                # the stale backlog down to the last ~16
                # chunks (~1 s) so the sender resyncs to live
                # audio instead of feeding minutes-old speech
                # after the stall, and log throttled.
                self._send_drop_count = int(getattr(
                    self, "_send_drop_count", 0)) + 1
                # Voice-control batch (2026-11-16, B1): start the
                # failure streak on the FIRST failure only — the
                # streak start is what the watchdog's third rule
                # measures, so refreshing it on every failure
                # would make a continuously-failing send look
                # forever fresh and never trip.
                if getattr(self, "_last_send_failure_at", None) is None:
                    self._last_send_failure_at = time.monotonic()
                try:
                    while audio_manager.mic_queue.qsize() > 16:
                        audio_manager.mic_queue.get_nowait()
                        self._send_drop_count += 1
                except Exception:  # noqa: BLE001
                    pass
                if (self._send_drop_count == 1
                        or self._send_drop_count % 50 == 0):
                    print(f"[Send Audio Error]: {e} "
                          f"(dropped {self._send_drop_count} "
                          f"chunk(s) total)")
                await asyncio.sleep(0.05)

    async def _receive_loop(self, audio_manager):
        """Receives model responses (audio chunks, text, tool calls) from Gemini Live."""
        try:
            # v5.32: session.receive() yields ONE model turn and
            # then ENDS — that is the library's contract (its
            # receive() breaks when the turn completes). The old
            # code ran a single async-for over it, so after the
            # first turn nobody was listening any more and the
            # session sat connected and deaf (field-reported
            # 2026-10-22: connected, never responded). Re-enter
            # receive() for every turn, for the session's life.
            # Voice-control batch (2026-11-16, B2): a cleanly
            # closed session can make receive() return immediately
            # with zero yields — the old loop re-entered it at
            # once, forever, with no sleep and no guard, a busy
            # spin that left is_connected True. Count consecutive
            # empty re-entries and end the session after three,
            # so auto-reconnect can repair it; the backoff keeps
            # the loop from spinning in the meantime. A live turn
            # always yields at least one response, so three
            # consecutive empties cannot be a healthy session.
            _empty_receives = 0
            # getattr on is_connected: the send-loop note above
            # applies here too (test fakes without the attr).
            while self._running \
                    and getattr(self, "is_connected", True) \
                    and self._session is not None:
                _yielded = False
                async for response in self._session.receive():
                    _yielded = True
                    # v5.54.1: EVERY yielded response is
                    # response-plane activity — server content,
                    # tool calls, cancellations and resumption
                    # updates alike. A session that yields
                    # nothing for a whole silence budget after
                    # voice is dead, whatever its flag says.
                    self._last_response_at = time.monotonic()
                    # B9 (v5.45): a resumption update carries the
                    # handle the NEXT connect should offer —
                    # persist it on receipt.
                    try:
                        _upd = getattr(response,
                                       "session_resumption_update", None)
                        if _upd is not None:
                            _handle = getattr(_upd, "new_handle", None)
                            if _handle and getattr(_upd, "resumable", True):
                                _save_resume_handle(str(_handle))
                    except Exception:  # noqa: BLE001 — never break receive
                        pass
                    # RS-1 (v5.56): goAway — the server's
                    # "I will stop servicing this session soon"
                    # (LiveServerGoAway.time_left). OPPORTUNISTIC
                    # only: field evidence since Aug 2026 is that
                    # goAway is not reliably sent, so the 7 s
                    # watchdog above remains the primary trip and
                    # nothing here is depended on. When it DOES
                    # arrive, returning ends _receive_loop, which
                    # ends _run_session like any other loop death
                    # (DISCONNECTED surfaces, the GUI's
                    # auto-reconnect re-presents the handle —
                    # deliberately NOT cleared: a server-announced
                    # end is exactly when resumption should be
                    # tried). The resumption update on the same
                    # message, if any, is already persisted above.
                    try:
                        _go_away = getattr(response, "go_away", None)
                    except Exception:  # noqa: BLE001
                        _go_away = None
                    if _go_away is not None:
                        self._go_away_received = True
                        _time_left = getattr(_go_away, "time_left", None)
                        print(f"[Live] goAway received "
                              f"(time_left={_time_left}) — ending "
                              "the session for a resume reconnect.")
                        _log_line(f"Live goAway received "
                                  f"(time_left={_time_left}) — session "
                                  "ended for a resume reconnect.")
                        self._notify_status("LIVE_GOAWAY")
                        return
                    server_content = getattr(response,
                                             "server_content", None)
                    if server_content is not None:
                        # Model turn
                        model_turn = server_content.model_turn
                        _model_turn_text = False
                        if model_turn:
                            for part in model_turn.parts:
                                # Audio playback
                                if part.inline_data:
                                    pcm_data = part.inline_data.data
                                    # Voice-control batch (2026-11-16,
                                    # O3): a UI audio sink must never
                                    # kill the receive loop — a raising
                                    # sink used to end the session.
                                    if self.on_audio_data:
                                        try:
                                            self.on_audio_data(pcm_data)
                                        except Exception:  # noqa: BLE001
                                            pass
                                    audio_manager.enqueue_playback(pcm_data)
                                    self._notify_status("SPEAKING")
                                    # P2 speed pass: the first audio
                                    # chunk of the turn closes the
                                    # first-sound clock (first-wins
                                    # inside the recorder), and any
                                    # filler still queued is cut —
                                    # the model's words win.
                                    self._ensure_speed_pass(audio_manager)
                                    _lat = getattr(
                                        self, "_turn_latency", None)
                                    if _lat is not None:
                                        _lat.note_first_audio()
                                        _lat.note_first_enqueue()
                                    _bridge = getattr(
                                        self, "_filler_bridge", None)
                                    if _bridge is not None:
                                        _bridge.note_model_audio()

                                # Text transcript — round-3
                                # section C fix queue item 13: a
                                # model_turn text part is a reply
                                # like any other, so it goes
                                # through the same buffer and
                                # flush as the output
                                # transcription (see
                                # _flush_output_transcript)
                                # instead of emitting directly —
                                # the old direct call landed the
                                # assistant bubble above its own
                                # question whenever the user's
                                # words had not flushed yet. The
                                # part is only buffered here; the
                                # flush runs below, AFTER the
                                # input handler for this message,
                                # so a message carrying both the
                                # reply and the input's finish
                                # still orders the question first.
                                if part.text:
                                    # Chat UI (2026-11-16): a stop
                                    # drops the rest of this reply
                                    # — the parts already emitted
                                    # stand as the partial reply.
                                    if getattr(
                                            self,
                                            "_reply_stop_requested",
                                            False):
                                        continue
                                    self._output_transcript_buf += \
                                        part.text
                                    _model_turn_text = True

                        # User input transcription (round-3 fix
                        # #5, #56): accumulate the user's spoken
                        # words; flush once per utterance (see
                        # _handle_input_transcription). Handled
                        # BEFORE the output transcription below:
                        # round-3 section C fix queue item 9
                        # (C1-F2) — when one server message
                        # carries both sides, the question is
                        # accumulated (and flushed on finished /
                        # turn_complete) before the answer is
                        # even looked at, so the answer can
                        # never flush above its own question.
                        self._handle_input_transcription(
                            server_content)

                        # Assistant output transcription
                        # (v5.94 text-replies): accumulate
                        # streamed reply text; flush once per
                        # reply (see _handle_output_transcription).
                        self._handle_output_transcription(
                            server_content)

                        # Round-3 section C fix queue item 13:
                        # the model_turn text parts buffered
                        # above flush here — after the input
                        # handler for this message, so the
                        # question has had its chance to land
                        # first. The flush is the one emission
                        # point: while the input is still open
                        # it defers (the input flush releases
                        # it), and on a closed input the part
                        # lands immediately, one bubble per
                        # part, exactly as before.
                        # record_on_failure carries the
                        # field-check fix #17 guarantee: the
                        # turn was already spoken, so a bubble
                        # that cannot be shown is still
                        # recorded to memory and surfaced, not
                        # kept for a retry that may never come.
                        if _model_turn_text:
                            self._flush_output_transcript(
                                record_on_failure=True)

                        # P2 speed pass: the turn ended — close
                        # the latency record (idempotent: the
                        # interaction-end note below calls the
                        # same finalize, and only the first
                        # emits).
                        if getattr(server_content, "turn_complete", False):
                            self._finalize_turn_latency()

                        # Interruption detection: user started speaking while model was outputting
                        if server_content.interrupted:
                            audio_manager.interrupt_playback()
                            # Round-3 section C fix queue item 9
                            # (C1-F2): the barge-in itself is a
                            # spoken utterance — its words show
                            # first, as one bubble, and only then
                            # the partial reply (the old order
                            # flushed the reply first, so the
                            # answer sat above its own question).
                            # The input flush releases a reply
                            # held for it, so the output call
                            # below is a no-op when held.
                            self._flush_input_transcript()
                            # Preserve the partial transcribed
                            # reply as one bubble.
                            self._flush_output_transcript()
                            self._notify_status("LISTENING")
                            # P2 speed pass: the interruption is
                            # part of the turn's latency record,
                            # and a playing filler is cut with the
                            # reply — the user barged in on both.
                            _lat = getattr(self, "_turn_latency", None)
                            if _lat is not None:
                                _lat.note_interrupted()
                            _bridge = getattr(
                                self, "_filler_bridge", None)
                            if _bridge is not None:
                                _bridge.cancel()

                        # Turn complete — v5.54 (Wave 0): judged
                        # against the tracked interaction status,
                        # not the bare flag: on the 3.x models a
                        # turn can complete while the interaction
                        # runs on (a non-blocking tool call, more
                        # reasoning), and only the IDLE message
                        # that follows earns the LISTENING flip.
                        # The 2.5 models send no status and behave
                        # exactly as before (see the helpers).
                        _status = _interaction_status_of(server_content)
                        _prev_status = self._interaction_status
                        if _status is not None:
                            self._interaction_status = _status
                        _note = _turn_status_notification(
                            _prev_status, _status,
                            bool(server_content.turn_complete))
                        if _note is not None:
                            # MN-2.8: the interaction ended — one
                            # ledger row for the completed turn,
                            # naming the connected model (GAPS-22,
                            # B-V5), not the config pick.
                            _cost_record_live(
                                server_content,
                                model=getattr(self, "connected_model",
                                              None))
                            self._notify_status(_note)
                            # P2 speed pass: the interaction ended
                            # — close the latency record (see the
                            # turn_complete finalize above; only
                            # the first emits).
                            self._finalize_turn_latency()

                    # v5.54 (Wave 0): tool-call CANCELLATION —
                    # on the 3.x models the server may withdraw
                    # calls it has superseded (the user barged
                    # in, the plan changed). Record the ids; the
                    # tool worker never dispatches a cancelled
                    # call and never delivers its result. A call
                    # already executing cannot be recalled — its
                    # (dropped) result is the honest limit, and
                    # the funnel's confirm gate still guards
                    # anything state-changing.
                    try:
                        _cancel = getattr(
                            response, "tool_call_cancellation", None)
                        if _cancel is not None:
                            for _cid in (
                                    getattr(_cancel, "ids", None)
                                    or []):
                                self._cancelled_call_ids.add(str(_cid))
                    except Exception:  # noqa: BLE001 — never break receive
                        pass

                    # Handle tool calls (v5.44, H2): the batch is
                    # handed to the tool worker and the receive
                    # loop moves on IMMEDIATELY — a 30 s command or
                    # a 60 s confirm gate must never deafen the
                    # session while it runs. Execution, ordering
                    # and delivery live in _tool_worker_loop.
                    # Voice-control batch (2026-11-16, B7): the
                    # neighbouring lines all use defensive getattr
                    # — a response without tool_call used to raise
                    # AttributeError into the outer except and end
                    # a healthy session.
                    tool_call = getattr(response, "tool_call", None)
                    if tool_call is not None:
                        self._notify_status("THINKING")
                        if self._tool_queue is None:
                            # _receive_loop run without _run_session
                            # (embedded/test use): start the worker
                            # lazily so the seam still holds.
                            self._tool_queue = asyncio.Queue()
                            self._tool_worker_task = (
                                asyncio.create_task(
                                    self._tool_worker_loop()))
                        self._tool_queue.put_nowait(
                            list(tool_call.function_calls))
                if not _yielded:
                    _empty_receives += 1
                    if _empty_receives >= 3:
                        print("[Live] receive() returned empty 3 times "
                              "in a row — the session is closed in "
                              "fact; ending it for auto-reconnect.")
                        _log_line("Live receive loop ended: receive() "
                                  "returned empty 3 times — session "
                                  "closed, ending for auto-reconnect.")
                        return
                    await asyncio.sleep(0.05)
                else:
                    _empty_receives = 0
                    # Round-3 fix #2 (#55): a model turn completed
                    # (receive() broke after yielding at least one
                    # response) — the next turn starts a NEW capped
                    # tool loop. Batches already enqueued for the
                    # finished turn passed the gate before this
                    # reset; batches for the next turn can only be
                    # enqueued after the loop re-enters receive(),
                    # so the reset cannot steal a round from a
                    # batch already counted.
                    self._reset_live_tool_rounds()

        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            # RS-1 (v5.56): a mid-session 1008 close clears the
            # handle — the server just refused this session's
            # standing, so the GUI's auto-reconnect must start
            # cold rather than re-presenting a refused handle
            # into an immediate second refusal. Any other
            # receive error leaves the handle alone: the next
            # connect may still resume.
            if _is_stale_handle_close(e):
                _clear_resume_handle()
                _log_line("Live session closed with 1008 — "
                          "cleared the saved handle; the "
                          "reconnect starts cold.")
            print(f"[Receive Loop Error]: {e}")
            # v5.32: the raw cause belongs in the sendable log too
            # (this death now ends the session — see _run_session).
            _log_line(f"Receive loop ended on an error: {e}")

    @staticmethod
    def _execute_tool_sync(fc):
        """One tool call through the real registry (executor
        thread only — tools.execute can block for tens of seconds
        on a command or the human confirm gate; the gate's own
        semantics are unchanged, it simply runs here instead of
        on the receive loop). The v5.33 router tolerance
        (use_toolbox relayed-args JSON strings) lives inside
        tools.execute and is inherited untouched.

        Domain-router calls (names starting with "route_")
        dispatch through core/domain_routers.dispatch_route,
        which executes the named tool through the same
        tools.execute funnel — the router is a dispatch
        shape, never a bypass."""
        # Live staleness stage, P2 (2026-11-05): mark this
        # context live-origin for the duration of the call, so
        # the funnel stage can tell a live call from any other
        # caller without guessing from tool names and without
        # changing execute()'s signature. The marker survives
        # route_/toolbox re-entry (same thread, nested
        # execute()) and is cleared in finally — it cannot leak
        # to the next call on this thread or back to the event
        # loop. It carries origin ONLY, never an observation.
        from ..funnel import live_origin as _live_origin
        _origin_token = _live_origin.set_live_origin()
        try:
            fname = fc.name
            fargs = dict(fc.args) if fc.args else {}
            if fname.startswith("route_"):
                from ..brain import domain_routers as _routers_mod  # Phase 3 V1b: moved to core/voice/

                return _routers_mod.dispatch_route(
                    tools, fname, fargs.get("tool", ""),
                    fargs.get("args", {}))
            # Phase 2 (audit 4 C07): the Live native call is
            # converted to the canonical envelope and dispatched
            # through it — the funnel dict comes back verbatim
            # (to_funnel), so this wiring cannot change what the
            # worker does with the result. (The route_ branch
            # above is a dispatch shape, not a tool call: the
            # inner tool it resolves to runs through the same
            # funnel, so it is receipt-covered regardless.)
            from ..funnel import tool_envelope as _envelope  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_envelope moved to core/funnel/

            _inv = _envelope.from_live_call(fname, fargs)
            return _envelope.execute_envelope(_inv, tools.execute) \
                .to_funnel()
        finally:
            _live_origin.reset_live_origin(_origin_token)

    async def _deliver_tool_responses(self, function_responses):
        """Sends tool results back to Gemini (v5.32: via the
        documented send_tool_response; the deprecated unified
        send() is the fallback for older libraries)."""
        # Voice-control batch (2026-11-16, B3): a stale _session
        # used to satisfy this call alone — never deliver into a
        # session that is gone or disconnected. getattr: test
        # fakes without is_connected read as connected.
        if not (self._session and getattr(self, "is_connected", True)):
            return
        send_tool = getattr(self._session,
                            "send_tool_response", None)
        if callable(send_tool):
            await send_tool(function_responses=function_responses)
        else:
            tool_resp = types.LiveClientToolResponse(
                function_responses=function_responses
            )
            await self._session.send(input=tool_resp)

    @staticmethod
    def _observe_name_for_call(fc, result) -> str:
        """B12 plan auto-complete observation name for one executed
        call (router-finish, 2026-10-27). Bookkeeping only: this
        never touches execution, gating, validation, or results —
        it only decides which tool name the plan step matcher
        sees. Direct calls observe under their own name.
        Dispatcher calls observe under the INNER tool's name, so
        a plan step declaring the inner tool completes exactly
        like a direct call:
        - use_toolbox with action=call -> args.tool (existing
          behaviour, kept verbatim, including the fallback to
          the dispatcher name when the result says the inner
          tool did not run);
        - route_* domain-router calls -> args.tool (new). The
          router is a dispatch shape and dispatch_route returns
          the funnel dict directly, so a failed inner call
          already has a non-success status and the observer
          completes nothing — but the same did-not-run /
          unknown-tool string guard is applied anyway, so a
          success-shaped refusal string can never complete a
          step under a name that did not run.
        A route_* call with no tool arg observes under the
        router name (no step declares a router name, so
        nothing completes — the honest outcome)."""
        name = fc.name
        args = fc.args if isinstance(getattr(fc, "args", None), dict) \
            else {}
        inner = ""
        if name == "use_toolbox" and \
                str(args.get("action", "")).lower() == "call":
            inner = str(args.get("tool") or name)
        elif name.startswith("route_"):
            inner = str(args.get("tool") or "")
        if inner and inner != name:
            if isinstance(result, dict):
                # Voice-control batch (2026-11-16, C2): use_toolbox
                # now returns the inner funnel dict for a failed
                # call instead of a "did not run:" string — a
                # non-success status is the same guard, so the
                # step observes under the dispatcher name and
                # can never auto-complete on a call that did not
                # run. (The string sniff below is kept for
                # router results that still carry the text.)
                if result.get("status") not in (None, "success"):
                    return name
                if isinstance(result.get("result"), str) and (
                        "did not run:" in result["result"]
                        or "Unknown tool" in result["result"]):
                    return name
            return inner
        return name

    @staticmethod
    def _observe_args_for_call(fc, observed_name) -> dict:
        """Round-3 fix #9 (A-RU5): the executed call's arguments
        for the B12 observer, matching _observe_name_for_call.
        Direct calls observe under their own name with their own
        args. A routed call observed under the INNER tool
        (use_toolbox action=call, route_*) observes with the
        INNER tool's args, which ride in args["args"] (a JSON
        string from Live models is parsed, as the funnel does) —
        so a step declaring inner args is matched against the
        args the inner tool actually ran with. When the name
        fell back to the dispatcher (inner call failed), the
        dispatcher's own args are returned; no step declares a
        dispatcher name, so nothing completes either way."""
        args = fc.args if isinstance(getattr(fc, "args", None), dict) \
            else {}
        if observed_name != fc.name:
            inner = args.get("args")
            if isinstance(inner, str):
                try:
                    import json as _json

                    inner = _json.loads(inner)
                except ValueError:
                    inner = None
            return dict(inner) if isinstance(inner, dict) else {}
        return args

    def _record_cancelled_during_execution(self, batch, outcomes,
                                           dispatched, upto=None):
        """Receipt the executed calls whose results the delivery
        filter drops (field-check fix #2, T7 #17). A call that
        ran and was then cancelled by the server leaves a
        "cancelled" receipt (dispatched=True — it ran, so it
        must not be silently re-run) stating the result was
        dropped at delivery. Calls that never dispatched are
        not recorded here (the pre-dispatch branch and the
        stopped-remainder helper own those). `upto` bounds the
        scan to the executed prefix when the batch stops
        mid-loop; None scans the whole batch (the delivery
        path)."""
        limit = len(batch) if upto is None else min(upto, len(batch))
        try:
            from .. import tool_receipts as _receipts_mod  # Phase 3 V1b: moved to core/voice/
        except Exception:  # noqa: BLE001 - receipts never
            return  # break the batch
        for index in range(limit):
            if index not in dispatched:
                continue
            if outcomes[index] is None:
                continue
            fc = batch[index]
            if _call_id_of(fc) not in self._cancelled_call_ids:
                continue
            try:
                _receipts_mod.try_record_cancelled(
                    tool=fc.name,
                    args=dict(fc.args) if fc.args else {},
                    summary="cancelled during execution — "
                            "result dropped at delivery",
                    dispatched=True)
            except Exception:  # noqa: BLE001 - receipts never
                pass  # break the batch

    def _record_stopped_remainder(self, batch, dispatched,
                                  from_index: int) -> list[str]:
        """Receipt the calls a stop abandons (field-check fix #2,
        T7 #18). For every remainder index that never dispatched,
        one "cancelled" receipt (dispatched=False — safe to
        re-run) names the drop; a call the server already
        cancelled keeps the pre-dispatch summary. Dispatched
        remainder calls get no receipt (the funnel receipts
        their executions) but are named, so the stop log line
        can list every dropped result. Returns the tool names
        whose results were dropped on stop."""
        dropped: list[str] = []
        try:
            from .. import tool_receipts as _receipts_mod  # Phase 3 V1b: moved to core/voice/
        except Exception:  # noqa: BLE001 - receipts never
            return dropped  # break the batch
        for index in range(from_index, len(batch)):
            fc = batch[index]
            if index in dispatched:
                dropped.append(fc.name)
                continue
            if _call_id_of(fc) in self._cancelled_call_ids:
                summary = "cancelled before dispatch"
            else:
                summary = "batch stopped — call not delivered"
            try:
                _receipts_mod.try_record_cancelled(
                    tool=fc.name,
                    args=dict(fc.args) if fc.args else {},
                    summary=summary)
                dropped.append(fc.name)
            except Exception:  # noqa: BLE001 - receipts never
                pass  # break the batch
        return dropped

    async def _run_tool_batch(self, batch):
        """Executes one tool_call message's calls and delivers
        their results IN CALL ORDER (H2): read-only calls (the
        registry's _READ_ONLY_TOOLS classification — reused, not
        re-listed) are launched together and may overlap;
        state-changing calls run one at a time in issue order.
        on_tool_call fires per call, in order, on this (event
        loop) side as each ordered result lands.

        Barge-in/stop: if the session stops mid-batch, the
        pending delivery is ABANDONED — the results are dropped,
        never sent into a dying (or the next) session. The
        executor threads themselves cannot be killed; their
        results simply go nowhere. Field-check fix #2 (T7 #18):
        the drop is no longer silent — calls that never
        dispatched leave a "cancelled" receipt, and the stop
        log line names every dropped result.

        v5.48 (programme item 28): the same truth governs the
        per-call deadline added here. Each await is wrapped in
        asyncio.wait_for(timeout=deadline_for(...)) when the
        tool's class carries a finite deadline, and the shared
        cool-down registry (core/funnel/tools_handlers.py) is honoured before
        dispatch — but a wait_for timeout does NOT kill the
        executor thread: the deadline is detection + reporting +
        cool-down, not thread murder. The abandoned call may
        still finish inside tools.execute (whose own sync
        deadline also applies to its handler run); its late
        result goes nowhere, exactly like a stopped batch's."""
        loop = asyncio.get_running_loop()
        outcomes: list[Any] = [None] * len(batch)
        read_only: dict[int, asyncio.Future] = {}
        # Field-check fix #2 (T7 #17/#18): the batch tracks
        # which calls actually dispatched, so a stop or a
        # mid-execution cancellation can be receipted honestly
        # — a call that ran and whose result was dropped leaves
        # a "cancelled" receipt; a call that never dispatched
        # leaves the pre-dispatch receipt (or the stopped one).
        dispatched: set[int] = set()
        # P2 speed pass: the tool wait is part of the turn's
        # latency record, and a tool batch is a wait the filler
        # bridge covers — the model asked for tools, the user
        # hears nothing until the results come back.
        self._ensure_speed_pass()
        _lat = getattr(self, "_turn_latency", None)
        if _lat is not None:
            _lat.note_tool_batch_start()
        _bridge = getattr(self, "_filler_bridge", None)
        if _bridge is not None:
            _bridge.arm_tools()
        # v5.48 (programme item 28): a tool+target still cooling
        # down from a recent timeout is not dispatched at all —
        # its result is the shared registry's refusal, in the
        # same dict shape tools.execute returns for its errors.
        # Audit A5: a cool-down refusal is status "refused"
        # (kind "timeout" preserved for kind-branching).
        blocked: dict[int, Any] = {}
        for index, fc in enumerate(batch):
            _fargs = dict(fc.args) if fc.args else {}
            _cooling = timeout_cooldown_active(fc.name, _fargs)
            if _cooling is not None:
                blocked[index] = {"status": "refused",
                                  "kind": "timeout",
                                  "message": _cooling}
        # v5.54 (Wave 0): calls the server has already cancelled
        # (tool_call_cancellation — the 3.x non-blocking shape)
        # are never dispatched at all; the checks below read the
        # cancelled set LIVE, so a cancellation that lands while
        # earlier calls in this batch run still stops the later
        # ones, and one that lands during a call's own execution
        # drops its result at delivery (see the delivery build).
        for index, fc in enumerate(batch):
            _cid = _call_id_of(fc)
            if _cid is not None and _cid in self._cancelled_call_ids:
                _log_line(f"Tool call '{fc.name}' was cancelled "
                          "by the server before dispatch — "
                          "not executed.")
        # Audit A5 (WIR-04): read-only calls are pre-launched ONLY
        # when every dispatchable call in the batch is read-only.
        # The old code launched all reads up front, so a later
        # read could observe pre-write state when an earlier
        # call in the same batch changed it. With any write
        # present, every call runs in issue order below (the
        # second loop awaits each inline executor future in
        # order); blocked/cancelled calls never dispatch, so
        # they do not force serialization.
        _dispatchable = [
            _fc for _i, _fc in enumerate(batch)
            if _i not in blocked
            and _call_id_of(_fc) not in self._cancelled_call_ids]
        # Round-3 fix #2 (#55): the live loop's hard round cap —
        # one batch is one round, _max_live_tool_rounds() rounds
        # per model turn. A batch with no dispatchable call
        # consumes no round; a batch past the cap executes
        # nothing, delivers no results, and the user is told
        # once per turn (see _live_tool_round_allowed). The
        # counter resets at the turn boundary (_receive_loop)
        # and at session start (_run_session) — a user 'continue'
        # starts a NEW capped loop, never extends the old one.
        if _dispatchable and not self._live_tool_round_allowed():
            return
        _all_read_only = all(
            _fc.name in _READ_ONLY_TOOLS for _fc in _dispatchable)
        for index, fc in enumerate(batch):
            if index in blocked:
                continue
            if _call_id_of(fc) in self._cancelled_call_ids:
                continue
            if _all_read_only and fc.name in _READ_ONLY_TOOLS:
                read_only[index] = asyncio.ensure_future(
                    loop.run_in_executor(
                        None, self._execute_tool_sync, fc))
                dispatched.add(index)
        for index, fc in enumerate(batch):
            if not self._running:
                # Field-check fix #2 (T7 #18): a stop abandons
                # the pending outcomes — record the calls that
                # never dispatched, and name every dropped
                # result instead of returning in silence.
                self._record_cancelled_during_execution(
                    batch, outcomes, dispatched, upto=index)
                dropped = [batch[j].name
                           for j in range(index)
                           if outcomes[j] is not None]
                dropped += self._record_stopped_remainder(
                    batch, dispatched, index)
                if dropped:
                    _log_line(
                        "The batch was stopped — the results of "
                        + ", ".join(dropped)
                        + " were not delivered to the model.")
                return
            if _call_id_of(fc) in self._cancelled_call_ids:
                # Phase 4 (C04): a call skipped here never
                # dispatches, so it leaves a receipt stating
                # "cancelled" (dispatched=False — safe to
                # re-run), not a silent gap and not an
                # "unknown" that would block re-execution.
                # The pre-launch loop above is NOT a decision
                # point, so it records nothing (one record
                # per cancelled call, from this loop only).
                try:
                    from .. import tool_receipts as _receipts_mod  # Phase 3 V1b: moved to core/voice/
                    _receipts_mod.try_record_cancelled(
                        tool=fc.name,
                        args=dict(fc.args) if fc.args else {},
                        summary="cancelled before dispatch")
                except Exception:  # noqa: BLE001 — receipts
                    pass  # never break the batch
                continue  # cancelled while earlier calls ran
            if index in blocked:
                result = blocked[index]
            else:
                _fargs = dict(fc.args) if fc.args else {}
                _deadline = deadline_for(fc.name, _fargs)
                _future = (read_only[index] if index in read_only
                           else loop.run_in_executor(
                               None, self._execute_tool_sync, fc))
                dispatched.add(index)
                try:
                    if _deadline is None:
                        result = await _future
                    else:
                        # shield() is load-bearing: cancelling a
                        # RUNNING run_in_executor future only
                        # lands when its thread finishes, so a
                        # bare wait_for(_future) would "time out"
                        # only after a wedged call eventually
                        # returned — detection far too late. The
                        # shield's own future cancels at once;
                        # wait_for raises on time and the inner
                        # executor future runs on, abandoned.
                        result = await asyncio.wait_for(
                            asyncio.shield(_future),
                            timeout=_deadline)
                except asyncio.CancelledError:
                    # Session stop / loop cancellation: never
                    # swallowed into a result — the batch dies
                    # here and the worker loop re-raises it.
                    raise
                except asyncio.TimeoutError:
                    note_tool_timeout(fc.name, _fargs)
                    # Audit A5 (P1-3): the abandoned batch call
                    # gets a durable operation id and an
                    # outcome-unknown result, exactly like the
                    # funnel timeout. Its late result finalizes
                    # the operation exactly once via the future's
                    # done-callback (the callback never delivers
                    # anything — delivery already happened).
                    try:
                        from ..funnel import tool_operations as _ops  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_operations moved to core/funnel/
                        from .. import tool_receipts as _tr_mod  # Phase 3 V1b: moved to core/voice/

                        _batch_op = _ops.record_timeout(
                            fc.name, "live_batch",
                            args_signature=_tr_mod.args_signature(
                                fc.name, _fargs))
                    except Exception:  # noqa: BLE001
                        import uuid as _uuid

                        _batch_op = _uuid.uuid4().hex

                    def _finalize_late(_fut, _op=_batch_op,
                                       _tool=fc.name):
                        try:
                            if _fut.cancelled():
                                _outcome = {"status": "failed",
                                            "note": "cancelled"}
                            elif _fut.exception() is not None:
                                _outcome = {
                                    "status": "failed",
                                    "error": type(
                                        _fut.exception()).__name__}
                            else:
                                _res = _fut.result()
                                _outcome = {
                                    "status": (
                                        _res.get("status")
                                        if isinstance(_res, dict)
                                        else "completed")}
                        except Exception:  # noqa: BLE001
                            _outcome = {"status": "failed"}
                        try:
                            from ..funnel import tool_operations as _ops2  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_operations moved to core/funnel/

                            _ops2.finalize_operation(_op, _outcome)
                        except Exception:  # noqa: BLE001
                            pass

                    try:
                        _future.add_done_callback(_finalize_late)
                    except Exception:  # noqa: BLE001
                        pass
                    result = {
                        "status": "timeout", "kind": "timeout",
                        "outcome": "unknown",
                        "operation_id": _batch_op,
                        "message": (
                            f"'{fc.name}' timed out after "
                            f"{_deadline:g} second(s) and did "
                            f"not finish. The underlying call "
                            f"could not be killed, so it has "
                            f"been abandoned — it may still "
                            f"complete (or stay wedged) in the "
                            f"background. Nothing is claimed "
                            f"about its result: the outcome is "
                            f"UNKNOWN under operation "
                            f"{_batch_op}. Do not automatically "
                            f"retry this call until that "
                            f"operation is reconciled.")}
                except Exception as _exc:  # noqa: BLE001
                    # Audit A5 (WIR-05): one call's unexpected
                    # exception must never abort the whole batch
                    # response. It becomes a stable structured
                    # internal_error result for THIS call only;
                    # siblings still deliver. The log carries a
                    # correlation id + tool name + exception type
                    # only — never secrets or payloads.
                    _cid = _call_id_of(fc) or f"batch-{index}"
                    _log_line(
                        f"Tool call '{fc.name}' failed "
                        f"unexpectedly (id {_cid}): "
                        f"{type(_exc).__name__} — sibling "
                        f"results unaffected.")
                    print(f"[Tool Batch Error]: '{fc.name}' "
                          f"(id {_cid}) raised "
                          f"{type(_exc).__name__}; delivering "
                          f"an internal_error result for that "
                          f"call only.")
                    result = {
                        "status": "failure",
                        "kind": "internal_error",
                        "message": (
                            f"Tool '{fc.name}' failed "
                            f"unexpectedly (id {_cid}); its "
                            f"sibling results are unaffected.")}
            # B12 (v5.45): plan auto-complete — observe the real
            # execution; announcements ride the result returned
            # to the model and the transcript.
            try:
                from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/

                _observe_name = self._observe_name_for_call(fc, result)
                _observe_result = result
                _observe_args = self._observe_args_for_call(
                    fc, _observe_name)
                _anns = _planner.observe_tool_execution(
                    _observe_name, _observe_result, _observe_args)
                if _anns and isinstance(result, dict):
                    result = dict(result)
                    result["result"] = (
                        str(result.get("result") or "")
                        + " " + " ".join(_anns))
                for _ann in _anns:
                    if self.on_transcript:
                        try:
                            self.on_transcript("assistant", _ann)
                        except Exception as exc:  # noqa: BLE001
                            # Field-check fix #20 (F2 follow-up):
                            # a raising announcement consumer
                            # must not swallow silently — the
                            # announcement is the user's only
                            # sight of the plan step completing
                            # (the model already has it in the
                            # result, the planner state is
                            # internal), so the loss note carries
                            # the text itself and the feed keeps
                            # the record. One note per failed
                            # announcement: announcements are
                            # sparse (one per completed step),
                            # not a stream, so there is no flood
                            # to bound — and the loop continues,
                            # so a later announcement in this
                            # call still gets its attempt.
                            self._loss_note(
                                "The plan announcement could "
                                "not be shown in the chat: "
                                "%s — the announcement was: %s"
                                % (_short_exc(exc), _ann))
            except Exception:  # noqa: BLE001 — bookkeeping never breaks a batch
                pass
            outcomes[index] = result
            if not self._running:
                # Field-check fix #2 (T7 #18): as the loop-top
                # return, plus this call's own result — it ran,
                # but its result is dropped with the rest.
                self._record_cancelled_during_execution(
                    batch, outcomes, dispatched, upto=index + 1)
                dropped = [batch[j].name
                           for j in range(index + 1)
                           if outcomes[j] is not None]
                dropped += self._record_stopped_remainder(
                    batch, dispatched, index + 1)
                if dropped:
                    _log_line(
                        "The batch was stopped — the results of "
                        + ", ".join(dropped)
                        + " were not delivered to the model.")
                return  # stopped mid-batch: drop everything pending
            if self.on_tool_call:
                # Audit A5 (WIR-05): a callback exception must not
                # kill the batch either — log it and continue.
                try:
                    fargs = dict(fc.args) if fc.args else {}
                    self.on_tool_call(fc.name, fargs, result)
                except Exception as _cb_exc:  # noqa: BLE001
                    _log_line(
                        f"on_tool_call failed for '{fc.name}': "
                        f"{type(_cb_exc).__name__} — batch "
                        f"continues.")
        if not self._running:
            # Field-check fix #2 (T7 #18): every result was
            # computed and none was delivered — receipt the
            # cancelled executions, and name the dropped tools.
            self._record_cancelled_during_execution(
                batch, outcomes, dispatched)
            dropped = [fc.name for j, fc in enumerate(batch)
                       if outcomes[j] is not None]
            if dropped:
                _log_line(
                    "The batch was stopped — the results of "
                    + ", ".join(dropped)
                    + " were not delivered to the model.")
            return
        # Field-check fix #2 (T7 #17): an executed call whose
        # result the filter below drops still leaves a receipt
        # stating it was cancelled — the execution happened,
        # and the receipt is the only trace of it.
        self._record_cancelled_during_execution(
            batch, outcomes, dispatched)
        # v5.54 (Wave 0): a call cancelled at ANY point — before
        # dispatch, mid-batch, or during its own execution — has
        # its result dropped here: the server said the call is
        # superseded, and delivering its result would let the
        # model act on a plan it already abandoned. (A call that
        # finished executing before its cancellation arrived did
        # run — that limit is the API's shape, recorded in the
        # receive loop; its transcript line already fired.)
        function_responses = [
            types.FunctionResponse(
                name=fc.name,
                id=fc.id,
                response={"result": outcomes[index]},
            )
            for index, fc in enumerate(batch)
            if _call_id_of(fc) not in self._cancelled_call_ids
        ]
        if function_responses:
            # P2 speed pass: the tool wait ends when the results
            # go back — the model's next audio is timed from
            # here, not from the batch start. The bridge
            # disarms with the wait (see note_tool_results).
            _lat = getattr(self, "_turn_latency", None)
            if _lat is not None:
                _lat.note_tool_results()
            _bridge = getattr(self, "_filler_bridge", None)
            if _bridge is not None:
                _bridge.note_tool_results()
            try:
                await self._deliver_tool_responses(function_responses)
            except Exception:  # noqa: BLE001 - field-check
                # fix #2 (T5): the tools already ran, but the
                # model never got their results — retry once,
                # then surface the drop instead of letting the
                # worker loop swallow it as a bare print
                try:
                    await self._deliver_tool_responses(
                        function_responses)
                except Exception as exc2:  # noqa: BLE001
                    _names = ", ".join(
                        getattr(fr, "name", "?")
                        for fr in function_responses)
                    self._loss_note(
                        "The results of %s ran but failed to "
                        "deliver to the model (%s); the model "
                        "never saw them — rephrase or repeat "
                        "the request."
                        % (_names, _short_exc(exc2)))

    def _reset_live_tool_rounds(self) -> None:
        """Round-3 fix #2 (#55): the turn-boundary reset for the
        live tool-round cap — called when a model turn completes
        (_receive_loop) and at session start (_run_session), so a
        user 'continue' starts a NEW capped loop, never extends
        the old one. Also the seam the cap tests drive to simulate
        a turn end."""
        self._live_tool_rounds = 0
        self._live_tool_cap_announced = False

    def _live_tool_round_allowed(self) -> bool:
        """Round-3 fix #2 (#55): the live loop's hard round cap.
        One batch is one round; _max_live_tool_rounds() rounds
        (8, parity with the cloud text loop) per model turn.
        Returns True and consumes a round, or False when the cap
        is hit — the batch then executes nothing. The user is
        told once per turn (chat line + activity.log, via
        _loss_note); further refused batches in the same turn
        log only, so the chat is never flooded."""
        cap = _max_live_tool_rounds()
        rounds = getattr(self, "_live_tool_rounds", 0)
        if rounds >= cap:
            if not getattr(self, "_live_tool_cap_announced", False):
                self._live_tool_cap_announced = True
                self._loss_note(
                    "I stopped after %d tool rounds in this turn — "
                    "that's the limit, so I don't keep going in "
                    "circles. The calls after that didn't run. "
                    "Say 'continue' and I'll have another %d rounds "
                    "to pick it up." % (cap, cap))
            else:
                _log_line(
                    "Live tool-round cap: refused a batch at %d "
                    "rounds this turn." % cap)
            return False
        self._live_tool_rounds = rounds + 1
        return True

    async def _tool_worker_loop(self):
        """Drains the tool queue for the session's life, one batch
        at a time — batches therefore deliver strictly in the
        order the model issued them, across messages too.

        Round-3 fix #2 (#55) overturns the old B7 pin: the live
        loop now HAS a finite hard round cap — one batch is one
        round, _max_live_tool_rounds() (8) rounds per model turn,
        enforced in _run_tool_batch, reset at the turn boundary
        (_receive_loop) and at session start (_run_session). The
        text brains' caps (core/voice/local_client.py,
        core/voice/cloud_text_client.py) are the parity precedent;
        a looping live session is now bounded the same way."""
        while True:
            batch = await self._tool_queue.get()
            try:
                await self._run_tool_batch(batch)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — one bad batch
                print(f"[Tool Worker Error]: {e}")  # never kills it
                _log_line(f"Tool worker batch failed: {e}")
            finally:
                self._tool_queue.task_done()
