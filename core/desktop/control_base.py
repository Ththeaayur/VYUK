"""control_base concern extracted from core/control.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/control."""

import sys
from pathlib import Path


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"  # Phase 3 D2b: one level deeper in core/desktop/


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _routed_backend():
    """v5.50 (programme items 41/42): when a NON-Windows platform
    backend serves this process — the scripted FakeBackend under
    VYUK_PLATFORM=fake, or an EXPERIMENTAL native backend
    (darwin/linux) — the hands/windows surface is the backend's,
    and the public functions below delegate to it. This module's
    legacy bodies ARE the Windows implementation (the
    WindowsBackend delegates back into them), so when the Windows
    backend serves (backend.name == "windows") this returns None
    and every function runs its pre-v5.50 body unchanged —
    Windows behaviour is byte-identical. Resolved lazily at call
    time, like the WindowsBackend's own delegation."""
    try:
        from ..platform import get_backend  # Phase 3 D2b: platform stays flat in core/

        backend = get_backend()
    except Exception:  # noqa: BLE001 — seam failure = legacy path
        return None
    return backend if backend.name != "windows" else None


def _truncate(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


def _input_blocked() -> str | None:
    """The v4.3 failsafe check for the legacy hands (v4.4).

    core/desktop/cursor.py and core/desktop/uia.py have always refused to dispatch
    while the failsafe watchdog (core/failsafe.py) is tripped; the
    older handlers in this module bypassed it — an audit MAJOR,
    since "her hands stop when the failsafe trips" was then false
    for the most-used input tools. Returns the refusal message when
    tripped, else None. Any import/check failure reads as "not
    blocked": the failsafe module failing must not disable the hands
    wholesale (cursor.py's convention, copied)."""
    try:
        from .. import failsafe  # Phase 3 D2b: failsafe stays flat in core/

        if failsafe.tripped():
            return (
                "VYUK's failsafe has tripped — her hands are stopped "
                "until a human re-arms them from the tray menu."
            )
    except Exception:  # noqa: BLE001
        pass
    return None
