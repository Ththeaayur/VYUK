"""Shared helpers and seams for the skills_habits pack.

House helpers, persistence seams and the one constant shared across
impl groups (_MAX_NOTE_CHARS, used by both habit_track and mood_log).
Dependency bottom of the split: helpers <- tracking, mind <- review.
"""

import datetime
import json

# INTENTIONAL: _MAX_NOTE_CHARS moved here from the habits section of
# the old module because two impl groups use it (habit_track in
# tracking, mood_log in mind); §2 P3(c) shared-name rule.
_MAX_NOTE_CHARS = 120


def _coerce_int(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _coerce_float(value):
    """A float, or None when the value isn't numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_amount(value: float) -> str:
    """'1,299.5' / '100' — plain number, no currency symbol."""
    text = f"{value:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _today() -> datetime.date:
    return datetime.date.today()


def _duration_words(seconds: float) -> str:
    """'2 hours 5 minutes' / '45 minutes' / 'under a minute'."""
    total_min = int(round(max(0.0, seconds) / 60.0))
    if total_min < 1:
        return "under a minute"
    hours, minutes = divmod(total_min, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


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


def _screen_time_path():
    """screen_time.json in the app data dir — the same file
    skills_system_health resolves via core/config.py's DATA_DIR,
    resolved fresh on every call."""
    from .config import DATA_DIR
    return DATA_DIR / "screen_time.json"


def _read_screen_time():
    """The parsed screen-time state dict, or None when the file
    doesn't exist or can't be read. Read-only: this pack never
    writes the counter."""
    try:
        text = _screen_time_path().read_text(encoding="utf-8")
        state = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    return state if isinstance(state, dict) else None


def _unique_key(category: str, base: str) -> str:
    """base, or base with a counter suffix, that isn't taken in
    the category — repeated logs never overwrite each other."""
    candidate = base
    suffix = 1
    while _store().get_fact(category, candidate) is not None:
        suffix += 1
        candidate = f"{base}|{suffix}"
    return candidate
