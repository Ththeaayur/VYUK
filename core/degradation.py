"""Degradation notice (UI Phase 1, item 23).

The plain banner across the top of the centre stage when a voice
turn degrades because the voices / brain files are missing —
naming what's missing and the fix route. Dismissible, never
silent, never cryptic. It is the only banner on Home.

The two readiness checks are module-level callables (reassignable
— the repro pins them) that delegate to the offline pack's own
readiness functions (``core/voice/local_assets_*``): the ears are
the Whisper model, the voice is the Kokoro voice. A check that
cannot run reads as NOT ready (fail-closed: an unprovable voice
is a degraded voice, never a healthy one).
"""

from __future__ import annotations


def whisper_ready() -> bool:
    """True when the ears (Whisper model) are on disk and usable."""
    try:
        from .voice.local_assets_whisper import (
            whisper_ready as _whisper_ready,
        )

        return bool(_whisper_ready())
    except Exception:  # noqa: BLE001 — unprovable ears: degraded
        return False


def kokoro_voice_ready() -> bool:
    """True when the voice (Kokoro model + voices) is usable."""
    try:
        from .voice.local_assets_kokoro import voice_ready as _voice_ready

        return bool(_voice_ready())
    except Exception:  # noqa: BLE001 — unprovable voice: degraded
        return False


def degradation_notice() -> dict | None:
    """The banner's content, or None when nothing is degraded.

    Returns {"text", "fix_route"} — the text names what's missing
    in plain words; fix_route is the real place to fix it
    (Settings -> Voice, where the pack is managed).
    """
    try:
        ears = bool(whisper_ready())
    except Exception:  # noqa: BLE001 — a failing check is degraded
        ears = False
    try:
        voice = bool(kokoro_voice_ready())
    except Exception:  # noqa: BLE001 — a failing check is degraded
        voice = False
    if ears and voice:
        return None
    missing = []
    if not ears:
        missing.append("her ears (speech recognition) are not ready")
    if not voice:
        missing.append("her voice is not ready")
    text = ("Voice is degraded — " + " and ".join(missing) + ". "
            "She can still hear you through the cloud, but local "
            "voice will not work until the pack finishes.")
    return {"text": text, "fix_route": "Settings → Voice"}
