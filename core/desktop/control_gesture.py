"""control_gesture concern extracted from core/control.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/control."""

import contextlib
import math
import threading
from .control_base import (_DATA_DIR, _is_windows, _routed_backend, _truncate, _input_blocked)


_GESTURE_TAKEOVER_PX = 60  # cursor.py's _TAKEOVER_TOLERANCE_PX, shared value


_GESTURE_POLL_S = 0.03


_GESTURE_REASON = "user input during assistant gesture"


_gesture_lock = threading.Lock()


_gesture_depth = 0


_gesture_expected: tuple[int, int] | None = None


_gesture_watcher: threading.Thread | None = None


_gesture_position_reader = None   # () -> (x, y) | None


_gesture_trip_fn = None           # (reason: str) -> None


def _read_gesture_mouse_pos():
    """Current cursor position via the injected reader, else the
    native Windows cursor position. None when unreadable
    (headless, no display) — never raises."""
    reader = _gesture_position_reader
    if reader is not None:
        try:
            pos = reader()
            return (int(pos[0]), int(pos[1])) if pos is not None else None
        except Exception:  # noqa: BLE001
            return None
    try:
        from .cursor_input import _read_cursor_position

        return _read_cursor_position()
    except Exception:  # noqa: BLE001
        return None


def _trip_for_gesture() -> None:
    """Trip the failsafe for a gesture takeover (injectable seam)."""
    trip_fn = _gesture_trip_fn
    if trip_fn is not None:
        trip_fn(_GESTURE_REASON)
        return
    try:
        from .. import failsafe  # Phase 3 D2b: failsafe stays flat in core/

        failsafe.trip_now(_GESTURE_REASON)
    except Exception:  # noqa: BLE001 — tripping is best-effort here
        pass


def gesture_active() -> bool:
    """True while an assistant gesture guard is held."""
    with _gesture_lock:
        return _gesture_depth > 0


def _note_synthetic_mouse_pos(pos) -> None:
    """Record the last cursor position VYUK herself set, so the
    watcher does not mistake her own move for a user takeover.
    Call BEFORE dispatching a move (the watcher may sample between
    the dispatch and a post-move note). Never raises."""
    global _gesture_expected
    try:
        if pos is not None:
            with _gesture_lock:
                _gesture_expected = (int(pos[0]), int(pos[1]))
    except Exception:  # noqa: BLE001
        pass


def check_gesture_takeover(current=None) -> bool:
    """One takeover poll: True (and a failsafe trip) when a gesture
    is active and the real cursor sits over _GESTURE_TAKEOVER_PX
    from the last synthetic/expected position. The watcher thread
    calls this on its cadence; the suite calls it directly with a
    faked position. Never raises."""
    global _gesture_expected
    try:
        if not gesture_active():
            return False
        if current is None:
            current = _read_gesture_mouse_pos()
        if current is None:
            return False
        current = (int(current[0]), int(current[1]))
        with _gesture_lock:
            expected = _gesture_expected
            if expected is None:
                _gesture_expected = current
                return False
        if math.hypot(current[0] - expected[0],
                      current[1] - expected[1]) > _GESTURE_TAKEOVER_PX:
            _trip_for_gesture()
            return True
        return False
    except Exception:  # noqa: BLE001 — a broken check must not break input
        return False


def _gesture_watch_loop() -> None:
    while gesture_active():
        threading.Event().wait(_GESTURE_POLL_S)
        if not gesture_active():
            break
        if check_gesture_takeover():
            break


def _begin_assistant_gesture() -> None:
    """Enter the guard (nesting-safe). Starts the mouse watcher on
    the outermost entry and seeds the expected position."""
    global _gesture_depth, _gesture_expected, _gesture_watcher
    start_watcher = False
    with _gesture_lock:
        _gesture_depth += 1
        if _gesture_depth == 1:
            _gesture_expected = None
            if _gesture_watcher is None or not _gesture_watcher.is_alive():
                start_watcher = True
    if start_watcher:
        pos = _read_gesture_mouse_pos()
        if pos is not None:
            _note_synthetic_mouse_pos(pos)
            watcher = threading.Thread(
                target=_gesture_watch_loop, daemon=True,
                name="AssistantGestureWatch")
            with _gesture_lock:
                _gesture_watcher = watcher
            watcher.start()


