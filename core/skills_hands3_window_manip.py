"""Window-manipulation implementations for the hands3 skills pack.

Split out of core/skills_window_control.py by SKILLS SPLIT PHASE A BATCH 1
(window-manip + window-focus). Same functions, verbatim — only the
module-level import block is new. The capture-scroll helpers this
module calls (_input_blocked, _drag_hold, _NOT_WINDOWS) live in the
capture-scroll module (SKILLS SPLIT PHASE A BATCH 2); the cross-import
sits at the bottom, after all definitions, so this module imports
cleanly in any order — facade-first or standalone.
"""

import subprocess
import sys
import time
from pathlib import Path


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# ---------------------------------------------------------------------------
# Small OS helpers — every risky call lives behind one of these
# ---------------------------------------------------------------------------


def _user32():
    """ctypes user32 (Windows only), with the cursor module's
    DPI-awareness applied first so rectangles are true pixels.
    Raises on non-Windows — callers guard with _is_windows()."""
    import ctypes

    try:
        from .desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        cursor._ensure_dpi_aware()
    except Exception:  # noqa: BLE001 — awareness is best-effort
        pass
    return ctypes.windll.user32


def _foreground_hwnd():
    """Handle of the foreground window, or None."""
    if not _is_windows():
        return None
    try:
        return _user32().GetForegroundWindow() or None
    except Exception:  # noqa: BLE001
        return None


def _title_of(hwnd) -> str:
    """Best-effort window title via control.py's reader."""
    try:
        from .desktop.control import _window_title  # Phase 3 D4a: control hub moved to core/desktop/

        return _window_title(_user32(), hwnd) or ""
    except Exception:  # noqa: BLE001
        return ""


def _window_rect(hwnd):
    """(left, top, right, bottom) of a window, or None."""
    if not _is_windows() or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:  # noqa: BLE001
        return None


def _centre(rect):
    left, top, right, bottom = rect
    return (left + right) // 2, (top + bottom) // 2


def _monitors() -> list[dict]:
    """Monitors as dicts (index from 1, primary first): device name,
    full rect and work-area rect. Windows only; [] on any failure."""
    if not _is_windows():
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = _user32()

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFOEX(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32)]

        found: list[dict] = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.POINTER(RECT), ctypes.c_double)

        def _collect(hmonitor, _hdc, _rect, _data):
            info = MONITORINFOEX()
            info.cbSize = ctypes.sizeof(MONITORINFOEX)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                r, w = info.rcMonitor, info.rcWork
                found.append({
                    "device": info.szDevice,
                    "primary": bool(info.dwFlags & 1),
                    "left": r.left, "top": r.top,
                    "right": r.right, "bottom": r.bottom,
                    "width": r.right - r.left, "height": r.bottom - r.top,
                    "work_left": w.left, "work_top": w.top,
                    "work_right": w.right, "work_bottom": w.bottom,
                })
            return 1

        user32.EnumDisplayMonitors(0, None, callback_type(_collect), 0)
        found.sort(key=lambda m: (not m["primary"], m["left"], m["top"]))
        for i, m in enumerate(found, start=1):
            m["index"] = i
        return found
    except Exception:  # noqa: BLE001
        return []


def _monitor_for_window(hwnd, monitors):
    """The monitor a window sits on: user32's own answer first
    (MonitorFromWindow), then centre containment, then primary."""
    if not monitors:
        return None
    try:
        import ctypes

        user32 = _user32()
        hmon = user32.MonitorFromWindow(hwnd, 2)  # DEFAULTTONEAREST
        if hmon:
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFOEX(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                            ("szDevice", wintypes.WCHAR * 32)]

            info = MONITORINFOEX()
            info.cbSize = ctypes.sizeof(MONITORINFOEX)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                for m in monitors:
                    if m["device"] == info.szDevice:
                        return m
    except Exception:  # noqa: BLE001 — fall through to containment
        pass
    rect = _window_rect(hwnd)
    if rect is not None:
        cx, cy = _centre(rect)
        for m in monitors:
            if m["left"] <= cx < m["right"] and m["top"] <= cy < m["bottom"]:
                return m
    for m in monitors:
        if m["primary"]:
            return m
    return monitors[0]


