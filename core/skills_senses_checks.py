"""
SENSES-2 skills for VYUK — the check-up pack for her ears,
her voice and her camera eye: test the microphone, test the
speakers, measure the room's noise, record a voice memo, read a
selection aloud, summarise the camera's view, match the screen
brightness to the room, and keep a basic ear out for sudden
sounds.

Every device touch sits behind a small module-level helper
(_record_seconds, _play_samples, _capture_camera, _speak,
_grab_selection, _set_screen_brightness, _transcribe) so tests
can monkeypatch the hardware away. The handlers themselves only
measure, decide and narrate — in the plain style of the sibling
packs: human-readable strings, failures stated honestly, caps
disclosed. Handlers never raise — an exception becomes an honest
"I couldn't…" line, and on a machine without the device every
tool degrades to words, never a crash.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import math
import re
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

from . import senses
from .config import config

_RATE_IN = int(getattr(config, "sample_rate_in", 16000) or 16000)
_RATE_OUT = int(getattr(config, "sample_rate_out", 24000) or 24000)

_MIC_CHECK_SECONDS = 3.0
_NOISE_SECONDS = 5.0
_NOTE_MIN_SECONDS = 3
_NOTE_MAX_SECONDS = 120


class _DeviceUnavailable(Exception):
    """A device or library couldn't be reached; the message is
    already phrased for the user."""


# ---------------------------------------------------------------------------
# Device helpers — the only places hardware is touched
# ---------------------------------------------------------------------------


def _record_seconds(seconds: float, rate: int = _RATE_IN):
    """Record mono int16 audio for `seconds` seconds.

    Returns (samples, rate) with samples a 1-D numpy int16 array.
    Raises _DeviceUnavailable with a user-ready reason."""
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001 — missing lib counts too
        raise _DeviceUnavailable(
            f"the audio library isn't available ({e})") from e
    frames = max(1, int(seconds * rate))
    try:
        data = sd.rec(frames, samplerate=rate, channels=1,
                      dtype="int16")
        sd.wait()
    except Exception as e:  # noqa: BLE001
        raise _DeviceUnavailable(
            f"no microphone could be opened ({e})") from e
    return np.asarray(data).reshape(-1), rate


def _play_samples(samples, rate: int = _RATE_OUT) -> None:
    """Play float32 mono samples through the default output.
    Raises _DeviceUnavailable with a user-ready reason."""
    try:
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001
        raise _DeviceUnavailable(
            f"the audio library isn't available ({e})") from e
    try:
        sd.play(samples, rate)
        sd.wait()
    except Exception as e:  # noqa: BLE001
        raise _DeviceUnavailable(
            f"no speaker could be opened ({e})") from e


def _default_device_name(kind: str) -> str | None:
    """Name of the OS-default input/output device, or None."""
    try:
        import sounddevice as sd

        dev = sd.query_devices(kind=kind)
        name = dev.get("name") if isinstance(dev, dict) else None
        return str(name) if name else None
    except Exception:  # noqa: BLE001 — display only, never control
        return None


def _levels(samples) -> tuple[float, float]:
    """(peak, rms) of int16 samples, as 0..1 fractions."""
    import numpy as np

    if samples is None or len(samples) == 0:
        return 0.0, 0.0
    floats = samples.astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(floats)))
    rms = float(np.sqrt(np.mean(floats ** 2)))
    return peak, rms


def _write_wav(path: Path, samples, rate: int) -> None:
    """Write mono int16 samples as a plain WAV file."""
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def _transcribe(samples, rate: int) -> str | None:
    """Best-effort transcript of int16 samples via the product's
    Whisper seam. Returns None when transcription isn't available.

    Never triggers a download: it only runs when
    local_assets.whisper_ready() says the weights are already on
    this PC (bundled or cached)."""
    try:
        from .voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/

        if not local_assets.whisper_ready():
            return None
        from faster_whisper import WhisperModel

        bundled = local_assets.bundled_whisper_path()
        model_arg = (str(bundled) if bundled is not None
                     else config.whisper_model)
        model = WhisperModel(model_arg, device="cpu",
                             compute_type="int8")
        audio = samples.astype("float32") / 32768.0
        segments, _info = model.transcribe(audio)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text or None
    except Exception:  # noqa: BLE001 — best effort by design
        return None


def _capture_camera():
    """One webcam frame as (image_bytes, mime), or None."""
    from .desktop.vision import vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/

    return vision_manager.capture_webcam()


def _speak(text: str) -> tuple[bool, str]:
    """Speak via the product's system-voice seam (skills_plus.
    speak_text). Returns (spoken?, note) — the note is the seam's
    own honest line when it couldn't speak."""
    try:
        from .skills_plus import speak_text

        # Fix 7: speak_text returns an Outcome; the (spoken?, note)
        # contract here is string-based, so unwrap explicitly.
        result = str(speak_text(text))
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    if result.startswith("Reading aloud now"):
        return True, result
    return False, result


