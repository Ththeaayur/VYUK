from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import Callable
from typing import Any

try:
    from google import genai
    from google.genai import types
except Exception:  # noqa: BLE001 — a build without google-genai must
    # still import: the local brain keeps working, and cloud paths
    # report the missing library honestly instead of crashing.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from .. import injection
from ..config import config, save_env_setting
from ..funnel.tools_registry import tools
from ..languages import reply_language_directive
from ..memory import episodes
from ..memory.memory import memory_store

# INTENTIONAL (skills split B47): connect/session-spine mixin. Each
# sibling import is exactly a name its methods resolve by bare name
# (B45 P3 lesson, per-mixin): resume/realtime/proxy/helpers families
# + the B46 error-cost names (_error_text, _log_line,
# _is_model_rejection — _try_candidates resolves all three).
from .live_client_error_cost import (
    _error_text,
    _is_model_rejection,
    _log_line,
)
from .live_client_helpers import (
    _ALREADY_CONNECTED_BACKOFF_S,
    _bump_epoch,
    _offered_declarations,
    _WATCHDOG_AUDIBLE_SILENCE_S,
    _WATCHDOG_SEND_FAIL_S,
    _WATCHDOG_SILENCE_S,
    _WATCHDOG_TICK_S,
    _WATCHDOG_VOICE_QUIET_S,
    CONNECT_BUDGET_S,
)
from .live_client_proxy import (
    _apply_system_proxy_env,
    _remove_exported_proxy,
)
from .live_client_realtime import (
    _build_realtime_input_config,
    _is_input_config_rejection,
    _without_realtime_input_config,
    LIVE_PREFIX_PADDING_MS,
    LIVE_SILENCE_DURATION_MS,
)
from .live_client_resume import (
    _ambient_packet_text,
    _build_context_window_compression,
    _clear_resume_handle,
    _client_is_enterprise,
    _config_resume_handle,
    _config_transparent,
    _connect_timeout_for,
    _is_already_connected_close,
    _is_stale_handle_close,
    _is_transparent_rejection,
    _live_model_candidates,
    _load_resume_handle,
    _resume_config_for_mode,
    _strip_cleared_handle,
    _without_resume_handle,
    _without_transparent,
)


