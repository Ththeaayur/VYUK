"""Part of the skills_system_inventory split (Phase A batch A28): see
skills_system_inventory.py (facade) for the pack docstring and registration.
Move only — bodies verbatim from the original module.
"""

# INTENTIONAL: cross-part names — power functions resolve these bare
# names in their defining module's globals, so they are imported here,
# not relied on from the facade (bare-name rule, plan §2 P3).
from .skills_system3_helpers import (
    _NOT_WINDOWS,
    _is_windows,
    _looks_admin_denied,
    _run,
    _tail,
)


# ---------------------------------------------------------------------------
# POWER — what is keeping the PC awake / which sleep states exist
# ---------------------------------------------------------------------------


def _format_power_requests(rc: int, out: str, err: str) -> str:
    """Format `powercfg /requests` output. Pure — tested directly.
    Conservative: entries are anything that is neither a section
    header nor a 'None.' line."""
    text = out or err
    if rc != 0:
        if _looks_admin_denied(text):
            return ("Couldn't read power requests — powercfg /requests "
                    "needs an administrator context, which VYUK isn't "
                    "running in.")
        return (f"Couldn't read power requests: "
                f"{_tail(text) or 'powercfg failed.'}")
    entries: list[str] = []
    cleaned: list[str] = []
    for raw in (out or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        cleaned.append(line)
        if line.endswith(":") or line.lower() == "none.":
            continue
        entries.append(line)
    if not entries:
        return ("Nothing is keeping your PC awake — every power "
                "request category (display, system, away mode, "
                "execution) reports None.")
    body = "\n".join(cleaned)
    cap = 1200
    note = ""
    if len(body) > cap:
        body = body[:cap].rstrip()
        note = ("\n…(powercfg's full report was longer; "
                "the rest is truncated.)")
    return f"Power requests keeping the PC awake:\n{body}{note}"


def power_requests() -> str:
    """What currently blocks sleep, per powercfg /requests. Read-only."""
    if not _is_windows():
        return f"powercfg only exists on Windows — {_NOT_WINDOWS}"
    rc, out, err = _run(["powercfg", "/requests"], timeout=30)
    return _format_power_requests(rc, out, err)


def _format_sleep_states(rc: int, out: str, err: str) -> str:
    """Format `powercfg /a` output, cleaned and capped. Pure."""
    if rc != 0:
        return (f"Couldn't read the sleep states: "
                f"{_tail(err or out) or 'powercfg failed.'}")
    cleaned = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    if not cleaned:
        return "powercfg reported no sleep-state information."
    body = "\n".join(cleaned)
    cap = 1500
    if len(body) > cap:
        body = body[:cap].rstrip() + \
            "\n…(the report was longer; the rest is truncated.)"
    return f"Sleep states on this PC:\n{body}"


def sleep_states() -> str:
    """Which sleep states this PC supports, per powercfg /a. Read-only."""
    if not _is_windows():
        return f"powercfg only exists on Windows — {_NOT_WINDOWS}"
    rc, out, err = _run(["powercfg", "/a"], timeout=30)
    return _format_sleep_states(rc, out, err)


# ---------------------------------------------------------------------------
# HIBERNATE + FAST STARTUP — registry-backed power settings
# ---------------------------------------------------------------------------

_POWER_KEY = r"SYSTEM\CurrentControlSet\Control\Power"
_SESSION_POWER_KEY = (r"SYSTEM\CurrentControlSet\Control"
                      r"\Session Manager\Power")


def _read_dword(subkey: str, value_name: str):
    """(value, error) for an HKLM DWORD; value is None when absent."""
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey)
    except OSError as e:
        return None, f"couldn't open the registry key: {e}"
    try:
        value, _type = winreg.QueryValueEx(key, value_name)
        return int(value), ""
    except FileNotFoundError:
        return None, ""
    except OSError as e:
        return None, f"couldn't read the value: {e}"
    finally:
        winreg.CloseKey(key)


def hibernate_setting(action: str = "status") -> str:
    """Read or set hibernation (powercfg /hibernate + registry state)."""
    act = (action or "status").strip().lower()
    if act not in ("on", "off", "status"):
        return (f"Unknown hibernate action: '{action}'. "
                "Use one of: on, off, status.")
    if not _is_windows():
        return f"Hibernation is a Windows feature — {_NOT_WINDOWS}"
    if act == "status":
        value, problem = _read_dword(_POWER_KEY, "HibernateEnabled")
        if problem:
            return f"Couldn't read the hibernate setting — {problem}"
        if value is None:
            return ("The registry doesn't record a HibernateEnabled "
                    "value, so VYUK can't confirm whether hibernation "
                    "is on — Windows only writes it once the setting "
                    "has been configured.")
        return f"Hibernation is {'on' if value else 'off'} on this PC."
    rc, out, err = _run(["powercfg", "/hibernate", act], timeout=30)
    if rc != 0:
        detail = _tail(err or out) or "powercfg failed."
        if _looks_admin_denied(detail):
            return ("Couldn't change hibernation — that needs an "
                    "administrator context, which VYUK isn't running "
                    f"in. Windows said: {detail}")
        return f"Couldn't change hibernation: {detail}"
    value, _problem = _read_dword(_POWER_KEY, "HibernateEnabled")
    if value is not None:
        state = "on" if value else "off"
        if state == act:
            return f"Hibernation is now {act} (confirmed in the registry)."
        return (f"powercfg accepted the change, but the registry now "
                f"reports hibernation as {state} — check before "
                "trusting it.")
    return (f"Hibernation {act} command accepted by powercfg; the "
            "registry doesn't expose a state value to confirm against.")


def fast_startup_control(action: str = "status") -> str:
    """Read or set Fast Startup (HiberbootEnabled) in the registry."""
    act = (action or "status").strip().lower()
    if act not in ("status", "on", "off"):
        return (f"Unknown fast-startup action: '{action}'. "
                "Use one of: status, on, off.")
    if not _is_windows():
        return f"Fast Startup is a Windows feature — {_NOT_WINDOWS}"
    if act == "status":
        value, problem = _read_dword(_SESSION_POWER_KEY, "HiberbootEnabled")
        if problem:
            return f"Couldn't read the fast-startup setting — {problem}"
        if value is None:
            return ("Fast Startup isn't explicitly set in the "
                    "registry, so the Windows default applies — "
                    "fast startup ON, as on most installs.")
        return f"Fast Startup is {'on' if value else 'off'} on this PC."
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             _SESSION_POWER_KEY, 0, winreg.KEY_SET_VALUE)
    except PermissionError:
        return ("Couldn't change Fast Startup — writing that "
                "registry key needs an administrator context, which "
                "VYUK isn't running in.")
    except OSError as e:
        return f"Couldn't open the fast-startup registry key: {e}"
    try:
        winreg.SetValueEx(key, "HiberbootEnabled", 0, winreg.REG_DWORD,
                          1 if act == "on" else 0)
    except PermissionError:
        return ("Couldn't change Fast Startup — writing that "
                "registry key needs an administrator context, which "
                "VYUK isn't running in.")
    except OSError as e:
        return f"Couldn't write the fast-startup setting: {e}"
    finally:
        winreg.CloseKey(key)
    value, _problem = _read_dword(_SESSION_POWER_KEY, "HiberbootEnabled")
    if value is not None:
        state = "on" if value else "off"
        if state == act:
            return f"Fast Startup is now {act} (confirmed in the registry)."
        return (f"The registry write ran, but the value now reads "
                f"{state} — check before trusting it.")
    return (f"Fast Startup set to {act}; the value couldn't be "
            "read back to confirm.")
