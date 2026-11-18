"""Live meeting capture -> transcription -> notes (R7-4, meetily pattern).

The pattern, not the code: meetily (Zackriya-Solutions/meetily, MIT,
32.5k stars) is a Tauri/Rust + Next.js app — nothing in it can be
adopted by a Python/PySide6 tree. What is built here is VYUK's own
code for the same shape: capture a live meeting, transcribe it
on-device, and produce notes from the transcript, with nothing
leaving the machine.

Gap this closes (proven read-only before writing):
- core/daily_meeting.py:178 — produce_meeting_notes(transcript, gate)
  takes a TRANSCRIPT string; the meeting flow was transcript-in only.
- core/voice/asr.py:185 — transcribe_with_words / transcribe_text
  (faster-whisper, already declared, already the session engine) had
  NO callers except the availability probe; the tree had no
  ``transcribe*`` funnel tool (recorded at
  core/funnel/toolbox_tokens.py:441-449).
- No live meeting capture existed anywhere (no record_meeting /
  capture_meeting). GAPS-20 (D-10) wired the system-audio
  loopback into the session: core/loopback.py now opens a real
  capture session (pyaudiowpatch-subprocess rung, sounddevice
  rung as fallback) and start_meeting takes an explicit opt-in
  ``system_audio`` parameter (a device name, or "default"). The
  system track is a second 16 kHz mono int16 WAV, transcribed
  alongside the mic track. PyAudioWPatch is NOT a declared
  dependency (Windows-only wheel, known-optional); its licence
  (Apache-2.0) is recorded in THIRD_PARTY_LICENSES.md.

Consent + visibility (hard, the MediaPipe privacy pattern):
capture starts ONLY on the user's explicit per-session say-so — the
start_meeting tool call itself, gated by
capture_guard.ensure_capture_allowed("microphone") (durable grant or
a per-session ask; a Settings-off mic or a denylisted foreground
window refuses). The system-audio track is an ADDITIONAL explicit
opt-in: start_meeting's ``system_audio`` parameter names a device,
and the track is gated by ensure_capture_allowed("system_audio")
(the per-session ask + the denylist; there is no durable Settings
toggle for system audio yet — see capture_guard — so the ask is
its gate). The on-screen recording indicator is live for the
whole capture (note_capture_started / note_capture_ended bracket
the session, including transcription), and stopping is one action
(stop_meeting). No audio leaves the machine: the WAVs, the
transcript and the notes are written to the user's data dir only,
and this module imports no network module at all (asserted by the
suite).

NO diarisation in this batch: speaker labels need pyannote (torch +
gated Hugging Face models) — not available. The transcript is one
undifferentiated track, labelled "meeting audio", and the stop result
says so plainly — with a system-audio track the transcript has two
source-labelled sections ([microphone] / [system audio]) and still
no speaker labels inside either section. Do not promise speaker ID.

Shape:
- 16 kHz mono int16 WAV, streamed to disk per audio callback
  (bounded memory — a meeting is hours long, never held in RAM).
- Transcription in 120 s segments through asr.transcribe_with_words.
- Meeting cap 4 hours; the cap is a guardrail, surfaced honestly
  in status/stop, never a silent cut. The system-audio track
  shares the cap: its own WAV stops at 4 hours with the reason
  surfaced in the stop result.
- The system track (GAPS-20): a second 16 kHz mono int16 WAV,
  ``<stem>_system.wav``, fed by the loopback session's converted
  frames (float32 stereo at the device rate -> 16 kHz mono int16,
  core/loopback._to_meeting_pcm). Transcribed alongside the mic
  track; the notes are built from the two source-labelled sections.
- transcribe_audio resamples foreign-rate WAVs to 16 kHz EXPLICITLY
  (linear interpolation, own code, documented) because asr.py
  refuses non-16 kHz input rather than silently resampling — so the
  tree's own 44.1 kHz record_audio clips are transcribable too.
"""

from __future__ import annotations

import json
import struct
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

from . import capture_guard
from . import loopback
from .daily_meeting import MeetingGate, produce_meeting_notes
from .skills_media_helpers import _DATA_DIR
from core.funnel.tools_registry_core import _funnel_only_fn


