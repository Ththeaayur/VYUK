"""
Shared input guard for VYUK (v5.45, H20 + H23a).

ONE password rule for every typing path. Before this module each
hand had its own version: uia_type_text checked the target's UIA
IsPassword, the browser pack checked the DOM input type, and
control.type_text — the most-used typing path of all — checked
nothing, so "type my password into the focused field" worked
whenever the field wasn't reached through UIA or the browser.

The rule now lives here:

    focused_control_is_password() -> True / False / UNKNOWN

It reads the FOCUSED element's UIA IsPassword property through
core.desktop.uia's seams. UNKNOWN means "UIA is present but the property
could not be determined" — and UNKNOWN refuses (fail closed).
When UIA is unavailable ENTIRELY, the answer splits by platform
(v5.48, programme item 25): on Windows — the platform the guard
exists for, where pywinauto is now a hard dependency — that
refuses too (GUARD_UNAVAILABLE_TEXT, fail closed); only OFF
Windows does the English name regex (password|passcode|\\bpin\\b)
get a say, and then only as a hint over the focused control's
NAME when a name can be had at all — it is never the decider
when UIA answered.

typing_blocked_reason() is what typing paths call: None means
"type away", otherwise the honest refusal text to return as the
tool result (never an exception):

    "focused field looks like a password field — I won't type into it"

H23a — elevated-target honesty lives here too:
foreground_elevated() reports whether the foreground window
belongs to a process running at a HIGHER integrity level than
VYUK (Windows only, guarded; None = unknown). Windows silently
drops synthetic input sent from a lower-integrity process to an
elevated window, so a receipt that claims success would be a lie;
the input tools check this after dispatch and say so instead.

Everything is seam-injectable for the suites (this module must be
provable on headless Linux): _focused_element_getter,
_uia_available_fn and _integrity_probe are the seams, plus
_last_input_probe (v5.48, item 22 — set via set_last_input_probe)
behind last_input_tick(), the desktop's last-input clock that
type_text's mid-type user-takeover abort reads.
"""

from __future__ import annotations

import re
import sys

UNKNOWN = "UNKNOWN"

PASSWORD_REFUSAL = (
    "focused field looks like a password field — I won't type into it"
)
PASSWORD_REFUSAL_TEXT = (
    "Refused: the focused field looks like a password field — "
    "I won't type into it. Passwords are yours to type."
)
UNKNOWN_REFUSAL_TEXT = (
    "Refused: I can't verify the focused field isn't a password "
    "field — I won't type into it. Click a plain text field "
    "first, or type it yourself if it's a password."
)
# v5.48 (programme item 25): the guard itself is down on a
# Windows machine — fail CLOSED (see typing_blocked_reason).
GUARD_UNAVAILABLE_TEXT = (
    "Refused: typing refused because the password guard "
    "(UI Automation) is unavailable on this Windows machine — "
    "I can't verify the focused field isn't a password field, "
    "so I won't type into it. Type it yourself, or restart "
    "VYUK so the guard can load."
)

