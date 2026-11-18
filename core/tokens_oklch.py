"""OKLCH / HCT-tone math (split from core/tokens.py, B6)."""

from __future__ import annotations

import math

# INTENTIONAL (tokens split B6): cross-part names live in
# tokens_theme; this module imports exactly the ones it uses.
from .tokens_theme import _HEX_RE, _hex_rgb, _rgb_hex


# ------------------------------------------------------------- OKLCH (stage 3)
#: Forward linear-sRGB -> LMS matrix (Ottosson). The inverse used by
#: :func:`hex_from_oklch` is derived from this matrix at import time
#: (see :func:`_invert3`), never transcribed separately.
_FWD_M = (
    (0.4122214708, 0.5363325363, 0.0514459929),
    (0.2119034982, 0.6806995451, 0.1073969566),
    (0.0883024619, 0.5846794441, 0.3258963231),
)


def _invert3(matrix) -> tuple[tuple[float, float, float], ...]:
    """Exact inverse of a 3x3 matrix (determinant / adjugate)."""
    (a, b, c), (d, e, f), (g, h, i) = matrix
    A = e * i - f * h
    B = -(d * i - f * g)
    C = d * h - e * g
    det = a * A + b * B + c * C
    if det == 0:
        raise ValueError("singular matrix")
    adj = (
        (A, -(b * i - c * h), b * f - c * e),
        (B, a * i - c * g, -(a * f - c * d)),
        (C, -(a * h - b * g), a * e - b * d),
    )
    return tuple(tuple(adj[r][col] / det for col in range(3))
                 for r in range(3))


_INV_M = _invert3(_FWD_M)


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def oklch_of(hex_color: str) -> tuple[float, float, float]:
    """OKLCH (L 0..1, C >= 0, H degrees 0..360) for a #RRGGBB colour.

    Pure-Python OKLab (Ottosson matrices). Raises ValueError on a
    malformed hex string.
    """
    if not isinstance(hex_color, str) or not _HEX_RE.match(hex_color):
        raise ValueError(f"expected #RRGGBB, got {hex_color!r}")
    r, g, b = (v / 255.0 for v in _hex_rgb(hex_color))
    r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    l_ = sum(_FWD_M[0][k] * v for k, v in enumerate((r, g, b)))
    m_ = sum(_FWD_M[1][k] * v for k, v in enumerate((r, g, b)))
    s_ = sum(_FWD_M[2][k] * v for k, v in enumerate((r, g, b)))
    l_, m_, s_ = l_ ** (1.0 / 3.0), m_ ** (1.0 / 3.0), s_ ** (1.0 / 3.0)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_ = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    C = math.hypot(a, b_)
    H = math.degrees(math.atan2(b_, a)) % 360.0
    return (L, C, H)


def hex_from_oklch(L: float, C: float, H: float) -> tuple[str, bool]:
    """Inverse OKLCH -> (#RRGGBB, clipped?).

    Out-of-gamut results are clipped to the sRGB cube and reported
    via the second element — clipping is data, not a silent fix.
    The LMS -> linear-sRGB step uses ``_INV_M`` (the exact inverse
    of the forward matrix); a 1e-9 epsilon keeps floating-point
    overshoot at a gamut edge (e.g. a channel at 1.0000000002)
    from misreporting as clipped.
    """
    h = math.radians(H % 360.0)
    a = C * math.cos(h)
    b_ = C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b_
    m_ = L - 0.1055613458 * a - 0.0638541728 * b_
    s_ = L - 0.0894841775 * a - 1.2914855480 * b_
    l_, m_, s_ = l_ ** 3, m_ ** 3, s_ ** 3
    r = sum(_INV_M[0][k] * v for k, v in enumerate((l_, m_, s_)))
    g = sum(_INV_M[1][k] * v for k, v in enumerate((l_, m_, s_)))
    b = sum(_INV_M[2][k] * v for k, v in enumerate((l_, m_, s_)))
    raw = (_linear_to_srgb(r), _linear_to_srgb(g), _linear_to_srgb(b))
    clipped = any(v < -1e-9 or v > 1.0 + 1e-9 for v in raw)
    clamped = tuple(max(0.0, min(1.0, v)) for v in raw)
    return (_rgb_hex(*(v * 255.0 for v in clamped)), clipped)


def hct_tone(hex_color: str) -> float:
    """HCT tone APPROXIMATED by OKLCH lightness (0..100).

    True HCT tone comes from CAM16 / Material Color Utilities, which
    are not bundled. This is an authoring approximation: it orders
    colours by perceived lightness the same way a tone ladder needs,
    but it must not be quoted as an HCT measurement.
    """
    return round(oklch_of(hex_color)[0] * 100.0, 1)


def tonal_ladder(hex_color: str,
                 tones: tuple[int, ...] = (0, 10, 20, 30, 40, 50, 60,
                                           70, 80, 90, 100)) -> dict[int, dict]:
    """Tonal ladder: same hue, lightness stepped by tone.

    Chroma is tapered by ``1 - (2L - 1)**4`` so the ladder lands
    exactly on black at tone 0 and white at tone 100 (holding full
    chroma at the endpoints is out of gamut and would return
    near-black / near-white with a clipped flag instead). Mid tones
    keep ~full chroma (tone 40/60 keep 99.8%). Each step reports its
    hex, its effective OKLCH triple, and whether the result was
    clipped to gamut. Tone here is the OKLCH-lightness
    approximation documented in :func:`hct_tone`.
    """
    _, C, H = oklch_of(hex_color)
    ladder = {}
    for tone in tones:
        L = tone / 100.0
        taper = 1.0 - (2.0 * L - 1.0) ** 4
        C_eff = C * taper
        hex_value, clipped = hex_from_oklch(L, C_eff, H)
        ladder[tone] = {
            "hex": hex_value,
            "oklch": (L, C_eff, H),
            "clipped": clipped,
        }
    return ladder


#: Alias: the ladder's name in HCT vocabulary (same approximation).
hct_tonal_ladder = tonal_ladder
