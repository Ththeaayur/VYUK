# Brain helpers (B5 split): module-level helpers moved out of
# core/brain/brain.py verbatim; used by the session mixins.

def _bump_epoch(reason: str) -> None:
    """v5.48 (programme item 20): advance the session epoch when
    this proxy swaps or stops the live inner session, voiding any
    confirmation approval still in flight from the previous
    session (core/session_epoch). Guarded: epoch bookkeeping
    never breaks a session swap."""
    try:
        from .. import session_epoch  # Phase 3 B2h: brain hub moved to core/brain/brain.py

        session_epoch.bump_epoch(reason)
    except Exception:  # noqa: BLE001
        pass


def _ambient_packet_text() -> str:
    """The NOSE ambient packet (v5.44, N4), lazily imported
    (house style): a missing/failing ambient module degrades to
    "" — today's behaviour — and never breaks a session."""
    try:
        from . import ambient  # Phase 3 B1b: ambient moved into core/brain/ — intra-package now

        return ambient.ambient_packet() or ""
    except Exception:  # noqa: BLE001 — ambient is a nicety
        return ""
