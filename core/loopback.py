"""WASAPI loopback capture rungs (v5.58, Wave 3, EA-13).

" Hear what the PC itself is playing" — two rungs, neither of
which imports PyAudioWPatch into the app process:

1. PyAudioWPatch WASAPI loopback rung — SUBPROCESS-ISOLATED
   ONLY. PyAudioWPatch cannot co-reside with sounddevice's
   bundled PortAudio in one process, so ``core.loopback_worker``
   (the only module allowed to import pyaudiowpatch) runs as a
   fresh subprocess and reports back over stdout JSON.
   ``probe_pyaudiowpatch_subprocess`` is the isolation proof:
   it spawns that worker and returns its report, including
   ``sounddevice_in_process`` (False by construction — a
   subprocess shares no modules with its parent). This module
   itself contains NO pyaudiowpatch import at any level; the
   test suite asserts that by AST so a future edit cannot
   silently break the isolation.

2. Minimal-diff WASAPI-default rung — sounddevice only. On
   Windows, sounddevice can open the system-default output's
   WASAPI loopback device when PortAudio exposes one; this rung
   enumerates (lazily importing sounddevice) and reports the
   candidates. It opens nothing here: capture needs a chosen
   device + a live Windows target, and this VM is Linux, so on
   this platform the rung honestly reports unavailable with
   the reason, instead of pretending a loopback exists.

Both rungs are OFF by default and neither changes the mic or
playback streams. ``status()`` is the single report the
coordinator reads: platform, both rungs' availability, and
the recommended backend (None until a rung is actually
available on the target machine).
"""

from __future__ import annotations

