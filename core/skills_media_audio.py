# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: moved verbatim from
# core/skills_media.py (see that facade). Imports travel with the unit
# (plan SS2 P3); cross-part names imported with INTENTIONAL comments.

# INTENTIONAL (media split): shared names live in skills_media_helpers.
from datetime import datetime
from pathlib import Path
from .skills_media_helpers import (
    _DATA_DIR,
    _NOT_WINDOWS,
    _RECORD_RATE,
    _human_size,
    _is_windows,
)


# INTENTIONAL (B-D4r): the B-D4 volume wrapper owns the volume
# logic (session identity, read-back comparison, honest failures);
# this tool only translates its dicts into the prose the voice
# path speaks. The matching rule has ONE definition — the
# wrapper's _name_matches — reused here for the read path so the
# rule never exists twice to drift apart.
from .desktop import volume_control as _volume_control
from .desktop.volume_control import _name_matches as _vc_name_matches

_volume_backend = None  # test seam: fake volume backend, or None


def set_volume_backend(backend) -> None:
    """Test seam: inject a fake volume backend (same protocol as
    volume_control's backend), or None to restore the real pycaw
    path. An injected backend IS the engine under test, so the
    platform check in app_volume is skipped for it — the suite
    and the prover run the tool's logic on the fake while the
    real backend is honestly unavailable on this machine."""
    global _volume_backend
    _volume_backend = backend


