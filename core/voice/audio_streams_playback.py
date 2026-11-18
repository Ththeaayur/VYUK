"""Streams-playback mixin, split out of core/voice/audio.py
(B33 part 2 of 2): playback transport, sentence tracking,
render-reference tap, and front-end status. Composed into
AudioManager by the facade. Methods resolve bare names in
this module's globals."""

import queue
from typing import Any

import numpy as np


class AudioStreamsPlaybackMixin:
    @property
    def sentences_left(self) -> int:
        """Sentences tracked in the playback FIFO, the one being
        consumed included. Stays 0 when nothing was ever begun —
        Gemini Live audio never calls begin_sentence, so its count
        is honestly 0 rather than a guess."""
        return len(self._sentence_fifo)

    @property
    def speech_sentence(self) -> tuple[int, int, str] | None:
        """(index, total, text) of the sentence whose blocks are
        leaving the queue now, or None. Derived from the FIFO head,
        which the playback callback retires as blocks are consumed."""
        if not self._sentence_fifo:
            return None
        head = self._sentence_fifo[0]
        return (head["index"], head["total"], head["text"])

    def display_bands(self, speaking: bool) -> list[float]:
        """The bands the orb should perform (v5.42): her own
        playback spectrum while she is speaking, the microphone
        spectrum otherwise. Returns the live list, matching how
        telemetry consumed spectrum_bands before."""
        return self.playback_bands if speaking else self.spectrum_bands

    def _playback_callback(self, outdata: np.ndarray, frames: int, time_info, status):
        """Streams audio to speakers."""
        if self._interrupt_event.is_set():
            # Interruption requested: drain the STALE blocks and
            # output silence. Field-check fix #13 (M13c): drain
            # only the blocks that were queued at interrupt time.
            # Blocks enqueued after the interrupt — the next turn's
            # first chunks, arriving in the ~43 ms before this tick
            # — must survive: the interrupt discards what was
            # playing/queued AT the interrupt, not what arrives
            # after it. The snapshot is a queue depth taken in
            # interrupt_playback(); blocks the bounded queue's
            # drop-oldest path evicted since then were stale blocks
            # leaving from the head, so they reduce the drain count
            # one-for-one. (Residual bound, stated plainly: a
            # callback tick whose get_nowait lands in the
            # microsecond window between the snapshot and the event
            # set would make the drain overshoot by one block.)
            # The sentence FIFO is NOT cleared here:
            # interrupt_playback() already cleared the stale
            # records, so any record present now belongs to a
            # post-interrupt turn and must survive with its blocks.
            stale = getattr(self, "_interrupt_drain_blocks", 0)
            dropped_since = (getattr(self, "playback_dropped_blocks", 0)
                             - getattr(self, "_interrupt_dropped_at", 0))
            stale = max(0, stale - max(0, dropped_since))
            for _ in range(stale):
                try:
                    self.playback_queue.get_nowait()
                except queue.Empty:
                    break
            self._interrupt_event.clear()
            outdata.fill(0)
            self.speaker_level = 0.0
            self.playback_bands = [b * 0.8 for b in self.playback_bands]
            # A surviving new turn is still engaged: report the
            # queue, not a blanket False.
            self.is_playing = not self.playback_queue.empty()
            return

        if self.playback_paused:
            # Paused (v5.17): hold position — emit silence and
            # consume NOTHING, so resume picks up mid-sentence.
            # is_playing deliberately keeps reporting the queue: it
            # means "a speech turn is engaged" (blocks pending or
            # flowing), which stays true through a pause; consumers
            # reading it as "turn over" would drop the karaoke /
            # sentences-left state mid-turn. speaker_level is the
            # audible-truth meter and reads 0 while paused.
            outdata.fill(0)
            self.speaker_level = 0.0
            self.playback_bands = [b * 0.8 for b in self.playback_bands]
            self.is_playing = not self.playback_queue.empty()
            return

        try:
            chunk = self.playback_queue.get_nowait()
            self._note_block_consumed()
            if len(chunk) < frames:
                # Pad with silence if short
                outdata[:len(chunk)] = chunk.reshape(-1, 1)
                outdata[len(chunk):].fill(0)
            else:
                outdata[:] = chunk[:frames].reshape(-1, 1)
            
            raw_float = chunk.astype(np.float32) / 32768.0
            self.speaker_level = min(1.0, float(np.sqrt(np.mean(raw_float ** 2))) * 4.0)
            # Playback spectrum (v5.42): analyse the samples
            # actually written to outdata (after pad/truncate),
            # with the mic path's binning/scaling/threshold.
            out_float = outdata.astype(np.float32) / 32768.0
            pb_rms = float(np.sqrt(np.mean(out_float ** 2)))
            if pb_rms > 0.001:
                self.playback_bands = self._spectrum_bands_from_mono(out_float[:, 0])
            else:
                self.playback_bands = [b * 0.8 for b in self.playback_bands]
            self.is_playing = True
        except queue.Empty:
            outdata.fill(0)
            self.speaker_level = 0.0
            self.playback_bands = [b * 0.8 for b in self.playback_bands]
            self.is_playing = False

    def tap_render_reference(self, pcm_bytes: bytes) -> tuple[bool, str]:
        """EA-1/EA-29 far-end tap: her TTS render, pre-device.

        Feeds the v5.58 render-reference AEC sidecar (core/aec)
        with the exact PCM about to be queued for playback —
        the reference the mic path is cleaned against. Runs on
        the enqueue thread, never on the PortAudio callback, and
        never raises: a failed tap only degrades echo
        cancellation. Returns (accepted, engine-or-reason).
        """
        try:
            if self._aec is None:
                from ..aec import RenderReferenceAEC  # Phase 3 V1a-1: audio chain moved to core/voice/

                self._aec = RenderReferenceAEC()
            accepted = bool(self._aec.feed_render(pcm_bytes))
            engine = getattr(self._aec.engine, "engine", "aec")
            return accepted, (engine if accepted else
                              "tap rejected (malformed PCM)")
        except Exception as e:  # noqa: BLE001
            return False, f"aec unavailable: {e}"

    def front_end_status(self) -> dict[str, Any]:
        """v5.58 front-end status (VAD/ASR/SmartTurn/AEC/NS/loopback).

        Lazy imports, never raises: a module that fails to
        import reports itself as unavailable in its own slot
        instead of breaking this call. Does NOT probe the
        loopback subprocess (that spawns a process); use
        core/loopback.status() when a live loopback verdict is
        needed. Wake state is untouched — nothing here changes
        what ships ON by default (wake stays OFF).
        """
        out: dict[str, Any] = {}
        try:
            from .vad import VoiceActivityDetector

            out["vad"] = VoiceActivityDetector().status()
        except Exception as e:  # noqa: BLE001
            out["vad"] = {"backend": "unavailable", "error": str(e)}
        try:
            from .asr import faster_whisper_available

            out["asr"] = {
                "engine": ("faster-whisper" if faster_whisper_available()
                           else "unavailable"),
                "word_timestamps": True,
            }
        except Exception as e:  # noqa: BLE001
            out["asr"] = {"engine": "unavailable", "error": str(e)}
        try:
            from .smartturn import EndOfTurnDetector

            out["smartturn"] = EndOfTurnDetector().status()
        except Exception as e:  # noqa: BLE001
            out["smartturn"] = {"backend": "unavailable", "error": str(e)}
        try:
            out["aec"] = (self._aec.status() if self._aec is not None
                          else {"engine": "not tapped yet",
                                "webrtc_apm_available": False,
                                "on_realtime_callback": False,
                                # Field-check fix #12 (M11c): same
                                # honesty as the tapped branch — no
                                # reference has even been collected,
                                # and nothing is applied to the mic.
                                "applied_to_mic": False,
                                "note": (
                                    "Echo cancellation is not running: "
                                    "no render reference has been "
                                    "collected yet, and the microphone "
                                    "audio is never cleaned against "
                                    "one.")})
        except Exception as e:  # noqa: BLE001
            out["aec"] = {"engine": "unavailable", "error": str(e)}
        try:
            from .noise_suppress import (
                GTCRNSuppressor, RNNoiseSuppressor,
                deepfilternet_status, select_single_suppressor)

            out["noise_suppression"] = {
                "rnnoise": RNNoiseSuppressor().status(),
                "gtcrn": GTCRNSuppressor().status(),
                "selected": select_single_suppressor(
                    [RNNoiseSuppressor(), GTCRNSuppressor()]),
                "deepfilternet": deepfilternet_status(),
                # Field-check fix #12 (M11c): the suppressors'
                # process() methods have no caller on the mic
                # path — the slot says so in plain words instead
                # of letting a "selected" entry read as "noise
                # suppression is running".
                "applied_to_mic": False,
                "note": (
                    "No noise suppressor runs on the microphone "
                    "audio — what she hears is the raw mic signal."),
            }
        except Exception as e:  # noqa: BLE001
            out["noise_suppression"] = {"error": str(e)}
        # GAPS-20 (D-10): the loopback slot now reports the LIVE
        # capture state (read from core.loopback.capture_status(),
        # no spawn) alongside the standing note — a meeting with a
        # system-audio track reads active here.
        try:
            from core import loopback as _loopback

            live = _loopback.capture_status()
        except Exception:  # noqa: BLE001 — a broken loopback read is "no live capture"
            live = {"active": False}
        out["loopback"] = {
            "probed": False,
            "capture_active": bool(live.get("active")),
            "capture": live,
            "note": "call core.loopback.status() for a live verdict "
                    "(it spawns the isolated worker); capture_active "
                    "here is the live session state, read without "
                    "spawning",
        }
        return out

    def enqueue_playback(self, pcm_bytes: bytes) -> int:
        """Enqueues raw 24kHz int16 PCM bytes received from Gemini Live.

        Returns the number of blocks queued (v5.17) — sentence
        tracking counts in these blocks; older callers ignore it.
        An empty chunk queues nothing; an odd-length chunk loses
        its one trailing byte instead of raising (O3, below).
        """
        # v5.58 (EA-1): tap the render pre-device for the AEC
        # sidecar. Guarded + non-raising by construction; the
        # queued audio below is byte-identical either way.
        self.tap_render_reference(pcm_bytes)
        if not pcm_bytes:
            return 0
        if len(pcm_bytes) % 2:
            # Voice-control batch (2026-11-16, O3): a malformed
            # odd-length chunk from the Live receive loop used to
            # raise out of np.frombuffer and kill the loop. One
            # trailing byte is 1/48000 s of audio — drop it and
            # keep the chunk, instead of dropping the chunk or
            # the session. Counted; logged throttled (first +
            # every 100th), like the mic side's drop counting.
            pcm_bytes = pcm_bytes[:-1]
            self._playback_odd_chunks = getattr(
                self, "_playback_odd_chunks", 0) + 1
            if self._playback_odd_chunks == 1 or \
                    self._playback_odd_chunks % 100 == 0:
                print(f"[Audio] Odd-length playback chunk "
                      f"(#{self._playback_odd_chunks}): dropped "
                      f"1 trailing byte")
        chunk = np.frombuffer(pcm_bytes, dtype=np.int16)
        # Split into blocks of chunk_size
        blocks = 0
        for i in range(0, len(chunk), self.chunk_size):
            block = chunk[i:i + self.chunk_size]
            self._put_playback_block(block)
            blocks += 1
        return blocks

    def _put_playback_block(self, block) -> None:
        """Puts one block, dropping the oldest on a full queue
        (voice-control batch, 2026-11-16, O4): the queue is
        bounded, so a plain put() would block the Live receive
        loop forever on a stalled output stream. A dropped block
        also retires one sentence-FIFO count — the block left
        the queue, just not through the speaker, so the head
        record must not stick waiting for a consumption that
        will never come. Drops are counted
        (playback_dropped_blocks) and logged throttled."""
        try:
            self.playback_queue.put_nowait(block)
            return
        except queue.Full:
            pass
        try:
            self.playback_queue.get_nowait()
        except queue.Empty:
            pass
        _note = getattr(self, "_note_block_consumed", None)
        if _note is not None:
            try:
                _note()
            except Exception:  # noqa: BLE001 — FIFO is decoration
                pass
        self.playback_dropped_blocks = getattr(
            self, "playback_dropped_blocks", 0) + 1
        if self.playback_dropped_blocks == 1 or \
                self.playback_dropped_blocks % 100 == 0:
            print(f"[Audio] Playback queue full: dropped "
                  f"{self.playback_dropped_blocks} block(s) total")
        try:
            self.playback_queue.put_nowait(block)
        except queue.Full:
            pass

    # ------------------------------------------------------ transport
    def begin_sentence(self, block_count: int, index: int, total: int, text: str):
        """Starts tracking one sentence's blocks (v5.17).

        Call right after enqueue_playback with its return value.
        A sentence with no blocks is never tracked: nothing would
        consume it, so the FIFO head would stick forever."""
        if block_count <= 0:
            return
        self._sentence_fifo.append({
            "remaining": int(block_count),
            "index": int(index),
            "total": int(total),
            "text": str(text),
        })

    def _note_block_consumed(self):
        """Counts one queue block out of the head sentence record,
        retiring the record when its last block leaves."""
        if self._sentence_fifo:
            head = self._sentence_fifo[0]
            head["remaining"] -= 1
            if head["remaining"] <= 0:
                self._sentence_fifo.popleft()

    def set_playback_paused(self, paused: bool):
        """Pauses or resumes playback in place (v5.17). Pausing
        takes audible effect on the next callback tick; the level
        meter drops immediately so the GUI never shows her
        'speaking' over silence."""
        self.playback_paused = bool(paused)
        if self.playback_paused:
            self.speaker_level = 0.0

    def skip_current_sentence(self) -> bool:
        """Drops the rest of the sentence currently playing (v5.17).

        The head record's remaining blocks sit at the head of the
        playback queue — enqueue order IS queue order — so draining
        exactly that many leaves the next sentence's blocks
        untouched. Works while paused (the drain is silent either
        way). Returns False when nothing is tracked."""
        if not self._sentence_fifo:
            return False
        remaining = self._sentence_fifo[0]["remaining"]
        for _ in range(max(0, remaining)):
            try:
                self.playback_queue.get_nowait()
            except queue.Empty:
                break
        self._sentence_fifo.popleft()
        return True

    def interrupt_playback(self):
        """Immediately interrupts assistant playback.

        Field-check fix #13 (M13c): snapshot the queue depth (and
        the drop counter) BEFORE setting the interrupt event, so
        the next callback tick drains only the blocks that were
        queued at the interrupt — audio enqueued after it (the
        next turn's first chunks) survives the tick instead of
        being drained as stale. An enqueue landing between the
        snapshot and the set is post-interrupt by definition and
        must survive. The sentence FIFO is cleared here, at
        interrupt time: any record appended after this point
        belongs to a post-interrupt turn."""
        self._sentence_fifo.clear()
        self._interrupt_drain_blocks = self.playback_queue.qsize()
        self._interrupt_dropped_at = getattr(
            self, "playback_dropped_blocks", 0)
        self._interrupt_event.set()

    def clear_sentence_tracking(self):
        """Drops all sentence tracking WITHOUT touching the queue
        (v5.17). For callers that reshape the playback queue
        themselves — the local session's barge-in fade drains and
        re-enqueues a faded slice directly, so the FIFO records
        (which counted the drained blocks) would otherwise linger
        on sentences that will never play, freezing the karaoke
        caption and the sentences-left count."""
        self._sentence_fifo.clear()