def _end_assistant_gesture() -> None:
    global _gesture_depth, _gesture_expected
    with _gesture_lock:
        _gesture_depth = max(0, _gesture_depth - 1)
        if _gesture_depth == 0:
            _gesture_expected = None


@contextlib.contextmanager
def assistant_gesture():
    """Context manager form of the guard: `with assistant_gesture():`."""
    _begin_assistant_gesture()
    try:
        yield
    finally:
        _end_assistant_gesture()


def _foreground_title() -> str | None:
    """Title of the foreground window, or None when it can't be
    determined (non-Windows, no foreground window, untitled window,
    API failure). Used for receipts that name where dispatched input
    actually went (H13) — best-effort, never raises."""
    if not _is_windows():
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or None
    except Exception:  # noqa: BLE001
        return None


def _foreground_elevated():
    """True/False/None via core/input_guard (v5.45, H23a). Seam:
    tests assign a fake here; None = unknown, receipts unchanged."""
    try:
        from .. import input_guard  # Phase 3 D2b: input_guard stays flat in core/

        return input_guard.foreground_elevated()
    except Exception:  # noqa: BLE001
        return None


def _foreground_is_shell():
    """True when the foreground window is a shell/terminal, via
    core/input_guard.foreground_is_shell (v5.46, programme
    item 7 — process-name + window-class probes, fail-closed
    when both are blind on Windows). The shared probe logic
    lives in input_guard, NOT the funnel registry: the registry imports this
    module (through core/desktop/control.py), so importing the registry from here would be a cycle.
    Seam: tests assign a fake here (like _foreground_elevated);
    a broken probe reads as not-a-shell, matching
    _foreground_elevated's never-break-the-hands convention —
    the executor gate remains the fail-closed layer."""
    try:
        from .. import input_guard  # Phase 3 D2b: input_guard stays flat in core/

        return bool(input_guard.foreground_is_shell())
    except Exception:  # noqa: BLE001
        return False


def _note_input_pressed(kind, obj) -> None:
    try:
        from .. import failsafe  # Phase 3 D2b: failsafe stays flat in core/

        failsafe.note_input_pressed(kind, failsafe.input_ident(obj), obj)
    except Exception:  # noqa: BLE001
        pass


def _note_input_released(kind, obj) -> None:
    try:
        from .. import failsafe  # Phase 3 D2b: failsafe stays flat in core/

        failsafe.note_input_released(kind, failsafe.input_ident(obj))
    except Exception:  # noqa: BLE001
        pass


_expected_lock = threading.Lock()


_expected_foreground = None      # identity dict | None


_foreground_identity_fn = None   # test seam: () -> identity dict | None


def _foreground_identity():
    """The current foreground identity {hwnd, pid, title, proc}
    — every field None when it can't be determined (off
    Windows, headless, API failure). Never raises. The seam
    _foreground_identity_fn replaces this wholesale in tests."""
    if _foreground_identity_fn is not None:
        try:
            ident = _foreground_identity_fn()
            return dict(ident) if ident else {
                "hwnd": None, "pid": None, "title": None, "proc": None}
        except Exception:  # noqa: BLE001
            return {"hwnd": None, "pid": None, "title": None,
                    "proc": None}
    title = _foreground_title()
    hwnd = pid = None
    if _is_windows():
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow() or None
        except Exception:  # noqa: BLE001 — identity is best-effort
            hwnd = None
        if hwnd:
            # The pid probe is separate on purpose: a pid failure
            # must not discard a successfully-read hwnd, the
            # strongest identity this record has.
            try:
                pid_box = ctypes.c_ulong(0)
                user32.GetWindowThreadProcessId(
                    hwnd, ctypes.byref(pid_box))
                pid = pid_box.value or None
            except Exception:  # noqa: BLE001
                pid = None
    proc = None
    try:
        from .. import input_guard  # Phase 3 D2b: input_guard stays flat in core/

        proc = input_guard._foreground_process_name()
    except Exception:  # noqa: BLE001
        proc = None
    return {"hwnd": hwnd, "pid": pid, "title": title, "proc": proc}


