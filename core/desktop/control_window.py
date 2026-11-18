"""control_window concern extracted from core/control.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/control."""

from .control_base import (_DATA_DIR, _is_windows, _routed_backend, _truncate, _input_blocked)
from .control_gesture import (_GESTURE_TAKEOVER_PX, _GESTURE_POLL_S, _GESTURE_REASON, _gesture_lock, _gesture_depth, _gesture_expected, _gesture_watcher, _gesture_position_reader, _gesture_trip_fn, _read_gesture_mouse_pos, _trip_for_gesture, gesture_active, _note_synthetic_mouse_pos, check_gesture_takeover, _gesture_watch_loop, _begin_assistant_gesture, _end_assistant_gesture, assistant_gesture, _foreground_title, _foreground_elevated, _foreground_is_shell, _note_input_pressed, _note_input_released, _expected_lock, _expected_foreground, _foreground_identity_fn, _foreground_identity, note_expected_foreground, get_expected_foreground, clear_expected_foreground, set_expected_foreground, _foreground_label, _foreground_matches, _expectation_refusal, _last_input_tick, _tick_is_newer)


def _enum_windows():
    """Return [(hwnd, title)] for visible titled windows. Windows only."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.enum_windows()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    results = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value:
                    results.append((hwnd, buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return results


def _window_title(user32, hwnd) -> str:
    """Best-effort title lookup for one hwnd via user32 ("" on any
    failure). Used to name the window that actually holds focus when
    a focus request is refused (H2)."""
    try:
        import ctypes

        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:  # noqa: BLE001
        return ""


def virtual_desktop(action: str, title_substring: str = "") -> str:
    """Virtual-desktop verbs (v5.59, HA-5, pyvda — MIT, lazy,
    optional) through core/desktop/desktop.py's dispatch.

    This is the plain-Python entry point for the desktop verbs;
    window_control below routes its desktop_* actions here, so
    the model reaches them through that existing tool (and
    through use_toolbox's call to it) once the shipped
    window_control enum names the desktop actions — see
    desktop.py's docstring for why the enum step is the
    coordinator's, not this wave's. Actions: desktop_list,
    desktop_switch (title_substring = a desktop number or name),
    desktop_move ("Window title @2" / "Window title @Name"),
    desktop_pin, desktop_unpin. Never raises."""
    try:
        from . import desktop as _desktop_v559  # Phase 3 D2b: file now in core/desktop/ — . import binds the same desktop module (desktop_action lives on the module)

        return _desktop_v559.desktop_action(action, title_substring)
    except Exception as e:  # noqa: BLE001
        return f"Virtual desktop control failed: {e}"


def window_control(action: str, title_substring: str = "") -> str:
    """List/focus/minimize/maximize/close windows by title substring. Windows only.

    v5.59 (HA-5): desktop_list / desktop_switch / desktop_move /
    desktop_pin / desktop_unpin ride this same dispatcher (their
    implementation is core/desktop/desktop.py, pyvda, MIT, lazy and
    optional); the shipped tool schema's action enum does not
    name them yet, so the funnel refuses them until the
    coordinator adds the words to that enum — the handler here
    already accepts them, and direct Python callers (and the
    suite) can use them now.

    Acting actions follow the identify-first discipline (H1):
    matches are enumerated before anything is touched, an exact
    title match beats substring matches, and several matches mean a
    refusal listing the candidates — never a silent first match.
    Receipts are verified against the OS where a check exists (H2):
    focus is read back with GetForegroundWindow, minimize/maximize
    with IsIconic/IsZoomed, and close polls IsWindow because
    WM_CLOSE is only a request the app may defer."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.window_control(action, title_substring=title_substring)
    action = (action or "").strip().lower()
    valid = ("list", "focus", "minimize", "maximize", "close",
             "desktop_list", "desktop_switch", "desktop_move",
             "desktop_pin", "desktop_unpin")
    if action not in valid:
        return f"Unknown window action: '{action}'. Use one of: {', '.join(valid)}."
    if action.startswith("desktop_"):
        # HA-5 branch: desktop_list is a read (no failsafe gate,
        # like list); the acting desktop verbs stop when the
        # failsafe has tripped, like every other acting hand.
        if action != "desktop_list":
            blocked = _input_blocked()
            if blocked:
                return blocked
        return virtual_desktop(action, title_substring)
    if not _is_windows():
        return "Window control is only available on Windows — this system is not Windows, so no windows were touched."
    try:
        import ctypes

        windows = _enum_windows()
        if action == "list":
            if not windows:
                return "No visible windows with titles were found."
            lines = [f"• {title}" for _hwnd, title in windows[:50]]
            # H11: the 50-line cap is stated, like every sibling list.
            header = f"Open windows ({len(windows)}):"
            if len(windows) > 50:
                header = f"Open windows ({len(windows)}, showing first 50):"
            return header + "\n" + "\n".join(lines)

        # Acting actions stop when the failsafe has tripped (H12) —
        # the same rule the input hands in this module follow.
        blocked = _input_blocked()
        if blocked:
            return blocked

        needle = (title_substring or "").strip().lower()
        if not needle:
            return f"Window '{action}' needs a title_substring to match a window."
        user32 = ctypes.windll.user32
        titles = {h: t for h, t in windows}
        fg_hwnd = None
        try:
            fg_hwnd = user32.GetForegroundWindow()
        except Exception:  # noqa: BLE001 — best-effort read-back
            fg_hwnd = None

        # H1: enumerate first. Exact-title matches win over substring
        # matches; anything beyond a single candidate is a refusal.
        exact = [(h, t) for h, t in windows if t.lower() == needle]
        partial = [(h, t) for h, t in windows if needle in t.lower()]
        candidates = exact if exact else partial
        if not candidates:
            return f"No window found with title containing '{title_substring}'."
        if len(candidates) > 1:
            lines = []
            for i, (h, t) in enumerate(candidates, 1):
                mark = " (currently focused)" if h == fg_hwnd else ""
                lines.append(f"{i}. {t}{mark}")
            kind = "with that exact title" if exact else "containing that text"
            return (
                f"{len(candidates)} windows match '{title_substring}' ({kind}) — "
                "I won't guess which one to act on. The rule: I act only on a "
                "unique match or a single exact-title match.\n"
                + "\n".join(lines)
                + "\nAsk again with the exact full title of the right window."
            )
        hwnd, title = candidates[0]
        if exact:
            match_note = " (exact title match)"
            if len(partial) > 1:
                match_note += (
                    f"; {len(partial) - 1} other window(s) also contain "
                    f"'{title_substring}' and were left alone"
                )
        else:
            match_note = f" (only match for '{title_substring}')"
        SW_RESTORE, SW_MINIMIZE, SW_MAXIMIZE, WM_CLOSE = 9, 6, 3, 0x0010
        if action == "focus":
            user32.ShowWindow(hwnd, SW_RESTORE)
            sent = user32.SetForegroundWindow(hwnd)
            # H2: SetForegroundWindow routinely fails under Windows
            # foreground-lock rules (the taskbar button flashes
            # instead) — verify before claiming focus moved.
            now_fg = None
            try:
                now_fg = user32.GetForegroundWindow()
            except Exception:  # noqa: BLE001
                now_fg = None
            if now_fg is not None and now_fg != hwnd:
                if now_fg:
                    holder = titles.get(now_fg) or _window_title(user32, now_fg)
                    holder_txt = f"'{holder}'" if holder else "another window"
                else:
                    holder_txt = "no window at all"
                return (
                    "Focus was refused by Windows: "
                    f"'{title}' did not become the foreground window — focus "
                    f"is still on {holder_txt}. (Windows limits which apps "
                    "may steal focus; clicking the window or its taskbar "
                    "button will focus it.)"
                )
            if now_fg is None and not sent:
                return (
                    "Focus was refused by Windows: SetForegroundWindow "
                    f"reported failure for '{title}', and the foreground "
                    "window could not be read back to confirm otherwise."
                )
            # v5.48 (programme item 22): focus is VERIFIED here —
            # record this foreground as the expectation the next
            # type_text / press_hotkey checks against.
            note_expected_foreground()
            return f"Focused window: {title}{match_note}"
        if action == "minimize":
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            iconic = None
            try:
                iconic = bool(user32.IsIconic(hwnd))
            except Exception:  # noqa: BLE001
                iconic = None
            if iconic is False:
                return f"Sent minimize to '{title}', but it does not appear minimized."
            return f"Minimized window: {title}{match_note}"
        if action == "maximize":
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            zoomed = None
            try:
                zoomed = bool(user32.IsZoomed(hwnd))
            except Exception:  # noqa: BLE001
                zoomed = None
            if zoomed is False:
                return f"Sent maximize to '{title}', but it does not appear maximized."
            return f"Maximized window: {title}{match_note}"
        # close
        posted = user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if posted == 0:
            return f"Could not send the close request to '{title}' — the window did not accept it."
        # H2: WM_CLOSE is a request, not a result — poll briefly to
        # learn whether the window actually went away.
        import time

        gone = False
        try:
            for _ in range(6):
                if not user32.IsWindow(hwnd):
                    gone = True
                    break
                time.sleep(0.15)
        except Exception:  # noqa: BLE001 — unverifiable; say "sent"
            gone = False
        if gone:
            return f"Closed window: {title}{match_note}"
        return (
            f"Sent close request to window: {title}{match_note} — it is "
            "still open, so the app may be asking about unsaved changes."
        )
    except Exception as e:  # noqa: BLE001
        return f"Window control failed: {e}"
