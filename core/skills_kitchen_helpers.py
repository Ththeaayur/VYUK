"""Shared kitchen helpers (moved verbatim from core/skills_kitchen.py).

Small number/amount formatting helpers used by both the scale and
plan impl modules. No behaviour change; see the split batch report.
"""

from fractions import Fraction


def _coerce_float(value):
    """A float, or None when the value isn't numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_num(value: float) -> str:
    """'225' / '33.5' — plain number, no trailing zeros."""
    text = f"{value:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _qty_text(value: float) -> str:
    """A kitchen amount: whole numbers plain, common fractions
    as fractions ('1 1/2', '2/3'), anything else a trimmed
    2-decimal number."""
    frac = Fraction(value).limit_denominator(8)
    if abs(float(frac) - value) > 0.02:
        return _fmt_num(round(value, 2))
    whole, rem = divmod(frac.numerator, frac.denominator)
    if rem == 0:
        return str(whole)
    if whole:
        return f"{whole} {rem}/{frac.denominator}"
    return f"{rem}/{frac.denominator}"


def _duration_words(minutes: float) -> str:
    """'65 minutes' / '1 hour 35 minutes' / '2 hours'."""
    total = int(round(minutes))
    hours, mins = divmod(total, 60)
    if hours and mins:
        return (f"{hours} hour{'s' if hours != 1 else ''} "
                f"{mins} minutes")
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{total} minutes"