class GeminiLiveSessionLifecycleMixin:
    # INTENTIONAL (skills split B47): connect/session spine — __init__,
    # prompt/model adoption, session-run + watchdog loops, start,
    # candidate attempts. Cross-mixin self-calls
    # (to receive/send mixins) resolve through the composed class MRO.


    def __init__(self):
        self.is_connected = False
        self._session = None
        self._client: genai.Client | None = None
        # v5.51 (item 13): the constructed client's API mode —
        # False = Developer API (api_key auth), True = Enterprise
        # (Vertex). Set in start() where the client is built; it
        # decides whether the resumption config may carry the
        # Enterprise-only `transparent` parameter.
        self._enterprise_mode: bool = False
        self._running = False
        self._send_task: asyncio.Task | None = None
        self._receive_task: asyncio.Task | None = None
        # H2 (v5.44): tool calls execute OFF the receive loop — a
        # worker task drains this queue of tool-call batches.
        self._tool_queue: asyncio.Queue | None = None
        self._tool_worker_task: asyncio.Task | None = None
        # v5.54 (Wave 0, wave-32 stay path): the 3.x Live models
        # run tool calls NON-BLOCKING by default — the model may
        # cancel calls it has superseded (tool_call_cancellation)
        # and the interaction outlives the turn that issued them
        # (interaction_status). The adapter tracks both: ids the
        # server cancelled (never executed if still queued, their
        # results never delivered), and the latest interaction
        # status (turn completion is judged against it — see
        # _turn_status_notification).
        self._cancelled_call_ids: set[str] = set()
        self._interaction_status: str | None = None
        # Round-3 fix #2 (#55): the live tool-round cap's
        # per-turn state — rounds used this model turn, and
        # whether the cap announcement already fired this turn.
        # Reset at the turn boundary (_receive_loop) and at
        # session start (_run_session).
        self._live_tool_rounds = 0
        self._live_tool_cap_announced = False
        # Text-replies (v5.94): buffer for assistant output-
        # transcription chunks. The server streams transcribed
        # reply text in small pieces; the session accumulates
        # them and emits ONE on_transcript("assistant", ...) per
        # reply on flush (see _flush_output_transcript) so a
        # reply lands as a single bubble on the existing path.
        # Empty between replies; never fed to audio playback.
        self._output_transcript_buf: str = ""
        # Round-3 fix #5 (#56): buffer for USER input-
        # transcription chunks — the mirror of the output
        # buffer. The server streams a transcription of the
        # user's spoken words in small pieces; the session
        # accumulates them and emits ONE on_transcript("user",
        # ...) per utterance on flush (see
        # _flush_input_transcript), verbatim as STT heard —
        # never cleaned up — so the user's words land as a
        # single "You" bubble on the existing local-path
        # channel. Empty between utterances; never fed to
        # audio playback.
        self._input_transcript_buf: str = ""
        self._ambient_packet: str = ""  # N4: packet this session holds
        # v5.54.1 watchdog stamps (monotonic seconds; None until
        # the session starts): last response-plane activity from
        # the server, and last voice-level audio sent to it.
        self._last_response_at: float | None = None
        self._last_voice_at: float | None = None
        self._watchdog_task: asyncio.Task | None = None
        # P2 speed pass (2026-11-16): the per-turn latency
        # recorder, the recent-turns ledger, the filler bridge,
        # and the filler's watchdog task. Created in
        # _run_session (see _ensure_speed_pass); None until a
        # session runs.
        self._turn_latency = None
        self._latency_ledger = None
        self._filler_bridge = None
        self._filler_task: asyncio.Task | None = None
        # RS-1 (v5.56): True once this session received a
        # goAway message (see _receive_loop). The flag is a
        # fixture seam and a diagnostic: the session end it
        # causes is what drives the resume reconnect, and the
        # handle is deliberately NOT cleared on goAway (an
        # expected server-side end is exactly when the handle
        # should be re-presented).
        self._go_away_received: bool = False

        # Callbacks
        self.on_status_change: Callable[[str], None] | None = None
        self.on_transcript: Callable[[str, str], None] | None = None
        # Chat UI (2026-11-16): per-chunk callback for the live
        # reply — the same server transcription the buffer
        # accumulates, emitted as it arrives so the transcript
        # streams in real time with the voice. A chunk callback
        # must never break the loop: every emission is guarded
        # (see _handle_output_transcription).
        self.on_transcript_chunk: Callable[[str, str], None] | None = None
        self.on_audio_data: Callable[[bytes], None] | None = None
        self.on_tool_call: Callable[[str, dict[str, Any], Any], None] | None = None
        # Chat UI (2026-11-16): Stop. Set by stop_reply(); while
        # set, transcription chunks are accumulated but not
        # emitted (the GUI has already finalised the reply as
        # stopped), model-turn text parts are dropped, and the
        # turn-end flush logs the partial text to memory without
        # emitting a second bubble. Cleared by the flush — a
        # flush is always a turn end.
        self._reply_stop_requested: bool = False

    def _notify_status(self, status: str):
        if self.on_status_change:
            self.on_status_change(status)

    def _notify_retry(self, detail: str) -> None:
        """Put a connect retry on screen (round-3 fix #6, #58).

        Every retry point in `_try_candidates` / `start()` calls
        this instead of only printing + logging: the user watches
        CONNECTING with no word that a retry is happening. The
        "CONNECTING:" prefix keeps every status consumer in the
        connecting presentation — the GUI's `_on_status` CONNECTING
        branch, the room status word ("thinking"), the orb's
        CONNECTING swell — while `_on_status` shows the retry
        words themselves in the state label. `detail` names the
        real cause (timed out / refused / couldn't resume) —
        never a fake progress claim. The 45 s envelope is
        untouched: this only surfaces retries, never extends or
        resets the deadline.
        """
        self._notify_status(f"CONNECTING: retrying — {detail}")

    def _build_live_prompt(self, router_prompt: str = "") -> str:
        """The connect-time system prompt (extracted from start(),
        v5.43): the persona (core/config.py's system_instruction,
        byte-identical — continuity NEVER edits it), the reply-
        language directive, the memory block read once at start,
        then the continuity digest as its OWN block, then the
        injection rule, then the tool-router prompt when routed.

        The digest (v5.43, B10/B14 — core/episodes.continuity_
        digest) is the Live brain's carry-over: this session has
        no seedable history, so earlier today arrives as one
        compact, timestamped, clearly-labelled block alongside
        the memory block. When there is nothing earlier (empty
        journal and logs) the digest is "" and the assembled
        prompt is byte-identical to the pre-v5.43 assembly."""
        full_prompt = config.system_instruction
        # v5.41: non-English home directive ("" for English).
        full_prompt += reply_language_directive()
        memories = memory_store.get_memory_context_prompt()
        if memories:
            full_prompt += f"\n\n{memories}"
        # Conversion Wave 8: PF-28 learning-feed fact lines +
        # sleeptime ranked file-store block, as one optional
        # supplement block ("" on a default install — empty feed
        # + empty file store — so the prompt stays byte-identical
        # to before; expiry honoured by fact_prompt_lines, the
        # frozen DB never touched by the sleeptime block).
        try:
            from .. import w8_wiring as _w8  # Phase 3 V1b: moved to core/voice/

            _supplements = _w8.prompt_supplements()
        except Exception:  # noqa: BLE001 — supplements never break
            _supplements = ""
        if _supplements:
            full_prompt += _supplements
        # Ambient context (v5.44, N4): the NOSE snapshot rides at
        # the same place the memory block goes. An empty packet
        # adds nothing — the prompt stays byte-identical to before.
        ambient_packet = _ambient_packet_text()
        self._ambient_packet = ambient_packet
        if ambient_packet:
            full_prompt += f"\n\n{ambient_packet}"
        try:
            digest = episodes.continuity_digest()
        except Exception:  # noqa: BLE001 — continuity never blocks
            digest = ""
        if digest:
            full_prompt += f"\n\n{digest}"
        # Injection defence (v5.3): tool results reach this brain
        # wrapped as untrusted data (core.injection producers);
        # the system instruction carries the matching rule.
        full_prompt += f"\n\n{injection.PROMPT_RULE}"
        if router_prompt:
            full_prompt += f"\n\n{router_prompt}"
        # Conversion Wave 8: context ledger + prompt-cache-first
        # assembly, enforced BEFORE the brain call. The stable
        # prefix (persona + language directive) is assembled
        # byte-identical with a SHA-256 cache key (recorded on
        # the session for cache-hit checks); the full prompt is
        # pre-flighted against the hard budgets — over budget
        # drops the supplements first, then refuses fail-closed
        # (ValueError propagates; an over-budget prompt is never
        # sent). Ledger/pre-flight failures other than budget
        # refusal never break session start.
        try:
            from .. import w8_wiring as _w8  # Phase 3 V1b: moved to core/voice/
        except Exception:  # noqa: BLE001
            _w8 = None
        if _w8 is not None:
            try:
                _cached = _w8.note_stable_prefix(
                    [config.system_instruction,
                     reply_language_directive()])
                self._w8_cache_key = _cached.cache_key
            except Exception:  # noqa: BLE001 — cache key is advisory
                pass
            full_prompt, _preflight = _w8.enforce_prompt(
                full_prompt, memories, _supplements)
        return full_prompt

    def _adopt_working_model(self, model_id: str):
        """A fallback Live model answered: remember it as the default
        so the next connect goes straight to a name Google offers,
        and leave a trace in the activity log + a benign SETUP line."""
        print(f"[Live] Connected with fallback voice model '{model_id}' — saving it as the default.")
        _log_line(f"Configured model '{config.model_name}' was rejected; "
                  f"fallback '{model_id}' connected and was saved as MODEL_NAME.")
        try:
            save_env_setting("MODEL_NAME", model_id)
            config.model_name = model_id
        except Exception:  # noqa: BLE001
            pass
        self._notify_status(
            f"SETUP: Connected with the '{model_id}' voice model — saved as her cloud voice model.")

    async def _run_session(self, session, audio_manager):
        """Runs an established Live session until it ends — the exact
        body start() used to run inside its `async with` block."""
        self._session = session
        self.is_connected = True
        # The session's own loop, captured for thread-side seams
        # that must schedule work onto it (N4's ambient refresh).
        self._event_loop = asyncio.get_running_loop()
        # v5.54.1: the watchdog's stamps start at session birth
        # — a fresh session owes no response yet, and the first
        # voice after birth starts the silence clock honestly.
        _now = time.monotonic()
        self._last_response_at = _now
        self._last_voice_at = None
        # Mic remake (v6.3.x): the audible stamp (sound above
        # the 0.002 floor, even when it never crosses the voice
        # floor) and the send-drop count, both session-scoped
        # like the voice stamp above.
        self._last_audible_at = None
        # Round-3 fix Q8: the typed-turn stamp — the time a
        # genuine user text turn was last sent (send_text).
        # Session-scoped like the voice stamp; bracketed
        # internal injections never stamp it.
        self._last_text_at = None
        self._send_drop_count = 0
        self._go_away_received = False
        # Voice-control batch (2026-11-16, B1/B4): the send
        # streak stamps (success clears the failure streak;
        # a failure starts it) and the cross-session state
        # that must not leak into the next session — a stale
        # IN_PROGRESS vetoes the LISTENING flip on no-status
        # models, a stale cancelled-id set grows unboundedly,
        # and a partial output-transcript buffer flushes as a
        # stray bubble into the next session's first turn.
        self._last_send_success_at = None
        self._last_send_failure_at = None
        self._interaction_status = None
        self._cancelled_call_ids = set()
        # Round-3 fix #2 (#55): a fresh session run starts with
        # a fresh tool-round budget (see _reset_live_tool_rounds).
        self._live_tool_rounds = 0
        self._live_tool_cap_announced = False
        self._output_transcript_buf = ""
        self._notify_status("LISTENING")

        # Launch concurrent send and receive loops, plus the H2
        # tool worker (v5.44): tool execution lives there, never
        # on the receive loop.
        self._tool_queue = asyncio.Queue()
        self._tool_worker_task = asyncio.create_task(
            self._tool_worker_loop())
        self._send_task = asyncio.create_task(self._send_audio_loop(audio_manager))
        self._receive_task = asyncio.create_task(self._receive_loop(audio_manager))
        # P2 speed pass (2026-11-16): the per-session latency
        # recorder + filler bridge, pre-created here so the
        # filler watchdog exists from session birth (the loops
        # create them lazily too, for test-driven use — see
        # _ensure_speed_pass). The watchdog is NOT in the
        # FIRST_COMPLETED set below: it must never end the
        # session, and it is cancelled with the rest at
        # teardown.
        self._ensure_speed_pass(audio_manager)
        self._filler_task = asyncio.create_task(
            self._filler_watchdog_loop())
        # RS-1 (v5.56): the response-plane watchdog runs as a
        # fourth task in the FIRST_COMPLETED set below — when
        # it fires (returns), the session ends exactly like
        # any other loop death: DISCONNECTED surfaces and the
        # GUI's auto-reconnect does its job, and start()'s
        # resume-first config re-presents the surviving handle
        # (a watchdog death is an unexpected drop, never a
        # clean close, so the handle is NOT cleared here).
        self._watchdog_task = asyncio.create_task(
            self._live_watchdog_loop())

        # v5.32: the session ends when EITHER loop ends. The old
        # gather() waited for BOTH: when the receive side died
        # (the server closed the socket), the send loop — which
        # only stops on request — kept the corpse "connected"
        # forever, a deaf zombie the GUI's auto-reconnect could
        # never see, let alone repair. Now the first loop to
        # finish ends the session and the other is cancelled, so
        # a drop surfaces as DISCONNECTED and the reconnect
        # machinery can do its job.
        done, pending = await asyncio.wait(
            {self._send_task, self._receive_task,
             self._tool_worker_task, self._watchdog_task},
            return_when=asyncio.FIRST_COMPLETED)
        # P2 speed pass: the filler watchdog dies with the
        # session — it is cancelled here with the rest, never
        # left ticking into the next session.
        _filler_task = getattr(self, "_filler_task", None)
        if _filler_task is not None:
            pending = set(pending)
            pending.add(_filler_task)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if not task.cancelled():
                # Retrieve (the loops report their own errors);
                # this only keeps asyncio from logging them as
                # never-retrieved.
                task.exception()

    async def _live_watchdog_loop(self):
        """Response-plane watchdog — RS-1 (v5.56), the ONE
        watchdog (see the constants block: this replaces
        v5.54.1's 45 s emergency bound, threshold now 7 s,
        tick now 1 s).

        Every tick, while the session claims connected: when
        voice-level audio was sent AFTER the server's last
        response-plane activity, that activity is older than
        _WATCHDOG_SILENCE_S, AND no voice-level audio has been
        sent for _WATCHDOG_VOICE_QUIET_S (round-3 fix Q3: the
        user finished speaking — while voice is still arriving
        the server's silence is correct, it answers once the
        turn ends, so a long monologue must not trip), the
        session is dead in fact — return, which ends _run_session
        exactly like any other loop death (DISCONNECTED surfaces,
        the GUI's auto-reconnect takes over and re-presents the
        surviving resumption handle). Before returning it logs
        the death and notifies LIVE_WATCHDOG, which the GUI
        renders as one plain-language chat line — the user is
        told the connection went quiet and is reconnecting,
        never left talking to a corpse that claims to listen.
        Never raises; a cancelled watchdog is a normal session
        end, not an error. The handle is NOT cleared here: a
        watchdog death is an unexpected drop, and clearing
        would turn every resume reconnect into a cold start.
        The audible rule below carries the same quiescence
        guard (same shape, same bug for a quiet monologue);
        the send-failure rule is untouched (not a silence
        rule). Round-3 fix Q8 adds a fourth rule, the typed
        turn: a typed message unanswered for _WATCHDOG_SILENCE_S
        ends the session with a LIVE_DEAD notice — the
        voice/audible rules never see a typed turn, so without
        it a typed turn into a dead session sat forever. A typed
        turn is complete at send time, so it carries no
        quiescence condition."""
        try:
            while self._running:
                await asyncio.sleep(_WATCHDOG_TICK_S)
                if not self._running or not self.is_connected:
                    continue
                last_resp = self._last_response_at
                last_voice = self._last_voice_at
                if last_resp is None:
                    continue
                if last_voice is not None and last_voice > last_resp:
                    # Round-3 fix Q3: the trip also requires
                    # voice quiescence — the user has stopped
                    # speaking for _WATCHDOG_VOICE_QUIET_S. While
                    # voice is still arriving the server's silence
                    # is its correct behaviour (it answers once
                    # the turn ends), so a long monologue must not
                    # trip; once the user stopped, 7 s of server
                    # silence means the session is dead.
                    _now = time.monotonic()
                    silence = _now - last_resp
                    quiet = _now - last_voice
                    if (silence > _WATCHDOG_SILENCE_S
                            and quiet > _WATCHDOG_VOICE_QUIET_S):
                        print(f"[Live] Watchdog: no server response for "
                              f"{silence:.0f}s after voice was sent — "
                              "ending the session so auto-reconnect can "
                              "repair it.")
                        _log_line(f"Live watchdog fired: {silence:.0f}s "
                                  "without a server response after voice "
                                  "was sent — session ended for "
                                  "auto-reconnect.")
                        self._notify_status("LIVE_WATCHDOG")
                        return
                # Mic remake (v6.3.x), second rule: audible audio
                # (above the 0.002 floor, below the voice floor)
                # sent after the last response, with 30 s of
                # silence since — the 7 s voice rule can never
                # see a quiet voice, so without this a half-open
                # session stays "connected" indefinitely. The
                # longer bound is deliberate: sub-floor audio is
                # weaker evidence than voice, so it gets more
                # time before the session is ended.
                last_aud = getattr(self, "_last_audible_at", None)
                if last_aud is not None and last_aud > last_resp:
                    # Round-3 fix Q3, same guard as rule 1: a
                    # quiet monologue over 30 s is the same bug
                    # at the audible floor — the trip needs the
                    # user to have stopped sending audible audio
                    # for _WATCHDOG_VOICE_QUIET_S.
                    _now = time.monotonic()
                    silence = _now - last_resp
                    quiet = _now - last_aud
                    if (silence > _WATCHDOG_AUDIBLE_SILENCE_S
                            and quiet > _WATCHDOG_VOICE_QUIET_S):
                        print(f"[Live] Watchdog: no server response for "
                              f"{silence:.0f}s after audible audio was "
                              "sent — ending the session so "
                              "auto-reconnect can repair it.")
                        _log_line(f"Live watchdog fired: {silence:.0f}s "
                                  "without a server response after "
                                  "audible audio was sent — session "
                                  "ended for auto-reconnect.")
                        self._notify_status("LIVE_WATCHDOG")
                        return
                # Round-3 fix Q8: the typed-turn rule — a typed
                # turn is a turn too, and the voice/audible rules
                # above never see one. A typed turn into a
                # dead-on-arrival session (connected, the server
                # never yields anything) sat forever with no
                # detection, no notice, no retry — section B F2.
                # A typed turn is complete the moment it is sent,
                # so there is no "still speaking" phase and no
                # quiescence condition: the trip is simply no
                # server response at all for _WATCHDOG_SILENCE_S
                # (the same 7 s budget as rule 1 — the server's
                # silence budget, not a new number) after the text
                # was sent. Any yielded response after the text
                # disarms it (last_text > last_resp fails), so a
                # healthy session answering at its normal 1–3 s
                # latency can never trip this. Bracketed internal
                # injections never stamp _last_text_at (see
                # send_text), so a notice the model is told not
                # to answer cannot arm it.
                last_text = getattr(self, "_last_text_at", None)
                if last_text is not None and last_text > last_resp:
                    _now = time.monotonic()
                    _age = _now - last_text
                    if _age > _WATCHDOG_SILENCE_S:
                        print(f"[Live] Watchdog: no server response for "
                              f"{_age:.0f}s after a typed message was "
                              "sent — ending the session so "
                              "auto-reconnect can repair it.")
                        _log_line(f"Live watchdog fired: {_age:.0f}s "
                                  "without a server response after a "
                                  "typed message was sent — session "
                                  "ended for auto-reconnect.")
                        self._notify_status("LIVE_DEAD")
                        return
                # Voice-control batch (2026-11-16, B1), third rule:
                # the send path itself has failed continuously —
                # every send in the streak raised/timed out, so
                # the voice/audible stamps above (which only fire
                # on a SUCCESSFUL send) never armed. A failing
                # send is a dead session even if the receive side
                # still answers: the user is speaking into a void.
                # The streak start is cleared by any successful
                # send, so transient failures never trip this.
                last_fail = getattr(self, "_last_send_failure_at", None)
                if last_fail is not None:
                    failing_s = time.monotonic() - last_fail
                    if failing_s > _WATCHDOG_SEND_FAIL_S:
                        print(f"[Live] Watchdog: audio sends failing "
                              f"for {failing_s:.0f}s — ending the "
                              "session so auto-reconnect can repair "
                              "it.")
                        _log_line(f"Live watchdog fired: audio sends "
                                  f"failing for {failing_s:.0f}s — "
                                  "session ended for auto-reconnect.")
                        self._notify_status("LIVE_WATCHDOG")
                        return
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 — a guard never breaks audio
            print(f"[Live] Watchdog error (ignored): {e}")

    async def start(self, audio_manager):
        """Initiates the live bidirectional session.

        Self-healing model selection: tries the configured Live model,
        then the other current Live ids (KNOWN_LIVE_MODELS), but ONLY
        when Google rejected the model name itself. A bad key or a
        network failure aborts immediately with the real error — the
        fallback chain must never mask those."""
        if not config.api_key:
            self._notify_status("MISSING_API_KEY")
            return
        if genai is None:
            self._notify_status(
                "ERROR: The Google library (google-genai) isn't installed "
                "in this build, so the cloud brain can't start.")
            return

        self._running = True
        self._notify_status("CONNECTING")
        injection.clear_taint()  # fresh session, fresh trust state (v5.3)
        _bump_epoch("live start")  # v5.48 (programme item 20)

        try:
            # Pre-flight payload check (v5.32): build the
            # declarations and measure them BEFORE the client
            # or any network object exists. The offered-set
            # builders never refuse for budget reasons — no
            # profile flies the fitted list anymore (see
            # _offered_declarations) — so an over-budget
            # fitted list cannot kill any session.
            # Tool router (v5.33) + domain routers
            # (2026-10-27): offer the profile's resident set
            # + one router declaration per catalogue domain +
            # the use_toolbox dispatcher (assistant ~29 KB
            # incl. prompt, desktop ~41 KB incl. prompt,
            # before routers) instead of the whole fitted
            # registry. Fallback: a router failure falls
            # back to the desktop set, logged; router-off
            # mode keeps resident + domain routers without
            # use_toolbox.
            declarations, routed, _profile_label = \
                _offered_declarations(tools, config)
            print(f"[Live] Tool profile '{_profile_label}': "
                  f"offering {len(declarations)} declarations"
                  + ("; the full registry stays reachable "
                     "through use_toolbox and the domain routers."
                     if routed else
                     "; domain routers carry the rest, "
                     "without use_toolbox."))
            print(f"[Live] Pre-flight: tool payload "
                  f"{tools.gemini_payload_size(declarations)} bytes "
                  f"(budget {tools._GEMINI_DECL_BUDGET}).")

            proxy_value = _apply_system_proxy_env()
            self._client = genai.Client(api_key=config.api_key)
            # v5.51 (item 13): record the API mode at construction
            # — it shapes the resumption config built below.
            self._enterprise_mode = _client_is_enterprise(self._client)

            # Build memory-augmented system instruction (the
            # assembly lives in _build_live_prompt — persona +
            # memory block + the v5.43 continuity digest as its
            # own block + the injection rule + the router prompt).
            router_prompt = ""
            if routed:
                from ..funnel.toolbox import ROUTER_PROMPT  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/
                router_prompt = ROUTER_PROMPT
            full_prompt = self._build_live_prompt(router_prompt)

            # Prepare Live configuration. Gemini-safe declarations
            # (v5.27): the raw registry schemas contain shapes the
            # Live API rejects at connect time (non-string enums,
            # empty nested objects, parameters blocks with no fields).
            # declarations were built by the pre-flight above.
            live_tools = [types.Tool(function_declarations=declarations)] if declarations else None

            # A8 (v5.44): explicit realtime input config, matched
            # to the local feel (see the constants at module top).
            # Fail soft: a library without these types yields None
            # and the config is built exactly as before.
            realtime_config = _build_realtime_input_config()
            config_kwargs: dict[str, Any] = {}
            # B9 (v5.45): resumption (with the persisted handle
            # when one exists — resume is tried BEFORE cold start)
            # + context-window compression, both fail-soft.
            # v5.51 (item 13): transparent issuance is requested
            # only in Enterprise mode — on the Developer API the
            # parameter itself is rejected and kills the connect,
            # so Developer sessions are built handle-only.
            # RS-1 (v5.56): the mode branch — _load already
            # enforces the 10-min window, so a handle offered
            # here is fresh; a stale one reads as None and the
            # connect starts cold.
            _resume_cfg = _resume_config_for_mode(
                _load_resume_handle(), self._enterprise_mode)
            if _resume_cfg is not None:
                config_kwargs["session_resumption"] = _resume_cfg
                if _resume_cfg.handle:
                    print("[Live] Resumption: offering the saved "
                          "session handle for a resume-first connect.")
            _compression_cfg = _build_context_window_compression()
            if _compression_cfg is not None:
                config_kwargs["context_window_compression"] = _compression_cfg
            if realtime_config is not None:
                config_kwargs["realtime_input_config"] = realtime_config
                applied = (
                    "[Live] Realtime input config applied: automatic "
                    "activity detection on, start-of-speech HIGH, "
                    "end-of-speech LOW, "
                    f"silence {LIVE_SILENCE_DURATION_MS} ms, "
                    f"prefix padding {LIVE_PREFIX_PADDING_MS} ms.")
                print(applied)
                _log_line(applied[len("[Live] "):])

            session_config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                # Text-replies (v5.94): assistant-side output
                # transcription. The model still answers by
                # audio (modality unchanged, voice sounds exactly
                # as before); the server additionally streams a
                # transcription of its spoken reply, which the
                # receive loop turns into one text bubble per
                # reply.
                output_audio_transcription=types.AudioTranscriptionConfig(),
                # Round-3 fix #5 (#56): user-side input
                # transcription, per ruling #56 — "what you say
                # is transcribed on screen as you speak, so
                # mishearing is visible immediately ... exactly
                # what the STT heard, never a cleaned-up
                # version". The server streams a transcription
                # of the user's spoken words, which the receive
                # loop turns into one "You" bubble per utterance
                # on the existing local-path channel (see
                # _handle_input_transcription). This overturns
                # the v5.94 "deliberately NOT enabled" — the
                # ruling's live path now holds. Guards stand:
                # mic off/denied means no audio is sent, so no
                # input transcription arrives and nothing is
                # transcribed. If the server sends no
                # transcription, nothing changes: voice-only, as
                # today.
                input_audio_transcription=types.AudioTranscriptionConfig(),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=config.voice_name
                        )
                    )
                ),
                system_instruction=types.Content(
                    parts=[types.Part.from_text(text=full_prompt)]
                ),
                tools=live_tools,
                **config_kwargs,
            )

            # B2 (v5.44): ONE connect deadline for the whole start
            # — every candidate in this pass and the proxy retry
            # below share it (see _try_candidates).
            connect_deadline = time.monotonic() + CONNECT_BUDGET_S
            candidates = _live_model_candidates()
            connected, timed_out, last_rejection = (
                await self._try_candidates(
                    candidates, session_config, audio_manager,
                    deadline=connect_deadline))
            if not connected and proxy_value:
                # v5.29: the exported system proxy is OUR guess, not
                # the user's setting. The field shape it answers:
                # Test key passes and a plain script connects, while
                # the app — the only route forced through the
                # detected proxy — cannot. If every candidate failed
                # with our proxy in place, undo the export, rebuild
                # the client, and try the whole list once, direct.
                _remove_exported_proxy(proxy_value)
                _log_line("Cloud connect failed with the system proxy "
                          "exported — retrying once without it.")
                print("[Live] Retrying the cloud connect without the "
                      "system proxy.")
                # Round-3 fix #6 (#58): the retry shows on screen.
                self._notify_retry("trying again without the system proxy.")
                self._client = genai.Client(api_key=config.api_key)
                connected, timed_out, last_rejection = (
                    await self._try_candidates(
                        candidates, session_config, audio_manager,
                        deadline=connect_deadline))
            if not connected:
                self._report_exhaustion(candidates, timed_out,
                                        last_rejection)

        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            _log_line(f"Connect failed: {e}")
            self._notify_status(f"ERROR: {str(e)[:300]}")
        finally:
            self.is_connected = False
            self._running = False
            # Voice-control batch (2026-11-16, B3): the session
            # reference must not outlive the session — a stale
            # _session made "_session is truthy" read as "session
            # alive" for the paths that check it alone (send loop,
            # tool-response delivery). Cleared here and in stop().
            self._session = None
            self._watchdog_task = None
            _bump_epoch("live session end")  # v5.48 (programme item 20)
            self._notify_status("DISCONNECTED")

    async def _try_candidates(self, candidates, session_config,
                              audio_manager, deadline=None):
        """One pass over the voice-model candidates (v5.29,
        extracted from start() so the proxy retry can run the pass
        twice). Returns (connected, timed_out, last_rejection); a
        connected pass has already run its session to the end.
        Per-candidate behaviour is the v5.28 contract: a rejection
        or a stall moves on to the next candidate.

        v5.44 (B2): the pass runs against ONE shared connect
        deadline (start() computes it; the proxy retry pass gets
        the SAME one, so the budget covers both passes). The
        first candidate may wait CONNECT_FIRST_TIMEOUT_S, later
        candidates CONNECT_LATER_TIMEOUT_S, and no attempt starts
        once the budget is spent. v5.44 (A8): a candidate that
        rejects the realtime input config is retried once,
        immediately, WITHOUT it — a config quirk must never cost
        a working model."""
        if deadline is None:
            deadline = time.monotonic() + CONNECT_BUDGET_S
        # LIVEFIX: never re-offer a handle the store no longer
        # holds — the proxy retry pass reuses start()'s config, so
        # a handle cleared during the first pass must be stripped
        # here, before any candidate sees it.
        session_config = _strip_cleared_handle(session_config)
        timed_out: list[str] = []
        last_rejection: Exception | None = None
        for index, model_id in enumerate(candidates):
            attempt_config = session_config
            config_retried = False
            transparent_retried = False
            # B9 (v5.45): this candidate's first attempt carries
            # the saved resumption handle when the config has one.
            resume_pending = _config_resume_handle(attempt_config) is not None
            while True:
                timeout = _connect_timeout_for(index, deadline)
                if timeout <= 0:
                    _log_line("Cloud connect budget "
                              f"({CONNECT_BUDGET_S:.0f} s) spent — "
                              "no further candidates attempted.")
                    print("[Live] Cloud connect budget spent; "
                          "stopping candidate attempts.")
                    return False, timed_out, last_rejection
                connect_cm = self._client.aio.live.connect(
                    model=model_id, config=attempt_config)
                try:
                    # Handshake timeout (v5.22, brain audit 2 #4):
                    # a blocked route used to hang in CONNECTING
                    # forever. v5.28: a timeout no longer aborts
                    # the pass — a stall can be model-specific
                    # (field-proven 2026-10-22), so it moves on to
                    # the next candidate like a rejection. v5.44:
                    # the wait is the B2 per-candidate slice of
                    # the shared budget, not a flat 25 s each.
                    session = await asyncio.wait_for(
                        connect_cm.__aenter__(), timeout=timeout)
                except (asyncio.TimeoutError, TimeoutError):
                    if resume_pending:
                        # B9 fail-soft: a resumed attempt that
                        # stalls is a resumption failure — clear
                        # the handle and retry THIS candidate cold.
                        resume_pending = False
                        _clear_resume_handle()
                        attempt_config = _without_resume_handle(attempt_config)
                        session_config = attempt_config
                        _log_line("Resumed connect stalled — cleared "
                                  "the saved handle; retrying cold.")
                        print("[Live] Resume attempt stalled; "
                              "retrying this model cold.")
                        # Round-3 fix #6 (#58): the retry shows on screen.
                        self._notify_retry(
                            "the saved session didn't answer, trying "
                            "this voice model again.")
                        continue
                    timed_out.append(model_id)
                    _log_line(f"Live handshake for '{model_id}' timed "
                              f"out after {timeout:g} s — trying the "
                              "next model.")
                    print(f"[Live] Voice model '{model_id}' timed out "
                          f"after {timeout:g} s; trying the next "
                          "candidate.")
                    # Round-3 fix #6 (#58): the retry shows on
                    # screen — but only when there IS a next
                    # candidate; a timeout on the last one ends
                    # the pass, and "trying the next one" would
                    # be a lie there.
                    if index + 1 < len(candidates):
                        self._notify_retry(
                            f"the voice model '{model_id}' timed out "
                            f"after {timeout:g} s, trying "
                            f"'{candidates[index + 1]}' next.")
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    if (not transparent_retried
                            and _config_transparent(attempt_config)
                            and _is_transparent_rejection(e)):
                        # v5.51 (item 13): the API rejected the
                        # `transparent` resumption parameter itself
                        # (it is Enterprise-only; a Developer API
                        # endpoint refuses it and the whole connect
                        # with it). Drop just that parameter — the
                        # handle stays — and retry exactly once.
                        # The degraded config replaces the pass's
                        # config so no later candidate re-offers
                        # the parameter.
                        transparent_retried = True
                        attempt_config = _without_transparent(
                            attempt_config)
                        session_config = attempt_config
                        _log_line("Transparent resumption "
                                  "unavailable in this API mode — "
                                  "handle-based resumption in use; "
                                  "retrying without the transparent "
                                  "parameter.")
                        print("[Live] Transparent resumption "
                              "unavailable in this API mode; "
                              "retrying with handle-based "
                              "resumption only.")
                        # Round-3 fix #6 (#58): the retry shows on screen.
                        self._notify_retry(
                            "the connection refused one resumption "
                            "option, trying again without it.")
                        continue
                    if resume_pending:
                        # B9 fail-soft: ANY failure of a resumed
                        # attempt (a rejected/expired handle above
                        # all) falls back to a cold start for this
                        # candidate, silently — and the handle is
                        # cleared so it cannot poison the next one.
                        # RS-1 (v5.56): a 1008 failure is the
                        # named stale-handle case of this branch
                        # (same cold retry, logged as stale so
                        # the field story names the cause).
                        resume_pending = False
                        _clear_resume_handle()
                        attempt_config = _without_resume_handle(attempt_config)
                        session_config = attempt_config
                        if _is_already_connected_close(e):
                            # LIVEFIX: the server still holds a live
                            # session for this handle (typically a
                            # 1006 drop the server has not reaped
                            # yet) — this is NOT a stale handle, and
                            # must never be logged as one. Give the
                            # server a brief bounded chance to
                            # release it before the cold retry; the
                            # sleep is capped by the shared connect
                            # deadline, and a cold attempt that
                            # hits the same condition fails
                            # honestly into the GUI's reconnect
                            # episode (see the raise path below)
                            # instead of looping here.
                            _log_line(f"Resume attempt for '{model_id}' "
                                      "refused: the server still holds "
                                      "a live session for this handle "
                                      "(already connected) — cleared "
                                      "the saved handle; waiting "
                                      "briefly, then retrying cold.")
                            print(f"[Live] Resume refused for "
                                  f"'{model_id}': server still holds "
                                  "this session — waiting briefly, "
                                  "then retrying cold.")
                            _remaining = deadline - time.monotonic()
                            _delay = (min(_ALREADY_CONNECTED_BACKOFF_S,
                                          _remaining)
                                      if _remaining > 0 else 0.0)
                            if _delay > 0:
                                await asyncio.sleep(_delay)
                        elif _is_stale_handle_close(e):
                            _log_line(f"Resume attempt for '{model_id}' "
                                      "rejected with 1008 (stale handle) — "
                                      "cleared the saved handle; "
                                      "retrying cold.")
                        _log_line(f"Resume attempt for '{model_id}' "
                                  f"failed ({_error_text(e)[:120]}) — "
                                  "cleared the saved handle; retrying cold.")
                        print(f"[Live] Resume failed for '{model_id}'; "
                              "retrying cold.")
                        # Round-3 fix #6 (#58): the retry shows on screen.
                        self._notify_retry(
                            f"the saved session for '{model_id}' "
                            "couldn't be resumed, starting fresh.")
                        continue
                    if (not config_retried
                            and getattr(attempt_config,
                                        "realtime_input_config", None)
                            is not None
                            and _is_input_config_rejection(e)):
                        # A8 fail-soft: this candidate balked at
                        # the realtime input config — retry it at
                        # once without the config (still inside
                        # the same B2 budget slice discipline:
                        # the loop recomputes the remaining time).
                        config_retried = True
                        attempt_config = (
                            _without_realtime_input_config(
                                attempt_config))
                        _log_line(f"Voice model '{model_id}' rejected "
                                  "the realtime input config — "
                                  "retrying it without one.")
                        print(f"[Live] Voice model '{model_id}' "
                              "rejected the realtime input config; "
                              "retrying without it.")
                        # Round-3 fix #6 (#58): the retry shows on screen.
                        self._notify_retry(
                            f"the voice model '{model_id}' refused one "
                            "setting, trying again without it.")
                        continue
                    if not _is_model_rejection(e):
                        if _is_already_connected_close(e):
                            # LIVEFIX: a cold attempt hitting the
                            # same server-side session lock is
                            # named, not swallowed — the raise
                            # below still ends this pass, and the
                            # GUI's reconnect episode (2/5/15/30 s
                            # backoff) is the outer retry loop.
                            _log_line(f"Voice model '{model_id}' cold "
                                      "connect refused: the server "
                                      "still holds a live session "
                                      "(already connected).")
                        raise
                    last_rejection = e
                    print(f"[Live] Voice model '{model_id}' was rejected by Google: {e}")
                    _log_line(f"Voice model '{model_id}' rejected by Google: {e}")
                    # Round-3 fix #6 (#58): the retry shows on
                    # screen — but only when there IS a next
                    # candidate; a rejection on the last one ends
                    # the pass, and "trying the next one" would
                    # be a lie there.
                    if index + 1 < len(candidates):
                        self._notify_retry(
                            f"the voice model '{model_id}' was refused, "
                            f"trying '{candidates[index + 1]}' next.")
                    break
                try:
                    # GAPS-22 (B-V5): the model actually serving —
                    # set on EVERY successful connect, not just when
                    # the fallback differs from config, so the
                    # summariser and the cost record name the model
                    # the session is really on. The singleton keeps
                    # serving this model until the next connect.
                    self.connected_model = model_id
                    if model_id != config.model_name:
                        self._adopt_working_model(model_id)
                    # v5.54 (Wave 0): the milestone log's first
                    # successful Live connect — the handshake has
                    # succeeded at this point; record_once means
                    # only the first one ever lands (WF-55).
                    try:
                        from .. import milestones as _milestones  # Phase 3 V1b: moved to core/voice/

                        _milestones.record_once(
                            "first_live_connect", model=model_id)
                    except Exception:  # noqa: BLE001 — never break connect
                        pass
                    await self._run_session(session, audio_manager)
                finally:
                    await connect_cm.__aexit__(None, None, None)
                return True, timed_out, last_rejection
        return False, timed_out, last_rejection
