"""Wave 9 (v5.64) — one design-token pipeline for all 11 themes.

One DTCG source -> a Style-Dictionary-shaped build -> generated
QSS / Python tokens, for every theme in the shipped set
(``Cyan``, ``Sunset``, ``Violet``, ``Graphite``, ``Aurora``,
``Ember``, ``Mono``, ``Rose``, ``Contrast``, ``Amber``, ``Crimson``).

Pipeline stages (mirroring Style Dictionary's shape, implemented
here in stdlib only — the npm ``style-dictionary`` package is NOT
bundled, NOT a dependency, and NOT required):

  1. SOURCE — :data:`DTCG_SOURCE`, W3C Design Token Community Group
     format (``$value`` / ``$type`` / ``$description``). Core accent
     values are literals; nothing is fetched, nothing is guessed.
  2. RESOLVE — :func:`build_python_tokens` fills each theme out to
     the full resolved set (hover / pressed / disabled derivatives
     via the same mix rules the shipped resolver uses, chrome
     overrides where a theme states them).
  3. TRANSFORM — OKLCH authoring values (:func:`oklch_of`),
     HCT-tone-approximated tonal ladders (:func:`tonal_ladder`), and
     Radix-shaped 12-step semantics (:func:`radix_steps`).
  4. PLATFORM OUTPUTS — :func:`style_dictionary_build` emits the
     Python token dicts and the QSS token blocks, in memory only.
     Qt QSS has no variables, so a QSS token block is a
     documentation/authoring block, NOT applied QSS; the applied
     output is :func:`generate_stylesheet`.

INV-11 BYTE-IDENTITY RULE (hard rule, read before touching this file
or anything it generates):

  * :func:`prove_byte_identity` compares the generated themed
    stylesheet against the CURRENT shipped stylesheet bytes, theme
    by theme, and reports per-theme identical/different with diffs.
    It also compares the resolved token dicts, which is the
    token-level check independent of the QSS template.
  * If ANY theme differs: DO NOT re-pin, DO NOT modify the shipped
    stylesheet or any shipped style file, and DO NOT check generated
    output into the tree. Keep the differing generated output OUT of
    the tree (it exists only in memory, from the generator functions
    in this module) and report the diff + surface the explicit
    re-pin decision for the gate.
  * Generated output may be checked in ONLY if byte-identical to the
    current shipped bytes. This module itself checks in the DTCG
    source + generator only — no generated file is written by this
    module, ever, so the rule holds regardless of the proof outcome.

What the proof does and does not prove (honest scope): the QSS
template and the glass-card treatment are template inputs, read from
the shipped style module at proof time; the values under test are
the DTCG per-theme accent/chrome values. A wrong DTCG value changes
the generated sheet and the proof goes red. The proof does NOT
re-derive the QSS template itself, and a green proof does NOT mean
the template was re-verified.

Colour science, stated plainly (no borrowed authority):

  * OKLCH is computed here in pure Python from the published OKLab
    forward matrices (Bjorn Ottosson). The forward values are
    verified against known anchors (red: L 0.6280, C 0.2577,
    H 29.23; white L 1, black L 0). The inverse LMS -> linear-sRGB
    matrix is the exact matrix inverse of the forward matrix,
    computed at import by :func:`_invert3` — NOT the familiar
    XYZ -> sRGB matrix, which is a different step and does NOT
    invert this forward matrix (that transcription error was caught
    by the round-trip check during development: saturated colours
    came back up to 171/255 off). Round-trips are verified in the
    suite (exact, 0/255, for all 18 theme accents).
  * HCT tone is APPROXIMATED by OKLCH lightness (tone = L * 100).
    True HCT needs CAM16 / Material Color Utilities, which are not
    bundled; the ladder is an authoring approximation and is labelled
    as such wherever it surfaces. It is not a Material claim.
  * Radix 12-step SEMANTICS: the step ROLES follow the Radix docs
    (1 app background ... 9 solid ... 12 high-contrast text). The
    step VALUES are local derivations from the theme's own chrome +
    accent (formulas in :func:`radix_steps`), NOT Radix's published
    per-hue scale hexes. Never present them as Radix's scales.

No new dependencies, no telemetry, no network, stdlib only.
"""

from __future__ import annotations  # INTENTIONAL (tokens split B6):
# the old module carried this future import, which binds the name
# `annotations` in the module namespace; the facade keeps it so the
# dir diff against the old module is empty.

# INTENTIONAL (tokens split B6): the old module's stdlib
# imports, re-exported so the facade namespace matches the old one.
# Template is imported but never used in the old module either;
# it travels here for the dir-diff bar, not as an impl import.
import copy
import difflib
import hashlib
import math
import re
from string import Template  # noqa: F401

# INTENTIONAL (tokens split B6): every name the old module defined
# is re-exported from tokens_theme, so existing importers work unchanged
# and with identical function objects.
from .tokens_theme import (
    CYAN_ACCENT,
    DTCG_SOURCE,
    MODULE_VERSION,
    RESOLVED_KEYS,
    _AMBER,
    _BG,
    _DANGER,
    _GLASS_CARD_GRADIENT,
    _GLASS_CARD_GRADIENT_INDIGO,
    _GLASS_CARD_TOP,
    _HEX_RE,
    _HOUSE_ACCENT_2_HOVER,
    _HOUSE_ACCENT_2_PRESSED,
    _HOUSE_ACCENT_HOVER,
    _HOUSE_ACCENT_PRESSED,
    _HOUSE_DISABLED_A,
    _HOUSE_DISABLED_B,
    _HOUSE_PALE,
    _LINE_STRONG,
    _LINE_SUBTLE,
    _NEUTRAL,
    _OK,
    _SURFACE_1,
    _SURFACE_2,
    _SURFACE_3,
    _TEXT,
    _TEXT_FAINT,
    _TEXT_MUTED,
    _color,
    _hex_rgb,
    _mix,
    _rgb_hex,
    _theme,
    _theme_value,
    _triplet,
    build_all_python_tokens,
    build_python_tokens,
    dtcg_source,
    theme_names,
)

# INTENTIONAL (tokens split B6): every name the old module defined
# is re-exported from tokens_gate, so existing importers work unchanged
# and with identical function objects.
from .tokens_gate import (
    MIN_CUSTOM_ACCENT_CONTRAST,
    _LEGACY_THEME_NAMES,
    _luminance,
    contrast_ratio,
    gate_custom_accent,
    migrate_theme_name,
)

# INTENTIONAL (tokens split B6): every name the old module defined
# is re-exported from tokens_oklch, so existing importers work unchanged
# and with identical function objects.
from .tokens_oklch import (
    _FWD_M,
    _INV_M,
    _invert3,
    _linear_to_srgb,
    _srgb_to_linear,
    hct_tonal_ladder,
    hct_tone,
    hex_from_oklch,
    oklch_of,
    tonal_ladder,
)

# INTENTIONAL (tokens split B6): every name the old module defined
# is re-exported from tokens_radix, so existing importers work unchanged
# and with identical function objects.
from .tokens_radix import (
    RADIX_ROLES,
    radix_steps,
)

# INTENTIONAL (tokens split B6): every name the old module defined
# is re-exported from tokens_outputs, so existing importers work unchanged
# and with identical function objects.
from .tokens_outputs import (
    _remap,
    _sha256,
    generate_qss_tokens,
    generate_stylesheet,
    proof_table_text,
    prove_byte_identity,
    style_dictionary_build,
)
