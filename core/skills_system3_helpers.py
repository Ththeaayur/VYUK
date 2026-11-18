"""Part of the skills_system_inventory split (Phase A batch A28): see
skills_system_inventory.py (facade) for the pack docstring and registration.
Move only — bodies verbatim from the original module.
"""

import subprocess
import sys


# ---------------------------------------------------------------------------
# Helpers (same shape as skills_system_monitor.py — packs stay self-contained)
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            text = f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
            return text.replace(".0 ", " ")
        size /= 1024
    return f"{size:.1f} TB"


_NOT_WINDOWS = "this system is not Windows, so nothing was changed."


def _run(args: list[str], timeout: int = 60,
         cancel_token=None) -> tuple[int, str, str]:
    """Run a command, capturing output. Returns (returncode, stdout, stderr);
    returncode is -1 when the command could not be started at all and -2 on
    timeout, with the reason in stderr. Never raises, never uses a shell.
    stdin is /dev/null so no tool can hang waiting for a console answer.

    B20: returncode -3 when the step's cancellation token
    cancelled the run — the caller maps -3 to a cancelled
    outcome (never to a timeout, never to success). With a
    token the child is polled (run_with_cancel) instead of
    blocked on in subprocess.run, so a Stop pressed while the
    command runs ends it at the next poll (~0.1 s). Without
    a token the path below is byte-identical to today."""
    if cancel_token is not None:
        from .funnel.tool_cancel import run_with_cancel
        _pop: dict = {"stdin": subprocess.DEVNULL}
        if _is_windows():
            _pop["creationflags"] = 0x08000000  # CREATE_NO_WINDOW — no console flash
        return run_with_cancel(args, timeout, cancel_token, **_pop)
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "stdin": subprocess.DEVNULL,
    }
    if _is_windows():
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW — no console flash
    try:
        completed = subprocess.run(args, **kwargs)
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except FileNotFoundError:
        return -1, "", f"{args[0]} was not found on this PC."
    except subprocess.TimeoutExpired:
        return -2, "", f"{args[0]} took longer than {timeout} seconds and was stopped."
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


def _tail(text: str, limit: int = 600) -> str:
    """The last few meaningful lines of a command's output, for honest reports."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    joined = "\n".join(lines[-6:])
    return joined[:limit]


_SEP = "\x1f"  # unit separator: fields in PowerShell output ride on this,
               # never on localised labels or column spacing (v4.7 rule).


def _ps_structured(command: str, timeout: int = 60,
                   cancel_token=None) -> tuple[int, list[list[str]], str]:
    """Run a PowerShell command whose output lines are fields joined by
    _SEP, and return (returncode, parsed rows, stderr). Parsing is purely
    structural — no localised header or label is ever matched.

    B20: ``cancel_token`` is threaded to _run, so a Stop pressed
    while the command runs ends it at the next poll."""
    rc, out, err = _run(["powershell", "-NoProfile", "-Command", command],
                        timeout=timeout, cancel_token=cancel_token)
    rows: list[list[str]] = []
    for line in (out or "").splitlines():
        if line.strip():
            rows.append(line.split(_SEP))
    return rc, rows, err


def _field(row: list[str], idx: int) -> str:
    """One field of a structured row, '' when the row is short."""
    return row[idx].strip() if len(row) > idx else ""


def _bool_word(text: str) -> str:
    """A PowerShell True/False field as plain English."""
    lowered = (text or "").strip().lower()
    if lowered == "true":
        return "on"
    if lowered == "false":
        return "off"
    return "unknown"


def _looks_admin_denied(text: str) -> bool:
    lowered = (text or "").lower()
    return ("access" in lowered and "denied" in lowered) \
        or "administrator" in lowered or "elevat" in lowered \
        or "permission" in lowered or "0x80041003" in lowered