_SAMPLE_RATE = 16000  # config.py:548 sample_rate_in=16000; asr.py takes 16 kHz only
_SEGMENT_SECONDS = 120  # transcription segment, matching record_audio's 120 s cap shape
_MAX_MEETING_SECONDS = 4 * 3600  # guardrail cap; surfaced, never a silent cut
_MEETING_DIR = "recordings"  # same dir record_audio writes to (skills_media_helpers._DATA_DIR)

# Honest absence, stated wherever a transcript is shown: no speaker
# labels until a diarisation path exists (pyannote needs torch +
# gated HF models — not available).
_NO_DIARISATION = (
    "Speaker labels are not available in this build — the transcript "
    "below is one undifferentiated track of the meeting audio "
    "(no diarisation path exists yet)."
)

# The two-track transcript: the sections are labelled by SOURCE
# (which side of the call the audio came from), not by speaker —
# there are still no speaker labels inside either section.
_NO_DIARISATION_TWO_TRACK = (
    "Speaker labels are not available in this build — the transcript "
    "below has two sections, [microphone] (this side of the call) "
    "and [system audio] (the other side), and neither section has "
    "speaker labels (no diarisation path exists yet)."
)


def _meeting_dir() -> Path:
    """data/recordings/ — the user's data dir, same as record_audio."""
    target = _DATA_DIR / _MEETING_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _meeting_stem() -> str:
    """meeting_YYYYMMDD_HHMMSS — the meeting's date in the name."""
    return "meeting_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def _meeting_system_path(wav_path: Path) -> Path:
    """The system-audio WAV next to the mic WAV: <stem>_system.wav."""
    return wav_path.with_name(f"{wav_path.stem}_system.wav")


def _meeting_path() -> Path:
    """A fresh recording path — a second meeting in the same second
    never overwrites the first one's WAV (appends _2, _3, ...)."""
    directory = _meeting_dir()
    stem = _meeting_stem()
    path = directory / f"{stem}.wav"
    n = 2
    while path.exists():
        path = directory / f"{stem}_{n}.wav"
        n += 1
    return path


def _resample_to_16k(pcm_bytes: bytes, src_rate: int) -> bytes:
    """Linear-interpolation resample of mono int16 PCM to 16 kHz.

    Own code, explicit, documented — asr.py refuses non-16 kHz input
    rather than silently resampling, so a foreign-rate WAV (e.g. a
    44.1 kHz record_audio clip) is converted HERE, in the open, before
    it reaches the engine. src_rate == 16000 returns the input
    unchanged.
    """
    if int(src_rate) == _SAMPLE_RATE:
        return bytes(pcm_bytes)
    if int(src_rate) <= 0:
        raise ValueError("src_rate must be positive")
    n_in = len(pcm_bytes) // 2
    if n_in == 0:
        return b""
    samples = struct.unpack(f"<{n_in}h", pcm_bytes[: n_in * 2])
    n_out = max(1, round(n_in * _SAMPLE_RATE / int(src_rate)))
    step = n_in / n_out
    out = []
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = samples[j]
        b = samples[j + 1] if j + 1 < n_in else a
        out.append(int(a + frac * (b - a)))
    return struct.pack(f"<{len(out)}h", *out)


def _transcribe_pcm(pcm_bytes: bytes) -> dict[str, Any]:
    """One asr.py call (the seam the suite/prover patch).

    Lazy import so this module imports cleanly where faster-whisper
    is absent; asr.py itself never raises for a missing engine —
    it returns engine="unavailable" with an error string.
    """
    from .voice import asr

    return dict(asr.transcribe_with_words(pcm_bytes))


def _transcribe_wav(path: Path) -> dict[str, Any]:
    """Transcribe a WAV file in 120 s segments via asr.py.

    Returns {"text", "engine", "error"} — engine is "faster-whisper"
    on success, "unavailable" when the engine cannot run (with the
    first error), or "no-audio" when the file holds no frames.
    Nothing is fabricated: an unavailable engine yields empty text
    and the error, never a made-up transcript.
    """
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sampwidth = wav.getsampwidth()
        rate = wav.getframerate()
        n_frames = wav.getnframes()
        if channels != 1 or sampwidth != 2:
            return {
                "text": "",
                "engine": "unavailable",
                "error": (
                    f"unsupported WAV format: {channels} channel(s), "
                    f"{sampwidth * 8}-bit — this path takes mono 16-bit only"
                ),
            }
        if n_frames == 0:
            return {"text": "", "engine": "no-audio",
                    "error": "the recording holds no audio"}
        frames_per_segment = _SEGMENT_SECONDS * rate
        texts: list[str] = []
        first_error: str | None = None
        while True:
            raw = wav.readframes(frames_per_segment)
            if not raw:
                break
            pcm = _resample_to_16k(raw, rate)
            result = _transcribe_pcm(pcm)
            if result.get("engine") == "unavailable":
                if first_error is None:
                    first_error = str(result.get("error") or "engine unavailable")
                continue
            text = str(result.get("text") or "").strip()
            if text:
                texts.append(text)
    if not texts and first_error is not None:
        return {"text": "", "engine": "unavailable", "error": first_error}
    return {"text": "\n".join(texts), "engine": "faster-whisper", "error": None}


