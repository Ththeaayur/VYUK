"""
VYUK's build version — the single source of truth (v5.10).

`VERSION` is what core/health/updater.py compares the update feed against
and what the staging/backup bookkeeping records. The Settings About
label derives from this value (v5.3) — this module is the only place
the version is written down.

Version strings are dotted numerics ("5.2.0"). Comparison is
numeric-tuple based — "5.10.0" is NEWER than "5.9.0" — via
`parse_version` below; never compare version strings lexically.
"""

from __future__ import annotations

VERSION = "6.3.0"


def parse_version(text: object) -> tuple[int, ...] | None:
    """Parses a dotted-numeric version string into a comparable
    tuple of ints, or None when the text isn't one. Each component
    must be a non-negative integer; anything else ("5.x", "v5.2",
    "", None) is unparseable rather than silently coerced."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(".")
    if not parts or any(not p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def version_newer(candidate: str, current: str) -> bool:
    """True when `candidate` is strictly newer than `current`.
    Unparseable input is never "newer" — a garbled feed must not
    trigger an update. Shorter tuples pad with zeros, so "5.2"
    equals "5.2.0"."""
    cand = parse_version(candidate)
    cur = parse_version(current)
    if cand is None or cur is None:
        return False
    width = max(len(cand), len(cur))
    cand += (0,) * (width - len(cand))
    cur += (0,) * (width - len(cur))
    return cand > cur
