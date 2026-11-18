"""
Presence pack (app level) for VYUK, v2.1: Windows auto-start and
single-instance enforcement.

- Auto-start uses the per-user Run key
  (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, value
  "Nova" — the pre-rename plumbing name, kept by design so
  existing installs keep their auto-start entry; see the naming
  keep-list in README.md) — no admin rights, no service, no
  installer magic. The
  command launches main.py with --minimized (main.py turns that into
  the NOVA_START_MINIMIZED=1 env var the window reads), through
  pythonw.exe when it sits next to the interpreter so no console
  window flashes at sign-in.
- Single instance uses a QLockFile in the system temp dir. The
  caller must keep the returned object alive for the life of the
  process; dropping it releases the lock.

Everything here degrades politely: on a non-Windows machine (or if
winreg / PySide6 are unavailable) the functions return a clear
answer instead of raising — presence niceties must never be the
reason VYUK fails to start.

Standard library only at import time; winreg and PySide6 are
imported lazily inside the functions that need them.
"""

import sys
import tempfile
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "Nova"
_LOCK_NAME = "Nova_instance.lock"


def autostart_command() -> str:
    """The exact command the Run key should execute at sign-in."""
    from .config import PROJECT_ROOT

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else exe
    main_py = PROJECT_ROOT / "main.py"
    return f'"{interpreter}" "{main_py}" --minimized'


def autostart_enabled() -> bool | None:
    """True/False on Windows; None when not on Windows (or winreg is
    unavailable) — callers render None as "not available here"."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            try:
                winreg.QueryValueEx(key, _VALUE_NAME)
                return True
            except FileNotFoundError:
                return False
    except FileNotFoundError:
        return False
    except OSError:
        return None


def set_autostart(enabled: bool) -> str:
    """Turn auto-start on/off. Returns a human sentence; never raises."""
    if sys.platform != "win32":
        return "Auto-start is a Windows feature — this PC isn't running Windows."
    try:
        import winreg
    except ImportError:
        return "Auto-start is unavailable here — the Windows registry module (winreg) is missing."
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
                return "Auto-start is on — VYUK will launch (minimized) when you sign in to Windows."
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass  # already off — deleting a missing value is success
            return "Auto-start is off — VYUK will no longer launch when you sign in."
    except OSError as e:
        return f"Could not change auto-start: {e}"


class _FailOpenLock:
    """Truth stand-in returned when QLockFile can't even be imported.

    Presence is a nicety: if PySide6 is somehow absent, launching a
    (theoretically duplicate) VYUK beats refusing to launch at all.
    """


def acquire_single_instance():
    """Try to claim VYUK's single-instance lock.

    Returns the live lock object on success (KEEP IT REFERENCED —
    garbage-collecting it releases the lock), None when another
    instance already holds it. If PySide6 is unavailable, returns a
    truthy dummy (fail-open). The lock is marked never-stale
    (setStaleLockTime(0)): if a crashed VYUK leaves the lock file
    behind, see TROUBLESHOOTING — the fix is deleting
    Nova_instance.lock from the temp dir.
    """
    try:
        from PySide6.QtCore import QLockFile
    except Exception:  # noqa: BLE001 — fail-open by design, see docstring
        return _FailOpenLock()
    lock = QLockFile(str(Path(tempfile.gettempdir()) / _LOCK_NAME))
    lock.setStaleLockTime(0)
    if lock.tryLock(100):
        return lock
    return None