def _pick_window(title_substring: str):
    """Identify-first window picking (the window_control discipline):
    (hwnd, title) for a unique match — an exact title beats substring
    matches — or (None, refusal) listing the candidates."""
    from .desktop.control import _enum_windows  # Phase 3 D4a: control hub moved to core/desktop/

    needle = (title_substring or "").strip().lower()
    if not needle:
        return None, "Give part of the window's title so I know which window to use."
    windows = _enum_windows()
    exact = [(h, t) for h, t in windows if t.lower() == needle]
    partial = [(h, t) for h, t in windows if needle in t.lower()]
    candidates = exact if exact else partial
    if not candidates:
        return None, f"No window found with title containing '{title_substring}'."
    if len(candidates) > 1:
        listing = "; ".join(f"'{t}'" for _h, t in candidates[:8])
        extra = f" (and {len(candidates) - 8} more)" if len(candidates) > 8 else ""
        return None, (f"'{title_substring}' matches {len(candidates)} windows: "
                      f"{listing}{extra}. Say the exact title so the wrong "
                      "window is never used.")
    return candidates[0], ""


# ---------------------------------------------------------------------------
# WINDOW SNAP — exact halves/quarters of the active window's monitor
# ---------------------------------------------------------------------------

_SNAP_POSITIONS = ("left", "right", "top-left", "top-right",
                   "bottom-left", "bottom-right", "maximise")


def _snap_rect(position: str, mon: dict):
    """Target (left, top, right, bottom) inside the monitor's work
    area (the part of the screen the taskbar doesn't cover)."""
    wl, wt = mon["work_left"], mon["work_top"]
    wr, wb = mon["work_right"], mon["work_bottom"]
    mid_x, mid_y = (wl + wr) // 2, (wt + wb) // 2
    table = {
        "left": (wl, wt, mid_x, wb),
        "right": (mid_x, wt, wr, wb),
        "top-left": (wl, wt, mid_x, mid_y),
        "top-right": (mid_x, wt, wr, mid_y),
        "bottom-left": (wl, mid_y, mid_x, wb),
        "bottom-right": (mid_x, mid_y, wr, wb),
    }
    return table[position]


def window_snap(position: str) -> str:
    """Snap the active window to a half/quarter of its screen, or maximise it."""
    pos = (position or "").strip().lower()
    if pos == "maximize":  # accept the US spelling for the same slot
        pos = "maximise"
    if pos not in _SNAP_POSITIONS:
        return (f"Unknown snap position: '{position}'. Use one of: "
                f"{', '.join(_SNAP_POSITIONS)}.")
    if not _is_windows():
        return f"Snapping windows uses Windows window APIs — {_NOT_WINDOWS}"
    blocked = _input_blocked()
    if blocked:
        return blocked
    try:
        user32 = _user32()
        hwnd = _foreground_hwnd()
        if not hwnd:
            return "No window is currently in focus, so there's nothing to snap."
        title = _title_of(hwnd) or "(untitled window)"
        monitors = _monitors()
        if not monitors:
            return "Could not read the monitor list, so I can't place the window exactly."
        mon = _monitor_for_window(hwnd, monitors)
        SW_MAXIMIZE, SW_RESTORE = 3, 9
        if pos == "maximise":
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            zoomed = None
            try:
                zoomed = bool(user32.IsZoomed(hwnd))
            except Exception:  # noqa: BLE001
                zoomed = None
            if zoomed is False:
                return (f"Sent maximise to '{title}', but it does not "
                        "appear maximised — the app may refuse resizing.")
            return (f"Maximised '{title}' on monitor {mon['index']} "
                    f"({mon['width']}×{mon['height']}).")
        # A maximised or minimised window must be restored before
        # SetWindowPos geometry means anything.
        try:
            if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
        except Exception:  # noqa: BLE001
            pass
        left, top, right, bottom = _snap_rect(pos, mon)
        SWP_NOZORDER = 0x0004
        if not user32.SetWindowPos(hwnd, 0, left, top,
                                   right - left, bottom - top, SWP_NOZORDER):
            return f"Windows refused to move '{title}'."
        actual = _window_rect(hwnd)
        if actual is not None:
            al, at, ar, ab = actual
            if (abs(al - left) > 2 or abs(at - top) > 2
                    or abs(ar - right) > 2 or abs(ab - bottom) > 2):
                return (f"I asked Windows to snap '{title}' to the {pos} "
                        f"of monitor {mon['index']}, but it actually sits "
                        f"at ({al}, {at})–({ar}, {ab}) instead of "
                        f"({left}, {top})–({right}, {bottom}) — the app "
                        "may enforce its own size or position.")
        where = "left half" if pos == "left" else \
            "right half" if pos == "right" else f"{pos} quarter"
        return (f"Snapped '{title}' to the {where} of monitor "
                f"{mon['index']} — it now covers ({left}, {top}) to "
                f"({right}, {bottom}) on the {mon['width']}×{mon['height']} "
                "screen.")
    except Exception as e:  # noqa: BLE001
        return f"Could not snap the window: {e}"


