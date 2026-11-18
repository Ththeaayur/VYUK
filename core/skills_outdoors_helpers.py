"""Outdoors shared helpers — split from core/skills_outdoors.py (skills split Phase A, batch A10).
Move only: the code below is verbatim from the original file apart
from the module-level import lines at the top.
"""

# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _coerce_float(value):
    """A float, or None when the value isn't numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_num(value: float) -> str:
    """'1,250' / '7.5' — plain number, trailing '.0' dropped."""
    text = f"{value:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _duration_words(minutes: float) -> str:
    """'50 minutes' / '1 hour 20 minutes' / '2 hours'."""
    total = int(round(minutes))
    hours, mins = divmod(total, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins or not parts:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts)


def _clock(minutes: float) -> str:
    """Minutes since midnight as 'HH:MM', wrapped into the day."""
    total = int(round(minutes)) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------------------
# Persistence seam (plants + pet feeding logs)
# ---------------------------------------------------------------------------


def _store():
    """The memory store singleton — this pack's only persistence
    seam. Tests substitute a temp MemoryStore by replacing this
    function; handlers never touch the singleton directly."""
    from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    return memory_store


def _facts(category: str) -> list:
    """Every stored fact in one category ({id, key, value, ...})."""
    return [f for f in _store().get_all_facts()
            if f.get("category") == category]


_PLANTS_CATEGORY = "plants"
_PET_CATEGORY = "pet_feeding"

