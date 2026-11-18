"""Input dispatch for core/desktop/cursor.py (B35 part 2 of 2):
Windows SendInput path, failsafe consult, position/dispatch.
"""

# B35 INTENTIONAL: geometry names this family reads by bare name
# (_is_windows, _ensure_dpi_aware, _virtual_bounds) come from the
# geometry module, not the facade — same objects as the facade
# re-exports (geometry is the defining module).
from .cursor_geometry import (
    _ensure_dpi_aware,
    _is_windows,
    _virtual_bounds,
)


def _read_cursor_position():
    """(x, y) of the mouse cursor in screen pixels via user32
    GetCursorPos (Windows), or None anywhere else / on failure.
    Never raises."""
    if not _is_windows():
        return None
    try:
        import ctypes

        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:  # noqa: BLE001 — not Windows / no user32
        pass
    return None


class _Button:
    """Button names as plain strings — the duck-type replacement
    for the old input library's Button enum. Callers keep writing
    Button.left / Button.right / Button.middle; the mouse handle
    below takes the string."""
    left = "left"
    right = "right"
    middle = "middle"


# Back-compat alias: call sites import Button from this module.
Button = _Button


class _WinMouse:
    """A ctypes SendInput mouse — the duck-type replacement for
    the old input library's mouse Controller. Windows only;
    constructed only by _mouse_controller() after the Windows
    check, so no OS call can fail at construction. Every method
    raises RuntimeError when SendInput refuses the event, so
    callers' existing except paths report it honestly."""

    @property
    def position(self):
        pos = _read_cursor_position()
        if pos is None:
            raise RuntimeError("could not read the cursor position")
        return pos

    @position.setter
    def position(self, xy):
        if not _sendinput_move(int(xy[0]), int(xy[1])):
            raise RuntimeError("SendInput did not accept the move")

    def click(self, button, count=1):
        if not _sendinput_click(str(button), int(count)):
            raise RuntimeError("SendInput did not accept the click")

    def press(self, button):
        if not _sendinput_button_down(str(button)):
            raise RuntimeError("SendInput did not accept the button-down")

    def release(self, button):
        if not _sendinput_button_up(str(button)):
            raise RuntimeError("SendInput did not accept the button-up")

    def scroll(self, dx, dy):
        if not _sendinput_scroll(int(dx), int(dy)):
            raise RuntimeError("SendInput did not accept the scroll")


def _mouse_controller():
    """(_WinMouse, _Button, None) on Windows, or (None, None,
    error_string) elsewhere. The mouse is driven with the native
    Windows SendInput API now — there is no third-party input
    library behind it."""
    _ensure_dpi_aware()
    if not _is_windows():
        return None, None, (
            "Cursor control unavailable: this build of VYUK drives "
            "the mouse with the native Windows SendInput API, which "
            "does not exist on this system."
        )
    return _WinMouse(), _Button, None


def _read_position(mouse):
    """Current cursor position as (x, y) ints, or None if unreadable."""
    try:
        pos = mouse.position
        return int(pos[0]), int(pos[1])
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Windows SendInput — the native dispatch path for moves and clicks
# ---------------------------------------------------------------------------
# SendInput is the documented native API — events land in the same
# input queue as real hardware. Used on Windows only, only for
# moves, clicks, scrolls and key events, and ANY failure (no
# user32, bad metrics, a short send count) returns False so
# callers fail honestly; non-Windows platforms never touch this
# code. There is no third-party input library behind any of it.

# GAPS-17 (review flaw D-1 remainder + D-2 second half): the INPUT
# structs live HERE, once, at module scope — this module is the
# single input engine. Before this change the same structs were
# defined five times (three local copies in this file, two more
# in core/desktop/uia_send.py), and uia_send called user32.SendInput
# directly, bypassing the hub. Every keyboard/mouse event in the
# tree now goes through the two senders below; the struct layout
# is the hub's spelling (dwExtraInfo c_size_t), byte-identical
# to uia_send's old c_void_p spelling for the keyboard payload
# (proven by gap_gaps17.py G6 and pinned by prove_gaps17.py S6).
import ctypes
from ctypes import wintypes


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT),
                ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x1000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_MOUSEEVENTF_ABSOLUTE = 0x8000
_WHEEL_DELTA = 120

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008  # GAPS-5 (BLOCKS #8): the scan-code flag

_SENDINPUT_BUTTONS = {
    "left": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
    "right": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
    "middle": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
}