_PASSWORD_NAME_RE = re.compile(r"password|passcode|\bpin\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Seams (tests fake these; production leaves them None)
# ---------------------------------------------------------------------------

_focused_element_getter = None   # () -> UIA wrapper | None
_uia_available_fn = None         # () -> bool
_integrity_probe = None          # () -> (fg_level, own_level) | None
_last_input_probe = None         # () -> int tick | None (v5.48, item 22)


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def uia_available() -> bool:
    """True when the UIA stack can answer questions at all
    (Windows + pywinauto importable). The fallback name-hint path
    is allowed ONLY when this is False."""
    if _uia_available_fn is not None:
        try:
            return bool(_uia_available_fn())
        except Exception:  # noqa: BLE001
            return False
    if not _is_windows():
        return False
    try:
        import pywinauto  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _focused_element():
    """The UIA wrapper for the element holding keyboard focus, or
    None. Production path goes through core.desktop.uia's focused-element
    seam; never raises."""
    if _focused_element_getter is not None:
        try:
            return _focused_element_getter()
        except Exception:  # noqa: BLE001
            return None
    try:
        from .desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        getter = getattr(uia, "_focused_element", None)
        if getter is None:
            return None
        return getter()
    except Exception:  # noqa: BLE001
        return None


def element_is_password(el):
    """True / False / UNKNOWN for one element's UIA IsPassword,
    via core.desktop.uia._is_password_control (the existing seam)."""
    if el is None:
        return UNKNOWN
    try:
        from .desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        verdict = uia._is_password_control(el)
    except Exception:  # noqa: BLE001
        return UNKNOWN
    if verdict is True:
        return True
    if verdict is False:
        return False
    return UNKNOWN


def focused_control_is_password():
    """True / False / UNKNOWN for the FOCUSED control (H20).

    True/False come from the element's UIA IsPassword property.
    UNKNOWN when UIA answered nothing usable: no focused element,
    or the property could not be read."""
    if not uia_available():
        return UNKNOWN
    return element_is_password(_focused_element())


def _focused_name_hint() -> str | None:
    """Best-effort NAME of the focused control for the fallback
    hint path (only consulted when UIA is unavailable entirely)."""
    try:
        from .desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        label = uia._focused_control_label()
        return label or None
    except Exception:  # noqa: BLE001
        return None


def typing_blocked_reason() -> str | None:
    """The H20 gate every typing path calls before injecting
    keystrokes: None to proceed, or the honest refusal text.

    Decision order:
      * UIA available and says password      -> refuse (password)
      * UIA available and says not-password  -> proceed (the name
        regex NEVER overrides a UIA answer)
      * UIA available but UNKNOWN            -> refuse (unknown)
      * UIA unavailable entirely, ON WINDOWS  -> refuse (guard
        unavailable — v5.48, programme item 25: LEGACY CONTRACT
        CHANGE, with reason. This platform is the one the guard
        exists for; pywinauto is a hard dependency since v5.48,
        so "UIA down" on Windows means the install is broken,
        and typing blind into a possible password field is the
        exact failure H20 closed — fail closed instead of
        falling through to a name hint.)
      * UIA unavailable entirely, OFF Windows -> name-regex hint
        over the focused control's name when a name exists;
        no name to judge -> proceed (there is no UIA here at
        all — e.g. a Linux dev desktop — and no evidence of a
        password field; the hint is a hint, not a veto)."""
    if not uia_available():
        if _is_windows():
            return GUARD_UNAVAILABLE_TEXT
        name = _focused_name_hint()
        if name and _PASSWORD_NAME_RE.search(name):
            return PASSWORD_REFUSAL_TEXT
        return None
    verdict = focused_control_is_password()
    if verdict is True:
        return PASSWORD_REFUSAL_TEXT
    if verdict == UNKNOWN:
        return UNKNOWN_REFUSAL_TEXT
    return None


# ---------------------------------------------------------------------------
# H23a — elevated foreground detection (Windows only, guarded)
# ---------------------------------------------------------------------------


def _integrity_levels():
    """(foreground_level, own_level) integrity RIDs, or None.

    Windows only: the foreground window's process token integrity
    vs this process's. Any failure (not Windows, no foreground
    window, token unreadable) is None — unknown, never a guess.
    The seam _integrity_probe replaces this wholesale in tests."""
    if _integrity_probe is not None:
        try:
            return _integrity_probe()
        except Exception:  # noqa: BLE001
            return None
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        def _level_for(process_id, own=False):
            handle = (kernel32.GetCurrentProcess() if own else
                      kernel32.OpenProcess(0x1000, False, process_id))
            if not handle:
                return None
            try:
                token = wintypes.HANDLE(0)
                if not advapi32.OpenProcessToken(
                        handle, 0x0008, ctypes.byref(token)):
                    return None
                try:
                    size = wintypes.DWORD(0)
                    advapi32.GetTokenInformation(
                        token, 25, None, 0, ctypes.byref(size))
                    if not size.value:
                        return None
                    buf = ctypes.create_string_buffer(size.value)
                    if not advapi32.GetTokenInformation(
                            token, 25, buf, size.value,
                            ctypes.byref(size)):
                        return None
                    # TOKEN_MANDATORY_LABEL: first field is a
                    # SID_AND_ATTRIBUTES; the SID's last sub-
                    # authority is the integrity RID.
                    sid = ctypes.cast(
                        buf, ctypes.POINTER(ctypes.c_void_p))[0]
                    if not sid:
                        return None
                    count = ctypes.cast(
                        sid + 1, ctypes.POINTER(ctypes.c_ubyte))[0]
                    rid = ctypes.cast(
                        sid + 8 + 4 * (count - 1),
                        ctypes.POINTER(ctypes.c_uint32))[0]
                    return int(rid)
                finally:
                    kernel32.CloseHandle(token)
            finally:
                if not own:
                    kernel32.CloseHandle(handle)

        fg = _level_for(pid.value)
        own = _level_for(0, own=True)
        if fg is None or own is None:
            return None
        return fg, own
    except Exception:  # noqa: BLE001 — detection is best-effort
        return None


def foreground_elevated():
    """True when the foreground window's process runs at a HIGHER
    integrity level than VYUK (input to it is dropped by Windows),
    False when levels are known and the target is not higher,
    None when it can't be determined (incl. non-Windows)."""
    levels = _integrity_levels()
    if levels is None:
        return None
    try:
        fg, own = levels
        return int(fg) > int(own)
    except Exception:  # noqa: BLE001
        return None


ELEVATED_NOTE = (
    "The foreground window belongs to an elevated process — "
    "Windows drops synthetic input sent to elevated windows from "
    "a non-elevated app, so this input could not be delivered "
    "to the elevated window."
)


# ---------------------------------------------------------------------------
# Last-input tick (v5.48, programme item 22) — the "did the USER
# just touch the keyboard/mouse?" clock behind type_text's
# mid-type takeover abort. Windows exposes it as
# GetLastInputInfo: the tick count (GetTickCount timebase,
# milliseconds, wrapping at 2**32) of the most recent input
# event on the desktop — genuine OR synthetic, it does not
# distinguish. The sampling discipline that makes it usable
# despite that lives in core/desktop/control.type_text (sample right
# AFTER each of her own batches; only a tick NEWER than that
# post-batch sample counts as the user). Off Windows there is
# no equivalent probe here, so this returns None and the
# caller skips the check entirely.
# ---------------------------------------------------------------------------


def set_last_input_probe(fn) -> None:
    """Inject the last-input-tick probe (tests fake it, exactly
    like _integrity_probe above). Pass None to restore the real
    Windows probe."""
    global _last_input_probe
    _last_input_probe = fn


def last_input_tick():
    """Tick of the most recent desktop input event, or None
    when it can't be known (off Windows, API failure, broken
    probe). Never raises."""
    if _last_input_probe is not None:
        try:
            value = _last_input_probe()
            return None if value is None else int(value)
        except Exception:  # noqa: BLE001 — unknown reads as None
            return None
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT),
                        ("dwTime", wintypes.DWORD)]

        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(
                ctypes.byref(info)):
            return None
        return int(info.dwTime)
    except Exception:  # noqa: BLE001 — the probe is best-effort
        return None


