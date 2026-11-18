"""Subprocess worker for EA-13 WASAPI loopback (v5.58, Wave 3).

This is the ONLY module in the tree allowed to import
``pyaudiowpatch`` — and it does so only inside this worker
process, never in the app process. Rationale (task EA-13):
PyAudioWPatch cannot co-reside with sounddevice's bundled
PortAudio in one process, so loopback capture is either
subprocess-isolated (this worker) or sole-owner (a process
that never imports sounddevice). This worker proves the first
shape: it is spawned fresh by ``core.loopback``, and its probe
report includes ``sounddevice_in_process`` — which must be
False even when the parent process has sounddevice imported,
because a subprocess does not inherit the parent's modules.

Run: ``python -m core.loopback_worker probe`` or
``python -m core.loopback_worker capture --device <name|index|default>
[--seconds N] [--max-frames N]`` from the package root
(PYTHONPATH must include the package root). Output is JSON on
stdout, nothing else on stdout. ``probe`` never opens a device
and never records audio: it only reports whether the import
succeeds and whether WASAPI loopback devices are enumerable.

``capture`` (GAPS-20, D-10) opens the chosen WASAPI loopback
device and streams NDJSON lines on stdout: one ``hello`` line
(device, rate, channels), one ``frame`` line per chunk
(base64 float32 PCM, rate, channels, frame count, monotonic
timestamp), then one ``end`` line (reason, frames, dropped
chunks). A refusal (no pyaudiowpatch, no such device) is a
single ``error`` line, never a traceback on stdout. The
stream is bounded: --seconds (default 14400, the meeting
4-hour cap) and --max-frames (default 0 = no frame cap) stop
it with the reason in the end line — a runaway worker stops
itself. The parent (core.loopback) converts the float32 stereo
frames to the meeting's 16 kHz mono int16 shape.

PyAudioWPatch is NOT a declared dependency (Windows-only
wheel, known-optional); its licence (Apache-2.0) is recorded
in THIRD_PARTY_LICENSES.md (read from the 0.2.12.9 wheel
dist-info, 2026-10-25 CC-1 gate). Do not add it to
requirements.txt on the basis of this file.
"""

from __future__ import annotations

import base64
import json
import queue
import sys
import time

_CAPTURE_DEFAULT_SECONDS = 14400.0  # the meeting 4-hour cap
_QUEUE_MAX = 512  # chunks; a slow parent drops new chunks, counted


def probe() -> dict:
    """Report PyAudioWPatch presence + process isolation state."""
    report: dict = {
        "pyaudiowpatch_available": False,
        "pyaudiowpatch_error": None,
        "sounddevice_in_process": "sounddevice" in sys.modules,
        "platform": sys.platform,
        "wasapi_loopback": False,
        "devices": [],
    }
    try:
        import pyaudiowpatch as pyaudio  # noqa: F401
    except Exception as e:  # noqa: BLE001
        report["pyaudiowpatch_error"] = f"{type(e).__name__}: {e}"
        return report
    report["pyaudiowpatch_available"] = True
    report["sounddevice_in_process"] = "sounddevice" in sys.modules
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            for i in range(pa.get_device_count()):
                try:
                    info = pa.get_device_info_by_index(i)
                except Exception:  # noqa: BLE001
                    continue
                if info.get("isLoopbackDevice"):
                    report["devices"].append(info.get("name"))
            report["wasapi_loopback"] = bool(report["devices"])
        finally:
            try:
                pa.terminate()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        report["pyaudiowpatch_error"] = f"enumerate failed: {e}"
    return report


def _loopback_devices(pa) -> list[dict]:
    """All WASAPI loopback devices, as PyAudio info dicts."""
    out = []
    for i in range(pa.get_device_count()):
        try:
            info = pa.get_device_info_by_index(i)
        except Exception:  # noqa: BLE001
            continue
        if info.get("isLoopbackDevice"):
            out.append(dict(info))
    return out


def _resolve_loopback_device(pa, device: str) -> dict:
    """Resolve --device to a loopback device-info dict.

    "default" = the loopback device for the default output
    device (name match), else the first loopback device; an
    integer = the PyAudio device index (which must be a loopback
    device); anything else = an exact device name
    (case-insensitive). Raises ValueError naming the candidates
    when nothing matches — the caller reports it as an error
    line, never by opening the wrong device.
    """
    devices = _loopback_devices(pa)
    if not devices:
        raise ValueError("no WASAPI loopback device is available")
    want = str(device or "default").strip()
    if want.lower() == "default":
        try:
            default = pa.get_default_output_device_info()
            default_name = str(default.get("name") or "")
        except Exception:  # noqa: BLE001 — no default, take the first
            default_name = ""
        for info in devices:
            if default_name and str(info.get("name") or "") == default_name:
                return info
        return devices[0]
    try:
        index = int(want)
    except ValueError:
        index = None
    if index is not None:
        for info in devices:
            if int(info.get("index", -1)) == index:
                return info
        raise ValueError(
            f"no loopback device at index {index} "
            f"(loopback devices: {', '.join(str(d.get('index')) for d in devices)})")
    lowered = want.lower()
    for info in devices:
        if str(info.get("name") or "").lower() == lowered:
            return info
    raise ValueError(
        f"no loopback device named {want!r} "
        f"(loopback devices: {', '.join(str(d.get('name')) for d in devices)})")


