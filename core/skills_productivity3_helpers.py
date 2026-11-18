"""Shared helpers for the Productivity-3 pack.

Phase A batch A29 part 1 (skills split): _coerce_int moved verbatim
from core/skills_productivity_day.py. It is shared by both families
(focus-day and life-admin, plan section 2 P3(c)), so it lives here
at the bottom of the dependency order: helpers <- parts <- facade.
"""

def _coerce_int(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))
