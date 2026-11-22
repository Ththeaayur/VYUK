"""Local-live session mixin: Streaming speech chain: queue, producer/consumer, playback.

Batch 5 (skills split, local_client closer): moved verbatim
from core/voice/local_client.py; methods resolve self-calls
through the composed LocalLiveSession class.
"""

import asyncio
import numpy as np
import queue
import sys
from ..config import (config)
from .local_client_chunking import (_BREAK_NONE, _SSMD_BREAK_RE, _prosody_tag_units, _rechunk_for_speech, _rechunk_with_breaks)
from .local_client_pcm import (_BARGE_FADE_SAMPLES, _CROSSFADE_SAMPLES, _float_to_pcm, _join_with_pause, _pause_sample_count, _pcm_edge_silence, _pcm_to_float, _utterance_loudness_pcm, _whisper_factor, whisper_active_now)
from .local_client_verbal import (_speech_text, _ssmd_display)


class LocalLiveSessionSpeechMixin:
    # ------------------------------------------------------------------ #
    # Speech out: backend-dependent chain — builtin: Kokoro → Piper →
    # Windows SAPI → text-only; ollama: Piper → SAPI → text-only
    #
    # B-R1-3s: the rung that spoke is RECORDED (self._last_tts_rung),
    # never guessed afterwards. A Kokoro failure that falls to Piper
    # is a NAMED fallback — the record says "piper" — never a silent
    # swap to a licence-gated voice.
    # ------------------------------------------------------------------ #
    async def _speak_via_chain(self, text: str, chain) -> str | None:
        """Try each TTS provider in order; return the winning rung
        name ("kokoro" / "piper" / "sapi"), or None when no voice
        spoke. Records self._last_tts_rung and prints one short
        line naming the rung — a Kokoro failure that falls to
        Piper is a NAMED fallback, never a silent swap."""
        for provider in chain:
            if await provider(text):
                rung = provider.__name__.removeprefix("_speak_")
                self._last_tts_rung = rung
                print(f"[LocalBrain] Spoke via {rung}"
                      + (" (isolated Kokoro child)"
                         if rung == "kokoro" else ""))
                return rung
        self._last_tts_rung = None
        return None

    async def _speak(self, text: str):
        if not text:
            return
        # v5.51 (M-4): the speech choke point — every local /
        # lite / cloud-text voice funnels through this method
        # (the lite and Groq sessions speak via a helper
        # LocalLiveSession). A text that still arrives headed
        # "Error: …" is a raw tool-error rendering, not a reply:
        # say the butler line instead. Model free text that
        # merely mentions an error mid-sentence is untouched.
        try:
            from .spoken_errors import spoken_line_for_raw_text  # Phase 3 V1b: moved to core/voice/

            _replacement = spoken_line_for_raw_text(text)
            if _replacement is not None:
                text = _replacement
        except Exception:  # noqa: BLE001 — the net must not break speech
            pass
        # Loophole batch 5 (L-25): the speech guard — the choke
        # point every local / lite / cloud-text voice funnels
        # through. Tainted content is never spoken verbatim and
        # never quoted as the assistant's own answer: a quote is
        # spoken only with its source named, an instruction span
        # is replaced by a refusal clause, and envelope markers
        # are stripped. Runs after the spoken-errors replacement
        # (a butler line is the assistant's own words, never
        # tainted) and before chunking, so the chunks the TTS
        # chain synthesises are already guarded. The guard never
        # raises and never silences a reply — a broken guard
        # returns the marker-stripped text.
        try:
            from ..speech_guard import guard_spoken_text

            text = guard_spoken_text(text)
        except Exception:  # noqa: BLE001 — the net must not break speech
            pass
        if not text:
            return
        self._speaking = True
        self._notify_status("SPEAKING")
        # B-R1-3s: a stale rung from a previous reply must never
        # read as this reply's voice — cleared here, set when a
        # rung actually speaks (below, or in _speak_chunked).
        self._last_tts_rung = None
        try:
            # Streaming speech (v2.7): a multi-sentence reply is spoken
            # sentence by sentence — the first sentence starts playing
            # while the rest synthesize. Single-sentence replies keep
            # the original whole-text path below, byte for byte.
            #
            # The chunks are carried as (raw, spoken) pairs plus a
            # flat list of speak-units (spoken text, speed multiplier,
            # chunk index, break strength): karaoke needs the raw
            # chunk for its display text, and emphasis (v5.17, M14)
            # expands a chunk into per-span units on the builtin
            # chain. A chunk with no ** spans is exactly one unit
            # holding its whole-chunk normalization.
            #
            # M1 (v5.43): the chunks come from _rechunk_for_speech
            # — the Goldilocks pre-pass over the full reply — not
            # from _split_sentences directly, so runts are merged
            # and monsters split before any synthesis starts; the
            # break strength rides each chunk's first unit into
            # the streaming consumer for M2's breathing pauses.
            # v5.58: a brain <break> tag additionally splits the
            # reply at the tag (see _rechunk_with_breaks) so the
            # tag becomes a real prepended pause; plain replies
            # (no break tag) take the untouched M1 path above.
            raw_chunks = _rechunk_for_speech(text)
            if _SSMD_BREAK_RE.search(text):
                raw_chunks = _rechunk_with_breaks(text)
            if self._backend != "ollama":
                # Mouth craft (v5.1): on the builtin chain the engines
                # speak the NORMALIZED text (see _speech_text) — the
                # caller keeps the verbatim reply for chat and memory.
                # A reply with nothing speakable in it (pure code
                # block, say) ends here instead of feeding an engine
                # an empty string.
                pairs: list[tuple[str, str]] = []
                units: list[tuple[str, float, int, int]] = []
                for raw, chunk_brk in raw_chunks:
                    # v5.58: units honour the SSMD-subset speed
                    # spans (<prosody rate>, <emphasis>) as well
                    # as the shipped ** spans, in document order
                    # (see _prosody_tag_units). The display raw
                    # is the tag-stripped text so karaoke never
                    # shows markup; plain chunks are unchanged.
                    subs = _prosody_tag_units(raw)
                    if len(subs) == 1 and subs[0][1] == 1.0:
                        spoken = _speech_text(subs[0][0])
                        if spoken:
                            idx = len(pairs)
                            pairs.append((_ssmd_display(raw), spoken))
                            units.append((spoken, 1.0, idx, chunk_brk))
                        continue
                    sent_units = []
                    for sub_text, mult in subs:
                        spoken = _speech_text(sub_text)
                        if spoken:
                            sent_units.append((spoken, mult))
                    if sent_units:
                        idx = len(pairs)
                        pairs.append((_ssmd_display(raw),
                                      _speech_text(raw)))
                        units.extend(
                            (spoken, mult, idx,
                             chunk_brk if n == 0 else _BREAK_NONE)
                            for n, (spoken, mult) in enumerate(sent_units))
                chunks = [spoken for _raw, spoken in pairs]
                text = _speech_text(text)
                if not chunks and not text:
                    return
            else:
                # v5.58: the Ollama stream keeps its verbatim
                # shape (no pauses, one unit per chunk) but
                # SSMD-subset markup is stripped from the spoken
                # text — a tag must never reach an engine on any
                # backend. Plain chunks pass through unchanged.
                pairs = [(_ssmd_display(chunk), _ssmd_display(chunk))
                         for chunk, _brk in raw_chunks]
                units = [(_ssmd_display(chunk), 1.0, idx, chunk_brk)
                         for idx, (chunk, chunk_brk)
                         in enumerate(raw_chunks)]
                chunks = [spoken for _raw, spoken in pairs]
                text = _ssmd_display(text)
            if len(chunks) > 1 and await self._speak_chunked(units, pairs):
                return
            if len(pairs) == 1:
                # The whole-text chain speaks the reply's only
                # sentence: karaoke tracks it (honestly — total 1)
                # when a PCM provider enqueues it via _play_pcm.
                self._karaoke_pending = (0, 1, pairs[0][0])
            if self._backend == "ollama":
                chain = (self._speak_piper, self._speak_sapi)
            else:
                chain = (self._speak_kokoro, self._speak_piper, self._speak_sapi)
            if await self._speak_via_chain(text, chain):
                return
            if not self._no_tts_noted:
                self._no_tts_noted = True
                print("[LocalBrain] No local voice available (no Kokoro or "
                      "Piper voice, no SAPI) — running text-only. See "
                      "TROUBLESHOOTING.md §17.")
        finally:
            self._speaking = False
            self._karaoke_pending = None  # never leak a stale tag
            self._notify_status("LISTENING")

    def set_speech_speed_override(self, factor) -> None:
        """Sets (or, with None, clears) the speech pace override.

        Effective builtin synthesis speed is
            _kokoro_speed() * (self._speech_speed_override
                               or config.briefing_speed or 1.0)
        * the per-unit emphasis multiplier — the override wins when
        set, config.briefing_speed is the default multiplier
        otherwise (the GUI's briefing preset writes briefing_speed,
        and live pace changes call this method). A non-positive or
        non-numeric factor is ignored rather than stored: a bad
        factor must never mute or crash the voice."""
        if factor is None:
            self._speech_speed_override = None
            return
        try:
            value = float(factor)
        except (TypeError, ValueError):
            return
        if value != value or value <= 0:  # NaN or non-positive
            return
        self._speech_speed_override = value

    def _session_speed_mult(self) -> float:
        """The session speed multiplier (M18): the explicit override
        if set, else config.briefing_speed, else 1.0 — garbage
        degrades to 1.0 (natural pace)."""
        if self._speech_speed_override:
            return float(self._speech_speed_override)
        try:
            value = float(getattr(config, "briefing_speed", 1.0))
        except (TypeError, ValueError):
            return 1.0
        if value != value or value <= 0:  # NaN or non-positive
            return 1.0
        return value

    def _drain_speech_queue(self):
        """Empties the pending chunk queue (barge-in flush)."""
        queue_ = self._speech_queue
        if queue_ is None:
            return
        while True:
            try:
                queue_.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _interrupt_playback(self):
        """Barge-in cut, faded instead of clipped (v5.1).

        The audio manager owns the output stream, and its
        interrupt_playback() drains every queued block at once — a
        cliff from full volume to silence. The still-queued samples
        are ones THIS session handed over, though, so the fade is
        applied here, at the last point the session controls them:
        drain the manager's playback queue, keep only a
        _BARGE_FADE_MS slice of what was about to play, fade it to
        zero on a raised-cosine curve (flat slope at both ends — no
        click), and hand that back as the only queued audio. The one
        block already inside the sound callback (≤1024 samples)
        plays out, then the faded tail, then silence. Falls back to
        the manager's hard interrupt whenever the queue can't be
        reshaped (no manager, no queue attribute, empty queue) — a
        failed fade must still cut.
        """
        mgr = self._audio_manager
        if mgr is None:
            return
        queue_ = getattr(mgr, "playback_queue", None)
        if queue_ is None:
            mgr.interrupt_playback()
            return
        blocks = []
        try:
            while True:
                blocks.append(queue_.get_nowait())
        except queue.Empty:
            pass
        if not blocks:
            mgr.interrupt_playback()
            return
        try:
            samples = np.concatenate(
                [np.asarray(block).ravel() for block in blocks]
            ).astype(np.float32) / 32768.0
            fade_n = min(samples.size, _BARGE_FADE_SAMPLES)
            curve = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade_n)))
            mgr.enqueue_playback(_float_to_pcm(samples[:fade_n] * curve))
            # The drained blocks carried sentence records in the
            # manager's FIFO; the faded slice is untracked, so the
            # records must go or karaoke/sentences-left freeze on
            # sentences that will never play (v5.17).
            clear = getattr(mgr, "clear_sentence_tracking", None)
            if callable(clear):
                clear()
        except Exception:  # noqa: BLE001 — a failed fade must still cut
            mgr.interrupt_playback()

    # ------------------------------------------------------------------ #
    # Crossfade join (v5.1): the consumer withholds each chunk's last
    # _CROSSFADE_SAMPLES and equal-power-crossfades them with the next
    # chunk's head, so consecutive synthesized sentences meet on a
    # ramp instead of a sample cliff. The withheld tail is flushed
    # after the final chunk; a barge-in discards it (the barge-in
    # fade covers that cut instead).
    # ------------------------------------------------------------------ #
    def _join_chunk(self, pcm: bytes) -> bytes:
        """Returns the playable part of `pcm`: the previous tail
        crossfaded into this chunk's head, plus this chunk's body
        minus its own withheld tail."""
        samples = _pcm_to_float(pcm)
        pieces = []
        tail = self._join_tail
        if tail is not None:
            self._join_tail = None
            n = min(tail.size, samples.size)
            if n:
                ramp = np.linspace(0.0, np.pi / 2.0, n)
                pieces.append(tail[:n] * np.cos(ramp)
                              + samples[:n] * np.sin(ramp))
                samples = samples[n:]
        hold = min(_CROSSFADE_SAMPLES, samples.size // 2)
        if hold:
            self._join_tail = samples[-hold:].copy()
            samples = samples[:-hold]
        pieces.append(samples)
        joined = (np.concatenate(pieces) if len(pieces) > 1
                  else pieces[0])
        return _float_to_pcm(joined)

    def _flush_join_tail(self) -> bytes | None:
        """The final withheld tail as PCM, or None when there is none."""
        tail = self._join_tail
        self._join_tail = None
        if tail is None or tail.size == 0:
            return None
        return _float_to_pcm(tail)

    # ------------------------------------------------------------------ #
    # Streaming speech (v2.7): sentence-chunked synthesis with a
    # one-sentence lookahead — a producer task synthesizes chunk i+1
    # while the consumer plays chunk i. The voice fallback order is
    # preserved per reply (Kokoro → Piper → SAPI → text-only): the
    # first chunk probes the PCM providers in order and the winner
    # synthesizes the whole reply; SAPI (which speaks directly and
    # yields no PCM) is streamed sentence-by-sentence instead.
    # Barge-in: the mic loop sets _speaking False and calls
    # _interrupt_playback(); the consumer checks _speaking before
    # every chunk, drains the pending queue via
    # _drain_speech_queue(), and cancels the producer — so pending
    # sentences are flushed, never played.
    # Mouth craft (v5.1): on the builtin backend the chunks arrive
    # pre-normalized by _speak (see _speech_text), each Kokoro chunk
    # is loudness-normalized at synthesis, and the consumer joins
    # consecutive chunks with an equal-power crossfade (see
    # _join_chunk). The Ollama backend's stream is untouched.
    # v5.43: the chunks arrive re-chunked (M1, _rechunk_for_speech)
    # with break strengths, and the consumer inserts M2 breathing
    # pauses at sentence/paragraph joins — see _speak_chunked.
    # ------------------------------------------------------------------ #
    async def _synth_producer(self, units: list[tuple[str, float, int, int]],
                              synth, first_pcm: bytes):
        """Feeds (pcm, text_len, sentence_index, break_strength)
        items into the speech queue, then a None sentinel. The
        first item is the already-synthesized first unit (the chain
        probe's result). Stops early when speech is interrupted."""
        queue_ = self._speech_queue
        if queue_ is None:
            return
        try:
            first_text, _mult, first_idx, first_brk = units[0]
            queue_.put_nowait(
                (first_pcm, len(first_text), first_idx, first_brk))
            for unit_text, unit_mult, unit_idx, unit_brk in units[1:]:
                if not self._speaking or not self._running:
                    break
                pcm = await synth(unit_text, unit_mult)
                if pcm:
                    queue_.put_nowait(
                        (pcm, len(unit_text), unit_idx, unit_brk))
        except asyncio.CancelledError:
            pass
        finally:
            queue_.put_nowait(None)

    async def _speak_chunked(self, units: list[tuple[str, float, int, int]],
                             sentences: list[tuple[str, str]]) -> bool:
        """Speaks a reply unit by unit. `units` are the speak-units
        (spoken text, speed multiplier, chunk index, break
        strength); `sentences` are the reply's (raw, spoken) chunk
        pairs — raw for karaoke display text, spoken for the
        whole-chunk fallback paths. Returns True when the reply was
        spoken (fully or up to a barge-in); False when no voice can
        speak it at all — the caller then falls back to the
        whole-text provider chain.

        Voice behaviour (v5.17): Kokoro synthesizes per unit, so
        emphasis spans get their 0.92 pace multiplier; Piper keeps
        whole-chunk synthesis (its units are regrouped to one per
        chunk, multipliers ignored — emphasis has no pacing effect
        there); SAPI speaks the whole chunks. The Ollama backend
        streams through Piper only, exactly one unit per chunk.
        Karaoke is hooked per played unit through _karaoke_pending
        (see _play_pcm): one begin_sentence per unit, tagged with
        its chunk's index/total/raw text.

        Breathing pauses (v5.43, M2; bands v5.58): on the builtin chain, a unit
        whose break strength is a sentence, paragraph, or clause break is
        played as [previous withheld tail][shaped silence][unit
        body] in ONE _play_pcm call — the pause is prepended to
        the FOLLOWING chunk, so a drained queue takes it with the
        chunk and barge-in never trails silence. Within-thought
        units (_BREAK_NONE) keep the equal-power crossfade join.
        The first played unit never gets a pause, whatever its
        break strength says. The Ollama stream is untouched.

        Loudness (v5.51, M-2/M-3): on the builtin chain the
        consumer now ASSEMBLES the whole utterance first — the
        joins and pauses above applied segment by segment, with
        each pause credited by the silence the M-3-trimmed chunk
        edges already supply, so the total inter-chunk silence
        still measures at the designed length — then passes the
        assembled utterance through the RMS loudness standard
        ONCE (_utterance_loudness_pcm), then plays the segments
        in order, karaoke tags and barge-in checks per segment as
        before. One gain per utterance is impossible once earlier
        chunks are already playing; synthesis still overlaps
        assembly through the producer task."""
        if self._backend == "ollama":
            attempts = ((self._synthesize_piper, units),)
        else:
            brk_by_idx: dict[int, int] = {}
            for _text, _mult, unit_idx, unit_brk in units:
                brk_by_idx.setdefault(unit_idx, unit_brk)
            sentence_units = [
                (spoken, 1.0, idx, brk_by_idx.get(idx, _BREAK_NONE))
                for idx, (_raw, spoken) in enumerate(sentences)
            ]
            attempts = (
                (self._synthesize_kokoro, units),
                (self._synthesize_piper, sentence_units),
            )

        self._join_tail = None  # a prior reply's leftover never leaks in
        for synth, synth_units in attempts:
            first_text, first_mult, _first_idx, _first_brk = synth_units[0]
            first_pcm = await synth(first_text, first_mult)
            if not first_pcm:
                continue
            # B-R1-3s: this synth won the first-chunk probe and
            # synthesizes the whole reply — record the rung that
            # spoke (the winner is the voice, not a guess).
            self._last_tts_rung = synth.__name__.removeprefix(
                "_synthesize_")
            print(f"[LocalBrain] Spoke via {self._last_tts_rung}"
                  + (" (isolated Kokoro child)"
                     if self._last_tts_rung == "kokoro" else ""))
            queue_: asyncio.Queue = asyncio.Queue()
            self._speech_queue = queue_
            producer = asyncio.create_task(
                self._synth_producer(synth_units, synth, first_pcm)
            )
            completed = False
            heard_any = False
            # v5.51 M-2: builtin segments are collected here in
            # play order and played only after the assembly (and
            # its single loudness pass) completes — see the
            # docstring. The Ollama branch below still plays each
            # chunk as it arrives, byte-identical to before.
            assembled: list[tuple[bytes, int | None, int | None]] = []
            prev_silence = 0  # trailing near-silence of the last
            # assembled segment — part of the M-3 pause credit.
            interrupted = False
            try:
                while True:
                    item = await queue_.get()
                    if item is None:
                        completed = True
                        break
                    if not self._speaking or not self._running:
                        self._drain_speech_queue()
                        interrupted = True
                        break
                    pcm, text_len, sent_idx, brk = item
                    if self._backend != "ollama":
                        if heard_any and brk != _BREAK_NONE:
                            tail_pcm = self._flush_join_tail()
                            body_pcm = self._join_chunk(pcm)
                            # v5.51 M-3: the trimmed chunk edges
                            # already supply part of the designed
                            # pause — credit the previous segment's
                            # trailing pad, the withheld tail and
                            # this body's leading pad against it, so
                            # the total inter-chunk silence measures
                            # at the designed pause for that break
                            # strength (clause / sentence /
                            # paragraph), not the pause plus pads.
                            credit = (
                                prev_silence
                                + _pcm_edge_silence(tail_pcm,
                                                    from_end=True)
                                + _pcm_edge_silence(body_pcm,
                                                    from_end=False))
                            pcm = _join_with_pause(
                                tail_pcm, brk, body_pcm,
                                pause_samples=max(
                                    0, _pause_sample_count(brk) - credit))
                        else:
                            pcm = self._join_chunk(pcm)
                        if pcm:
                            assembled.append((pcm, text_len, sent_idx))
                            prev_silence = _pcm_edge_silence(
                                pcm, from_end=True)
                    elif pcm:
                        self._karaoke_pending = (
                            sent_idx, len(sentences),
                            sentences[sent_idx][0])
                        await self._play_pcm(pcm, text_len=text_len)
                    heard_any = True
                    if not self._speaking or not self._running:
                        self._drain_speech_queue()
                        interrupted = True
                        break
            finally:
                producer.cancel()
                self._speech_queue = None
                self._karaoke_pending = None
            if self._backend != "ollama":
                if completed and not interrupted:
                    # The reply ran to its end: the last withheld
                    # tail joins the assembly so the final sentence
                    # keeps its full length.
                    tail_pcm = self._flush_join_tail()
                    if tail_pcm:
                        assembled.append((tail_pcm, None, None))
                    # v5.51 M-2: ONE loudness pass over the whole
                    # assembled utterance — the choke point every
                    # builtin chunked utterance passes exactly once.
                    gained = _utterance_loudness_pcm(
                        [seg for seg, _tl, _si in assembled])
                    for (seg_pcm, seg_len, seg_idx), out_pcm in zip(
                            assembled, gained):
                        if not out_pcm:
                            continue
                        if not self._speaking or not self._running:
                            break  # barge-in mid-playback: rest never plays
                        if seg_idx is not None:
                            self._karaoke_pending = (
                                seg_idx, len(sentences),
                                sentences[seg_idx][0])
                        await self._play_pcm(out_pcm, text_len=seg_len)
                else:
                    self._join_tail = None  # barge-in: tail is discarded
            return True

        # No PCM voice: SAPI speaks directly, so stream it per chunk.
        if sys.platform == "win32":
            try:
                import pyttsx3  # noqa: F401 — presence check, as in _speak_sapi
            except ImportError:
                return False
            # B-R1-3s: SAPI is the rung that speaks here.
            self._last_tts_rung = "sapi"
            print("[LocalBrain] Spoke via sapi")
            for _raw, spoken in sentences:
                if not self._speaking or not self._running:
                    break
                await self._speak_sapi(spoken)
            return True
        return False

    async def _play_pcm(self, pcm: bytes, text_len: int | None = None):
        """Enqueues 24 kHz int16 PCM and holds the SPEAKING state for
        roughly the clip's real duration so the orb/pill read correctly;
        barge-in (or stop) cuts the wait short. `text_len` applies the
        Piper path's historical chars-per-second cap on the hold.

        This is the one enqueue funnel every builtin PCM provider
        passes, so two v5.17 features live here:
        - Whisper hours (M15): inside the window the final samples —
          already loudness-normalized at synthesis — are scaled by
          config.whisper_factor. Builtin only: the Ollama backend's
          audio stays byte-identical, as it always has.
        - Karaoke (transport hooks): when the caller tagged this
          enqueue with _karaoke_pending, the manager is told which
          sentence the just-queued blocks belong to, via
          begin_sentence(blocks, index, total, raw_text) with the
          block count enqueue_playback returned. Best-effort — a
          manager without the API, or one that errors, never breaks
          speech."""
        if self.on_audio_data:
            self.on_audio_data(pcm)
        if (pcm and self._backend != "ollama"
                and whisper_active_now()):
            factor = _whisper_factor()
            if factor < 1.0:
                pcm = _float_to_pcm(_pcm_to_float(pcm) * factor)
        if self._audio_manager is not None:
            # Voice-control batch (2026-11-16, O7): the hold below
            # ends when THIS clip's blocks have left the queue, so
            # the backlog already ahead of us is measured here,
            # before the enqueue.
            _queue = getattr(self._audio_manager, "playback_queue", None)
            _backlog_blocks = 0
            if _queue is not None:
                try:
                    _backlog_blocks = _queue.qsize()
                except Exception:  # noqa: BLE001 — no honest signal
                    _backlog_blocks = 0
            blocks = self._audio_manager.enqueue_playback(pcm)
            pending, self._karaoke_pending = self._karaoke_pending, None
            if pending is not None:
                begin = getattr(
                    self._audio_manager, "begin_sentence", None)
                if begin is not None:
                    try:
                        begin(blocks, pending[0], pending[1], pending[2])
                    except Exception:  # noqa: BLE001 — karaoke is
                        pass           # decoration; speech comes first

        duration = (len(pcm) // 2) / config.sample_rate_out
        cap = (text_len / 12.0) if text_len else 30.0
        wait_s = min(duration, cap)
        # Voice-control batch (2026-11-16, O7): the hold used to be
        # a chars/sec guess capped at the clip's own duration, so
        # the orb flipped to LISTENING while her voice was still
        # queued (backlog, slow drain, or a long clip past the
        # cap). Now the hold ends when the blocks this clip queued
        # have actually left the queue — qsize() back to the
        # pre-enqueue backlog — and the guess survives only as a
        # watchdog cap (plus the backlog's own drain time, which
        # passes at real-time rate before our blocks can play).
        # No queue (or no chunk_size): the guess is all there is.
        _queue = None
        if self._audio_manager is not None:
            _queue = getattr(
                self._audio_manager, "playback_queue", None)
            if _queue is not None:
                try:
                    wait_s += _backlog_blocks * (
                        self._audio_manager.chunk_size
                        / config.sample_rate_out)
                except Exception:  # noqa: BLE001
                    _queue = None
        waited = 0.0
        while waited < wait_s and self._speaking and self._running:
            if _queue is not None:
                try:
                    if _queue.qsize() <= _backlog_blocks:
                        break
                except Exception:  # noqa: BLE001 — fall back to time
                    pass
            await asyncio.sleep(0.05)
            waited += 0.05

