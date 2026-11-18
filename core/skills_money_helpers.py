"""Part of the skills_money split (Phase A batch A14): see
skills_money.py (facade) for the pack docstring and registration.
Move only — bodies verbatim from the original module.
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


def _money(amount: float, currency: str = "") -> str:
    """'1,234.50', or '₹1,234.50' when a currency label is given."""
    text = f"{amount:,.2f}"
    return f"{currency}{text}" if currency else text


def _plain_num(amount: float) -> str:
    """A stored numeric string: no commas, no trailing zeros
    ('499', '499.5') — must stay float()-parseable."""
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def _per_unit(amount: float, currency: str = "") -> str:
    """A per-unit price: 2 decimals normally, 4 when tiny —
    per-gram prices would vanish at 2."""
    text = f"{amount:,.2f}" if amount >= 0.1 else f"{amount:,.4f}"
    return f"{currency}{text}" if currency else text


def _months_words(months: int) -> str:
    """34 → '2 years and 10 months'; 12 → '1 year'; 1 → '1 month'."""
    years, rem = divmod(int(months), 12)
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if rem:
        parts.append(f"{rem} month{'s' if rem != 1 else ''}")
    return " and ".join(parts) if parts else "less than a month"


# ---------------------------------------------------------------------------
# Seams — persistence sits behind this one helper so tests can
# monkeypatch a single name.
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
