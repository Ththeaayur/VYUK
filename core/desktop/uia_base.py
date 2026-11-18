"""uia_base concern extracted from core/desktop/uia.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/desktop/uia."""

import sys


_SCAN_CAP = 2000


_MATCH_CAP = 25


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _routed_backend():
    """v5.50 (programme items 41/42): when a NON-Windows platform
    backend serves this process — the scripted FakeBackend under
    VYUK_PLATFORM=fake, or an EXPERIMENTAL native backend
    (darwin/linux) — the accessibility surface is the backend's,
    and the public functions below delegate to it. This module's
    legacy bodies ARE the Windows implementation (the
    WindowsBackend delegates back into them), so when the Windows
    backend serves (backend.name == "windows") this returns None
    and every function runs its pre-v5.50 body unchanged —
    Windows behaviour is byte-identical. Resolved lazily at call
    time, like the WindowsBackend's own delegation, so test
    patching of this module's seams keeps working on Windows."""
    try:
        from ..platform import get_backend  # Phase 3 D2c: file now in core/desktop/

        backend = get_backend()
    except Exception:  # noqa: BLE001 — seam failure = legacy path
        return None
    return backend if backend.name != "windows" else None


def _truncate(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


def _failsafe_block() -> str | None:
    """Return the failsafe message when the v4.3 watchdog
    (core/failsafe.py) has tripped, else None. Read-only UIA tools
    (find / read tree) don't call this — only the acting tools do:
    when the failsafe trips, VYUK's hands stop until a human
    re-arms them from the tray menu."""
    try:
        from .. import failsafe  # Phase 3 D2c: file now in core/desktop/

        if failsafe.tripped():
            return (
                "VYUK's failsafe has tripped — her hands are stopped "
                "until a human re-arms them from the tray menu."
            )
    except Exception:  # noqa: BLE001
        pass
    return None


def _unavailable() -> str | None:
    """Return the 'not available' message for this PC, or None if UIA can run."""
    if not _is_windows():
        return (
            "UI Automation isn't available on this PC yet — it only works "
            "on Windows, and this system isn't Windows."
        )
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        return (
            "UI Automation isn't available in this build of VYUK — "
            "the UI Automation part didn't make it into the package."
        )
    except Exception as e:  # noqa: BLE001
        return f"UI Automation isn't available on this PC yet — pywinauto failed to load ({e})."
    return None


def _uia_window(title_substring: str = ""):
    """Return (window, description) via pywinauto UIA, or raise.

    Mirrors core/desktop/control_plus._uia_window: a title-matched window when
    `title_substring` is given, otherwise the foreground window.
    """
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


def _window_failure_text(exc: Exception) -> str:
    """Classify a descendant-scan failure for the result string
    (audit U1): an ambiguous title match and a window that went away
    are NOT 'the control isn't there', and must never be worded as a
    plain not-found."""
    name = type(exc).__name__
    msg = str(exc)
    lowered = msg.lower()
    if "Ambiguous" in name or "ambig" in lowered:
        return (
            "more than one window matches that title, so I can't "
            "tell which one to search — give a more specific "
            "window_title."
        )
    return (
        "the target window went away or stopped responding while I "
        f"was scanning it ({name}: {msg}) — it may have closed."
    )


_UIA_IS_PASSWORD_ID = 30019


_PASSWORD_NAME_RE = None  # built lazily to keep module import light


def _password_name_re():
    global _PASSWORD_NAME_RE
    if _PASSWORD_NAME_RE is None:
        import re

        _PASSWORD_NAME_RE = re.compile(r"password|passcode|\bpin\b", re.IGNORECASE)
    return _PASSWORD_NAME_RE


def _is_password_control(el) -> bool | None:
    """True/False from the element's UIA IsPassword property; None
    when the property can't be determined on this element."""
    _backend = _routed_backend()  # v5.50 (items 41/42): see _routed_backend
    if _backend is not None:
        return _backend.is_password_control(el)
    try:
        raw = el.element_info.element.GetCurrentPropertyValue(_UIA_IS_PASSWORD_ID)
        return bool(raw)
    except Exception:  # noqa: BLE001 — pattern not exposed this way
        return None


def _control_usable(el, facts) -> tuple[bool, str]:
    """(usable, reason) for clicking: enabled and visible with a
    real on-screen rectangle. Unknown state reads as usable — only
    positively-known bad state skips a control."""
    try:
        is_enabled = getattr(el, "is_enabled", None)
        if callable(is_enabled) and not is_enabled():
            return False, "disabled"
    except Exception:  # noqa: BLE001
        pass
    try:
        info_enabled = getattr(el.element_info, "enabled", None)
        if isinstance(info_enabled, bool) and not info_enabled:
            return False, "disabled"
    except Exception:  # noqa: BLE001
        pass
    try:
        is_visible = getattr(el, "is_visible", None)
        if callable(is_visible) and not is_visible():
            return False, "hidden"
    except Exception:  # noqa: BLE001
        pass
    try:
        rect = el.element_info.rectangle
        if rect is not None and (
            rect.right - rect.left <= 0 or rect.bottom - rect.top <= 0
        ):
            return False, "off-screen (zero-size rectangle)"
    except Exception:  # noqa: BLE001
        pass
    return True, ""


_TYPE_KEYS_SPECIALS = frozenset("%^+~(){}")


def _escape_type_keys(text: str) -> str:
    """Escape type_keys grammar characters: {%} {^} {+} {~} {(} {)} {{} {}}."""
    return "".join(
        "{" + ch + "}" if ch in _TYPE_KEYS_SPECIALS else ch for ch in text
    )


def _current_text(el) -> str:
    """Best-effort current value of a text control (for append mode)."""
    try:
        value = el.iface_value.Value
        if isinstance(value, str):
            return value
    except Exception:  # noqa: BLE001
        pass
    try:
        value = el.window_text()
        if isinstance(value, str):
            return value
    except Exception:  # noqa: BLE001
        pass
    return ""


def foreground_title() -> str | None:
    """Title of the foreground window, or None.

    Windows only (ctypes GetForegroundWindow + GetWindowTextW).
    None when there is no foreground window, its title is empty, or
    anything fails — callers treat None as "unknown", never as "".
    """
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.foreground_title()
    if not _is_windows():
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        if not user32.GetWindowTextW(hwnd, buf, length + 1):
            return None
        title = (buf.value or "").strip()
        return title[:120] if title else None
    except Exception:  # noqa: BLE001
        return None


def foreground_hwnd() -> int | None:
    """Handle of the foreground window, or None when unknown.

    Desktop programme, F-07: the marks table is stamped with the
    foreground window's HANDLE as well as its title, so a window
    replaced by another window with the SAME title still reads as
    stale. Mirrors foreground_title's shape (routed backend first,
    then ctypes on Windows, else None); never raises — callers
    treat None as "unknown", never as a change.
    """
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        fn = getattr(_backend, "foreground_hwnd", None)
        if callable(fn):
            try:
                hwnd = fn()
            except Exception:  # noqa: BLE001
                return None
            return int(hwnd) if hwnd else None
        return None
    if not _is_windows():
        return None
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None
    except Exception:  # noqa: BLE001
        return None