def note_expected_foreground() -> None:
    """Record the CURRENT foreground as the expected target for
    the next type_text / press_hotkey (v5.48, programme
    item 22). Called by the focus entry points (see the section
    header). Never raises."""
    global _expected_foreground
    try:
        ident = _foreground_identity()
    except Exception:  # noqa: BLE001
        return
    with _expected_lock:
        _expected_foreground = ident


def get_expected_foreground():
    """A copy of the recorded expectation, or None."""
    with _expected_lock:
        return (dict(_expected_foreground)
                if _expected_foreground is not None else None)


def clear_expected_foreground() -> None:
    """Forget the recorded expectation (typing returns to the
    pre-v5.48 no-expectation behaviour)."""
    global _expected_foreground
    with _expected_lock:
        _expected_foreground = None


def set_expected_foreground(identity) -> None:
    """Test seam (v5.48, programme item 22): set the expectation
    directly from an identity dict like _foreground_identity()
    returns, without a live foreground to capture."""
    global _expected_foreground
    with _expected_lock:
        _expected_foreground = dict(identity) if identity else None


def _foreground_label(identity) -> str:
    """A human name for an identity: its title, else its
    process, else a pid/hwno description, else 'an unknown
    window'."""
    if not identity:
        return "an unknown window"
    if identity.get("title"):
        return f"'{identity['title']}'"
    if identity.get("proc"):
        return f"a {identity['proc']} window"
    if identity.get("pid"):
        return f"the window of process {identity['pid']}"
    return "an unknown window"


def _foreground_matches(expected, current) -> bool:
    """True when `current` is the window `expected` recorded —
    strongest comparable identity wins (hwnd, then pid, then
    title; see the section header). Nothing comparable reads
    as a match: the expectation vetoes only on positive
    evidence that focus moved."""
    for field in ("hwnd", "pid", "title"):
        exp_val = expected.get(field) if expected else None
        cur_val = current.get(field) if current else None
        if exp_val and cur_val:
            return exp_val == cur_val
    return True


def _expectation_refusal():
    """None when typing may proceed under the focus expectation
    (no expectation recorded, or the foreground still matches
    it), else the refusal text — which NAMES the window that
    is focused now (v5.48, programme item 22)."""
    expected = get_expected_foreground()
    if expected is None:
        return None
    current = _foreground_identity()
    if _foreground_matches(expected, current):
        return None
    return (
        "Refused: the focused window is not the one VYUK was "
        f"aimed at — focus was set on {_foreground_label(expected)} "
        f"but {_foreground_label(current)} is focused now, so "
        "nothing was typed. Focus the intended window again, "
        "then ask me to type."
    )


def _last_input_tick():
    """The desktop's last-input tick via core/input_guard
    (v5.48, programme item 22), or None when unknown (off
    Windows the probe answers None and the mid-type takeover
    check is skipped). Never raises."""
    try:
        from .. import input_guard  # Phase 3 D2b: input_guard stays flat in core/

        return input_guard.last_input_tick()
    except Exception:  # noqa: BLE001
        return None


def _tick_is_newer(new, old) -> bool:
    """True when tick `new` is strictly newer than `old` on the
    GetTickCount timebase (unsigned 32-bit, wraps every ~49.7
    days — a delta in the forward half of the range is newer,
    the backward half is older)."""
    if new is None or old is None:
        return False
    return new != old and ((int(new) - int(old)) & 0xFFFFFFFF) < 0x80000000