class _SoundDeviceBackend:
    """The real mic backend: one sounddevice InputStream, 16 kHz.

    sounddevice is already declared (requirements.txt:6); the import
    is lazy so this module loads where sounddevice is absent, and a
    missing backend fails closed with an honest message instead of
    a crash. No second mic loop is built — this IS the tree's mic
    path shape (same rate, same device default as record_audio).
    """

    def __init__(self) -> None:
        self._stream = None
        self._on_chunk = None
        self._overflows = 0

    def open(self, on_chunk) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001 — absent dep fails closed
            raise RuntimeError(
                "sounddevice is not available on this machine, so the "
                "microphone cannot be opened for the meeting"
            ) from exc
        self._on_chunk = on_chunk

        def _callback(indata, frames, time_info, status):  # noqa: ANN001, ANN202
            if status is not None and getattr(status, "input_overflow", False):
                self._overflows += 1
            if self._on_chunk is not None:
                self._on_chunk(bytes(indata))

        self._stream = sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=1600,  # 100 ms callbacks at 16 kHz
            callback=_callback,
        )
        self._stream.start()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001 — stop is best-effort
                pass
            try:
                stream.close()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass

    @property
    def overflows(self) -> int:
        return self._overflows


# Test seam (mirrors skills_media_audio.set_volume_backend): the
# suite/prover inject a fake backend that feeds synthetic PCM, so
# the whole pipeline runs on a machine with no mic. Production
# always uses the real sounddevice backend unless a test sets one.
_CAPTURE_BACKEND: Any = None


def set_capture_backend(backend: Any) -> None:
    """Inject a capture backend (tests only); None restores the real one."""
    global _CAPTURE_BACKEND  # noqa: PLW0603 — the seam is the point
    _CAPTURE_BACKEND = backend


def reset_seams() -> None:
    """Restore the production capture backend (suite/prover tearDown)."""
    set_capture_backend(None)


