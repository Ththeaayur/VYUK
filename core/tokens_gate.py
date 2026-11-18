"""WCAG contrast + D3 migration + D4 accent gate (B6 split)."""

from __future__ import annotations

# INTENTIONAL (tokens split B6): cross-part names live in
# tokens_theme; this module imports exactly the ones it uses.
from .tokens_theme import (
    _BG,
    _HEX_RE,
    _hex_rgb,
    CYAN_ACCENT,
)


# ------------------------------------------------- Command Deck (Phase B) pure helpers
# Stdlib-only, no Qt: importable on both interpreters. The WCAG
# formula here deliberately parallels core/theme_packs.py's
# contrast_ratio (same formula, same 4.5 floor); the Phase B test
# cross-checks the two implementations agree.


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance of a #RRGGBB colour."""
    r, g, b = _hex_rgb(hex_color)

    def linear(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two #RRGGBB colours."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


#: Legacy theme names that migrate on read (guide D3). Anything not
#: listed passes through untouched — including "Custom".
_LEGACY_THEME_NAMES = {
    "indigo teal": "Cyan",
}


def migrate_theme_name(name):
    """Map a stored theme name to its current name (guide D3).

    Saved "Indigo Teal" becomes "Cyan"; every other value passes
    through unchanged (including None and non-strings). Matching is
    case-insensitive on the stripped name; the returned name is the
    canonical spelling. Call at every theme-read site so an existing
    install's stored value heals to Cyan on first read.
    """
    if not isinstance(name, str):
        return name
    return _LEGACY_THEME_NAMES.get(name.strip().lower(), name)


#: D4: a custom accent is usable only if BG-coloured button text on
#: it reads at WCAG AA (4.5:1) — the direction the tree already
#: treats as the floor (see the pressed-contrast comment in
#: gui/style.py). Anything else falls back to the cyan role.
MIN_CUSTOM_ACCENT_CONTRAST = 4.5


def gate_custom_accent(hexv: str) -> str:
    """Return a custom accent hex, or the cyan fallback (guide D4).

    A valid #RRGGBB accent whose BG-text contrast is >= 4.5:1 is
    kept (uppercased); anything invalid or below the floor returns
    CYAN_ACCENT. The stored value is never silently rewritten by
    this helper — callers store the returned (effective) value.
    """
    if not isinstance(hexv, str) or not _HEX_RE.match(hexv.strip()):
        return CYAN_ACCENT
    candidate = hexv.strip().upper()
    if contrast_ratio(_BG, candidate) >= MIN_CUSTOM_ACCENT_CONTRAST:
        return candidate
    return CYAN_ACCENT