# Key name -> Win32 virtual-key code (the ctypes hotkey path and
# the failsafe release path resolve through this one table).
_VK_MAP = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22,
    "insert": 0x2D, "shift": 0x10, "ctrl": 0x11, "control": 0x11,
    "alt": 0x12, "win": 0x5B, "windows": 0x5B, "cmd": 0x5B,
    "caps_lock": 0x14, "capslock": 0x14, "num_lock": 0x90, "scroll_lock": 0x91,
    "pause": 0x13,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

_VK_CHAR_MAP = {
    "-": 0xBD,   # VK_OEM_MINUS
    "=": 0xBB,   # VK_OEM_PLUS
    ",": 0xBC,   # VK_OEM_COMMA
    ".": 0xBE,   # VK_OEM_PERIOD
    "/": 0xBF,   # VK_OEM_2
    ";": 0xBA,   # VK_OEM_1
    "'": 0xDE,   # VK_OEM_7
    "[": 0xDB,   # VK_OEM_4
    "]": 0xDD,   # VK_OEM_6
    "\\": 0xDC,  # VK_OEM_5
    "`": 0xC0,   # VK_OEM_3
}

# Virtual-key code -> canonical name (first name wins for
# aliases: 0x0D is "enter", not "return"). The failsafe registry
# ident for a held key, and the friendly name in release-failure
# notes, both come from here.
_NAME_BY_VK = {}
for _name, _vk in _VK_MAP.items():
    _NAME_BY_VK.setdefault(_vk, _name)

# Lock keys are STATE TOGGLES, not held keys (failsafe H23b): a
# release sweep must never send them a key-up, which would flip
# the user's lock state.
_LOCK_VKS = frozenset({0x14, 0x90, 0x91})  # caps_lock, num_lock, scroll_lock


def _sendinput_events(events) -> bool:
    """Send (dwFlags, dx, dy[, mouseData]) mouse events via
    user32.SendInput.

    True only if Windows accepted every event; False anywhere else
    (non-Windows, ctypes layout trouble, short count) so callers
    fail honestly."""
    if not _is_windows() or not events:
        return False
    try:
        inputs = (_INPUT * len(events))()
        for i, event in enumerate(events):
            flags, dx, dy = event[0], event[1], event[2]
            data = event[3] if len(event) > 3 else 0
            inputs[i].type = 0  # INPUT_MOUSE
            inputs[i].u.mi.dx = int(dx)
            inputs[i].u.mi.dy = int(dy)
            inputs[i].u.mi.mouseData = int(data)
            inputs[i].u.mi.dwFlags = int(flags)
            inputs[i].u.mi.time = 0
            inputs[i].u.mi.dwExtraInfo = 0
        sent = ctypes.windll.user32.SendInput(
            len(events), inputs, ctypes.sizeof(_INPUT))
        return int(sent) == len(events)
    except Exception:  # noqa: BLE001 — any trouble at all: False
        return False


def _sendinput_keyboard(events) -> int:
    """Send (vk, scan, flags) keyboard events via user32.SendInput
    in ONE call — the hub's keyboard batch sender (GAPS-17).

    Returns Win32 SendInput's own return: the accepted EVENT
    count (0 on non-Windows, on any trouble, or when nothing was
    sent). Callers that need an all-or-nothing answer compare
    against len(events); callers that need partial-progress
    accounting (the UIA typing ladder's D-05) use the count
    directly. Never raises. This is the ONLY keyboard SendInput
    call site in the tree — the single-event senders below and
    core/desktop/uia_send.py both go through it."""
    if not _is_windows() or not events:
        return 0
    try:
        inputs = (_INPUT * len(events))()
        for i, event in enumerate(events):
            vk, scan, flags = event[0], event[1], event[2]
            inputs[i].type = 1  # INPUT_KEYBOARD
            inputs[i].u.ki.wVk = int(vk)
            inputs[i].u.ki.wScan = int(scan)
            inputs[i].u.ki.dwFlags = int(flags)
            inputs[i].u.ki.time = 0
            inputs[i].u.ki.dwExtraInfo = 0
        sent = ctypes.windll.user32.SendInput(
            len(events), inputs, ctypes.sizeof(_INPUT))
        return int(sent)
    except Exception:  # noqa: BLE001 — any trouble at all: 0
        return 0


def _sendinput_move(x, y) -> bool:
    """Move the cursor to pixel (x, y) via SendInput absolute motion
    across the virtual desktop; coordinates are normalised to
    0..65535 over the virtual bounds."""
    bounds = _virtual_bounds()
    if bounds is None:
        return False
    min_x, min_y, max_x, max_y = bounds
    span_x = max(1, max_x - min_x)
    span_y = max(1, max_y - min_y)
    nx = int(round((x - min_x) * 65535 / span_x))
    ny = int(round((y - min_y) * 65535 / span_y))
    flags = (_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE
             | _MOUSEEVENTF_VIRTUALDESK)
    return _sendinput_events([(flags, nx, ny)])


