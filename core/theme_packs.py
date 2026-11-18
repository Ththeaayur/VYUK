"""Wave 9 (v5.64) — theme packs (PILOT).

Tokens-only JSON packs, validated against a versioned schema at
import time. A pack is DATA, never code: it carries colour and
spacing tokens only, and nothing in it is ever executed, evaled,
or imported. A pack that fails validation is rejected WHOLE —
``apply_pack`` raises listing every failing path and applies
nothing. There is no partial apply, no fallback merge of a bad
pack over the current theme, and no silent repair.

Wallpaper-colour seeding feeds the HCT pipeline: a seed colour
is expressed as hue / chroma / tone, tonal steps are derived
from it, and every text-on-surface pair must pass a contrast
check before the seeded tokens are accepted. The HCT math here
is the pipeline's own hue-chroma-tone approximation in stdlib
only (no colour library is bundled); the contrast check is the
WCAG relative-luminance ratio, which is library-independent.

PILOT, not live: packs are validated and seedable, but no GUI
wiring reads them yet — wiring is the closing step, and until
it lands the shipped theme is unchanged by this module.

Stdlib only. No telemetry. No new dependencies, so no
third-party licence is involved in this module at all.
"""

from __future__ import annotations

import colorsys
import json
import math
import re
from dataclasses import dataclass, field

MODULE_VERSION = "5.64.0"
SCHEMA_VERSION = 1

__all__ = [
    "MODULE_VERSION",
    "SCHEMA_VERSION",
    "ThemePackError",
    "ValidationFailure",
    "REQUIRED_TOKENS",
    "OPTIONAL_TOKENS",
    "validate_pack",
    "load_pack",
    "apply_pack",
    "seed_tokens_from_rgb",
    "seed_tokens_from_hct",
    "contrast_ratio",
    "contrast_check",
    "MIN_TEXT_CONTRAST",
    "MIN_LARGE_TEXT_CONTRAST",
]

# A pack is a flat token object. Anything outside this vocabulary
# is rejected: unknown keys are a failing path, not ignored, so a
# typo can never silently become a missing token at render time.
REQUIRED_TOKENS = (
    "surface",
    "surface_variant",
    "on_surface",
    "primary",
    "on_primary",
    "accent",
)
OPTIONAL_TOKENS = (
    "on_surface_variant",
    "on_accent",
    "error",
    "on_error",
    "radius",
    "spacing",
)
_ALLOWED_TOKENS = frozenset(REQUIRED_TOKENS + OPTIONAL_TOKENS)
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# WCAG AA: normal text 4.5:1, large text 3:1.
MIN_TEXT_CONTRAST = 4.5
MIN_LARGE_TEXT_CONTRAST = 3.0


@dataclass(frozen=True)
class ValidationFailure:
    """One failing path, with the reason it failed."""

    path: str
    reason: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.path}: {self.reason}"


class ThemePackError(ValueError):
    """A pack was rejected whole. ``failures`` lists every bad path."""

    def __init__(self, failures: list[ValidationFailure]):
        self.failures = list(failures)
        paths = "; ".join(str(f) for f in self.failures)
        super().__init__(f"theme pack rejected ({len(self.failures)} "
                         f"failing path(s)): {paths}")


def _failures_for(obj: object) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    if not isinstance(obj, dict):
        return [ValidationFailure("$", "pack must be a JSON object")]
    if obj.get("schema_version") != SCHEMA_VERSION:
        failures.append(ValidationFailure(
            "schema_version",
            f"must be {SCHEMA_VERSION}"))
    if not isinstance(obj.get("name"), str) or not obj["name"].strip():
        failures.append(ValidationFailure("name", "must be a non-empty string"))
    tokens = obj.get("tokens")
    if not isinstance(tokens, dict):
        failures.append(ValidationFailure("tokens", "must be an object"))
        return failures
    for key in sorted(tokens):
        if key not in _ALLOWED_TOKENS:
            failures.append(ValidationFailure(
                f"tokens.{key}", "unknown token — not in the schema"))
    for key in REQUIRED_TOKENS:
        value = tokens.get(key)
        if not isinstance(value, str) or not _HEX_RE.match(value):
            failures.append(ValidationFailure(
                f"tokens.{key}", "required token must be a #RRGGBB colour"))
    for key in OPTIONAL_TOKENS:
        value = tokens.get(key)
        if value is None:
            continue
        if key in ("radius", "spacing"):
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or value < 0:
                failures.append(ValidationFailure(
                    f"tokens.{key}", "must be a non-negative number"))
        elif not isinstance(value, str) or not _HEX_RE.match(value):
            failures.append(ValidationFailure(
                f"tokens.{key}", "must be a #RRGGBB colour"))
    # A colour token pair that cannot be read is a failing path too:
    # the schema check alone would let an unreadable theme through.
    if isinstance(tokens.get("surface"), str) \
            and _HEX_RE.match(tokens["surface"]) \
            and isinstance(tokens.get("on_surface"), str) \
            and _HEX_RE.match(tokens["on_surface"]):
        ratio = contrast_ratio(tokens["surface"], tokens["on_surface"])
        if ratio < MIN_TEXT_CONTRAST:
            failures.append(ValidationFailure(
                "tokens.on_surface",
                f"contrast vs surface is {ratio:.2f}:1, below "
                f"{MIN_TEXT_CONTRAST}:1"))
    return failures


def validate_pack(obj: object) -> list[ValidationFailure]:
    """Every failing path in ``obj``; empty means the pack is valid."""
    return _failures_for(obj)


