"""Radix-shaped 12 steps (split from core/tokens.py, B6)."""

from __future__ import annotations

# INTENTIONAL (tokens split B6): cross-part names live in
# tokens_theme; this module imports exactly the ones it uses.
from .tokens_theme import _mix, build_python_tokens


# ------------------------------------------------------------- Radix 12 steps
#: Step roles per the Radix colour docs. The VALUES in
#: :func:`radix_steps` are local derivations — see that docstring.
RADIX_ROLES: dict[int, str] = {
    1: "app background",
    2: "subtle background",
    3: "component background",
    4: "hover component background",
    5: "active component background",
    6: "subtle borders and separators",
    7: "interactive component borders",
    8: "hover interactive component borders",
    9: "solid backgrounds",
    10: "hover solid backgrounds",
    11: "low-contrast text",
    12: "high-contrast text",
}


def radix_steps(theme_name: str) -> dict[int, dict]:
    """Radix-shaped 12 steps for a theme (roles per Radix, values local).

    Derivation from the theme's own resolved tokens (all mixes use
    the same mix rule as the resolver):

      1 bg, 2 surface_1, 3 surface_2,
      4 surface_2 +10% accent, 5 surface_2 +18% accent,
      6 surface_2 +14% white, 7 surface_2 +35% accent,
      8 surface_2 +50% accent,
      9 accent, 10 accent_hover,
      11 accent +55% white, 12 text.

    These are NOT Radix's published per-hue scale values. A theme's
    Radix-shaped steps are useful for component-state authoring; they
    must not be shipped or quoted as Radix scales.
    """
    tok = build_python_tokens(theme_name)
    s2 = tok["surface_2"]
    accent = tok["accent"]
    values = {
        1: tok["bg"],
        2: tok["surface_1"],
        3: s2,
        4: _mix(s2, accent, 0.10),
        5: _mix(s2, accent, 0.18),
        6: _mix(s2, "#FFFFFF", 0.14),
        7: _mix(s2, accent, 0.35),
        8: _mix(s2, accent, 0.50),
        9: accent,
        10: tok["accent_hover"],
        11: _mix(accent, "#FFFFFF", 0.55),
        12: tok["text"],
    }
    return {step: {"hex": values[step], "role": RADIX_ROLES[step]}
            for step in range(1, 13)}