def _sendinput_click(button_key: str, count: int) -> bool:
    """Click via SendInput at the CURRENT cursor position."""
    pair = _SENDINPUT_BUTTONS.get(button_key)
    if pair is None:
        return False
    down, up = pair
    events = []
    for _ in range(max(1, int(count))):
        events.append((down, 0, 0))
        events.append((up, 0, 0))
    return _sendinput_events(events)


def _sendinput_button_down(button_key: str) -> bool:
    """Hold one mouse button down via SendInput (the press half
    of a drag, or the failsafe's held-button bookkeeping)."""
    pair = _SENDINPUT_BUTTONS.get(button_key)
    if pair is None:
        return False
    return _sendinput_events([(pair[0], 0, 0)])


def _sendinput_button_up(button_key: str) -> bool:
    """Release one mouse button via SendInput — the failsafe's
    button release path. False when the button name is unknown
    or SendInput refused the event."""
    pair = _SENDINPUT_BUTTONS.get(button_key)
    if pair is None:
        return False
    return _sendinput_events([(pair[1], 0, 0)])


def _sendinput_scroll(dx_steps: int, dy_steps: int) -> bool:
    """Scroll via SendInput wheel events (one step = one wheel
    notch, WHEEL_DELTA). Positive dy scrolls up, positive dx
    scrolls right. False when SendInput refused the event."""
    events = []
    if dy_steps:
        events.append((_MOUSEEVENTF_WHEEL, 0, 0,
                       int(dy_steps) * _WHEEL_DELTA))
    if dx_steps:
        events.append((_MOUSEEVENTF_HWHEEL, 0, 0,
                       int(dx_steps) * _WHEEL_DELTA))
    if not events:
        return True
    return _sendinput_events(events)


def _sendinput_key(vk: int, key_up: bool = False) -> bool:
    """Send one key event via SendInput (INPUT_KEYBOARD, virtual
    key): key-down by default, key-up with key_up=True — the
    failsafe's key release path and the hotkey path's dispatch.
    True only if Windows accepted the event; False anywhere
    else, so callers fail honestly. Never raises.

    GAPS-17: a thin wrapper over the hub batch sender — same
    wire bytes as before (one INPUT_KEYBOARD event, wVk set,
    wScan 0), same bool contract. The failsafe release wire
    format is unchanged."""
    return _sendinput_keyboard(
        [(int(vk), 0, _KEYEVENTF_KEYUP if key_up else 0)]) == 1


def _sendinput_scancode(scan: int, key_up: bool = False) -> bool:
    """Send one key event via SendInput (INPUT_KEYBOARD, scan
    code): key-down by default, key-up with key_up=True. The
    virtual key is 0, the scan code carries the key, dwFlags
    carries KEYEVENTF_SCANCODE (| KEYEVENTF_KEYUP) — the event
    shape a real key press has when the OS, not a virtual-key
    table, identifies the key. True only if Windows accepted
    the event; False anywhere else, so callers fail honestly.
    Never raises.

    GAPS-17: a thin wrapper over the hub batch sender — same
    wire bytes as before, same bool contract."""
    return _sendinput_keyboard(
        [(0, int(scan), _KEYEVENTF_SCANCODE
          | (_KEYEVENTF_KEYUP if key_up else 0))]) == 1


def _scan_for_vk(vk: int) -> int:
    """Scan code for a virtual-key code via MapVirtualKeyW
    (MAPVK_VK_TO_VSC = 0), or 0 when the VK has no scan code on
    this layout or the API is unavailable. Never raises."""
    if not _is_windows():
        return 0
    try:
        import ctypes

        return int(ctypes.windll.user32.MapVirtualKeyW(int(vk), 0))
    except Exception:  # noqa: BLE001 — no scan code available
        return 0


def _scan_layout_available() -> bool:
    """True when the layout APIs (VkKeyScanW / MapVirtualKeyW)
    answer — probed on the 'A' key, which every layout has.
    False off Windows, when user32 has no such entry, or when
    the probe returns no scan code. Never raises."""
    return _scan_for_vk(0x41) != 0