def _grab_selection() -> str:
    """The text currently selected anywhere on screen.

    Plants a sentinel on the clipboard, sends Ctrl+C through the
    product's hotkey seam, and reads what landed; the clipboard's
    previous contents are restored afterwards either way. Returns
    "" when nothing was selected. Raises _DeviceUnavailable."""
    try:
        import pyperclip
    except Exception as e:  # noqa: BLE001
        raise _DeviceUnavailable(
            f"the clipboard library isn't available ({e})") from e
    try:
        previous = pyperclip.paste()
    except Exception:  # noqa: BLE001 — clipboard may hold non-text
        previous = None
    sentinel = "\u200b__visor_selection_probe__"
    try:
        try:
            pyperclip.copy(sentinel)
        except Exception as e:  # noqa: BLE001
            raise _DeviceUnavailable(
                f"the clipboard couldn't be written ({e})") from e
        from .desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        control.press_hotkey("ctrl+c")
        time.sleep(0.3)
        try:
            grabbed = pyperclip.paste()
        except Exception as e:  # noqa: BLE001
            raise _DeviceUnavailable(
                f"the clipboard couldn't be read ({e})") from e
        if not grabbed or grabbed == sentinel:
            return ""
        return grabbed
    finally:
        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception:  # noqa: BLE001 — restore is best effort
                pass


def _set_screen_brightness(level: int) -> str:
    """Set the screen brightness through the product's verified
    seam (control.set_brightness reads the level back before it
    claims anything). Returns the seam's own result line."""
    from .desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

    return control.set_brightness(int(level))


def _frame_summary(image_bytes: bytes) -> dict:
    """Basic on-device facts about a camera frame: dimensions,
    light (via senses.frame_brightness) and dominant colour
    family. Deliberately basic — this is a summary, not a
    description, and the handlers say so."""
    from PIL import Image
    import io

    with Image.open(io.BytesIO(image_bytes)) as img:
        width, height = img.size
        small = img.convert("RGB")
        small.thumbnail((32, 32))
        pixels = list(small.getdata())
    if pixels:
        n = len(pixels)
        r = sum(p[0] for p in pixels) / n
        g = sum(p[1] for p in pixels) / n
        b = sum(p[2] for p in pixels) / n
        spread = max(r, g, b) - min(r, g, b)
        if spread < 25:
            colour = "mostly grey, muted tones"
        elif r >= g and r >= b:
            colour = "mostly warm, reddish tones"
        elif g >= r and g >= b:
            colour = "mostly greenish tones"
        else:
            colour = "mostly cool, bluish tones"
    else:
        colour = "no colour information"
    light = senses.frame_brightness(image_bytes)
    return {"width": width, "height": height, "colour": colour,
            "brightness": light["brightness"], "label": light["label"],
            "note": light["note"]}


# ---------------------------------------------------------------------------
# 1. mic_check
# ---------------------------------------------------------------------------


def mic_check() -> str:
    """Three seconds of listening, judged in words."""
    try:
        samples, _rate = _record_seconds(_MIC_CHECK_SECONDS)
        peak, rms = _levels(samples)
        name = _default_device_name("input")
        where = f" (microphone: {name})" if name else ""
        levels = (f"Peak level {round(peak * 100)}%, average "
                  f"{round(rms * 100)}% over "
                  f"{int(_MIC_CHECK_SECONDS)} seconds{where}.")
        if peak < 0.01:
            return ("Mic check: silent — I heard nothing at all. "
                    "Check the microphone is plugged in, not muted, "
                    f"and set as the default. {levels}")
        if peak >= 0.97:
            return ("Mic check: clipping — the loudest moments hit "
                    "the top and distort. Move back from the mic or "
                    f"lower its level in the sound settings. {levels}")
        if rms < 0.01:
            return ("Mic check: quiet — sound is reaching the "
                    "microphone, but very softly. Move closer or "
                    f"raise the mic level. {levels}")
        return ("Mic check: clear — a healthy level, neither "
                f"whispering nor distorting. {levels}")
    except _DeviceUnavailable as e:
        return f"I couldn't test the microphone: {e}."
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return f"I couldn't test the microphone: {e}"