# ---------------------------------------------------------------------------
# MOVE WINDOW TO MONITOR — the active window, keeping its relative
# place and size, the way Shift+Win+arrow behaves
# ---------------------------------------------------------------------------


def _resolve_monitor(spec: str, monitors, current):
    """(monitor, error): 'next' cycles, a number picks by index,
    'main'/'primary' picks the main screen, anything else is a
    fragment matched against device names."""
    text = (spec or "").strip().lower()
    if not text or text == "next":
        return monitors[current["index"] % len(monitors)], ""
    if text in ("main", "primary"):
        for m in monitors:
            if m["primary"]:
                return m, ""
        return monitors[0], ""
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(monitors):
            return monitors[idx - 1], ""
        listing = "; ".join(
            f"{m['index']}: {m['device']} ({m['width']}×{m['height']})"
            for m in monitors)
        return None, (f"There is no monitor {idx} — this PC has "
                      f"{len(monitors)} monitor(s): {listing}.")
    matches = [m for m in monitors
               if text in m["device"].lower()
               or text == f"monitor {m['index']}"]
    if len(matches) == 1:
        return matches[0], ""
    listing = "; ".join(
        f"{m['index']}: {m['device']} ({m['width']}×{m['height']})"
        for m in monitors)
    if not matches:
        return None, f"No monitor matches '{spec}'. Monitors: {listing}."
    return None, (f"'{spec}' matches more than one monitor. "
                  f"Monitors: {listing}.")