def _vk_scan_for_char(ch: str):
    """(scan, shift_scan, vk) for a single character on the
    CURRENT keyboard layout, or None when the character cannot
    be produced by a scan-code key press. VkKeyScanW resolves
    the character to its virtual key + shift state on this
    layout; MapVirtualKeyW converts the key to the physical
    scan code — so the event presses the key that produces the
    character HERE, not the key that produces it on a US
    layout. Returns None when VkKeyScanW cannot map the
    character, when the key has no scan code, when the
    character needs Ctrl or Alt as well as Shift (AltGr — this
    rung sends Shift only), or when the layout APIs are
    unavailable. Never raises."""
    if not _is_windows() or len(ch) != 1:
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        packed = int(user32.VkKeyScanW(ord(ch)))
        # VkKeyScanW returns a SHORT: -1 (0xFFFF) means
        # unmappable. (ctypes' default int return reads the
        # full register, so both spellings are checked.)
        if packed == -1 or packed == 0xFFFF:
            return None
        vk = packed & 0xFF
        shift = bool(packed & 0x0100)
        if packed & 0x0600:  # Ctrl (0x0200) or Alt (0x0400) too
            return None
        scan = _scan_for_vk(vk)
        if not scan:
            return None
        shift_scan = 0
        if shift:
            shift_scan = _scan_for_vk(0x10)  # VK_SHIFT
            if not shift_scan:
                return None
        return (scan, shift_scan, vk)
    except Exception:  # noqa: BLE001 — no layout resolution
        return None


def _failsafe_tripped() -> bool:
    """True when the v4.3 failsafe watchdog (core/failsafe.py) has
    tripped — the app hung or a human hit the stop flag, and all
    input control stays stopped until a human re-arms it. Import or
    module problems must never disable the cursor, so any failure
    here reads as "not tripped"."""
    try:
        from .. import failsafe  # Phase 3 D4d: cursor hub moved to core/desktop/; failsafe stays flat in core/

        return bool(failsafe.tripped())
    except Exception:  # noqa: BLE001
        return False


def _failsafe_status():
    """Tri-state failsafe consult for the click path (audit fix
    Batch 1 SEC-02; mirrors tools_registry._failsafe_state):
    ("clear", ""), ("tripped", reason) or ("unavailable", exc-name)
    when the consult itself fails. _failsafe_tripped above keeps
    its "never break the cursor" contract for the other hand
    callers; only the pixel-click path consumes this tri-state
    form, because only there does "cannot verify safety" have
    to mean "do not dispatch" rather than "proceed as clear".
    Never raises."""
    try:
        from .. import failsafe  # Phase 3 D4d: cursor hub moved to core/desktop/; failsafe stays flat in core/

        if bool(failsafe.tripped()):
            try:
                reason = str(failsafe.trip_reason() or "")
            except Exception:  # noqa: BLE001
                reason = ""
            return "tripped", reason
        return "clear", ""
    except Exception as exc:  # noqa: BLE001 — consult failure
        return "unavailable", type(exc).__name__


_FAILSAFE_MSG = ("VYUK's failsafe has tripped — her hands are stopped "
                 "until a human re-arms them from the tray menu.")


def _note_input_pressed(kind, obj) -> None:
    """Register a held input with the failsafe (v5.48, programme
    item 26 — a trip releases exactly what is tracked). Guarded
    like _failsafe_tripped: a broken failsafe never breaks the
    cursor."""
    try:
        from .. import failsafe  # Phase 3 D4d: cursor hub moved to core/desktop/; failsafe stays flat in core/

        failsafe.note_input_pressed(kind, failsafe.input_ident(obj), obj)
    except Exception:  # noqa: BLE001
        pass


def _note_input_released(kind, obj) -> None:
    """Unregister an input after its normal release (v5.48,
    programme item 26). Never raises."""
    try:
        from .. import failsafe  # Phase 3 D4d: cursor hub moved to core/desktop/; failsafe stays flat in core/

        failsafe.note_input_released(kind, failsafe.input_ident(obj))
    except Exception:  # noqa: BLE001
        pass


def _set_position(mouse, x, y) -> None:
    """Dispatch one cursor move: SendInput on Windows, the mouse
    handle's position setter as the universal fallback (which is
    SendInput too on Windows). Raises only if the fallback raises
    (callers already guard that)."""
    if _failsafe_tripped():
        raise RuntimeError(_FAILSAFE_MSG)
    if _is_windows() and _sendinput_move(x, y):
        return
    mouse.position = (x, y)


def _dispatch_click(mouse, Button, button_key: str, count: int) -> None:
    """Dispatch a click: SendInput on Windows, the mouse handle's
    click as the fallback."""
    if _failsafe_tripped():
        raise RuntimeError(_FAILSAFE_MSG)
    if _is_windows() and _sendinput_click(button_key, count):
        return
    mouse.click(getattr(Button, button_key), count)