class MeetingSession:
    """One live meeting: idle -> recording -> (stop) -> idle.

    A single session at a time — starting while one is recording
    refuses honestly. The WAV is streamed to disk per callback;
    nothing but a small buffer is ever held in RAM.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "idle"
        self._title = ""
        self._started_wall: str | None = None
        self._started_mono: float | None = None
        self._wav_path: Path | None = None
        self._wav: Any = None
        self._frames = 0
        self._cap_reached = False
        self._backend: Any = None
        # System-audio track (GAPS-20, D-10): a second WAV fed by
        # the loopback session. None when the meeting is mic-only.
        self._system_wav_path: Path | None = None
        self._system_wav: Any = None
        self._system_frames = 0
        self._system_cap_reached = False
        self._system_device: str | None = None
        self._system_backend: str | None = None
        self._last_result: dict[str, Any] | None = None

    # -- state ---------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def status(self) -> dict[str, Any]:
        """Current state for meeting_status (read-only, no side effect)."""
        elapsed = None
        if self._started_mono is not None and self._state == "recording":
            elapsed = time.monotonic() - self._started_mono
        return {
            "state": self._state,
            "title": self._title,
            "started_at": self._started_wall,
            "elapsed_s": elapsed,
            "wav_path": str(self._wav_path) if self._wav_path else None,
            "frames": self._frames,
            "cap_reached": self._cap_reached,
            "system_wav_path": (str(self._system_wav_path)
                                if self._system_wav_path else None),
            "system_frames": self._system_frames,
            "system_cap_reached": self._system_cap_reached,
            "system_device": self._system_device,
            "system_backend": self._system_backend,
        }

    # -- capture -------------------------------------------------------
    def _on_audio(self, pcm_bytes: bytes) -> None:
        """One audio callback: stream the chunk straight to the WAV."""
        with self._lock:
            if self._wav is None or self._cap_reached:
                return
            self._wav.writeframes(pcm_bytes)
            self._frames += len(pcm_bytes) // 2
            if self._frames >= _MAX_MEETING_SECONDS * _SAMPLE_RATE:
                self._cap_reached = True

    def _on_system_audio(self, pcm_bytes: bytes) -> None:
        """One loopback callback: stream the converted chunk
        straight to the system WAV (16 kHz mono int16, the same
        shape as the mic WAV, so _transcribe_wav handles it
        unchanged). The 4-hour cap applies here too."""
        with self._lock:
            if self._system_wav is None or self._system_cap_reached:
                return
            self._system_wav.writeframes(pcm_bytes)
            self._system_frames += len(pcm_bytes) // 2
            if self._system_frames >= _MAX_MEETING_SECONDS * _SAMPLE_RATE:
                self._system_cap_reached = True

    def start(self, title: str = "", system_audio: str = "") -> tuple[bool, str]:
        """Start recording — explicit per-session say-so only.

        The start_meeting tool call IS the say-so; the capture guard
        adds the durable-grant-or-ask on top, and a Settings-off mic
        or a denylisted foreground window refuses before any audio
        is touched. ``system_audio`` (GAPS-20, D-10) is an extra
        explicit opt-in: a device name (or "default") opens the
        system-audio loopback track alongside the mic, gated by
        ensure_capture_allowed("system_audio"); empty means mic
        only. Both guards are checked before any audio is touched,
        and a failed system track tears the mic down too — there
        is no half-started meeting.
        """
        if self._state == "recording":
            return False, (
                f"A meeting is already recording"
                f"{f' ({self._title!r})' if self._title else ''} "
                f"since {self._started_wall}; stop it before starting another."
            )
        allowed, message = capture_guard.ensure_capture_allowed("microphone")
        if not allowed:
            return False, message
        want_system = bool(str(system_audio or "").strip())
        if want_system:
            allowed, message = capture_guard.ensure_capture_allowed("system_audio")
            if not allowed:
                return False, message
        wav_path = _meeting_path()
        try:
            wav = wave.open(str(wav_path), "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(_SAMPLE_RATE)
        except OSError as exc:
            return False, f"Could not open the meeting recording file: {exc}"
        self._wav = wav
        self._wav_path = wav_path
        self._frames = 0
        self._cap_reached = False
        capture_guard.note_capture_started("microphone")
        backend = _CAPTURE_BACKEND if _CAPTURE_BACKEND is not None else _SoundDeviceBackend()
        try:
            backend.open(self._on_audio)
        except Exception as exc:  # noqa: BLE001 — a dead mic fails closed
            self._wav.close()
            self._wav = None
            capture_guard.note_capture_ended("microphone")
            try:
                wav_path.unlink()
            except OSError:
                pass
            return False, (
                f"Could not open the microphone for the meeting: {exc}. "
                "No audio was captured."
            )
        self._backend = backend
        # System track second: the mic is open, so a failure here
        # tears it down (no half-started meeting).
        system_wav = None
        system_wav_path = None
        if want_system:
            system_wav_path = _meeting_system_path(wav_path)
            try:
                system_wav = wave.open(str(system_wav_path), "wb")
                system_wav.setnchannels(1)
                system_wav.setsampwidth(2)
                system_wav.setframerate(_SAMPLE_RATE)
            except OSError as exc:
                backend.close()
                self._backend = None
                self._wav.close()
                self._wav = None
                capture_guard.note_capture_ended("microphone")
                try:
                    wav_path.unlink()
                except OSError:
                    pass
                return False, (
                    "Could not open the system-audio recording file: "
                    f"{exc}. No audio was captured."
                )
            self._system_wav = system_wav
            self._system_wav_path = system_wav_path
            self._system_frames = 0
            self._system_cap_reached = False
            capture_guard.note_capture_started("system_audio")
            ok, info = loopback.start_capture(
                device=str(system_audio).strip(),
                on_frame=self._on_system_audio)
            if not ok:
                loopback.reset_capture()
                self._system_wav.close()
                self._system_wav = None
                self._system_wav_path = None
                capture_guard.note_capture_ended("system_audio")
                backend.close()
                self._backend = None
                self._wav.close()
                self._wav = None
                capture_guard.note_capture_ended("microphone")
                try:
                    wav_path.unlink()
                except OSError:
                    pass
                try:
                    system_wav_path.unlink()
                except OSError:
                    pass
                return False, (
                    "Could not open system audio for the meeting: "
                    f"{info}. No audio was captured."
                )
            if isinstance(info, dict):
                self._system_device = str(info.get("device") or system_audio)
                self._system_backend = str(info.get("backend") or "")
        self._title = str(title or "").strip()
        self._started_wall = datetime.now().isoformat(timespec="seconds")
        self._started_mono = time.monotonic()
        self._state = "recording"
        message = (
            f"Recording the meeting"
            f"{f' ({self._title!r})' if self._title else ''} — the "
            "recording indicator is on. Say 'stop meeting' or run "
            "stop_meeting to finish and get the notes. Nothing leaves "
            "this machine."
        )
        if want_system:
            message += (
                f" System audio is also being captured from "
                f"{self._system_device} ({self._system_backend})."
            )
        return True, message

    def stop(self) -> tuple[bool, dict[str, Any]]:
        """Stop, transcribe, produce notes — one action.

        The indicator stays live until the notes are written (the
        meeting is still being handled), then ends. The result is
        always honest: an unavailable engine yields the recording's
        path and the error, never a fabricated transcript.
        """
        if self._state != "recording":
            return False, {"error": "No meeting is recording right now."}
        backend, self._backend = self._backend, None
        if backend is not None:
            try:
                backend.close()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass
        with self._lock:
            wav, self._wav = self._wav, None
            if wav is not None:
                wav.close()
        # System track teardown (GAPS-20, D-10): stop the loopback
        # session first so no frames arrive after the WAV closes,
        # then close the system WAV. This runs before every return
        # below, so a stopped meeting never leaves a live capture.
        system_stop = loopback.stop_capture()
        with self._lock:
            system_wav, self._system_wav = self._system_wav, None
            if system_wav is not None:
                system_wav.close()
        system_wav_path = self._system_wav_path
        system_frames = self._system_frames
        system_cap_reached = self._system_cap_reached
        system_backend = self._system_backend
        system_device = self._system_device
        had_system = system_wav_path is not None
        if isinstance(system_stop, dict) and system_stop.get("reason"):
            # The session's own end reason (cap / backend ended)
            # outranks the WAV's local cap flag when they disagree.
            if system_stop["reason"] in ("seconds cap reached",
                                         "frames cap reached"):
                system_cap_reached = True

        def _end_indicators():
            capture_guard.note_capture_ended("microphone")
            if had_system:
                capture_guard.note_capture_ended("system_audio")

        wav_path = self._wav_path
        title = self._title
        started_wall = self._started_wall
        elapsed = (
            time.monotonic() - self._started_mono
            if self._started_mono is not None
            else None
        )
        overflows = 0
        if backend is not None:
            overflows = int(getattr(backend, "overflows", 0) or 0)
        self._state = "idle"
        # Idle carries no stale recording identity — the finished
        # meeting's details live in last_result, not in the session.
        self._started_wall = None
        self._started_mono = None
        self._wav_path = None
        self._title = ""
        self._frames = 0
        self._cap_reached = False
        self._system_wav_path = None
        self._system_frames = 0
        self._system_cap_reached = False
        self._system_device = None
        self._system_backend = None
        result: dict[str, Any] = {
            "title": title,
            "started_at": started_wall,
            "elapsed_s": elapsed,
            "wav_path": str(wav_path) if wav_path else None,
            "cap_reached": self._cap_reached,
            "overflows": overflows,
            "transcript_path": None,
            "notes_path": None,
            "transcript": "",
            "notes": None,
            "error": None,
            "system_wav_path": (str(system_wav_path)
                                if system_wav_path else None),
            "system_frames": system_frames,
            "system_cap_reached": system_cap_reached,
            "system_backend": system_backend,
            "system_device": system_device,
            "system_transcript_path": None,
            "system_transcript": "",
            "system_error": None,
        }
        if wav_path is None:
            result["error"] = "The recording file was lost before stopping."
            self._last_result = result
            _end_indicators()
            return True, result
        # A4-filestat: the recording is re-read after the write — its
        # byte size rides the success line, never a bare "saved".
        try:
            result["wav_bytes"] = wav_path.stat().st_size
        except OSError:
            result["wav_bytes"] = None
        stem = wav_path.stem
        transcript_path = wav_path.with_suffix(".txt")
        notes_path = wav_path.with_name(f"{stem}_notes.json")
        result["transcript_path"] = str(transcript_path)
        result["notes_path"] = str(notes_path)
        try:
            transcribed = _transcribe_wav(wav_path)
        except Exception as exc:  # noqa: BLE001 — transcription never leaks a recording state
            transcribed = {
                "text": "",
                "engine": "unavailable",
                "error": f"transcription failed: {exc}",
            }
        if transcribed.get("engine") == "unavailable":
            result["error"] = str(transcribed.get("error") or "engine unavailable")
            transcript_path.write_text(
                "Meeting transcript — unavailable.\n\n"
                f"{_NO_DIARISATION}\n\n"
                f"Transcription could not run: {result['error']}\n"
                "The audio recording was saved; run transcribe_audio on it "
                "once the transcription engine is available.\n",
                encoding="utf-8",
            )
            self._last_result = result
            _end_indicators()
            return True, result
        if transcribed.get("engine") == "no-audio":
            result["error"] = "No audio was captured in the meeting."
            transcript_path.write_text(
                "Meeting transcript — no audio.\n\n"
                f"{_NO_DIARISATION}\n\n"
                "The recording holds no audio frames.\n",
                encoding="utf-8",
            )
            self._last_result = result
            _end_indicators()
            return True, result
        text = str(transcribed.get("text") or "")
        # System track (GAPS-20, D-10): transcribe the system WAV
        # alongside the mic WAV. A system transcription failure
        # never fails the stop — the mic notes still land, and the
        # failure is recorded in the result + the combined text.
        system_text = ""
        system_error = None
        if system_wav_path is not None:
            try:
                result["system_wav_bytes"] = system_wav_path.stat().st_size
            except OSError:
                result["system_wav_bytes"] = None
            system_transcript_path = system_wav_path.with_suffix(".txt")
            result["system_transcript_path"] = str(system_transcript_path)
            try:
                sys_transcribed = _transcribe_wav(system_wav_path)
            except Exception as exc:  # noqa: BLE001 — transcription never leaks a recording state
                sys_transcribed = {
                    "text": "",
                    "engine": "unavailable",
                    "error": f"transcription failed: {exc}",
                }
            if sys_transcribed.get("engine") == "unavailable":
                system_error = str(sys_transcribed.get("error")
                                   or "engine unavailable")
                system_transcript_path.write_text(
                    "Meeting transcript (system audio) — unavailable.\n\n"
                    f"{_NO_DIARISATION_TWO_TRACK}\n\n"
                    f"Transcription could not run: {system_error}\n"
                    "The system-audio recording was saved; run "
                    "transcribe_audio on it once the transcription "
                    "engine is available.\n",
                    encoding="utf-8",
                )
            elif sys_transcribed.get("engine") == "no-audio":
                system_transcript_path.write_text(
                    "Meeting transcript (system audio) — no audio.\n\n"
                    f"{_NO_DIARISATION_TWO_TRACK}\n\n"
                    "The system-audio recording holds no audio frames.\n",
                    encoding="utf-8",
                )
            else:
                system_text = str(sys_transcribed.get("text") or "")
                system_transcript_path.write_text(
                    f"Meeting transcript (system audio) — "
                    f"{started_wall or ''}\n\n"
                    f"{_NO_DIARISATION_TWO_TRACK}\n\n"
                    f"{system_text}\n",
                    encoding="utf-8",
                )
            try:
                result["system_transcript_bytes"] = (
                    system_transcript_path.stat().st_size)
            except OSError:
                result["system_transcript_bytes"] = None
            result["system_transcript"] = system_text
            result["system_error"] = system_error
        # The transcript the notes are built from: the mic text
        # alone when there is no system track (identical to before),
        # else the two source-labelled sections.
        if system_wav_path is not None:
            combined = "[microphone]\n" + text
            if system_error is not None:
                combined += ("\n\n[system audio]\n(transcription "
                             f"unavailable: {system_error})")
            elif system_text:
                combined += "\n\n[system audio]\n" + system_text
            else:
                combined += ("\n\n[system audio]\n(no system audio "
                             "was captured.)")
            diarisation_note = _NO_DIARISATION_TWO_TRACK
        else:
            combined = text
            diarisation_note = _NO_DIARISATION
        result["transcript"] = combined
        transcript_path.write_text(
            f"Meeting transcript — {started_wall or ''}\n\n"
            f"{diarisation_note}\n\n"
            f"{combined}\n",
            encoding="utf-8",
        )
        # The gate is honest: opt_in because the user explicitly
        # started this meeting, indicator_on because the recording
        # indicator is still live at this point (it ends below).
        gate = MeetingGate(opt_in=True, indicator_on=True)
        notes, notes_error = produce_meeting_notes(combined, gate)
        if notes_error is not None:
            result["error"] = f"Notes could not be produced: {notes_error}"
            self._last_result = result
            _end_indicators()
            return True, result
        result["notes"] = notes
        notes_path.write_text(
            json.dumps(notes, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # A4-filestat (continued): the transcript + notes files are
        # re-read after their writes too.
        try:
            result["transcript_bytes"] = transcript_path.stat().st_size
        except OSError:
            result["transcript_bytes"] = None
        try:
            result["notes_bytes"] = notes_path.stat().st_size
        except OSError:
            result["notes_bytes"] = None
        self._last_result = result
        _end_indicators()
        return True, result

    @property
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result


_SESSION = MeetingSession()


def get_session() -> MeetingSession:
    """The live meeting session (one at a time)."""
    return _SESSION


# -- funnel handlers ---------------------------------------------------

@_funnel_only_fn
def _tool_start_meeting(title: str = "", system_audio: str = "") -> str:
    ok, message = get_session().start(title, system_audio=system_audio)
    return message


@_funnel_only_fn
def _tool_stop_meeting() -> str:
    ok, result = get_session().stop()
    if not ok:
        return str(result.get("error") or "No meeting is recording right now.")
    lines = []
    title = result.get("title") or "the meeting"
    elapsed = result.get("elapsed_s")
    elapsed_s = f"{elapsed:.0f} seconds" if isinstance(elapsed, (int, float)) else "unknown length"
    lines.append(f"Meeting {title!r} stopped after {elapsed_s}.")
    if result.get("cap_reached"):
        lines.append(
            "It hit the 4-hour recording cap — the audio after the cap "
            "was not recorded."
        )
    if int(result.get("overflows") or 0) > 0:
        lines.append(
            f"The microphone dropped {result['overflows']} audio buffer(s) "
            "during the meeting, so there may be small gaps in the recording."
        )
    lines.append(f"Recording saved at {result.get('wav_path')}.")
    if isinstance(result.get("wav_bytes"), int):
        lines[-1] = (
            f"Recording saved at {result.get('wav_path')} "
            f"({result['wav_bytes']} bytes)."
        )
    if result.get("system_wav_path"):
        sys_line = (
            f"System-audio recording saved at "
            f"{result.get('system_wav_path')}")
        if isinstance(result.get("system_wav_bytes"), int):
            sys_line += f" ({result['system_wav_bytes']} bytes)"
        sys_line += (f" — captured from {result.get('system_device')} "
                     f"({result.get('system_backend')}).")
        lines.append(sys_line)
        if result.get("system_cap_reached"):
            lines.append(
                "The system-audio track hit the 4-hour recording cap — "
                "the system audio after the cap was not recorded."
            )
        if result.get("system_error"):
            lines.append(
                "The system-audio track could not be transcribed: "
                f"{result['system_error']} The system-audio recording "
                "is still saved — run transcribe_audio on it once the "
                "transcription engine is available."
            )
        elif result.get("system_transcript_path"):
            lines.append(
                "System-audio transcript saved at "
                f"{result.get('system_transcript_path')}.")
    if result.get("error"):
        lines.append(
            f"Notes could not be produced: {result['error']} "
            f"The transcript file is at {result.get('transcript_path')} — "
            "run transcribe_audio on the recording once the transcription "
            "engine is available."
        )
        return "\n".join(lines)
    lines.append(f"Transcript saved at {result.get('transcript_path')}.")
    notes = result.get("notes") or {}
    summary = str(notes.get("summary") or "")
    if summary:
        lines.append(f"Summary: {summary}")
    else:
        lines.append("No speech was detected in the recording, so there is no summary.")
    items = notes.get("action_items") or []
    if items:
        lines.append("Action items:")
        for item in items:
            lines.append(f"- {item}")
    lines.append(f"Notes saved at {result.get('notes_path')}.")
    lines.append(_NO_DIARISATION_TWO_TRACK
                 if result.get("system_wav_path") else _NO_DIARISATION)
    return "\n".join(lines)


@_funnel_only_fn
def _tool_meeting_status() -> str:
    status = get_session().status()
    if status["state"] != "recording":
        return "No meeting is recording right now."
    elapsed = status["elapsed_s"]
    elapsed_s = f"{elapsed:.0f} seconds" if isinstance(elapsed, (int, float)) else "unknown"
    line = (
        f"Recording a meeting"
        f"{f' ({status['title']!r})' if status['title'] else ''} — "
        f"started at {status['started_at']}, running for {elapsed_s}. "
        "The recording indicator is on."
    )
    if status["cap_reached"]:
        line += " It has hit the 4-hour recording cap — stop it to save the notes."
    if status.get("system_wav_path"):
        line += (
            f" System audio is also being captured from "
            f"{status.get('system_device')} ({status.get('system_backend')})."
        )
        if status.get("system_cap_reached"):
            line += " The system-audio track has hit the 4-hour cap."
    return line


@_funnel_only_fn
def _tool_transcribe_audio(filename: str = "") -> str:
    """Transcribe one recording from data/recordings/ via asr.py."""
    name = Path(str(filename or "")).name
    if not name:
        return "Give the file name of a recording in the recordings folder, e.g. transcribe_audio with filename 'meeting_20261110_120000.wav'."
    if name != str(filename or ""):
        return "The file must be a recording in the recordings folder — give just its file name, not a path."
    path = _meeting_dir() / name
    if path.suffix.lower() != ".wav":
        return f"{name} is not a .wav recording — transcribe_audio only takes .wav files from the recordings folder."
    if not path.is_file():
        return f"No recording named {name} is in the recordings folder."
    try:
        transcribed = _transcribe_wav(path)
    except Exception as exc:  # noqa: BLE001 — a bad file is an honest refusal
        return f"Could not transcribe {name}: {exc}"
    if transcribed.get("engine") == "unavailable":
        return (
            f"Transcription is unavailable right now: "
            f"{transcribed.get('error') or 'engine unavailable'}. "
            f"The recording {name} is still saved."
        )
    if transcribed.get("engine") == "no-audio":
        return f"{name} holds no audio, so there is nothing to transcribe."
    text = str(transcribed.get("text") or "").strip()
    if not text:
        return f"No speech was detected in {name}."
    return (
        f"Transcript of {name} (no speaker labels — diarisation is not "
        f"available in this build):\n{text}"
    )


def register_all(registry) -> None:
    """Register the four meeting tools (R7-4, meetily pattern)."""
    registry.register(
        name="start_meeting",
        description="Start recording a live meeting from the microphone and save it as a dated .wav file in VYUK's recordings folder. This is the ONLY way a meeting recording starts — it never starts on its own or in the background; the recording indicator shows while it runs. Use when the user asks to record a meeting. System audio (the other side of the call) is captured ONLY when system_audio names a device — the mic only, otherwise.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Optional title for the meeting (default: none)"},
                "system_audio": {"type": "string", "description": "Optional system-audio (other side of the call) capture: a device name, or 'default' for the first loopback device. Empty (default) means mic only. The system track is a second recording, transcribed alongside the mic track."},
            },
            "required": [],
        },
        handler=_tool_start_meeting,
    )
    registry.register(
        name="stop_meeting",
        description="Stop the meeting that is recording, transcribe it on this machine with the local speech engine, and write the notes (summary + action items) next to the recording. This is the one action that stops a meeting. Returns where the recording, transcript and notes were saved.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_tool_stop_meeting,
    )
    registry.register(
        name="meeting_status",
        description="Say whether a meeting is recording right now, and for how long it has been running. It only reports — it never starts or stops anything.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_tool_meeting_status,
    )
    registry.register(
        name="transcribe_audio",
        description="Transcribe one .wav recording from VYUK's recordings folder into text, using the local speech engine on this machine. The transcript has no speaker labels — diarisation is not available in this build. Use when the user asks for the words in a recording.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "File name of the recording in the recordings folder, e.g. 'meeting_20261110_120000.wav'"},
            },
            "required": ["filename"],
        },
        handler=_tool_transcribe_audio,
    )
