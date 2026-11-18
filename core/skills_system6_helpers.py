import json
import sys
import time
from datetime import date


# ---------------------------------------------------------------------------
# Helpers (each pack carries its own copies — the house convention)
# ---------------------------------------------------------------------------

_NOT_WINDOWS = "this system is not Windows, so nothing was changed."


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _now() -> float:
    return time.time()


def _today_str() -> str:
    return date.today().isoformat()


def _clamp_int(value, lo: int, hi: int, default: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _tail(text: str, limit: int = 300) -> str:
    """The last meaningful line of a command's output, for reports."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""


def _duration_words(seconds: float) -> str:
    """'about 48 seconds' / 'about 2 minutes 5 seconds'."""
    total = int(round(seconds))
    if total < 90:
        return f"about {total} second{'s' if total != 1 else ''}"
    minutes, secs = divmod(total, 60)
    out = f"about {minutes} minute{'s' if minutes != 1 else ''}"
    if secs:
        out += f" {secs} second{'s' if secs != 1 else ''}"
    return out


def _state_path(filename: str):
    """A JSON state file in the product data dir, resolved fresh."""
    from .config import DATA_DIR

    return DATA_DIR / filename


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt state = default
        return default


def _save_json(path, data) -> bool:
    try:
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 — state is best-effort, never fatal
        return False


def _ps_events(command: str, timeout: int = 60):
    """The family PowerShell seam (skills_system_monitor._ps_structured):
    run a command whose output lines are fields joined by the unit
    separator; returns (returncode, rows, stderr). Every event-log /
    device query in this pack goes through this one helper so the
    whole OS surface can be faked in tests."""
    from . import skills_system_monitor

    return skills_system_monitor._ps_structured(command, timeout=timeout)


def _ps_run(command: str, timeout: int = 60, cancel_token=None):
    """Run one PowerShell command via skills_system_monitor._run
    (returncode, stdout, stderr) — for the acting tools.

    B20: ``cancel_token`` is threaded to _run, so a Stop pressed
    while the command runs ends it at the next poll."""
    from . import skills_system_monitor

    return skills_system_monitor._run(
        ["powershell", "-NoProfile", "-Command", command],
        timeout=timeout, cancel_token=cancel_token)