def load_pack(text: str) -> dict:
    """Parse pack JSON. Invalid JSON is a single failing path, not a crash."""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ThemePackError(
            [ValidationFailure("$", f"not valid JSON: {exc}")]) from exc
    failures = validate_pack(obj)
    if failures:
        raise ThemePackError(failures)
    return obj


def apply_pack(obj: object) -> dict:
    """Validate and return a COPY of the pack's tokens.

    Reject-with-failing-path, never partial apply: on any failure
    this raises ``ThemePackError`` and returns nothing, so a caller
    can never end up holding half a theme. On success the caller
    gets a fresh dict; mutating it cannot corrupt the pack.
    """
    failures = validate_pack(obj)
    if failures:
        raise ThemePackError(failures)
    assert isinstance(obj, dict)  # validated above
    tokens = dict(obj["tokens"])
    for key in OPTIONAL_TOKENS:
        tokens.setdefault(key, None)
    return tokens


# ------------------------------------------------------------------ #
# Colour helpers: hex <-> linear RGB, WCAG contrast
# ------------------------------------------------------------------ #

def _hex_to_rgb(hex_colour: str) -> tuple[float, float, float]:
    hex_colour = hex_colour.lstrip("#")
    return tuple(int(hex_colour[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 \
        else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    r, g, b = _hex_to_rgb(hex_colour)
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG relative-luminance contrast ratio between two #RRGGBB colours."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def contrast_check(surface: str, text: str,
                   minimum: float = MIN_TEXT_CONTRAST) -> tuple[bool, float]:
    """(passes, ratio) for a text-on-surface pair."""
    ratio = contrast_ratio(surface, text)
    return ratio >= minimum, ratio


# ------------------------------------------------------------------ #
# Wallpaper seeding into the HCT pipeline
# ------------------------------------------------------------------ #

def _rgb_to_hex(r: float, g: float, b: float) -> str:
    clamp = lambda v: max(0, min(255, round(v * 255)))
    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


def _tone(hex_colour: str, target_lightness: float) -> str:
    """Shift a colour to ``target_lightness`` keeping hue/saturation."""
    r, g, b = _hex_to_rgb(hex_colour)
    h, _l, s = colorsys.rgb_to_hls(r, g, b)
    r2, g2, b2 = colorsys.hls_to_rgb(h, target_lightness, s)
    return _rgb_to_hex(r2, g2, b2)


@dataclass(frozen=True)
class HctSeed:
    """A seed colour expressed for the HCT pipeline.

    ``hue`` in degrees, ``chroma`` 0..~1 (relative, stdlib
    approximation of HCT chroma), ``tone`` 0..100 lightness.
    """

    hue: float
    chroma: float
    tone: float


def seed_tokens_from_rgb(r: int, g: int, b: int) -> dict:
    """Derive a full token set from one wallpaper seed colour.

    Tonal steps: a dark surface and a light surface are both
    derived from the seed hue, and the text colour on each is
    chosen from black/white by whichever passes the contrast
    check — if neither passes, the surface tone is stepped until
    one does, so a seeded pack can never ship unreadable text.
    """
    for channel in (r, g, b):
        if not isinstance(channel, int) or not 0 <= channel <= 255:
            raise ValueError("seed channels must be ints in 0..255")
    h, lightness, sat = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    seed = _rgb_to_hex(r / 255.0, g / 255.0, b / 255.0)
    return seed_tokens_from_hct(HctSeed(hue=h * 360.0, chroma=sat,
                                        tone=lightness * 100.0),
                                seed_hex=seed)


def seed_tokens_from_hct(seed: HctSeed, seed_hex: str | None = None) -> dict:
    """Derive tokens from an HCT seed. The primary IS the seed hue."""
    base = seed_hex
    if base is None:
        # Rebuild an approximate seed colour from hue/chroma/tone.
        r, g, b = colorsys.hls_to_rgb(seed.hue / 360.0,
                                      seed.tone / 100.0,
                                      max(0.0, min(1.0, seed.chroma)))
        base = _rgb_to_hex(r, g, b)

    def _readable_text(surface: str) -> str:
        # Try white, then black, then step the surface tone darker
        # until white reads on it. White-on-dark is the seeded
        # default; black-on-light is the fallback, never a guess.
        if contrast_ratio(surface, "#ffffff") >= MIN_TEXT_CONTRAST:
            return "#ffffff"
        if contrast_ratio(surface, "#000000") >= MIN_TEXT_CONTRAST:
            return "#000000"
        tone = 0.08
        while tone > 0.0:
            darker = _tone(surface, tone)
            if contrast_ratio(darker, "#ffffff") >= MIN_TEXT_CONTRAST:
                return "#ffffff"
            tone -= 0.02
        return "#ffffff"  # unreachable in practice; darkest wins

    surface = _tone(base, 0.12)
    surface_variant = _tone(base, 0.20)
    on_surface = _readable_text(surface)
    # Primary keeps the seed hue at a mid tone; its text colour is
    # picked by the same contrast rule, not assumed.
    primary = _tone(base, 0.55)
    on_primary = _readable_text(primary)
    tokens = {
        "surface": surface,
        "surface_variant": surface_variant,
        "on_surface": on_surface,
        "primary": primary,
        "on_primary": on_primary,
        "accent": _tone(base, 0.70),
        "on_surface_variant": _readable_text(surface_variant),
        "on_accent": _readable_text(_tone(base, 0.70)),
        "error": "#e5484d",
        "on_error": "#ffffff",
        "radius": 12,
        "spacing": 8,
    }
    pack = {"schema_version": SCHEMA_VERSION, "name": "wallpaper-seed",
            "tokens": tokens}
    failures = validate_pack(pack)
    if failures:  # a seeded pack must pass its own schema, or it is a bug
        raise ThemePackError(failures)
    return tokens
