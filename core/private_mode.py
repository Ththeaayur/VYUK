"""Private mode (loophole batch 5, #5).

One helper, one question: is a private turn in progress right
now? Every store a turn can write consults it at write time
(the transcript, the receipts, the audit chain, the Activity
feed, the cost ledger, the learn path) and writes nothing while
it is on. The mode is read live from the app config each time
— a turn that flips it on mid-turn suppresses its own stores,
and a turn that flips it off writes normally again.

FAIL-CLOSED: an unreadable config reads as private mode ON.
A config that cannot be read must never let a turn be recorded
at exactly the moment something is already wrong.
"""

from __future__ import annotations


def private_mode_on() -> bool:
    """True while private mode is on (a turn writes nothing).

    Never raises: a broken read reads as ON (fail-closed).
    """
    try:
        from .config import config as _app_config

        return bool(getattr(_app_config, "private_mode", False))
    except Exception:  # noqa: BLE001 — unreadable config: private
        return True
