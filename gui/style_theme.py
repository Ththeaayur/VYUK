"""Accent themes + stylesheet builder (split from gui/style.py, GUI split #6).

Accent themes (THEMES), theme derivation/resolution, colour remapping,
font scaling, density, and build_stylesheet().
"""

import colorsys
import re

from .style_sheet import PREMIUM_STYLE, _QSS, _TOKENS
from .style_tokens import (
    ACCENT,
    ACCENT_2,
    BG,
    SURFACE_1,
    SURFACE_2,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
)

# ------------------------------------------------------------- accent themes
# PREMIUM_STYLE above is the house sheet (the Indigo Teal palette),
# generated once — it is what every existing caller applies, and it
# does not change. build_stylesheet() layers three things on top of
# that same sheet:
#
#   * Accent themes. A theme is a small dict of hex colours. The
#     house sheet spells the accent family in a handful of known
#     ways — the two accent hexes, their rgb triplets at many
#     alphas, and the hand-tuned hover / pressed / pale / disabled
#     derivatives — so a themed build rewrites exactly those
#     spellings, in a single pass, and nothing else can drift.
#   * The glass card treatment: themed builds brighten the cards'
#     top hairline (CARD_TOP) and give the card gradients a soft
#     top sheen — white-alpha layering, the QSS stand-in for an
#     inner glow (Qt has no box-shadow), so it follows every
#     palette, Mono included.
#   * font_scale and density: pure value transforms on the
#     generated sheet — one multiplies every font-size value, the
#     other every padding value — so they provably touch nothing
#     else. At their defaults (and with no theme) build_stylesheet()
#     returns PREMIUM_STYLE itself, byte for byte.

# The house spellings a themed build rewrites.
_HOUSE_ACCENT_HOVER = "#5DDEFF"
_HOUSE_ACCENT_2_HOVER = "#64A2FF"
_HOUSE_ACCENT_PRESSED = "#2FBCE0"
_HOUSE_ACCENT_2_PRESSED = "#367AE0"
_HOUSE_PALE = "#E6FBFF"  # the result-card title tint
_HOUSE_DISABLED_A = "#185B6E"
_HOUSE_DISABLED_B = "#1B3D6E"

# Glass treatment (themed builds only): a brighter top hairline and
# a sheen that catches the top of the card, then falls away.
_GLASS_CARD_TOP = "rgba(255, 255, 255, 88)"
_GLASS_CARD_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    "stop:0 rgba(255, 255, 255, 40), stop:0.10 rgba(255, 255, 255, 16), "
    "stop:1 rgba(255, 255, 255, 10))"
)
_GLASS_CARD_GRADIENT_INDIGO = (
    "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    "stop:0 rgba(53, 214, 255, 64), stop:0.10 rgba(53, 214, 255, 30), "
    "stop:1 rgba(53, 214, 255, 18))"
)

_DENSITY_FACTORS = {"comfortable": 1.0, "compact": 0.75}

# Theme dicts: "accent" / "accent_2" / "highlight" are the core
# (the orb themes' mid / edge / hi); hover, pressed and disabled
# derivatives are derived when a theme does not state them, and a
# theme may also override chrome tokens (bg / surface_1 /
# surface_2 / text / text_muted / text_faint) and the focus ring
# (focus_border).
THEMES: dict[str, dict] = {
    # The four orb palettes from visualizer.THEMES, as full themes.
    "Cyan": {"accent": "#35D6FF", "accent_2": "#3D8BFF",
              "highlight": "#E6FBFF"},
    "Sunset": {"accent": "#FF9A7A", "accent_2": "#FF7EB6",
               "highlight": "#FFE8D6"},
    "Violet": {"accent": "#A78BFA", "accent_2": "#7C8CFF",
               "highlight": "#EFE9FF"},
    # visualizer.THEMES' "Mono" palette, kept verbatim (its grays
    # carry a blue cast, so it lives under its own name — the
    # "Mono" below is the pure-grayscale theme).
    "Graphite": {"accent": "#C9CED6", "accent_2": "#8A919C",
                 "highlight": "#FFFFFF"},
    # New in v5.16: aurora teal-green into violet.
    "Aurora": {"accent": "#3DDAB4", "accent_2": "#8B7CFF",
               "highlight": "#D9FFF2"},
    # New in v5.16: warm amber into copper.
    "Ember": {"accent": "#FFAB5E", "accent_2": "#E2703A",
              "highlight": "#FFE9D2"},
    # New in v5.16: pure grayscale chrome, white accent — every
    # accent value is R == G == B, stated explicitly so the
    # derivatives stay neutral too.
    "Mono": {"accent": "#FFFFFF", "accent_2": "#C4C4C4",
             "highlight": "#FFFFFF",
             "accent_hover": "#FFFFFF", "accent_2_hover": "#D8D8D8",
             "accent_pressed": "#D9D9D9",
             "accent_2_pressed": "#A8A8A8"},
    # New in v5.16: rose pink into rose-gold.
    "Rose": {"accent": "#FB9DBD", "accent_2": "#E8B08D",
             "highlight": "#FFE3EE"},
    # High-contrast (v5.16): near-black chrome, full-strength
    # text, a bright yellow accent and a thickened focus ring.
    "Contrast": {"accent": "#FFE600", "accent_2": "#FFFFFF",
                 "highlight": "#FFF9C4",
                 "bg": "#000000", "surface_1": "#0A0A0A",
                 "surface_2": "#141414",
                 "text": "#FFFFFF", "text_muted": "#E8E8E8",
                 "text_faint": "#CFCFCF",
                 "focus_border": "2px solid #FFE600"},
    # Command Deck (Phase B): the cyan role's siblings.
    "Amber": {"accent": "#FFB020", "accent_2": "#FF6A00",
              "highlight": "#FFF1D6"},
    "Crimson": {"accent": "#FF5468", "accent_2": "#FD465A",
                "highlight": "#FFE3E6"},
}

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return (int(value[1:3], 16), int(value[3:5], 16),
            int(value[5:7], 16))


