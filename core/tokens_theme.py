"""Theme source + resolver (split from core/tokens.py, B6)."""

from __future__ import annotations

import copy
import re


MODULE_VERSION = "5.64.0"


# ------------------------------------------------------------- house chrome
# Mirrored from the shipped style module (the single dark chrome all
# themes inherit unless they override it). These are template inputs
# for the proof's remap step, not DTCG theme values — the DTCG chrome
# group below carries the same values as tokens.
_BG = "#04090E"


_SURFACE_1 = "#0C1D28"


_SURFACE_2 = "#08131B"


_SURFACE_3 = "#122636"  # raised / selected (style.py only token; not in RESOLVED_KEYS)


_TEXT = "#E6FBFF"


_TEXT_MUTED = "#B0C1CD"


_TEXT_FAINT = "#7E9AA6"


# Command Deck role colours (guide §5.2 palette). These are token
# values, not resolved keys — the proof does not compare them;
# Phase B tests pin them here and in style.py.
_AMBER = "#FFB020"


_OK = "#5CF2A8"


_DANGER = "#FF5468"


_NEUTRAL = "#7E9AA6"


_LINE_SUBTLE = "rgba(53, 214, 255, 36)"  # cyan 14%


_LINE_STRONG = "rgba(53, 214, 255, 102)"  # cyan 40% (guide range 30-45%)


#: The cyan role itself — the D4 custom-accent fallback.
CYAN_ACCENT = "#35D6FF"


# The house spellings a themed build rewrites (mirrored from the
# shipped style module): hover / pressed / pale / disabled.
_HOUSE_ACCENT_HOVER = "#5DDEFF"


_HOUSE_ACCENT_2_HOVER = "#64A2FF"


_HOUSE_ACCENT_PRESSED = "#2FBCE0"


_HOUSE_ACCENT_2_PRESSED = "#367AE0"


_HOUSE_PALE = "#E6FBFF"


_HOUSE_DISABLED_A = "#185B6E"


_HOUSE_DISABLED_B = "#1B3D6E"


# Glass-card treatment for themed builds (mirrored from the shipped
# style module; template input, not a token under test).
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


def _color(value: str, description: str) -> dict:
    return {"$value": value, "$type": "color", "$description": description}


def _theme(accent: str, accent_2: str, highlight: str,
           description: str, **extra) -> dict:
    entry = {
        "accent": _color(accent, f"{description}: primary accent"),
        "accent_2": _color(accent_2, f"{description}: secondary accent"),
        "highlight": _color(highlight, f"{description}: pale highlight"),
    }
    entry.update(extra)
    return entry