import base64
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def probe_pyaudiowpatch_subprocess(timeout_s: float = 30.0) -> dict[str, Any]:
    """Spawn the isolated worker and return its probe report.

    Never raises: a spawn failure, a timeout, or unparseable
    worker output is reported as unavailable with the reason.
    The worker process is fresh — that IS the isolation proof,
    and the report's ``sounddevice_in_process`` field lets the
    caller verify it rather than trust it.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "core.loopback_worker", "probe"],
            capture_output=True, text=True, timeout=float(timeout_s),
            cwd=str(_package_root()),
            env={**__import__("os").environ,
                 "PYTHONPATH": str(_package_root())},
        )
        report = json.loads(proc.stdout.strip().splitlines()[-1])
        report["subprocess_isolated"] = True
        report["returncode"] = proc.returncode
        return report
    except Exception as e:  # noqa: BLE001
        return {
            "pyaudiowpatch_available": False,
            "pyaudiowpatch_error": f"{type(e).__name__}: {e}",
            "sounddevice_in_process": None,
            "subprocess_isolated": True,
            "wasapi_loopback": False,
            "devices": [],
        }


def wasapi_default_candidates() -> dict[str, Any]:
    """Minimal-diff rung: enumerate loopback candidates via sounddevice.

    Lazy sounddevice import; on a platform without it (or
    without PortAudio) this reports unavailable, never raises.
    A device counts as a candidate when PortAudio marks it as
    a loopback device or its name says so — the caller still
    chooses explicitly; nothing auto-opens.
    """
    try:
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001
        return {
            "available": False,
            "reason": f"sounddevice unavailable: {e}",
            "candidates": [],
        }
    try:
        devices = sd.query_devices()
        candidates = []
        for i, dev in enumerate(devices):
            try:
                name = str(dev.get("name", ""))
                if dev.get("isLoopbackDevice") or "loopback" in name.lower():
                    candidates.append({"index": i, "name": name})
            except Exception:  # noqa: BLE001
                continue
        return {
            "available": bool(candidates),
            "reason": (None if candidates else
                       "no WASAPI loopback device enumerated"),
            "candidates": candidates,
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"enumerate failed: {e}",
                "candidates": []}


def status(probe_subprocess: bool = True) -> dict[str, Any]:
    """Combined loopback status. Set probe_subprocess=False to skip
    spawning (e.g. in a hot path); the subprocess rung then
    reports ``probed: False`` instead of a stale verdict. The
    capture state is the LIVE session's (no spawn): a capture
    started via start_capture() reads active here."""
    pyaudio_rung: dict[str, Any] = {"probed": False}
    if probe_subprocess:
        pyaudio_rung = probe_pyaudiowpatch_subprocess()
        pyaudio_rung["probed"] = True
    default_rung = wasapi_default_candidates()
    recommended = None
    if pyaudio_rung.get("pyaudiowpatch_available"):
        recommended = "pyaudiowpatch-subprocess"
    elif default_rung.get("available"):
        recommended = "sounddevice-wasapi-default"
    live = capture_status()
    return {
        "platform": sys.platform,
        "pyaudiowpatch_subprocess": pyaudio_rung,
        "wasapi_default": default_rung,
        "recommended_backend": recommended,
        "capture_active": bool(live.get("active")),
        "capture": live,
        "note": (
            "Loopback capture opens no device from status(). A live "
            "capture started via start_capture() reads active here; "
            "on this Linux VM both rungs are expected unavailable, "
            "and a Windows target with PyAudioWPatch installed "
            "activates the subprocess rung."
        ),
    }


# ---------------------------------------------------------------------------
# Capture session (GAPS-20, D-10)
# ---------------------------------------------------------------------------

_TARGET_RATE = 16000  # the meeting shape: 16 kHz mono int16
_CAPTURE_MAX_SECONDS = 14400.0  # the meeting 4-hour cap, shared
_CAPTURE_QUEUE_MAX = 1024  # frames; a slow consumer drops, counted
_WORKER_FIRST_LINE_TIMEOUT_S = 10.0

# Test seam (mirrors meeting.set_capture_backend): override the
# worker command prefix, e.g. [sys.executable, fake_script] for a
# suite that feeds canned NDJSON without pyaudiowpatch.
_WORKER_COMMAND_OVERRIDE: list[str] | None = None


def set_worker_command(cmd: list[str] | None) -> None:
    """Override the capture-worker command (tests only); None restores."""
    global _WORKER_COMMAND_OVERRIDE  # noqa: PLW0603 — the seam is the point
    _WORKER_COMMAND_OVERRIDE = list(cmd) if cmd else None


def reset_worker_command() -> None:
    """Restore the production worker command (suite/prover tearDown)."""
    set_worker_command(None)


def _to_meeting_pcm(data: bytes, channels: int, src_rate: int) -> bytes:
    """float32 interleaved PCM (1–2 channels, any rate) -> 16 kHz
    mono int16 — the meeting's exact shape.

    Downmix (mean of the channels) + float->int16 with clamping
    (NaN reads as silence, never a crash) + linear-interpolation
    resample to 16 kHz, mirroring meeting._resample_to_16k's
    algorithm so a loopback frame lands in the meeting WAV
    byte-identical in shape to a mic frame.
    """
    import struct

    n = len(data) // 4
    if n == 0:
        return b""
    vals = struct.unpack(f"<{n}f", data[: n * 4])
    if int(channels) == 2:
        mono = [(vals[i] + vals[i + 1]) * 0.5
                for i in range(0, n - 1, 2)]
    else:
        mono = list(vals)
    ints = []
    for v in mono:
        if v != v:  # NaN — silence, never a crash
            v = 0.0
        ints.append(max(-32768, min(32767, int(round(v * 32767)))))
    if int(src_rate) == _TARGET_RATE:
        return struct.pack(f"<{len(ints)}h", *ints)
    n_in = len(ints)
    if n_in == 0:
        return b""
    n_out = max(1, round(n_in * _TARGET_RATE / int(src_rate)))
    step = n_in / n_out
    out = []
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = ints[j]
        b = ints[j + 1] if j + 1 < n_in else a
        out.append(int(a + frac * (b - a)))
    return struct.pack(f"<{len(out)}h", *out)


class _SubprocessCapture:
    """The pyaudiowpatch-subprocess backend: one worker process,
    NDJSON frames on its stdout, read by a thread."""

    def __init__(self) -> None:
        self._proc = None
        self._reader = None
        self._queue: queue.Queue = queue.Queue(maxsize=_CAPTURE_QUEUE_MAX)
        self._dropped = 0
        self._ended = threading.Event()
        self._end_info: dict[str, Any] = {}
        self._hello: dict[str, Any] | None = None
        self._hello_or_error: queue.Queue = queue.Queue(maxsize=1)

    def start(self, device: str, seconds: float,
              max_frames: int) -> tuple[bool, dict[str, Any] | str]:
        cmd = list(_WORKER_COMMAND_OVERRIDE) if _WORKER_COMMAND_OVERRIDE else [
            sys.executable, "-m", "core.loopback_worker", "capture"]
        cmd += ["--device", str(device), "--seconds", str(float(seconds)),
                "--max-frames", str(int(max_frames))]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=str(_package_root()),
                env={**__import__("os").environ,
                     "PYTHONPATH": str(_package_root())})
        except Exception as e:  # noqa: BLE001 — spawn failure is a refusal
            return False, f"could not spawn the loopback worker: {e}"
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            first = self._hello_or_error.get(
                timeout=_WORKER_FIRST_LINE_TIMEOUT_S)
        except queue.Empty:
            self.stop()
            return False, ("the loopback worker did not answer "
                           f"(no hello/error in {_WORKER_FIRST_LINE_TIMEOUT_S}s)")
        if first.get("type") == "error":
            self.stop()
            return False, str(first.get("reason") or "worker refused")
        self._hello = first
        return True, {"device": first.get("device"),
                      "index": first.get("index"),
                      "rate": first.get("rate"),
                      "channels": first.get("channels")}

    def _read_loop(self) -> None:
        try:
            for line in self._proc.stdout:
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001 — a corrupt line is a lost frame
                    self._dropped += 1
                    continue
                kind = obj.get("type")
                if kind == "hello":
                    self._hello_or_error.put(obj)
                elif kind == "error":
                    self._end_info = {"reason": str(obj.get("reason") or "worker error")}
                    self._hello_or_error.put(obj)
                    self._ended.set()
                elif kind == "frame":
                    try:
                        raw = base64.b64decode(obj.get("pcm_b64") or "")
                        self._queue.put_nowait(
                            (raw, int(obj.get("rate") or 0),
                             int(obj.get("channels") or 0),
                             float(obj.get("t") or 0.0)))
                    except Exception:  # noqa: BLE001 — a bad frame is a lost frame
                        self._dropped += 1
                elif kind == "end":
                    self._end_info = {
                        "reason": str(obj.get("reason") or "ended"),
                        "frames": int(obj.get("frames") or 0),
                        "dropped_chunks": int(obj.get("dropped_chunks") or 0)}
                    self._ended.set()
        except Exception:  # noqa: BLE001 — reader death ends the capture
            pass
        finally:
            self._ended.set()

    def read_raw(self, timeout: float) -> tuple | None:
        """(float32 bytes, rate, channels, monotonic t) or None."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def ended(self) -> bool:
        return self._ended.is_set()

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def end_info(self) -> dict[str, Any]:
        return dict(self._end_info)

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001 — terminate is best-effort
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 — a hung worker is killed
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            # Close the pipes: an unclosed stdout/stderr raises a
            # ResourceWarning and leaks the fds.
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:  # noqa: BLE001 — close is best-effort
                    pass


