"""Control Plus split (B26): control_plus_helpers.py -- moved verbatim from core/desktop/control_plus.py; see the facade for the split map."""

import sys

def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _truncate(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


def _capture_screen_bytes():
    """Return (jpeg_bytes, mime) via vision_manager, or raise."""
    from .vision import vision_manager  # Phase 3 D4c: vision now in core/desktop/; D2b interim closed

    result = vision_manager.capture_screen()
    if isinstance(result, tuple):
        return result[0], result[1] if len(result) > 1 else "image/jpeg"
    return result, "image/jpeg"


def _input_blocked() -> str | None:
    """The v4.3 failsafe check, shared with core/control.py (v5.9).

    Every input-dispatching handler in this module consults the
    failsafe before it moves, clicks or drags — until this round,
    click_text / click_element / drag_mouse never did, so VYUK's
    hands kept working here after a trip (audit H3). The check itself
    lives in core/control.py (one wall, maintained in one place);
    this is only the import shim. Any failure to load it reads as
    "not blocked", matching control.py's own convention: a broken
    failsafe module must not disable the hands wholesale."""
    try:
        from .control import _input_blocked as shared_check  # Phase 3 D4a: control hub now in core/desktop/; D2b interim closed

        return shared_check()
    except Exception:  # noqa: BLE001
        return None


def _note_input_pressed(kind, obj) -> None:
    """Register a held input with the failsafe (v5.48, programme
    item 26 — a trip releases exactly what is tracked). Guarded
    like _input_blocked above: never raises."""
    try:
        from .. import failsafe  # Phase 3 D2b: failsafe stays flat in core/

        failsafe.note_input_pressed(kind, failsafe.input_ident(obj), obj)
    except Exception:  # noqa: BLE001
        pass


def _note_input_released(kind, obj) -> None:
    """Unregister an input after its normal release (v5.48,
    programme item 26). Never raises."""
    try:
        from .. import failsafe  # Phase 3 D2b: failsafe stays flat in core/

        failsafe.note_input_released(kind, failsafe.input_ident(obj))
    except Exception:  # noqa: BLE001
        pass


def _uia_window(title_substring: str = ""):
    """Return (window, description) via pywinauto UIA, or raise ImportError/RuntimeError."""
    import re as _re

    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    if title_substring:
        win = desktop.window(title_re=f".*{_re.escape(title_substring)}.*")
        win.wait("exists", timeout=5)
        return win, f"window matching '{title_substring}'"
    import ctypes

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("no foreground window")
    return desktop.window(handle=hwnd), "the foreground window"


# Hard budget for the get_window_controls walk (audit H18): the
# display cap alone doesn't bound the work — counting/materialising
# an unbounded tree is itself the hang risk on complex windows.
_CONTROLS_WALK_BUDGET = 2000
_CONTROLS_DISPLAY_CAP = 60