# ------------------------------------------------------------- DTCG source
# The single source of truth for this module. Theme core values match
# the shipped theme dicts verbatim; Mono's explicit derivatives and
# Contrast's chrome overrides are carried as explicit tokens (not
# re-derived) so the resolver below reproduces them exactly.
DTCG_SOURCE: dict = {
    "$metadata": {
        "tokenSetOrder": ["color.chrome", "color.theme"],
        "generator": (
            "tokens local Style-Dictionary-shaped build "
            "(stdlib only; NOT the npm style-dictionary package)"
        ),
        "note": (
            "Generated output is in-memory only and is checked into "
            "the tree ONLY if byte-identical to shipped bytes (INV-11). "
            "This file checks in source + generator only."
        ),
    },
    "color": {
        "chrome": {
            "bg": _color(_BG, "deep background"),
            "surface_1": _color(_SURFACE_1, "dialogs: one step above bg"),
            "surface_2": _color(_SURFACE_2, "popups: highest resting step"),
            "surface_3": _color(_SURFACE_3, "raised / selected"),
            "text": _color(_TEXT, "primary text"),
            "text_muted": _color(_TEXT_MUTED, "muted text"),
            "text_faint": _color(_TEXT_FAINT, "faint text"),
            "amber": _color(_AMBER, "acting / approval / degraded"),
            "ok": _color(_OK, "done / healthy"),
            "danger": _color(_DANGER, "fault"),
            "neutral": _color(_NEUTRAL, "neutral"),
            "line_subtle": _color(_LINE_SUBTLE, "subtle hairline"),
            "line_strong": _color(_LINE_STRONG, "strong hairline"),
        },
        "theme": {
            "Cyan": _theme(
                "#35D6FF", "#3D8BFF", "#E6FBFF", "house theme (Command Deck cyan)"),
            "Sunset": _theme(
                "#FF9A7A", "#FF7EB6", "#FFE8D6", "sunset theme"),
            "Violet": _theme(
                "#A78BFA", "#7C8CFF", "#EFE9FF", "violet theme"),
            "Graphite": _theme(
                "#C9CED6", "#8A919C", "#FFFFFF",
                "graphite theme (orb Mono palette, blue-cast grays)"),
            "Aurora": _theme(
                "#3DDAB4", "#8B7CFF", "#D9FFF2", "aurora theme"),
            "Ember": _theme(
                "#FFAB5E", "#E2703A", "#FFE9D2", "ember theme"),
            "Mono": _theme(
                "#FFFFFF", "#C4C4C4", "#FFFFFF",
                "pure-grayscale theme; derivatives stated explicitly "
                "so they stay neutral",
                accent_hover=_color("#FFFFFF", "Mono: explicit hover"),
                accent_2_hover=_color("#D8D8D8", "Mono: explicit hover 2"),
                accent_pressed=_color("#D9D9D9", "Mono: explicit pressed"),
                accent_2_pressed=_color("#A8A8A8",
                                        "Mono: explicit pressed 2"),
            ),
            "Rose": _theme(
                "#FB9DBD", "#E8B08D", "#FFE3EE", "rose theme"),
            "Contrast": _theme(
                "#FFE600", "#FFFFFF", "#FFF9C4",
                "high-contrast theme; overrides chrome + focus ring",
                bg=_color("#000000", "Contrast: near-black chrome"),
                surface_1=_color("#0A0A0A", "Contrast: surface 1"),
                surface_2=_color("#141414", "Contrast: surface 2"),
                text=_color("#FFFFFF", "Contrast: full-strength text"),
                text_muted=_color("#E8E8E8", "Contrast: muted text"),
                text_faint=_color("#CFCFCF", "Contrast: faint text"),
                focus_border={
                    "$value": "2px solid #FFE600",
                    "$type": "border",
                    "$description": "Contrast: thickened focus ring",
                },
            ),
            "Amber": _theme(
                "#FFB020", "#FF6A00", "#FFF1D6", "amber theme"),
            "Crimson": _theme(
                "#FF5468", "#FD465A", "#FFE3E6", "crimson theme"),
            # Light (Stage 0 features batch 5): the one light
            # chrome. Accent is a deepened Aura blue; accent_2 is
            # deepened further so it passes 4.5:1 as text
            # (#2563EB is 4.90 on the bg, 5.17 on white). Every
            # text-on-surface pair is >= 4.5:1 by computation.
            "Light": _theme(
                "#1D6FE0", "#2563EB", "#E3F0FF",
                "light theme; overrides chrome",
                bg=_color("#F7F9FC", "Light: window background"),
                surface_1=_color("#FFFFFF", "Light: dialog surface"),
                surface_2=_color("#EDF1F7", "Light: popup surface"),
                text=_color("#101826", "Light: primary text"),
                text_muted=_color("#4A5568", "Light: muted text"),
                text_faint=_color("#5F6B7A", "Light: faint text"),
            ),
        },
    },
    "spacing": {
        "xs": {"$value": 4, "$type": "dimension",
               "$description": "4-pt rhythm: xs"},
        "sm": {"$value": 8, "$type": "dimension",
               "$description": "4-pt rhythm: sm"},
        "md": {"$value": 12, "$type": "dimension",
               "$description": "4-pt rhythm: md"},
        "lg": {"$value": 16, "$type": "dimension",
               "$description": "4-pt rhythm: lg"},
        "xl": {"$value": 24, "$type": "dimension",
               "$description": "4-pt rhythm: xl"},
    },
    "radius": {
        "window": {"$value": 24, "$type": "dimension",
                   "$description": "frameless app shell"},
        "dialog": {"$value": 16, "$type": "dimension",
                   "$description": "floating dialogs"},
        "card": {"$value": 14, "$type": "dimension",
                 "$description": "cards, panels, chat bubbles"},
        "btn": {"$value": 10, "$type": "dimension",
                "$description": "buttons, inputs, nav rows"},
        "sm": {"$value": 8, "$type": "dimension",
               "$description": "window controls, palette rows"},
        "pill": {"$value": 18, "$type": "dimension",
                 "$description": "primary / danger action pills"},
        "pill_mic": {"$value": 28, "$type": "dimension",
                     "$description": "hero mic (56px circle)"},
        "pill_icon": {"$value": 21, "$type": "dimension",
                      "$description": "round icon buttons (42px circle)"},
        "chip": {"$value": 15, "$type": "dimension",
                 "$description": "chips and suggestion chips"},
        "pill_sm": {"$value": 13, "$type": "dimension",
                    "$description": "command pills, status pill"},
        "dock": {"$value": 20, "$type": "dimension",
                 "$description": "dock input"},
    },
    "font": {
        "title": {"$value": 20, "$type": "dimension",
                  "$description": "page titles (weight 600)"},
        "card_title": {"$value": 13, "$type": "dimension",
                       "$description": "card titles (weight 600)"},
        "body": {"$value": 13, "$type": "dimension",
                 "$description": "body text"},
        "caption": {"$value": 11.5, "$type": "dimension",
                    "$description": "muted captions / hints"},
        "micro": {"$value": 10.5, "$type": "dimension",
                  "$description": "timestamps, eyebrow microcopy"},
    },
}