class _SoundDeviceLoopbackCapture:
    """The sounddevice-wasapi-default backend: an in-process
    InputStream on the loopback device. Only used when the
    subprocess rung is unavailable (the recommended order)."""

    def __init__(self) -> None:
        self._stream = None
        self._queue: queue.Queue = queue.Queue(maxsize=_CAPTURE_QUEUE_MAX)
        self._dropped = 0
        self._ended = threading.Event()
        self._hello: dict[str, Any] | None = None

    def start(self, device: str, seconds: float,
              max_frames: int) -> tuple[bool, dict[str, Any] | str]:
        del seconds, max_frames  # the session enforces the caps on this rung
        try:
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001 — absent dep fails closed
            return False, f"sounddevice is not available: {e}"
        try:
            cands = wasapi_default_candidates()
            candidates = cands.get("candidates") or []
            if not candidates:
                return False, str(cands.get("reason")
                                  or "no WASAPI loopback device enumerated")
            want = str(device or "default").strip()
            chosen = None
            if want.lower() == "default":
                chosen = candidates[0]
            else:
                try:
                    index = int(want)
                except ValueError:
                    index = None
                if index is not None:
                    for cand in candidates:
                        if int(cand.get("index", -1)) == index:
                            chosen = cand
                else:
                    for cand in candidates:
                        if str(cand.get("name") or "").lower() == want.lower():
                            chosen = cand
            if chosen is None:
                return False, (
                    f"no loopback device {want!r} "
                    f"(loopback devices: {', '.join(str(c.get('name')) for c in candidates)})")
            index = int(chosen["index"])
            name = str(chosen.get("name") or "")
            info = sd.query_devices(index)
            try:
                rate = int(info.get("default_samplerate") or 48000)
            except (TypeError, ValueError):
                rate = 48000
            try:
                max_in = int(info.get("max_input_channels") or 0)
            except (TypeError, ValueError):
                max_in = 0
            channels = 2 if max_in >= 2 else 1

            def _callback(indata, frames, time_info, status):  # noqa: ANN001, ANN202
                try:
                    self._queue.put_nowait(
                        (bytes(indata), rate, channels, time.monotonic()))
                except queue.Full:
                    self._dropped += 1

            self._stream = sd.InputStream(
                device=index, samplerate=rate, channels=channels,
                dtype="float32", blocksize=2048, callback=_callback)
            self._stream.start()
        except Exception as e:  # noqa: BLE001 — a dead device fails closed
            return False, f"could not open loopback device: {e}"
        self._hello = {"device": name, "index": index,
                       "rate": rate, "channels": channels}
        return True, dict(self._hello)

    def read_raw(self, timeout: float) -> tuple | None:
        """(float32 bytes, rate, channels, monotonic t) or None."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def ended(self) -> bool:
        return self._ended.is_set()

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def end_info(self) -> dict[str, Any]:
        return {}

    def stop(self) -> None:
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
        self._ended.set()


class LoopbackCapture:
    """One live loopback capture session: a chosen device on one
    backend, normalised to 16 kHz mono int16 frames.

    Backend selection: an explicit backend is used as named;
    backend=None tries the subprocess rung first, then the
    sounddevice rung — but only when the subprocess refusal is
    about the RUNG (pyaudiowpatch missing / no loopback device
    enumerated). A device refusal (unknown name/index) is
    returned as-is: the name does not exist on either rung, and
    silently opening a different device would record the wrong
    room. A device INDEX is rung-specific, so backend=None with
    an integer device tries the subprocess rung only and refuses
    with the reason when that rung is unavailable.
    """

    def __init__(self) -> None:
        self._backend = None
        self._backend_name: str | None = None
        self._device: str | None = None
        self._device_info: dict[str, Any] = {}
        self._frames = 0
        self._started_mono: float | None = None
        self._max_seconds = _CAPTURE_MAX_SECONDS
        self._max_frames = 0
        self._end_reason: str | None = None
        self._on_frame = None
        self._pump: threading.Thread | None = None
        self._pump_stop = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=_CAPTURE_QUEUE_MAX)
        self._dropped = 0
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def resolved_device(self) -> str | None:
        """The device actually being captured (the backend's
        resolved name), falling back to the requested string."""
        return (str(self._device_info.get("device"))
                if self._device_info.get("device") else self._device)

    def _pump_loop(self) -> None:
        # The backend queue is drained BEFORE the ended flag is
        # honoured: a worker that finishes fast (or a device that
        # ends mid-chunk) must not lose the frames already queued.
        while not self._pump_stop.is_set():
            if self._backend is None:
                break
            if (self._max_frames and self._frames >= self._max_frames):
                self._end_reason = "frames cap reached"
                break
            if (self._started_mono is not None
                    and time.monotonic() - self._started_mono >= self._max_seconds):
                self._end_reason = "seconds cap reached"
                break
            raw = self._backend.read_raw(timeout=0.1)
            if raw is None:
                if getattr(self._backend, "ended", False):
                    info = self._backend.end_info
                    self._end_reason = str(info.get("reason") or "backend ended")
                    break
                continue
            data, rate, channels, stamp = raw
            try:
                pcm = _to_meeting_pcm(data, channels, rate)
            except Exception:  # noqa: BLE001 — a bad chunk is a lost chunk
                self._dropped += 1
                continue
            if self._max_frames:
                # The cap is exact, not chunk-granular: the final
                # chunk is truncated to the frames still allowed, so
                # the session never records past its cap.
                remaining = self._max_frames - self._frames
                if remaining <= 0:
                    self._end_reason = "frames cap reached"
                    break
                if len(pcm) // 2 > remaining:
                    pcm = pcm[: remaining * 2]
            self._frames += len(pcm) // 2
            try:
                self._queue.put_nowait((pcm, stamp))
            except queue.Full:
                self._dropped += 1
            if self._on_frame is not None:
                try:
                    self._on_frame(pcm)
                except Exception:  # noqa: BLE001 — a consumer failure never sinks the capture
                    pass
        if self._end_reason is None:
            self._end_reason = "stopped"
        self._active = False

    def start(self, device: str = "default", backend: str | None = None,
              max_seconds: float = _CAPTURE_MAX_SECONDS,
              max_frames: int = 0, on_frame=None) -> tuple[bool, dict[str, Any] | str]:
        """Open the capture. Returns (True, info) or (False, reason).

        info: {backend, device, rate, channels, max_seconds,
        max_frames}. A device index (int) with backend=None is
        refused: indices are rung-specific, so an index without
        a named backend cannot fall back honestly.
        """
        if self._active:
            return False, "a loopback capture is already active"
        if backend is None:
            try:
                int(str(device).strip())
                return False, (
                    f"device {device!r} is an index, and indices are "
                    "backend-specific — name the backend "
                    "(pyaudiowpatch-subprocess or "
                    "sounddevice-wasapi-default) or give a device name")
            except ValueError:
                pass
        self._max_seconds = float(max_seconds)
        self._max_frames = int(max_frames)
        self._on_frame = on_frame
        self._end_reason = None
        self._frames = 0
        self._dropped = 0
        backends = ([backend] if backend is not None else
                    ["pyaudiowpatch-subprocess",
                     "sounddevice-wasapi-default"])
        last_reason = ""
        rung_reasons: list[str] = []
        for name in backends:
            impl = (_SubprocessCapture() if name == "pyaudiowpatch-subprocess"
                    else _SoundDeviceLoopbackCapture())
            ok, info = impl.start(str(device), self._max_seconds,
                                  self._max_frames)
            if ok:
                self._backend = impl
                self._backend_name = name
                self._device = str(device)
                self._device_info = dict(info) if isinstance(info, dict) else {}
                self._started_mono = time.monotonic()
                self._active = True
                self._pump_stop.clear()
                self._pump = threading.Thread(target=self._pump_loop, daemon=True)
                self._pump.start()
                return True, {"backend": name,
                              "device": self.resolved_device,
                              "rate": self._device_info.get("rate"),
                              "channels": self._device_info.get("channels"),
                              "max_seconds": self._max_seconds,
                              "max_frames": self._max_frames}
            last_reason = str(info)
            rung_reasons.append(f"{name}: {last_reason}")
            if backend is not None:
                return False, last_reason
            # Fall back only when the refusal is about the rung,
            # never when it is about the device.
            if ("pyaudiowpatch is not available" in last_reason
                    or "no WASAPI loopback device" in last_reason
                    or "sounddevice is not available" in last_reason
                    or "no loopback device enumerated" in last_reason):
                continue
            return False, last_reason
        return False, ("; ".join(rung_reasons)
                       or last_reason or "no loopback backend is available")

    def read(self, timeout: float = 0.5) -> tuple[bytes, float] | None:
        """One converted frame (16 kHz mono int16 bytes, monotonic
        timestamp), or None on timeout / after the capture ended."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> dict[str, Any]:
        """End the capture; returns {frames, reason, backend,
        device, dropped_chunks}."""
        backend, self._backend = self._backend, None
        self._pump_stop.set()
        if backend is not None:
            try:
                backend.stop()
            except Exception:  # noqa: BLE001 — stop is best-effort
                pass
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.join(timeout=2)
        reason = self._end_reason or "stopped"
        dropped = self._dropped + (getattr(backend, "dropped", 0) or 0)
        info = {"frames": self._frames, "reason": reason,
                "backend": self._backend_name,
                "device": self.resolved_device,
                "dropped_chunks": dropped}
        self._active = False
        self._backend_name = None
        self._device = None
        self._device_info = {}
        return info

    def status(self) -> dict[str, Any]:
        """Live state, read-only, no side effect (no spawn)."""
        elapsed = None
        if self._started_mono is not None and self._active:
            elapsed = time.monotonic() - self._started_mono
        return {
            "active": self._active,
            "backend": self._backend_name,
            "device": self.resolved_device,
            "rate": self._device_info.get("rate"),
            "channels": self._device_info.get("channels"),
            "frames": self._frames,
            "elapsed_s": elapsed,
            "end_reason": self._end_reason,
            "dropped_chunks": self._dropped,
        }