def move_window_to_monitor(monitor: str = "next") -> str:
    """Move the active window to another monitor ('next', a number, or a name)."""
    if not _is_windows():
        return f"Moving windows between monitors uses Windows APIs — {_NOT_WINDOWS}"
    blocked = _input_blocked()
    if blocked:
        return blocked
    try:
        user32 = _user32()
        hwnd = _foreground_hwnd()
        if not hwnd:
            return "No window is currently in focus, so there's nothing to move."
        title = _title_of(hwnd) or "(untitled window)"
        monitors = _monitors()
        if not monitors:
            return "Could not read the monitor list, so the window was not moved."
        if len(monitors) < 2:
            m = monitors[0]
            return (f"There's only one monitor connected ({m['device']}, "
                    f"{m['width']}×{m['height']}), so '{title}' has "
                    "nowhere to move to.")
        current = _monitor_for_window(hwnd, monitors)
        target, err = _resolve_monitor(monitor, monitors, current)
        if target is None:
            return err
        if target["index"] == current["index"]:
            return f"'{title}' is already on monitor {target['index']} ({target['device']})."
        rect = _window_rect(hwnd)
        if rect is None:
            return f"Could not read the position of '{title}', so it was not moved."
        was_zoomed = False
        try:
            was_zoomed = bool(user32.IsZoomed(hwnd))
            if was_zoomed or user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                rect = _window_rect(hwnd) or rect
        except Exception:  # noqa: BLE001
            was_zoomed = False
        wl, wt, wr, wb = rect
        win_w, win_h = max(40, wr - wl), max(40, wb - wt)
        cur_w = max(1, current["work_right"] - current["work_left"])
        cur_h = max(1, current["work_bottom"] - current["work_top"])
        tgt_w = target["work_right"] - target["work_left"]
        tgt_h = target["work_bottom"] - target["work_top"]
        new_w = min(tgt_w, max(80, round(win_w * tgt_w / cur_w)))
        new_h = min(tgt_h, max(60, round(win_h * tgt_h / cur_h)))
        rel_x = (wl - current["work_left"]) / cur_w
        rel_y = (wt - current["work_top"]) / cur_h
        new_l = target["work_left"] + round(rel_x * max(0, tgt_w - new_w))
        new_t = target["work_top"] + round(rel_y * max(0, tgt_h - new_h))
        new_l = min(max(new_l, target["work_left"]),
                    target["work_right"] - new_w)
        new_t = min(max(new_t, target["work_top"]),
                    target["work_bottom"] - new_h)
        SWP_NOZORDER = 0x0004
        if not user32.SetWindowPos(hwnd, 0, new_l, new_t,
                                   new_w, new_h, SWP_NOZORDER):
            return f"Windows refused to move '{title}'."
        if was_zoomed:
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE on the new screen
        actual = _window_rect(hwnd)
        if actual is not None:
            acx, acy = _centre(actual)
            if not (target["left"] <= acx < target["right"]
                    and target["top"] <= acy < target["bottom"]):
                al, at, ar, ab = actual
                return (f"I asked Windows to move '{title}' to monitor "
                        f"{target['index']}, but it actually sits at "
                        f"({al}, {at})–({ar}, {ab}), which is not on "
                        "that screen — the move did not take.")
        note = " and maximised there" if was_zoomed else ""
        return (f"Moved '{title}' from monitor {current['index']} to "
                f"monitor {target['index']} ({target['device']}, "
                f"{target['width']}×{target['height']}){note}.")
    except Exception as e:  # noqa: BLE001
        return f"Could not move the window: {e}"


# ---------------------------------------------------------------------------
# WINDOW PIN TOGGLE ALL — unpin every always-on-top window at once
# ---------------------------------------------------------------------------

_GWL_EXSTYLE = -20
_WS_EX_TOPMOST = 0x00000008
_HWND_NOTOPMOST = -2


def window_pin_toggle_all() -> str:
    """Unpin every always-on-top window in one call."""
    if not _is_windows():
        return f"Window layering uses Windows APIs — {_NOT_WINDOWS}"
    blocked = _input_blocked()
    if blocked:
        return blocked
    try:
        import ctypes

        from .desktop.control import _enum_windows  # Phase 3 D4a: control hub moved to core/desktop/

        user32 = _user32()
        user32.GetWindowLongW.restype = ctypes.c_long
        SWP_FLAGS = 0x0002 | 0x0001 | 0x0010  # NOMOVE | NOSIZE | NOACTIVATE
        pinned = []
        for hwnd, title in _enum_windows():
            try:
                style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            except Exception:  # noqa: BLE001
                continue
            if style & _WS_EX_TOPMOST:
                pinned.append((hwnd, title))
        if not pinned:
            return ("No always-on-top windows right now — nothing is "
                    "pinned above the other windows.")
        unpinned, refused = [], []
        for hwnd, title in pinned:
            ok = user32.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0,
                                     SWP_FLAGS)
            still = None
            try:
                still = bool(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
                             & _WS_EX_TOPMOST)
            except Exception:  # noqa: BLE001
                still = None
            if ok and still is not True:
                unpinned.append(title)
            else:
                refused.append(title)
        lines = []
        if unpinned:
            shown = ", ".join(f"'{t}'" for t in unpinned[:10])
            extra = (f" and {len(unpinned) - 10} more"
                     if len(unpinned) > 10 else "")
            lines.append(f"Unpinned {len(unpinned)} always-on-top "
                         f"window(s): {shown}{extra}. They'll now "
                         "layer like normal windows.")
        if refused:
            shown = ", ".join(f"'{t}'" for t in refused[:10])
            lines.append(f"Windows refused to unpin: {shown}.")
        if not lines:
            return "No always-on-top windows could be unpinned."
        return " ".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Could not unpin the windows: {e}"