def _rgb_hex(r: float, g: float, b: float) -> str:
    def clamp(v: float) -> int:
        return max(0, min(255, int(round(v))))
    return "#{:02X}{:02X}{:02X}".format(clamp(r), clamp(g), clamp(b))


def _mix(hex_a: str, hex_b: str, frac_b: float) -> str:
    """hex_a moved frac_b of the way toward hex_b."""
    a = _hex_rgb(hex_a)
    b = _hex_rgb(hex_b)
    return _rgb_hex(*(a[i] + (b[i] - a[i]) * frac_b for i in range(3)))


def _triplet(hex_color: str) -> str:
    return "{}, {}, {}".format(*_hex_rgb(hex_color))


def derive_theme(accent_hex):
    """Build a complete theme dict from a single accent colour.

    The relationships mirror the built-in themes: accent_2 is the
    hue complement (rotated 180 degrees, same lightness and
    saturation) and highlight is a pale tint of the accent; hover /
    pressed / disabled derivatives are filled in by the resolver
    when the dict is used. Returns None for anything that is not a
    strict #RRGGBB string — garbage in, None out, never a
    half-built theme.
    """
    if not isinstance(accent_hex, str):
        return None
    candidate = accent_hex.strip()
    if not _HEX_RE.match(candidate):
        return None
    accent = candidate.upper()
    r, g, b = (v / 255.0 for v in _hex_rgb(accent))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    r2, g2, b2 = colorsys.hls_to_rgb((hue + 0.5) % 1.0, lightness,
                                     saturation)
    return {
        "accent": accent,
        "accent_2": _rgb_hex(r2 * 255, g2 * 255, b2 * 255),
        "highlight": _mix(accent, "#FFFFFF", 0.78),
    }


def _resolve_theme(theme: dict) -> dict:
    """Fill a theme dict out to every spelling a build rewrites."""
    accent = theme["accent"]
    accent_2 = theme["accent_2"]
    return {
        "accent": accent,
        "accent_2": accent_2,
        "accent_hover": theme.get("accent_hover")
        or _mix(accent, "#FFFFFF", 0.20),
        "accent_2_hover": theme.get("accent_2_hover")
        or _mix(accent_2, "#FFFFFF", 0.20),
        "accent_pressed": theme.get("accent_pressed")
        # Contrast floor: pressed gradients carry BG-coloured button
        # text, which must stay >= 4.5:1. At 0.18 the derived pressed
        # B-ends of Graphite / Aurora / Ember measured 4.31 / 4.20 /
        # 4.30 (below the floor); at 0.12 the worst themed pressed
        # end is 4.72. The pressed step still reads darker than rest.
        or _mix(accent, "#000000", 0.12),
        "accent_2_pressed": theme.get("accent_2_pressed")
        or _mix(accent_2, "#000000", 0.12),
        "highlight": theme.get("highlight")
        or _mix(accent, "#FFFFFF", 0.78),
        "disabled_a": theme.get("disabled_a")
        or _mix(accent, BG, 0.60),
        "disabled_b": theme.get("disabled_b")
        or _mix(accent_2, BG, 0.60),
        "bg": theme.get("bg", BG),
        "surface_1": theme.get("surface_1", SURFACE_1),
        "surface_2": theme.get("surface_2", SURFACE_2),
        "text": theme.get("text", TEXT),
        "text_muted": theme.get("text_muted", TEXT_MUTED),
        "text_faint": theme.get("text_faint", TEXT_FAINT),
        "focus_border": theme.get("focus_border"),
    }


