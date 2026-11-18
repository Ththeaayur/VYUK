"""Protected-credential-path rules — split from updater.py (B24)."""
from __future__ import annotations

from .updater_paths import _PROTECTED_BASENAMES  # INTENTIONAL: shared constant


# ---------------------------------------------------------------------------
# Gate B helpers — protected paths, staged-manifest verification,
# apply journal, and backup restore (master audit P0-3/P1-4/P0-4).
# ---------------------------------------------------------------------------


def _is_protected_rel(rel: str) -> bool:
    """True when `rel` (package-root-relative, posix) names a
    protected credential file. Any basename match counts, at any
    depth: no shipped file has these names, so a staged "gui/.env"
    could only plant or clobber credentials. ".env.example" is
    the shipped template and is explicitly allowed."""
    base = rel.rsplit("/", 1)[-1].lower()
    if base in _PROTECTED_BASENAMES:
        return True
    return base.startswith(".env.") and base != ".env.example"


def _protected_in_inventory(inventory) -> list[str]:
    """Sorted protected rels in an inventory (or staged file set),
    plus any data/-rooted rel (user data is never update payload).
    Empty means clean."""
    bad = [rel for rel in inventory
           if _is_protected_rel(rel) or rel == "data"
           or rel.startswith("data/")]
    return sorted(bad)
