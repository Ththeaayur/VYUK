"""Shared helpers for the System & Apps pack.

Phase A batch A31 part 1 (skills split): _is_windows, _human_size,
_NOT_WINDOWS, _run and _tail moved verbatim from
core/skills_system.py. They are shared by all three families
(apps-startup, services-power, env-disk, plan section 2 P3(c)), so
they live here at the bottom of the dependency order:
helpers <- parts <- facade. Behaviour unchanged.
"""

import subprocess
import sys

def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


_NOT_WINDOWS = "this system is not Windows, so nothing was changed."


def _run(args: list[str], timeout: int = 60,
         cancel_token=None) -> tuple[int, str, str]:
    """Run a command, capturing output. Returns (returncode, stdout, stderr);
    returncode is -1 when the command could not be started at all and -2 on
    timeout, with the reason in stderr. Never raises.

    B20: returncode -3 when the step's cancellation token
    cancelled the run — the caller maps -3 to a cancelled
    outcome (never to a timeout, never to success). With a
    token the child is polled (run_with_cancel) instead of
    blocked on in subprocess.run, so a Stop pressed while the
    command runs ends it at the next poll (~0.1 s). Without
    a token the path below is byte-identical to today."""
    if cancel_token is not None:
        from .funnel.tool_cancel import run_with_cancel
        _pop: dict = {}
        if _is_windows():
            _pop["creationflags"] = 0x08000000  # CREATE_NO_WINDOW — no console flash
        return run_with_cancel(args, timeout, cancel_token, **_pop)
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
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
    out = "\n".join(lines[-4:])
    return out[:limit]
