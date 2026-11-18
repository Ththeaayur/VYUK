import datetime
import json


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


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


def _coerce_bool(value) -> bool:
    """True for real booleans and the usual spoken spellings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in (
        "true", "yes", "1", "given", "done")


def _fmt_amount(value: float) -> str:
    """'1,299.5' / '100' — plain number, no currency symbol."""
    text = f"{value:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _clean(text) -> str:
    """Collapse whitespace in a free-text field."""
    return " ".join(str(text or "").split())


def _parse_date(text):
    """A date from a YYYY-MM-DD string, or None."""
    try:
        return datetime.date.fromisoformat(str(text or "").strip())
    except ValueError:
        return None


def _bad_date_line(text) -> str:
    return (f"I couldn't read that date ('{text}') — use "
            "YYYY-MM-DD, e.g. '2027-03-01'. Nothing was saved.")


def _add_months(day: datetime.date, months: int) -> datetime.date:
    """The date `months` calendar months after `day`, the day
    of month clamped to the target month's length (31 Jan + 1
    month lands on the last day of February, never March)."""
    total = (day.year * 12 + (day.month - 1)) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    if month == 12:
        first_next = datetime.date(year + 1, 1, 1)
    else:
        first_next = datetime.date(year, month + 1, 1)
    last_day = (first_next - datetime.timedelta(days=1)).day
    return datetime.date(year, month, min(day.day, last_day))


def _days_words(delta_days: int) -> str:
    """'today' / 'in 1 day' / 'in 20 days' / '1 day ago' /
    '20 days ago' for a signed day difference from today."""
    if delta_days == 0:
        return "today"
    if delta_days > 0:
        return (f"in {delta_days} day"
                f"{'s' if delta_days != 1 else ''}")
    ago = -delta_days
    return f"{ago} day{'s' if ago != 1 else ''} ago"


def _broken_note(broken: int, noun: str) -> str:
    """The standing disclosure for unreadable stored records."""
    return (f"({broken} stored {noun}"
            f"{'s' if broken != 1 else ''} couldn't be read "
            "and were skipped.)")


# ---------------------------------------------------------------------------
# The seam — persistence sits behind this one function so tests
# can substitute an in-memory store.
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


def _save(category: str, key: str, payload: dict) -> bool:
    """Store one payload; True when a fact already lived under
    that key (an update), False for a new record."""
    existed = _store().get_fact(category, key) is not None
    _store().save_fact(category, key, json.dumps(payload))
    return existed


def _load(category: str, required: tuple) -> tuple[list, int]:
    """(entries, broken_count): payloads parsed from one
    category's facts, each entry carrying its fact key as
    '_key'. A fact counts as broken when its payload won't
    parse or one of the `required` fields fails its check —
    required maps field name to a predicate."""
    entries = []
    broken = 0
    for fact in _facts(category):
        try:
            stored = json.loads(fact.get("value") or "")
            if not isinstance(stored, dict):
                raise ValueError
            for field, ok in required.items():
                if not ok(stored.get(field)):
                    raise ValueError
            stored["_key"] = str(fact.get("key") or "")
            entries.append(stored)
        except Exception:  # noqa: BLE001
            broken += 1
    return entries, broken


def _nonempty(value) -> bool:
    return bool(str(value or "").strip())


_MAX_NAME_CHARS = 80
_MAX_TEXT_CHARS = 200