# ---------------------------------------------------------------------------
# DRAG FILE ONTO — from the file's Explorer folder onto a named window
# ---------------------------------------------------------------------------


def _root_window_at(x: int, y: int):
    """Root window handle owning the screen point, or None."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = _user32()
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not hwnd:
            return None
        root = user32.GetAncestor(hwnd, 2)  # GA_ROOT
        return root or hwnd
    except Exception:  # noqa: BLE001
        return None


def _find_item_centre(explorer_hwnd, names):
    """(centre, error): find an Explorer item by display name via
    UI Automation. Names are tried in order (full file name first,
    then the name without its extension — Explorer can show either).
    (None, None) means the scan ran and found nothing."""
    try:
        from pywinauto import Desktop
    except ImportError:
        return None, ("Finding the file's icon isn't available in this build "
                      "of VYUK — the UI Automation part didn't make "
                      "it into the package.")
    except Exception as e:  # noqa: BLE001
        return None, f"UI Automation could not be loaded ({e})."
    try:
        win = Desktop(backend="uia").window(handle=explorer_hwnd)
        elements = win.descendants()
    except Exception as e:  # noqa: BLE001
        return None, (f"Could not read the Explorer window's contents "
                      f"({e}) — it may have closed.")
    wanted = [n.lower() for n in names if n]
    for el in elements:
        try:
            info = el.element_info
            name = (info.name or "").strip().lower()
            if name not in wanted:
                continue
            rect = info.rectangle
            if rect is None:
                continue
            return ((int(rect.left) + int(rect.right)) // 2,
                    (int(rect.top) + int(rect.bottom)) // 2), None
        except Exception:  # noqa: BLE001 — element died mid-walk
            continue
    return None, None


def _pick_explorer_window(folder_name, target_names, enum_windows=None,
                          find_centre=None, deadline_s=3.0,
                          sleep_s=0.3):
    """(hwnd, centre, err, saw_window): the Explorer window VERIFIED
    to show the folder's target item — never a title-substring
    guess.

    Desktop programme D-08: the old code bound the FIRST window
    whose title merely contained the folder's leaf name, so two
    folders sharing a leaf name ("Reports" in two places) collided
    silently. Now every title candidate is checked for the target
    item itself — exact-title candidates before substring ones —
    and exactly one verified window binds. More than one verified
    window (the same file name in both same-named folders) refuses
    as ambiguous instead of guessing. A UIA error with no verified
    window surfaces at once (no pointless repoll); a window that
    died mid-scan never sinks a good binding found in the same
    pass. The seams default to control._enum_windows and this
    module's _find_item_centre, resolved at call time so tests can
    stub them. Never raises."""
    if enum_windows is None:
        try:
            from .desktop.control import _enum_windows as enum_windows  # Phase 3 D4a: control hub moved to core/desktop/
        except Exception:  # noqa: BLE001 — listing is best-effort
            return None, None, "Could not list windows.", False
    if find_centre is None:
        find_centre = _find_item_centre
    deadline = time.time() + deadline_s
    saw_window = False
    while True:
        candidates = []  # (priority, hwnd): exact titles first
        seen = set()
        try:
            windows = enum_windows()
        except Exception:  # noqa: BLE001 — a dead enumeration
            windows = []  # is "no window came up", not a crash
        for h, t in windows:
            if h in seen:
                continue
            if t == folder_name:
                candidates.append((0, h))
                seen.add(h)
            elif folder_name in t:
                candidates.append((1, h))
                seen.add(h)
        if candidates:
            saw_window = True
        candidates.sort(key=lambda c: c[0])  # stable: keeps
        # enumeration order within a priority tier
        verified = []
        first_err = None
        for _prio, h in candidates:
            try:
                centre, err = find_centre(h, target_names)
            except Exception:  # noqa: BLE001 — a dead window
                continue  # never sinks a good binding
            if err is not None:
                if first_err is None:
                    first_err = err
                continue
            if centre is not None:
                verified.append((h, centre))
        if len(verified) == 1:
            return verified[0][0], verified[0][1], None, True
        if len(verified) > 1:
            return (None, None,
                    f"More than one Explorer window shows a folder "
                    f"named '{folder_name}' containing an item named "
                    f"'{target_names[0] if target_names else ''}', so "
                    f"I won't guess which one holds the file — open "
                    f"the right folder yourself and ask again.",
                    True)
        if first_err is not None:
            return None, None, first_err, True
        if time.time() >= deadline:
            break
        time.sleep(sleep_s)
    return None, None, None, saw_window


def drag_file_onto(path: str, window: str) -> str:
    """Drag a file from its folder onto a named window/app."""
    if not path or not str(path).strip():
        return "No file path given."
    target_path = Path(str(path)).expanduser()
    if not target_path.exists():
        return f"That path doesn't exist: {target_path}"
    if not _is_windows():
        return f"Dragging files between windows uses Windows APIs — {_NOT_WINDOWS}"
    blocked = _input_blocked()
    if blocked:
        return blocked
    try:
        user32 = _user32()
        picked, err = _pick_window(window)
        if picked is None:
            return err
        hwnd, title = picked
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                time.sleep(0.3)
        except Exception:  # noqa: BLE001
            pass
        rect = _window_rect(hwnd)
        if rect is None:
            return f"Could not read the position of '{title}', so nothing was dragged."
        drop_x, drop_y = _centre(rect)
        folder = target_path.parent
        try:
            subprocess.Popen(["explorer.exe", f"/select,{target_path}"])
        except Exception as e:  # noqa: BLE001
            return f"Could not open Explorer at '{folder}': {e}"
        # Bind the Explorer window showing this folder — verified
        # by the target item itself, never by title alone (D-08:
        # same-leaf-name folders used to collide silently).
        explorer_hwnd, centre, err, saw_window = _pick_explorer_window(
            folder.name, [target_path.name, target_path.stem])
        if err:
            return err
        if explorer_hwnd is None:
            if not saw_window:
                return (f"I opened Explorer at '{folder}', but its window "
                        "didn't come up, so I couldn't find the file's "
                        "icon to drag.")
            return (f"Explorer is showing '{folder}', but I couldn't "
                    f"find an item named '{target_path.name}' in it — "
                    "the view may still be loading, or the name may "
                    "display differently.")
        item_x, item_y = centre
        covering = _root_window_at(drop_x, drop_y)
        if covering is not None and covering != hwnd:
            cover_title = _title_of(covering) or "another window"
            return (f"The middle of '{title}' is covered by "
                    f"'{cover_title}', so a drop there would land on "
                    "the wrong window. Arrange the two windows side "
                    "by side — the file and the target both visible — "
                    "and ask again.")
        abort = _drag_hold([(item_x, item_y), (drop_x, drop_y)], 1.2)
        if abort:
            if "aborted" in abort.lower():
                return f"{abort} (The file was released where the drag stopped.)"
            return abort
        # B13: the vision landing check asks the B5 backend whether
        # the dropped file is now open in the target window. Where
        # vision cannot read, the honestly-unverified return stands
        # exactly as before (same message) — the check costs nothing
        # in the lab, where no weights exist.
        from . import tool_outcome as _outcome  # B13: seam wiring

        return _outcome.hands_landing_check(
            "drag_file",
            f"the file '{target_path.name}' open in the '{title}' window",
            f"Dragged '{target_path.name}' from '{folder}' onto "
            f"'{title}' (dropped at ({drop_x}, {drop_y}), the "
            "middle of that window). Whether that app opened or "
            "imported the file is up to the app — check it to "
            "confirm.")
    except Exception as e:  # noqa: BLE001
        return f"Could not drag the file: {e}"


# SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL (cycle-free cross-import):
# manip and capture import each other, so every module's own definitions
# precede its sibling imports — this import sits at the bottom, after all
# definitions, so importing in any order (facade-first or standalone) works.
from .skills_hands3_capture_scroll import _NOT_WINDOWS, _drag_hold, _input_blocked
