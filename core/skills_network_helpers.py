"""Shared helpers for the skills_network split (Phase A batch A23).

Moved verbatim from core/skills_network.py: the names every impl
module needs. Dependency bottom: helpers <- parts <- facade (§2 P3).
"""

import subprocess
import sys


def _is_windows() -> bool:
    return sys.platform.startswith("win")


_NOT_WINDOWS = "this system is not Windows, so nothing was changed."


def _run(argv: list[str], timeout: int = 20) -> tuple[int, str]:
    """Run argv (never a shell), return (returncode, combined output)."""
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out