# ---------------------------------------------------------------------------
# Shell / interpreter classification + foreground-shell detection
# (v5.46, programme items 3 + 7 — C1, A5, D-C3)
#
# The canonical name sets live HERE, not in the funnel gate: the
# executor gate (core/funnel/tools_gate.py) and the typing hands (core/desktop/control)
# both need them; tools imports control, control already imports
# input_guard, and input_guard imports neither — so this module
# is the one place both can reach without a cycle. core/funnel/tools_gate_shell.py
# imports these frozensets and re-exports them under its
# historical private names (_SHELL_LAUNCH_NAMES,
# _SHELL_FOREGROUND_PROCS, _SHELL_FOREGROUND_CLASSES), so every
# existing import and monkeypatch seam keeps working.
# ---------------------------------------------------------------------------

# Names that make a launch_application call a code execution,
# not an app launch: shells + script hosts (the v5.42 H11 set)
# plus — v5.46 item 3 (C1) — interpreters and code-exec hosts.
# The C1 reproduction: launch_application {app_name: "python3",
# arguments: '-c "open(...)"'} EXECUTED with no confirmation
# while run_command refused fail-closed, because this set had
# no interpreter names and arguments were never scrutinised.
SHELL_LAUNCH_NAMES = frozenset({
    "cmd", "command prompt", "powershell", "windows powershell",
    "pwsh", "terminal", "windows terminal", "bash", "wsl",
    "mshta", "cscript", "wscript",
    "cmd.exe", "wt.exe", "powershell.exe", "pwsh.exe", "bash.exe",
    "wsl.exe", "mshta.exe", "cscript.exe", "wscript.exe",
    # v5.46 (item 3, C1): interpreters / code-exec hosts.
    "python", "pythonw", "python3", "node",
    "regedit", "mmc", "msiexec", "rundll32", "regsvr32",
    "python.exe", "pythonw.exe", "python3.exe", "node.exe",
    "regedit.exe", "mmc.exe", "msiexec.exe", "rundll32.exe",
    "regsvr32.exe",
})