def _emit(out, obj: dict) -> None:
    out.write(json.dumps(obj) + "\n")
    out.flush()


def capture(device: str = "default", seconds: float = _CAPTURE_DEFAULT_SECONDS,
            max_frames: int = 0, out=None) -> int:
    """Open a WASAPI loopback stream; emit NDJSON on `out`.

    Returns 0 on a clean end (cap reached or interrupted), 1 on
    a refusal (no pyaudiowpatch / no such device / stream open
    failed). `out` defaults to sys.stdout; tests inject a
    StringIO and a fake pyaudiowpatch module. Nothing but JSON
    lines goes to `out` — diagnostics go to stderr.
    """
    out = sys.stdout if out is None else out
    try:
        import pyaudiowpatch as pyaudio
    except Exception as e:  # noqa: BLE001
        _emit(out, {"type": "error",
                    "reason": f"pyaudiowpatch is not available: {e}"})
        return 1
    try:
        pa = pyaudio.PyAudio()
    except Exception as e:  # noqa: BLE001
        _emit(out, {"type": "error",
                    "reason": f"could not start PyAudio: {e}"})
        return 1
    try:
        try:
            info = _resolve_loopback_device(pa, device)
        except ValueError as e:
            _emit(out, {"type": "error", "reason": str(e)})
            return 1
        index = int(info.get("index", 0))
        name = str(info.get("name") or "")
        try:
            rate = int(info.get("defaultSampleRate") or 48000)
        except (TypeError, ValueError):
            rate = 48000
        try:
            max_in = int(info.get("maxInputChannels") or 0)
        except (TypeError, ValueError):
            max_in = 0
        channels = 2 if max_in >= 2 else 1
        chunks: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        dropped = {"n": 0}

        def _callback(in_data, frame_count, time_info, status):  # noqa: ANN001, ANN202
            try:
                chunks.put_nowait((bytes(in_data), int(frame_count),
                                   time.monotonic()))
            except queue.Full:
                dropped["n"] += 1
            return (None, pyaudio.paContinue)

        try:
            stream = pa.open(
                format=pyaudio.paFloat32, channels=channels,
                rate=rate, input=True, input_device_index=index,
                frames_per_buffer=2048, stream_callback=_callback)
        except Exception as e:  # noqa: BLE001
            _emit(out, {"type": "error",
                        "reason": f"could not open loopback device "
                                  f"{name!r}: {e}"})
            return 1
        _emit(out, {"type": "hello", "device": name, "index": index,
                    "rate": rate, "channels": channels})
        deadline = time.monotonic() + float(seconds)
        frames = 0
        reason = "seconds cap reached"
        try:
            stream.start_stream()
            while True:
                if max_frames and frames >= int(max_frames):
                    reason = "frames cap reached"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "seconds cap reached"
                    break
                try:
                    data, count, stamp = chunks.get(
                        timeout=min(0.2, max(0.01, remaining)))
                except queue.Empty:
                    continue
                _emit(out, {"type": "frame",
                            "pcm_b64": base64.b64encode(data).decode("ascii"),
                            "rate": rate, "channels": channels,
                            "frames": count, "t": stamp})
                frames += count
        except KeyboardInterrupt:
            reason = "interrupted"
        finally:
            try:
                stream.stop_stream()
            except Exception:  # noqa: BLE001 — stop is best-effort
                pass
            try:
                stream.close()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass
        _emit(out, {"type": "end", "reason": reason, "frames": frames,
                    "dropped_chunks": dropped["n"]})
        return 0
    finally:
        try:
            pa.terminate()
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str]) -> int:
    if not argv:
        print(json.dumps({"error": "usage: python -m core.loopback_worker probe | capture [--device X] [--seconds N] [--max-frames N]"}))
        return 2
    if argv[0] == "probe":
        print(json.dumps(probe()))
        return 0
    if argv[0] != "capture":
        print(json.dumps({"error": "usage: python -m core.loopback_worker probe | capture [--device X] [--seconds N] [--max-frames N]"}))
        return 2
    device = "default"
    seconds = _CAPTURE_DEFAULT_SECONDS
    max_frames = 0
    i = 1
    while i < len(argv):
        arg = argv[i]
        try:
            if arg == "--device" and i + 1 < len(argv):
                device = argv[i + 1]
                i += 2
            elif arg == "--seconds" and i + 1 < len(argv):
                seconds = float(argv[i + 1])
                i += 2
            elif arg == "--max-frames" and i + 1 < len(argv):
                max_frames = int(argv[i + 1])
                i += 2
            else:
                print(json.dumps({"error": f"unknown capture argument: {arg}"}))
                return 2
        except (TypeError, ValueError):
            print(json.dumps({"error": f"bad value for {arg}"}))
            return 2
    return capture(device=device, seconds=seconds, max_frames=max_frames)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