_LIVE_CAPTURE: LoopbackCapture | None = None


def start_capture(device: str = "default", backend: str | None = None,
                  max_seconds: float = _CAPTURE_MAX_SECONDS,
                  max_frames: int = 0, on_frame=None) -> tuple[bool, dict[str, Any] | str]:
    """Start the module-level live capture (one at a time)."""
    global _LIVE_CAPTURE  # noqa: PLW0603 — the live session is module state
    if _LIVE_CAPTURE is not None and _LIVE_CAPTURE.active:
        return False, "a loopback capture is already active"
    session = LoopbackCapture()
    ok, info = session.start(device=device, backend=backend,
                             max_seconds=max_seconds, max_frames=max_frames,
                             on_frame=on_frame)
    if not ok:
        return False, info
    _LIVE_CAPTURE = session
    return True, info


def capture_status() -> dict[str, Any]:
    """Live capture state (no spawn, no side effect)."""
    if _LIVE_CAPTURE is None:
        return {"active": False}
    return _LIVE_CAPTURE.status()


def stop_capture() -> dict[str, Any]:
    """Stop the live capture; {} when none is active."""
    global _LIVE_CAPTURE  # noqa: PLW0603 — the live session is module state
    if _LIVE_CAPTURE is None:
        return {}
    session, _LIVE_CAPTURE = _LIVE_CAPTURE, None
    return session.stop()


def get_capture() -> LoopbackCapture | None:
    """The live capture session, or None."""
    return _LIVE_CAPTURE


def reset_capture() -> None:
    """Stop and clear the live capture (suite/prover tearDown)."""
    stop_capture()