# True shells / terminal hosts only — editors (code.exe) are NOT
# shells and stay on the untainted normal path. v5.46 (item 7,
# A5/D-C3): windowsterminal.exe (the Windows Terminal host —
# wt.exe is only its launcher alias), conhost.exe and
# openconsole.exe (the console hosts behind every classic
# console window), powershell_ise.exe added.
SHELL_FOREGROUND_PROCS = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe", "bash.exe",
    "wsl.exe", "mintty.exe", "conemu64.exe", "alacritty.exe",
    "windowsterminal.exe", "conhost.exe", "openconsole.exe",
    "powershell_ise.exe",
})

# Foreground WINDOW classes that mean "this window is a console"
# even when the owning process name is unknown or generic
# (v5.46, item 7). Stored lowercased; comparisons lowercase the
# probed class, so matching is case-insensitive.
SHELL_FOREGROUND_CLASSES = frozenset({
    "consolewindowclass",            # classic console windows
    "cascadia_hosting_window_class",  # Windows Terminal
    "pseudoconsolewindow",
})


def _foreground_process_name() -> str | None:
    """Process name owning the foreground window, or None when it
    can't be determined (non-Windows, headless, API failure).
    Windows-only via ctypes + psutil; never raises. Seam: suites
    monkeypatch this function."""
    if not _is_windows():
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        import psutil

        return psutil.Process(pid.value).name()
    except Exception:  # noqa: BLE001 — unknown foreground
        return None


def _foreground_window_class() -> str | None:
    """Window class of the foreground window, or None when it
    can't be determined (off-Windows, no foreground window, API
    failure). Windows-only via ctypes GetForegroundWindow +
    GetClassNameW; never raises. Seam: suites monkeypatch this
    function."""
    if not _is_windows():
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        buf = ctypes.create_unicode_buffer(512)
        if not user32.GetClassNameW(hwnd, buf, 512):
            return None
        return buf.value or None
    except Exception:  # noqa: BLE001 — unknown foreground
        return None


def _shell_verdict(proc_name, window_class, is_windows) -> bool:
    """The pure foreground-shell decision (single source, shared
    by this module's foreground_is_shell and core/funnel/tools_gate_shell.py's
    _foreground_is_shell, which feeds it through the gate's own
    monkeypatchable probe seams):

      * process name in SHELL_FOREGROUND_PROCS -> shell
      * window class in SHELL_FOREGROUND_CLASSES (case-
        insensitive) -> shell
      * BOTH probes unknown (None/empty) on Windows -> shell
        (v5.46 item 7: blind on the platform the gate exists
        for = gate; fail-closed)
      * both probes unknown OFF Windows -> not a shell (no
        Windows shell exists there; headless/dev/test behaviour
        is unchanged)
      * a positively-known non-shell foreground -> not a shell,
        on any platform."""
    proc = str(proc_name).strip().lower() if proc_name else None
    cls = str(window_class).strip().lower() if window_class else None
    if proc and proc in SHELL_FOREGROUND_PROCS:
        return True
    if cls and cls in SHELL_FOREGROUND_CLASSES:
        return True
    if proc is None and cls is None and is_windows:
        return True
    return False


def foreground_is_shell() -> bool:
    """True when the foreground window is a shell/terminal — by
    process name OR window class — or when both probes are blind
    on Windows (fail-closed, v5.46). Never raises. The typing
    hands (core/desktop/control) call this through their own local seam;
    core/funnel/tools_gate_shell.py has its own probe seams feeding the same verdict."""
    try:
        return _shell_verdict(
            _foreground_process_name(),
            _foreground_window_class(),
            _is_windows())
    except Exception:  # noqa: BLE001 — never raise from a check
        return False
