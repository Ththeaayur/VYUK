"""uia_discover concern extracted from core/desktop/uia.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/desktop/uia."""

from .uia_base import (_SCAN_CAP, _MATCH_CAP, _is_windows, _routed_backend, _failsafe_block, _window_failure_text, _UIA_IS_PASSWORD_ID, _PASSWORD_NAME_RE, _password_name_re, _is_password_control, _control_usable, _TYPE_KEYS_SPECIALS, _escape_type_keys, _current_text, foreground_title, foreground_hwnd)  # GAPS-20 (D-9): _truncate / _unavailable / _uia_window now live in uia_engine with the bodies that used them


def _descendants(win, cap: int = _SCAN_CAP):
    """(elements, failure) for a bounded descendant scan.

    elements is a list of at most `cap + 1` descendant wrappers
    (the extra one is the truncation tell), or
    None when the scan itself failed — the window died, or the
    title match turned out ambiguous at walk time. failure is then
    a user-facing explanation (see _window_failure_text). Callers
    must not report a failed scan as 'not found'.
    """
    try:
        elements = win.descendants()
    except Exception as e:  # noqa: BLE001
        return None, _window_failure_text(e)
    # Keep one element past the cap so callers can tell "the window
    # held exactly cap elements" from "the walk was cut short".
    return list(elements)[:cap + 1], None


def _element_facts(el) -> dict | None:
    """Best-effort {name, class_name, control_type, rect} for one element."""
    try:
        info = el.element_info
        name = (info.name or "").strip()
        class_name = (info.class_name or "").strip()
        control_type = (info.control_type or "").strip()
        rect = info.rectangle
        rect_text = (
            f"({rect.left},{rect.top})–({rect.right},{rect.bottom})"
            if rect is not None
            else "unknown position"
        )
        return {
            "name": name,
            "class_name": class_name,
            "control_type": control_type,
            "rect": rect_text,
        }
    except Exception:  # noqa: BLE001 — element died mid-walk
        return None


def _find_matches(win, name: str, control_type: str = ""):
    """Scan a window's descendants for controls whose name contains `name`.

    Returns (matches, scanned, truncated, failure):
    - matches: list of (wrapper, facts) pairs, capped at _MATCH_CAP.
    - scanned: elements actually examined. When the window offered
      more elements than the scan cap, only the capped prefix was
      examined (see truncated).
    - truncated: True when either cap cut the work short — the
      match cap stopped collection, or the scan cap stopped the
      walk before the window was fully searched. A truncated result
      must be worded "at least N", never an exact total (audit U2).
    - failure: None, or a user-facing explanation when the scan
      itself failed (window gone / ambiguous). Callers return it
      instead of any not-found wording (audit U1).
    """
    needle = str(name).strip().lower()
    type_needle = str(control_type or "").strip().lower()
    elements, failure = _descendants(win)
    if failure is not None:
        return [], 0, False, failure
    if len(elements) > _SCAN_CAP:
        truncated = True
        elements = elements[:_SCAN_CAP]
    else:
        truncated = False
    matches = []
    scanned = 0
    for el in elements:
        scanned += 1
        facts = _element_facts(el)
        if facts is None or not facts["name"]:
            continue
        if needle not in facts["name"].lower():
            continue
        if type_needle and type_needle not in facts["control_type"].lower():
            continue
        matches.append((el, facts))
        if len(matches) >= _MATCH_CAP:
            truncated = True
            break
    return matches, scanned, truncated, None


def _format_facts(facts: dict) -> str:
    cls = f", class {facts['class_name']}" if facts["class_name"] else ""
    return f"• {facts['name']} ({facts['control_type']}{cls}) at {facts['rect']}"


def _point_element(x: int, y: int):
    """pywinauto wrapper for the UIA element at a screen point.

    The only place ElementFromPoint is called: the raw COM element
    comes from pywinauto's shared IUIAutomation instance
    (pywinauto.uia_defines.IUIA) and is wrapped exactly the way the
    rest of this module wraps elements (UiaElementInfo inside a
    UIAWrapper), so _element_facts and friends work on it unchanged.
    Raises on any failure — describe_point is the guarded caller.
    """
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UiaElementInfo

    try:
        from pywinauto.win32structures import POINT
    except Exception:  # noqa: BLE001 — fall back to the ctypes POINT
        from ctypes import wintypes

        POINT = wintypes.POINT  # noqa: N806 — mirrors the pywinauto name
    raw = IUIA().iuia.ElementFromPoint(POINT(int(x), int(y)))
    if raw is None:
        return None
    return UIAWrapper(UiaElementInfo(raw))