#: Resolved-token keys, in the same set/order as the shipped resolver
#: returns (equality with the shipped resolver is checked by the proof).
RESOLVED_KEYS = (
    "accent", "accent_2", "accent_hover", "accent_2_hover",
    "accent_pressed", "accent_2_pressed", "highlight",
    "disabled_a", "disabled_b",
    "bg", "surface_1", "surface_2",
    "text", "text_muted", "text_faint", "focus_border",
    "selection_bg", "text_disabled",
    "input_bg", "input_bg_disabled", "input_border_disabled",
)


def dtcg_source() -> dict:
    """Deep copy of the DTCG source (callers must not mutate it)."""
    return copy.deepcopy(DTCG_SOURCE)


def theme_names() -> list[str]:
    """The 12 theme names, in DTCG source order."""
    return list(DTCG_SOURCE["color"]["theme"].keys())


# ------------------------------------------------------------- colour helpers
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return (int(value[1:3], 16), int(value[3:5], 16),
            int(value[5:7], 16))


def _rgb_hex(r: float, g: float, b: float) -> str:
    def clamp(v: float) -> int:
        return max(0, min(255, int(round(v))))
    return "#{:02X}{:02X}{:02X}".format(clamp(r), clamp(g), clamp(b))


def _mix(hex_a: str, hex_b: str, frac_b: float) -> str:
    """``hex_a`` moved ``frac_b`` of the way toward ``hex_b``.

    Re-implementation of the shipped mix rule (round-half-even via
    ``round`` in ``_rgb_hex``) — kept identical on purpose so the
    proof measures token values, not a rounding drift.
    """
    a = _hex_rgb(hex_a)
    b = _hex_rgb(hex_b)
    return _rgb_hex(*(a[i] + (b[i] - a[i]) * frac_b for i in range(3)))