def record_audio(seconds: int, filename: str = "") -> str:
    """Record the microphone for `seconds` seconds into data/recordings/.

    BLOCKING by design: the call returns only when the recording is
    finished (duration is hard-capped at 120 seconds).
    """
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "Recording needs a duration in whole seconds (1–120)."
    seconds = max(1, min(120, seconds))
    # v5.48 (programme item 21): the capture guard runs before
    # any audio stack is touched — the sensitive-window denylist
    # refuses FIRST, then the per-session microphone opt-in
    # (capture_policy) is checked. The legacy contract this
    # changes: record_audio used to start the microphone on a
    # model call alone, with no human ask.
    try:
        from . import capture_guard

        _allowed, _why = capture_guard.ensure_capture_allowed("microphone")
    except Exception as e:  # noqa: BLE001 — a broken guard refuses
        return f"Recording refused: the capture guard could not run ({e})."
    if not _allowed:
        return f"Recording refused: {_why}"
    try:
        import sounddevice as sd
    except ImportError:
        return ("Recording isn't available in this build of VYUK — the "
                "audio part didn't make it into the package.")
    except Exception as e:  # noqa: BLE001
        return f"Recording isn't available here — the audio stack failed to load: {e}"
    # Fail fast (and honestly) when no input device exists, instead of
    # blocking on a recording that can never start.
    try:
        inputs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
    except Exception as e:  # noqa: BLE001
        return f"No microphone could be reached: {e}"
    if not inputs:
        return ("No microphone was found on this PC, so nothing was recorded. "
                "Plug one in or check Windows sound settings.")
    try:
        import numpy  # noqa: F401 — sounddevice returns numpy frames
    except ImportError:
        return ("Recording isn't available in this build of VYUK — the "
                "audio part didn't make it into the package.")
    name = (filename or "").strip()
    if name:
        name = Path(name).name  # never let a filename escape the folder
        if not name:
            name = ""
    if not name:
        name = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
    if not name.lower().endswith(".wav"):
        name += ".wav"
    out_dir = _DATA_DIR / "recordings"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / name
    # v5.48 (programme item 21): the capture indicator brackets
    # the live recording — started before the microphone opens,
    # ended in a finally so a failed recording still clears it.
    capture_guard.note_capture_started("microphone")
    try:
        import numpy as np
        import sounddevice as sd

        frames = sd.rec(int(seconds * _RECORD_RATE), samplerate=_RECORD_RATE,
                        channels=1, dtype="int16")
        sd.wait()
    except Exception as e:  # noqa: BLE001
        return f"Recording failed: {e}"
    finally:
        capture_guard.note_capture_ended("microphone")
    try:
        import wave

        with wave.open(str(dest), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # int16
            wav.setframerate(_RECORD_RATE)
            wav.writeframes(np.asarray(frames, dtype="int16").tobytes())
    except Exception as e:  # noqa: BLE001
        return f"The audio was recorded but couldn't be saved: {e}"
    return (f"Recorded {seconds} second(s) from the microphone: {dest} "
            f"({_human_size(dest.stat().st_size)}).")


def list_audio_devices() -> str:
    """List the PC's audio input/output devices, marking the defaults."""
    try:
        import sounddevice as sd
    except ImportError:
        return ("Listing audio devices isn't available in this build of "
                "VYUK — the audio part didn't make it into the package.")
    except Exception as e:  # noqa: BLE001
        return f"Audio devices couldn't be read — the audio stack failed to load: {e}"
    try:
        devices = sd.query_devices()
        try:
            default_in, default_out = sd.default.device
        except Exception:  # noqa: BLE001 — defaults may be unset on odd systems
            default_in, default_out = None, None
    except Exception as e:  # noqa: BLE001
        return f"Audio devices couldn't be enumerated: {e}"
    if not devices:
        return "No audio devices were found on this PC."
    lines = []
    for index, dev in enumerate(devices):
        tags = []
        if index == default_in:
            tags.append("default input")
        if index == default_out:
            tags.append("default output")
        suffix = f" ({', '.join(tags)})" if tags else ""
        lines.append(
            f"• [{index}] {dev['name']} — in {dev['max_input_channels']}ch, "
            f"out {dev['max_output_channels']}ch{suffix}")
    return f"Audio devices ({len(devices)}):\n" + "\n".join(lines)


def _microphone_volume():
    """Return (endpoint_volume, error_string) — pycaw capture endpoint."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    except ImportError:
        return None, ("Microphone control isn't available in this build of VYUK "
                      "— the microphone part didn't make it into the package.")
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL

        device = AudioUtilities.GetMicrophone()
        if device is None:
            return None, "No microphone endpoint was found on this PC."
        interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume)), None
    except Exception as e:  # noqa: BLE001
        return None, f"Could not reach the microphone endpoint: {e}"


def set_microphone_mute(state: str = "toggle") -> str:
    """Mute / unmute / toggle the microphone itself (not the speakers)."""
    wanted = (state or "toggle").strip().lower()
    if wanted not in ("toggle", "on", "mute", "off", "unmute"):
        return (f"Unknown microphone state: '{state}'. "
                "Use 'toggle', 'on'/'mute' or 'off'/'unmute'.")
    if not _is_windows():
        return f"Microphone mute uses Windows audio endpoints — {_NOT_WINDOWS}"
    volume, err = _microphone_volume()
    if err:
        return err
    try:
        currently_muted = bool(volume.GetMute())
        target = (not currently_muted) if wanted == "toggle" else wanted in ("on", "mute")
        volume.SetMute(1 if target else 0, None)
        confirmed = bool(volume.GetMute())
    except Exception as e:  # noqa: BLE001
        return f"Could not change the microphone mute: {e}"
    if confirmed != target:
        return "Windows didn't keep the microphone mute change — it may be managed by another app."
    return "Microphone is now MUTED." if confirmed else "Microphone is now live (unmuted)."


def _audio_sessions():
    """Return (sessions, error_string) — pycaw sessions with a volume."""
    try:
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        return None, ("Per-app volume isn't available in this build of VYUK — "
                       "the volume part didn't make it into the package.")
    try:
        return AudioUtilities.GetAllSessions(), None
    except Exception as e:  # noqa: BLE001
        return None, f"Could not read the running audio sessions: {e}"


def _session_label(session) -> str:
    process = getattr(session, "Process", None)
    if process is None:
        return "System sounds"
    try:
        return process.name()
    except Exception:  # noqa: BLE001
        return "Unknown app"


def app_volume(app_name: str = "", level: int | None = None, mute: bool | None = None) -> str:
    """Read or set ONE app's volume (not the master volume).

    With no app name (or '*'), lists the apps currently able to make
    sound and their levels. Otherwise the name is a search hint that
    must resolve to exactly ONE session (B-D4 wrapper): an ambiguous
    name refuses, listing the candidates — it never changes every
    match, and never silently picks the first. A set is read back
    and compared; a mismatch is reported as a failure naming the
    actual level, never as "Per-app volume set".

    LOPHOLE FAMILY (B-D4) — "a volume command that changes the
    wrong sound": a name matching two apps changes the wrong one
    (or both); a session dying between list and set reports
    success; a set the device refuses reports success; an
    out-of-range value passed through or silently clamped; a
    silent no-match. ONE design closes the class — the B-D4
    wrapper (core/desktop/volume_control.py): session identity is
    (pid + session index), every set is read back and compared,
    master and app never share a code path, out-of-range is
    refused, no-match/dead/ambiguous are honest failures. This
    tool only translates the wrapper's dicts into the prose the
    voice path speaks; it holds no matching or setting logic of
    its own.

    Rejected alternatives: keeping the old substring-multi-match
    loop (the loophole itself — it changed EVERY match); turning
    the wrapper's failure dicts back into success prose (a lie in
    a new dress); changing the tool's schema or handler module
    path (breaks the registry allow-list).
    """
    backend = _volume_backend
    if backend is None and not _is_windows():
        return f"Per-app volume uses Windows audio sessions — {_NOT_WINDOWS}"
    needle = (app_name or "").strip()
    list_mode = needle.lower() in ("", "*")
    if list_mode:
        if level is not None or mute is not None:
            return ("Name one app to change its volume (or use set_volume_level "
                    "for the master volume).")
        listed = _volume_control.list_sessions(backend=backend)
        if not listed["ok"]:
            return listed["reason"]
        sessions = listed["sessions"]
        if not sessions:
            return "No apps are making (or ready to make) sound right now."
        lines = []
        for s in sessions:
            state = "muted" if s["muted"] else f"{s['volume']}%"
            lines.append(f"• {s['name']}: {state}")
        return "Per-app volume right now:\n" + "\n".join(lines)
    if level is None and mute is None:
        # Read mode: resolve the name through the listing — the
        # same rule as the wrapper's set path, so an ambiguous
        # name refuses here too instead of silently reading the
        # first match.
        listed = _volume_control.list_sessions(backend=backend)
        if not listed["ok"]:
            return listed["reason"]
        sessions = listed["sessions"]
        if not sessions:
            return "No apps are making (or ready to make) sound right now."
        matches = [s for s in sessions
                   if _vc_name_matches(needle, s["name"])]
        if not matches:
            running = ", ".join(sorted({s["name"] for s in sessions}))
            return (f"No app matching '{app_name}' is running with sound. "
                    f"Apps with audio right now: {running}.")
        if len(matches) > 1:
            lines = ", ".join(
                f"{s['name']} (pid {s['pid']}, {s['volume']}%, "
                f"{'muted' if s['muted'] else 'live'})" for s in matches)
            return (f"'{app_name}' matches {len(matches)} apps with sound — "
                    f"say which one by process id: {lines}.")
        s = matches[0]
        state = "muted" if s["muted"] else f"{s['volume']}%"
        return f"{s['name']} is at {state}."
    # Set mode: coerce the level the way the old tool did (a "40"
    # string becomes 40), then let the wrapper own the range
    # refusal and the read-back comparison.
    if level is not None:
        try:
            level = int(level)
        except (TypeError, ValueError):
            return "Volume level must be a whole number between 0 and 100."
    result = _volume_control.set_app_volume(
        name=needle, level=level, mute=mute, backend=backend)
    if result["ok"]:
        bits = []
        if level is not None:
            bits.append(f"volume {result['volume']}%")
        if mute is not None:
            bits.append("muted" if result["muted"] else "unmuted")
        return (f"Per-app volume set — {result['name']}: "
                + ", ".join(bits) + ".")
    return result["reason"]


# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: handlers keep their public
# module path (core.skills_media) so the override allow-list in
# core/funnel/tools_registry.py — which matches on handler.__module__ —
# keeps working with NO change to that file. The facade is the module's
# public identity; the impl modules are its private home.
record_audio.__module__ = "core.skills_media"
list_audio_devices.__module__ = "core.skills_media"
set_microphone_mute.__module__ = "core.skills_media"
app_volume.__module__ = "core.skills_media"