def describe_point(x: int, y: int) -> str | None:
    """Identify the control at screen point (x, y), or None.

    Return contract (exactly three shapes):
    - '"Save" (Button) in Notepad' — the element's name (capped at
      60 chars), its control type when known, and the owning
      top-level window's title when known; the '"name" (Type)' part
      stands alone when no window title could be read.
    - '(no named control) in Notepad' — the element itself is
      unnamed (a pane, a canvas, bare client area) but it lives in a
      titled window.
    - None — UIA can't run here (off-Windows / no pywinauto), the
      point resolved to nothing identifiable (bare desktop), or
      anything failed. NEVER raises.
    """
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.describe_point(x, y)
    # GAPS-20 (D-9): the Windows body lives in the one engine
    # interface now — uia_engine.describe_point (pywinauto only;
    # easy-uiauto declares no point capability).
    from . import uia_engine as _ue

    return _ue.describe_point(x, y)


def _focused_element():
    """The UIA wrapper for the element holding keyboard focus, or
    None (v5.45, H20 — the seam core/input_guard.py builds the one
    shared password rule on). Windows + pywinauto only; never
    raises."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.focused_element()
    if not _is_windows():
        return None
    try:
        import pywinauto  # noqa: F401 — presence probe
    except Exception:  # noqa: BLE001
        return None
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UiaElementInfo

        raw = IUIA().iuia.GetFocusedElement()
        if raw is None:
            return None
        return UIAWrapper(UiaElementInfo(raw))
    except Exception:  # noqa: BLE001
        return None


def _focused_control_label() -> str | None:
    """Label of the element holding keyboard focus right now —
    '"Name" (Type)' — or None when it can't be determined.
    Windows + pywinauto only; never raises. (A test seam, like
    foreground_title: suites patch this directly.)"""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.focused_control_label()
    # GAPS-20 (D-9): the Windows body lives in the one engine
    # interface now — uia_engine.focus_label (pywinauto only).
    from . import uia_engine as _ue

    return _ue.focus_label()


def desktop_snapshot() -> dict | None:
    """One probe snapshot: {"fg": foreground title | None,
    "focus": focused-control label | None}. Returns None when
    NOTHING could be observed — callers treat None as "the probe
    has no eyes here" and stay silent. Never raises."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.desktop_snapshot()
    try:
        fg = foreground_title()
    except Exception:  # noqa: BLE001
        fg = None
    try:
        focus = _focused_control_label()
    except Exception:  # noqa: BLE001
        focus = None
    if fg is None and focus is None:
        return None
    return {"fg": fg, "focus": focus}


def _safe_snapshot() -> dict | None:
    """desktop_snapshot() with the probe-failure contract applied:
    a raising snapshot seam degrades to None (unobserved), so the
    action's result stays exactly its pre-H5 text."""
    try:
        return desktop_snapshot()
    except Exception:  # noqa: BLE001
        return None


def uia_find_control(name: str, window_title: str = "", control_type: str = "") -> str:
    """Find UI controls by name via Windows UI Automation and report them.

    Reports each match's name, class, control type and on-screen
    rectangle so the caller can see exactly what would be acted on.
    """
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.uia_find_control(
            name, window_title=window_title, control_type=control_type)
    # GAPS-20 (D-9): the Windows find lives in the one engine
    # interface now — uia_engine.find_control (pywinauto first,
    # easy-uiauto pilot only on an empty pywinauto result, Electron
    # wall named instead of a bare not-found).
    from . import uia_engine as _ue

    return _ue.find_control(name, window_title=window_title,
                            control_type=control_type)


def uia_read_tree(window_title: str = "", max_depth: int = 3, max_nodes: int = 80) -> str:
    """Read a window's UI Automation control tree, depth- and count-bounded."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.uia_read_tree(
            window_title=window_title, max_depth=max_depth,
            max_nodes=max_nodes)
    # GAPS-20 (D-9): the Windows walk lives in the one engine
    # interface now — uia_engine.read_tree (pywinauto only; an
    # empty tree on a walled window names the Electron wall).
    from . import uia_engine as _ue

    return _ue.read_tree(window_title, max_depth=max_depth,
                         max_nodes=max_nodes)