def _remap_colors(sheet: str, resolved: dict) -> str:
    """Rewrite the house colour spellings to the theme's, once.

    Single-pass alternation: a replacement is never rescanned, so a
    theme colour that equals another house spelling (Violet's
    accent_2 is the house accent) lands exactly where intended.
    """
    pairs = [
        (ACCENT, resolved["accent"]),
        (ACCENT_2, resolved["accent_2"]),
        (_HOUSE_ACCENT_HOVER, resolved["accent_hover"]),
        (_HOUSE_ACCENT_2_HOVER, resolved["accent_2_hover"]),
        (_HOUSE_ACCENT_PRESSED, resolved["accent_pressed"]),
        (_HOUSE_ACCENT_2_PRESSED, resolved["accent_2_pressed"]),
        (_HOUSE_PALE, resolved["highlight"]),
        (_HOUSE_DISABLED_A, resolved["disabled_a"]),
        (_HOUSE_DISABLED_B, resolved["disabled_b"]),
        (BG, resolved["bg"]),
        (SURFACE_1, resolved["surface_1"]),
        (SURFACE_2, resolved["surface_2"]),
        (TEXT, resolved["text"]),
        (TEXT_MUTED, resolved["text_muted"]),
        (TEXT_FAINT, resolved["text_faint"]),
        (_triplet(ACCENT), _triplet(resolved["accent"])),
        (_triplet(ACCENT_2), _triplet(resolved["accent_2"])),
        (_triplet(TEXT), _triplet(resolved["text"])),
    ]
    mapping = {old: new for old, new in pairs if old != new}
    if not mapping:
        return sheet
    pattern = re.compile("|".join(
        re.escape(old) for old in
        sorted(mapping, key=len, reverse=True)))
    return pattern.sub(lambda m: mapping[m.group(0)], sheet)


def _format_px(value: float) -> str:
    rounded = round(value, 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


_FONT_SIZE_RE = re.compile(r"(font-size:\s*)(\d+(?:\.\d+)?)(px)")
_PADDING_RE = re.compile(r"(padding:\s*)([^;}]+)")
_PX_RE = re.compile(r"(\d+(?:\.\d+)?)px")


def _scale_fonts(sheet: str, scale: float) -> str:
    def repl(m: re.Match) -> str:
        return (m.group(1)
                + _format_px(float(m.group(2)) * scale)
                + m.group(3))
    return _FONT_SIZE_RE.sub(repl, sheet)


def _apply_density(sheet: str, factor: float) -> str:
    def repl(m: re.Match) -> str:
        scaled = _PX_RE.sub(
            lambda n: _format_px(float(n.group(1)) * factor) + "px",
            m.group(2))
        return m.group(1) + scaled
    return _PADDING_RE.sub(repl, sheet)


def build_stylesheet(theme=None, font_scale: float = 1.0,
                     density: str = "comfortable") -> str:
    """The premium stylesheet, optionally themed / scaled.

    theme: None (the house sheet), a THEMES name, or a theme dict
    as returned by derive_theme(). A themed build recolours the
    accent family. (Phase C: the old glass card treatment is
    retired — card rules are solid surfaces, so themed builds
    carry the glass keys but no rule uses them.)
    font_scale multiplies every font-size value (1.0 = as designed;
    the Settings choices are 0.9 / 1.0 / 1.1 / 1.2). density is
    "comfortable" (as designed) or "compact" (padding x 0.75).

    With no theme, font_scale 1.0 and comfortable density this
    returns PREMIUM_STYLE itself — the exact string existing
    callers already apply. Unknown theme names, non-numeric or
    non-positive font_scale, and unknown density values raise
    ValueError.
    """
    try:
        scale = float(font_scale)
    except (TypeError, ValueError):
        raise ValueError(
            f"font_scale must be a number, got {font_scale!r}"
        ) from None
    if scale <= 0:
        raise ValueError(f"font_scale must be positive, got {scale}")
    if density not in _DENSITY_FACTORS:
        raise ValueError(
            f"density must be one of {sorted(_DENSITY_FACTORS)}, "
            f"got {density!r}")

    resolved = None
    if theme is not None:
        if isinstance(theme, str):
            if theme not in THEMES:
                raise ValueError(
                    f"unknown theme {theme!r}; "
                    f"choose from {sorted(THEMES)}")
            theme = THEMES[theme]
        if (not isinstance(theme, dict)
                or "accent" not in theme or "accent_2" not in theme):
            raise ValueError(
                "theme must be a THEMES name or a theme dict with "
                "accent and accent_2 (see derive_theme())")
        resolved = _resolve_theme(theme)

    if resolved is None and scale == 1.0 and density == "comfortable":
        return PREMIUM_STYLE

    if resolved is None:
        sheet = PREMIUM_STYLE
    else:
        tokens = dict(_TOKENS)
        tokens["CARD_GRADIENT"] = _GLASS_CARD_GRADIENT
        tokens["CARD_GRADIENT_INDIGO"] = _GLASS_CARD_GRADIENT_INDIGO
        tokens["CARD_TOP"] = _GLASS_CARD_TOP
        if resolved["focus_border"]:
            tokens["FOCUS_BORDER"] = resolved["focus_border"]
        sheet = _remap_colors(_QSS.substitute(tokens), resolved)

    if scale != 1.0:
        sheet = _scale_fonts(sheet, scale)
    factor = _DENSITY_FACTORS[density]
    if factor != 1.0:
        sheet = _apply_density(sheet, factor)
    return sheet