# ---------------------------------------------------------------------------
# 2. speaker_check
# ---------------------------------------------------------------------------


def speaker_check() -> str:
    """Play a three-note tune and report where it went."""
    try:
        import numpy as np

        notes = (523.25, 659.25, 783.99)  # C5, E5, G5
        chunks = []
        t_gap = np.zeros(int(_RATE_OUT * 0.05), dtype=np.float32)
        for freq in notes:
            t = np.arange(int(_RATE_OUT * 0.18)) / _RATE_OUT
            tone = (0.35 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
            # Short fade at both ends so the notes don't click.
            fade = max(1, int(_RATE_OUT * 0.01))
            tone[:fade] *= np.linspace(0, 1, fade)
            tone[-fade:] *= np.linspace(1, 0, fade)
            chunks.extend([tone, t_gap])
        _play_samples(np.concatenate(chunks), _RATE_OUT)
        name = _default_device_name("output")
        where = f" through '{name}'" if name else " through the default speakers"
        return (f"Speaker check: I played a short three-note tune "
                f"(C–E–G){where}, and it played without an error. "
                "Only you can confirm you actually heard it — if "
                "it was silent, check the volume and that the right "
                "speakers are plugged in and selected.")
    except _DeviceUnavailable as e:
        return f"I couldn't run the speaker check: {e}."
    except Exception as e:  # noqa: BLE001
        return f"I couldn't run the speaker check: {e}"


# ---------------------------------------------------------------------------
# 3. room_noise_level
# ---------------------------------------------------------------------------


def room_noise_level() -> str:
    """Five seconds of the room, averaged and judged in words."""
    try:
        samples, _rate = _record_seconds(_NOISE_SECONDS)
        _peak, rms = _levels(samples)
        if rms <= 0:
            return ("Room noise: I heard nothing at all — either "
                    "the room is completely silent or the "
                    "microphone isn't picking anything up.")
        db = 20 * math.log10(rms)
        if db < -45:
            word = "a quiet room — barely any background noise"
        elif db < -30:
            word = "a calm office — soft background noise"
        elif db < -18:
            word = "a busy office — steady background noise"
        else:
            word = "loud — strong background noise"
        return (f"Room noise: {word}. The average level over "
                f"{int(_NOISE_SECONDS)} seconds measured "
                f"{round(db)} dB (about {round(rms * 100)}% of full "
                "volume). Nothing was recorded or kept — this was "
                "a measurement only.")
    except _DeviceUnavailable as e:
        return f"I couldn't measure the room noise: {e}."
    except Exception as e:  # noqa: BLE001
        return f"I couldn't measure the room noise: {e}"


# ---------------------------------------------------------------------------
# 4. voice_note
# ---------------------------------------------------------------------------


def voice_note(seconds: int = 10, title: str = "") -> str:
    """Record a memo to ~/Music/Nova, with a transcript beside it
    when the ears are set up on this PC."""
    try:
        try:
            secs = int(float(seconds))
        except (TypeError, ValueError):
            return ("A voice note needs a length in seconds, "
                    f"between {_NOTE_MIN_SECONDS} and "
                    f"{_NOTE_MAX_SECONDS}.")
        secs = max(_NOTE_MIN_SECONDS, min(_NOTE_MAX_SECONDS, secs))
        samples, rate = _record_seconds(float(secs))
        folder = Path.home() / "Music" / "Nova"
        folder.mkdir(parents=True, exist_ok=True)
        label = (title or "").strip()
        slug = re.sub(r"[^a-z0-9]+", "-",
                      label.lower()).strip("-")[:40] or "voice-note"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        wav_path = folder / f"{slug}-{stamp}.wav"
        _write_wav(wav_path, samples, rate)
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            return ("I recorded the note but couldn't save it — "
                    "the file didn't land on disk.")
        transcript = _transcribe(samples, rate)
        txt_path = wav_path.with_suffix(".txt")
        heading = label or "Voice note"
        when = datetime.now().strftime("%d %b %Y, %H:%M")
        if transcript:
            txt_path.write_text(f"{heading} — {when}\n\n{transcript}\n",
                                encoding="utf-8")
            preview = transcript[:120] + ("…" if len(transcript) > 120 else "")
            return (f"Voice note saved: {wav_path} ({secs} seconds"
                    f"{f', titled “{label}”' if label else ''}). "
                    "A transcript is saved beside it in "
                    f"{txt_path.name}. It starts: “{preview}”")
        txt_path.write_text(
            f"{heading} — {when}\n\n(Transcription wasn't available "
            "on this PC — the recording itself is saved as audio "
            "next to this file.)\n",
            encoding="utf-8")
        return (f"Voice note saved: {wav_path} ({secs} seconds"
                f"{f', titled “{label}”' if label else ''}). "
                "I couldn't transcribe it — speech-to-text isn't "
                "set up on this PC yet — so the note beside it "
                "says that honestly; the audio itself is complete.")
    except _DeviceUnavailable as e:
        return f"I couldn't record the voice note: {e}."
    except Exception as e:  # noqa: BLE001
        return f"I couldn't record the voice note: {e}"


# ---------------------------------------------------------------------------
# 5. read_selection_aloud
# ---------------------------------------------------------------------------


def read_selection_aloud() -> str:
    """Speak whatever text is selected anywhere on screen."""
    try:
        text = _grab_selection()
        if not text.strip():
            return ("Nothing seems to be selected — highlight "
                    "some text in any app and ask again. Your "
                    "clipboard was left exactly as it was.")
        spoken, note = _speak(text)
        preview = text.strip().replace("\n", " ")[:80]
        if spoken:
            return (f"Reading the selected text aloud now "
                    f"({len(text)} character(s)): “{preview}…”")
        shown = text if len(text) <= 2000 else text[:2000] + "…"
        return (f"I couldn't speak it ({note}) — here's the "
                f"selected text instead:\n{shown}")
    except _DeviceUnavailable as e:
        return f"I couldn't read the selection: {e}."
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read the selection: {e}"


# ---------------------------------------------------------------------------
# 6. describe_camera_view
# ---------------------------------------------------------------------------


def describe_camera_view() -> str:
    """One webcam frame, summarised on-device — basic facts,
    honestly labelled as basic."""
    try:
        frame = _capture_camera()
        if not frame:
            return ("No camera available — I couldn't open a "
                    "webcam to look. (The camera may be missing, "
                    "busy, or the vision extras aren't installed.)")
        image_bytes, _mime = frame
        summary = _frame_summary(image_bytes)
        return (f"Camera view: a {summary['width']}×"
                f"{summary['height']} frame, {summary['label']} "
                f"light (average brightness "
                f"{round(summary['brightness'] * 100)}%), "
                f"{summary['colour']}. That's a basic on-device "
                "summary — light, size and colour only. For a "
                "detailed description, ask me to look at the photo "
                "in conversation. The photo wasn't kept.")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't look through the camera: {e}"


# ---------------------------------------------------------------------------
# 7. screen_brightness_auto
# ---------------------------------------------------------------------------


def screen_brightness_auto() -> str:
    """Match the screen to the room's light, via one camera
    frame and the verified brightness seam."""
    try:
        frame = _capture_camera()
        if not frame:
            return ("I couldn't measure the room's light — no "
                    "camera is available to take a frame, so the "
                    "screen brightness was left unchanged.")
        image_bytes, _mime = frame
        light = senses.frame_brightness(image_bytes)
        # A dark room wants a gentler screen; a bright room needs
        # a stronger one to stay readable.
        target = max(10, min(100, round(25 + light["brightness"] * 70)))
        result = _set_screen_brightness(target)
        room = (f"The room reads {light['label']} — average "
                f"brightness {round(light['brightness'] * 100)}% "
                "from one camera frame.")
        if result.startswith("Brightness set to"):
            return (f"{room} I set the screen to {target}% to "
                    f"match. ({result})")
        return (f"{room} The matching screen level would be "
                f"about {target}%, but I couldn't set it: {result}")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't match the brightness to the room: {e}"


# ---------------------------------------------------------------------------
# 8. listen_for_sound — a basic sudden-sound watcher
# ---------------------------------------------------------------------------

_LISTEN_LOCK = threading.Lock()
_LISTEN: dict = {
    "stream": None, "started_at": None, "events": 0,
    "last_event_at": None, "last_peak": 0.0, "baseline": None,
    "_cooldown_until": 0.0,
}


def _listen_callback(indata, frames, time_info, status) -> None:
    """Energy/onset detection only: a block counts as a sudden
    sound when its loudness jumps far above the rolling baseline.
    This cannot tell a doorbell from a dropped pan — and the
    tool's wording never pretends otherwise. Never raises."""
    try:
        import numpy as np

        if indata is None or len(indata) == 0:
            return
        block = np.asarray(indata, dtype=np.float32).reshape(-1)
        rms = float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0
        now = time.monotonic()
        with _LISTEN_LOCK:
            baseline = _LISTEN["baseline"]
            if baseline is None:
                _LISTEN["baseline"] = rms
                return
            loud = rms > max(baseline * 4.0, baseline + 0.06)
            if loud and now >= _LISTEN["_cooldown_until"]:
                _LISTEN["events"] += 1
                _LISTEN["last_event_at"] = datetime.now()
                _LISTEN["last_peak"] = rms
                _LISTEN["_cooldown_until"] = now + 3.0
                peak_pct = round(rms * 100)
                try:
                    from . import senses_log

                    senses_log.append_event(
                        "sound",
                        "Sound watcher heard a sudden sound "
                        f"(peak level about {peak_pct}%).",
                        value=peak_pct, unit="%")
                except Exception:  # noqa: BLE001 — logging is a bonus
                    pass
            elif not loud:
                # The baseline learns only from quiet blocks, so a
                # long loud sound can't teach it to ignore itself.
                _LISTEN["baseline"] = baseline * 0.95 + rms * 0.05
    except Exception:  # noqa: BLE001 — a watcher must never crash
        pass


def _listen_status_line() -> str:
    with _LISTEN_LOCK:
        stream = _LISTEN["stream"]
        events = _LISTEN["events"]
        started = _LISTEN["started_at"]
        last = _LISTEN["last_event_at"]
    if stream is None or started is None:
        return ("The sound watcher isn't running. Start it and "
                "it will listen for sudden loud sounds — it hears "
                "that *something* happened, not what it was.")
    minutes = max(0, int((datetime.now() - started).total_seconds() // 60))
    last_line = (f" Last sound heard at "
                 f"{last.strftime('%H:%M:%S')}."
                 if last is not None else " Nothing sudden heard yet.")
    return (f"The sound watcher is running — listening for "
            f"{minutes} minute(s), {events} sudden sound(s) heard "
            f"so far.{last_line} It's a basic loudness watcher: "
            "it can't tell a doorbell from any other sudden noise.")


def listen_for_sound(action: str = "status") -> str:
    """Arm, disarm or report on the sudden-sound watcher."""
    try:
        act = (action or "status").strip().lower()
        if act == "status":
            return _listen_status_line()
        if act == "start":
            with _LISTEN_LOCK:
                if _LISTEN["stream"] is not None:
                    return ("The sound watcher is already running. "
                            + _listen_status_line())
            try:
                import sounddevice as sd
            except Exception as e:  # noqa: BLE001
                return ("I couldn't start the sound watcher: the "
                        f"audio library isn't available ({e}).")
            try:
                stream = sd.InputStream(
                    samplerate=_RATE_IN, channels=1, dtype="float32",
                    blocksize=2048, callback=_listen_callback)
                stream.start()
            except Exception as e:  # noqa: BLE001
                return ("I couldn't start the sound watcher: no "
                        f"microphone could be opened ({e}).")
            with _LISTEN_LOCK:
                _LISTEN.update(stream=stream,
                               started_at=datetime.now(), events=0,
                               last_event_at=None, last_peak=0.0,
                               baseline=None, _cooldown_until=0.0)
            return ("Sound watcher started. I'll listen for "
                    "sudden loud sounds — a chime, an alarm beep, a "
                    "knock — and log each one to the Senses "
                    "timeline. Honestly: it's a basic loudness "
                    "detector, so it knows *that* a sound happened, "
                    "never *what* it was. Say 'stop listening for "
                    "sounds' to switch it off.")
        if act == "stop":
            with _LISTEN_LOCK:
                stream = _LISTEN["stream"]
                events = _LISTEN["events"]
                started = _LISTEN["started_at"]
                _LISTEN.update(stream=None, started_at=None,
                               baseline=None)
            if stream is None:
                return "The sound watcher wasn't running."
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 — it's off either way
                pass
            minutes = (max(0, int((datetime.now() - started)
                                  .total_seconds() // 60))
                       if started else 0)
            return (f"Sound watcher stopped after {minutes} "
                    f"minute(s). It heard {events} sudden sound(s) "
                    "while it ran.")
        return ("I know 'start', 'stop' and 'status' for the "
                "sound watcher — which would you like?")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't manage the sound watcher: {e}"


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every SENSES-2 pack tool on a ToolRegistry-like object."""
    registry.register(
        name="mic_check",
        description="Test the microphone: records about three seconds and reports the level in words — clear, quiet, clipping or silent — with the peak and average levels and the microphone's name. Use when the user asks 'is my mic working?' or before a call. It only measures; nothing is played back and nothing is kept.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=mic_check,
    )
    registry.register(
        name="speaker_check",
        description="Play a short three-note test tune (C–E–G) through the speakers and report which output device it went to. Use when the user asks 'can you hear anything?' or thinks their sound is broken. It confirms the tune was sent without an error — only the user can confirm they actually heard it.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=speaker_check,
    )
    registry.register(
        name="room_noise_level",
        description="Listen to the room for about five seconds and report how noisy it is in words — quiet room, calm office, busy office or loud — with the measured average level. Use when the user asks how noisy their room is, or whether it's quiet enough for a call or a recording. Nothing is recorded or kept; it's a measurement only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=room_noise_level,
    )
    registry.register(
        name="voice_note",
        description="Record a voice memo (3 to 120 seconds) and save it as a WAV file in ~/Music/Nova, with a text transcript saved beside it when transcription is set up on this PC — and an honest note in that file when it isn't. Use when the user says 'take a voice note' or wants to dictate a memo for later.",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {"type": "integer",
                            "description": "How long to record, in seconds (default 10, between 3 and 120)"},
                "title": {"type": "string",
                          "description": "Optional title for the note, used in its filename, e.g. 'Shopping ideas'"},
            },
            "required": [],
        },
        handler=voice_note,
    )
    registry.register(
        name="read_selection_aloud",
        description="Read aloud whatever text is currently selected in any app: copies the selection with Ctrl+C, speaks it in the system voice, and puts the clipboard back exactly as it was. Use when the user says 'read this to me' about something highlighted on screen. Different from speak_text, which reads words the user gives her directly.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=read_selection_aloud,
    )
    registry.register(
        name="describe_camera_view",
        description="Take ONE webcam photo and summarise it on-device: the light level, the frame's size and its dominant colours — a basic summary, honestly labelled as such. Use when the user asks 'what's in front of the laptop?' or 'what can you see?'. If there's no camera it says so, and the photo isn't kept.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=describe_camera_view,
    )
    registry.register(
        name="screen_brightness_auto",
        description="Match the screen brightness to the room: measures the room's light from one webcam frame, sets the screen to a matching level and reports the level set. Use when the user says the screen feels too bright or too dim for the room. It says honestly when there's no camera or the brightness can't be set on this PC.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=screen_brightness_auto,
    )
    registry.register(
        name="listen_for_sound",
        description="Start, stop or check the sound watcher (action: 'start', 'stop' or 'status'). While it runs it listens for sudden loud sounds — a chime, an alarm beep, a knock — with a simple loudness detector and logs each one to the Senses timeline. It is a basic detector: it knows a sound happened, never what the sound was. Use when the user wants to know if the doorbell or an alarm went off.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["start", "stop", "status"],
                           "description": "'start' to begin listening, 'stop' to switch it off, 'status' to check whether it's running and what it has heard"},
            },
            "required": [],
        },
        handler=listen_for_sound,
    )