def _luminance(hex_color: str) -> float:
    """Relative luminance (0..1) of a #RRGGBB colour.

    Mirror of the shipped helper in gui/style_theme.py; keep
    the two in step (the proof compares their outputs).
    """
    def channel(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _hex_rgb(hex_color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _triplet(hex_color: str) -> str:
    return "{}, {}, {}".format(*_hex_rgb(hex_color))


def _theme_value(name: str, key: str) -> str | None:
    entry = DTCG_SOURCE["color"]["theme"].get(name)
    if entry is None:
        return None
    token = entry.get(key)
    if token is None:
        return None
    return token["$value"]


# ------------------------------------------------------------- resolve (stage 2)
def build_python_tokens(theme_name: str) -> dict:
    """Resolved token dict for one theme (DTCG -> Python tokens).

    Derivation rules mirror the shipped resolver exactly: hover is
    the accent mixed 20% toward white, pressed 12% toward black
    (the shipped 0.12: at 0.18 the derived pressed ends of
    Graphite / Aurora / Ember measured below the 4.5:1 floor),
    highlight 78% toward white when not stated, disabled is the
    accent mixed 60% toward the background. Explicit DTCG tokens
    (Mono's derivatives, Contrast's chrome + focus ring) win over
    derivation. Unknown theme names raise ValueError.
    """
    if theme_name not in DTCG_SOURCE["color"]["theme"]:
        raise ValueError(
            f"unknown theme {theme_name!r}; "
            f"choose from {theme_names()}"
        )
    chrome = DTCG_SOURCE["color"]["chrome"]
    accent = _theme_value(theme_name, "accent")
    accent_2 = _theme_value(theme_name, "accent_2")
    bg = _theme_value(theme_name, "bg") or chrome["bg"]["$value"]
    text = _theme_value(theme_name, "text") or chrome["text"]["$value"]
    # Light chrome (bg luminance > 0.5) needs two token values
    # the dark themes never did: a solid selection background
    # (the accent wash at 38% over a light surface is too pale
    # for the white selection text) and a disabled-text colour
    # that follows the theme text (the house near-white at 45%
    # would vanish on light). Mirrors the shipped resolver.
    # Look-fix 2 (2026-11-24): the input wells need the same
    # treatment — the house black-at-38% well is a mid-grey slab
    # on light (measured 148–154), so on light the well follows
    # the theme surface_2, the disabled well sits halfway between
    # surface_2 and the bg, and the disabled border follows the
    # theme text at a visible alpha (the house white-at-10 border
    # is invisible on light). Dark themes keep the house values.
    light = _luminance(bg) > 0.5
    _surface_2 = (_theme_value(theme_name, "surface_2")
                  or chrome["surface_2"]["$value"])
    return {
        "accent": accent,
        "accent_2": accent_2,
        "accent_hover": _theme_value(theme_name, "accent_hover")
        or _mix(accent, "#FFFFFF", 0.20),
        "accent_2_hover": _theme_value(theme_name, "accent_2_hover")
        or _mix(accent_2, "#FFFFFF", 0.20),
        "accent_pressed": _theme_value(theme_name, "accent_pressed")
        or _mix(accent, "#000000", 0.12),
        "accent_2_pressed": _theme_value(theme_name, "accent_2_pressed")
        or _mix(accent_2, "#000000", 0.12),
        "highlight": _theme_value(theme_name, "highlight")
        or _mix(accent, "#FFFFFF", 0.78),
        "disabled_a": _theme_value(theme_name, "disabled_a")
        or _mix(accent, _BG, 0.60),
        "disabled_b": _theme_value(theme_name, "disabled_b")
        or _mix(accent_2, _BG, 0.60),
        "bg": bg,
        "surface_1": _theme_value(theme_name, "surface_1")
        or chrome["surface_1"]["$value"],
        "surface_2": _surface_2,
        "text": text,
        "text_muted": _theme_value(theme_name, "text_muted")
        or chrome["text_muted"]["$value"],
        "text_faint": _theme_value(theme_name, "text_faint")
        or chrome["text_faint"]["$value"],
        "focus_border": _theme_value(theme_name, "focus_border"),
        "selection_bg": (
            accent if light
            else "rgba({}, 96)".format(_triplet(accent))
        ),
        "text_disabled": (
            "rgba({}, 115)".format(_triplet(text)) if light
            else "rgba({}, 115)".format(_triplet(chrome["text"]["$value"]))
        ),
        "input_bg": (
            _surface_2 if light else "rgba(0, 0, 0, 96)"
        ),
        "input_bg_disabled": (
            _mix(_surface_2, bg, 0.5) if light
            else "rgba(0, 0, 0, 48)"
        ),
        "input_border_disabled": (
            "rgba({}, 32)".format(_triplet(text)) if light
            else "rgba(255, 255, 255, 10)"
        ),
    }


def build_all_python_tokens() -> dict[str, dict]:
    """Resolved tokens for all 12 themes."""
    return {name: build_python_tokens(name) for name in theme_names()}
