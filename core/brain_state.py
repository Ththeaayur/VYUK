"""Brain + wake-word state seams (UI Phase 1, H-10).

Two live reads the Home status block / instrument strip need:

* ``brain_source()`` — "local" when the configured engine is
  local, "cloud" for any cloud engine (gemini / groq). It follows
  ``config.brain_engine`` live, never a stored label.
* ``wake_word_state()`` — "on" / "off" / "unavailable" from the
  live wake-word detector. No setting stands in for the live
  value.
"""

from __future__ import annotations


def brain_source() -> str:
    """"local" or "cloud", from the live engine setting."""
    try:
        from .config import config as _app_config

        engine = str(
            getattr(_app_config, "brain_engine", "gemini") or "gemini"
        ).strip().lower()
    except Exception:  # noqa: BLE001 — unreadable config: cloud
        return "cloud"
    return "local" if engine == "local" else "cloud"


def wake_word_state() -> str:
    """The wake-word detector's live state.

    "on" while the detector is enabled, "off" while it is
    disabled, "unavailable" when there is no detector to ask.
    """
    try:
        # Direct import, NOT through the facade: core code never
        # imports gui_facade (layering rule, test_gui_boundary
        # test_03). The facade's wake_word_detector re-exports
        # this same object.
        from .voice.wake_word import wake_word_detector as _detector
    except Exception:  # noqa: BLE001 — no detector module
        return "unavailable"
    if _detector is None:
        return "unavailable"
    try:
        return "on" if bool(getattr(_detector, "enabled", False)) else "off"
    except Exception:  # noqa: BLE001 — unreadable detector
        return "unavailable"
