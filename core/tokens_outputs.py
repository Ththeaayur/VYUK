"""Platform outputs + INV-11 proof (split from core/tokens.py, B6)."""

from __future__ import annotations

import difflib
import hashlib
import re

# INTENTIONAL (tokens split B6): cross-part names live in
# their owning modules; this module imports exactly the
# ones it uses.
from .tokens_theme import (
    _BG,
    _GLASS_CARD_GRADIENT,
    _GLASS_CARD_GRADIENT_INDIGO,
    _GLASS_CARD_TOP,
    _HOUSE_ACCENT_2_HOVER,
    _HOUSE_ACCENT_2_PRESSED,
    _HOUSE_ACCENT_HOVER,
    _HOUSE_ACCENT_PRESSED,
    _HOUSE_DISABLED_A,
    _HOUSE_DISABLED_B,
    _HOUSE_PALE,
    _SURFACE_1,
    _SURFACE_2,
    _TEXT,
    _TEXT_FAINT,
    _TEXT_MUTED,
    _hex_rgb,
    _triplet,
    RESOLVED_KEYS,
    build_all_python_tokens,
    build_python_tokens,
    dtcg_source,
    theme_names,
)
from .tokens_oklch import oklch_of
from .tokens_radix import radix_steps


# ------------------------------------------------------------- platform outputs
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


# White-alpha washes (hover / press feedback, hairlines, the
# sidebar tint): on a light theme they are remapped to the text
# colour at the same alpha by _remap (and by the shipped
# _remap_colors in gui/style_theme.py).
_WHITE_WASH_RE = re.compile(
    r"rgba\(255, 255, 255, ([0-9]*\.?[0-9]+)\)")


def generate_qss_tokens(theme_name: str) -> str:
    """QSS token block for a theme (authoring output, in-memory only).

    Qt QSS has no variables, so this block is a comment block that
    documents the theme's tokens for authoring/review — it is NOT
    applied QSS. Applied output is :func:`generate_stylesheet`.
    Deterministic: same theme always yields the same block.
    """
    tok = build_python_tokens(theme_name)
    lines = [
        f"/* ---- v5.64 tokens: {theme_name} (DTCG -> QSS tokens) ---- */",
    ]
    for key in RESOLVED_KEYS:
        lines.append(f"/* color.theme.{key}: {tok[key]} */")
    accent_oklch = oklch_of(tok["accent"])
    lines.append(
        "/* accent OKLCH: L={:.4f} C={:.4f} H={:.1f} "
        "(authoring value, see module docstring) */".format(*accent_oklch)
    )
    lines.append("/* Radix-shaped steps: see radix_steps() (local values) */")
    return "\n".join(lines) + "\n"


def _remap(sheet: str, resolved: dict) -> str:
    """Re-implementation of the shipped single-pass colour remap.

    Same pairs, same rule (a replacement is never rescanned; pairs
    whose old and new spellings are equal are skipped; the regex
    alternation is longest-first). Kept as an independent
    re-implementation so the proof measures token values.
    """
    # The pair list is explicit so the house spellings stay auditable
    # in one place.
    pairs = [
        ("#35D6FF", resolved["accent"]),
        ("#3D8BFF", resolved["accent_2"]),
        (_HOUSE_ACCENT_HOVER, resolved["accent_hover"]),
        (_HOUSE_ACCENT_2_HOVER, resolved["accent_2_hover"]),
        (_HOUSE_ACCENT_PRESSED, resolved["accent_pressed"]),
        (_HOUSE_ACCENT_2_PRESSED, resolved["accent_2_pressed"]),
        (_HOUSE_PALE, resolved["highlight"]),
        (_HOUSE_DISABLED_A, resolved["disabled_a"]),
        (_HOUSE_DISABLED_B, resolved["disabled_b"]),
        (_BG, resolved["bg"]),
        (_SURFACE_1, resolved["surface_1"]),
        (_SURFACE_2, resolved["surface_2"]),
        (_TEXT, resolved["text"]),
        (_TEXT_MUTED, resolved["text_muted"]),
        (_TEXT_FAINT, resolved["text_faint"]),
        (_triplet("#35D6FF"), _triplet(resolved["accent"])),
        (_triplet("#3D8BFF"), _triplet(resolved["accent_2"])),
        (_triplet(_TEXT), _triplet(resolved["text"])),
    ]
    mapping = {old: new for old, new in pairs if old != new}
    if mapping:
        pattern = re.compile("|".join(
            re.escape(old) for old in
            sorted(mapping, key=len, reverse=True)))
        sheet = pattern.sub(lambda m: mapping[m.group(0)], sheet)
    # Light chrome: the white-alpha washes the sheet uses for
    # hover / press feedback, hairlines and the sidebar tint
    # vanish on a light background. Remap each wash to the theme
    # text colour at the same alpha — the same strength, as a
    # dark wash. Mirror of gui/style_theme._remap_colors; keep
    # the two in step (the proof compares their outputs).
    if _luminance(resolved["bg"]) > 0.5:
        r, g, b = _hex_rgb(resolved["text"])
        sheet = _WHITE_WASH_RE.sub(
            lambda m: "rgba({}, {}, {}, {})".format(
                r, g, b, m.group(1)), sheet)
    return sheet


def generate_stylesheet(theme_name: str | None, shipped) -> str:
    """Generated themed stylesheet (in-memory only, never written).

    ``theme_name`` is one of the 12 themes; ``None`` yields the house
    sheet (no theme, no glass treatment). The QSS template and the
    glass treatment are template inputs read from the shipped style
    module; the DTCG values under test are the resolved tokens
    from :func:`build_python_tokens`.

    ``shipped`` is the shipped style module (gui/style), passed in
    by the caller — GAPS-15 (2026-11-13): this module used to
    import it itself, a core -> GUI import. The caller that has
    the style (the GUI side, or a test that loaded it) supplies
    it; this module never reaches into GUI.
    """
    _shipped = shipped

    tokens = dict(_shipped._TOKENS)
    if theme_name is None:
        return _shipped._QSS.substitute(tokens)
    resolved = build_python_tokens(theme_name)
    tokens["CARD_GRADIENT"] = _GLASS_CARD_GRADIENT
    tokens["CARD_GRADIENT_INDIGO"] = _GLASS_CARD_GRADIENT_INDIGO
    tokens["CARD_TOP"] = _GLASS_CARD_TOP
    if resolved["focus_border"]:
        tokens["FOCUS_BORDER"] = resolved["focus_border"]
    # Selection and disabled text follow the resolved theme
    # (light themes need a solid selection and a dark disabled
    # tint — mirrors gui/style.build_stylesheet).
    tokens["SELECTION_BG"] = resolved["selection_bg"]
    tokens["TEXT_DISABLED"] = resolved["text_disabled"]
    return _remap(_shipped._QSS.substitute(tokens), resolved)


def style_dictionary_build() -> dict:
    """Style-Dictionary-shaped build: source -> platform outputs.

    Returns ``{"source", "platforms": {"python", "qss_tokens",
    "oklch", "radix"}}`` — all in memory, deterministic (building
    twice yields equal output). Full stylesheets are NOT part of
    this build; they are produced by :func:`generate_stylesheet`
    and compared by :func:`prove_byte_identity`.
    """
    python_tokens = build_all_python_tokens()
    return {
        "source": dtcg_source(),
        "platforms": {
            "python": python_tokens,
            "qss_tokens": {name: generate_qss_tokens(name)
                           for name in theme_names()},
            "oklch": {name: {
                "accent": oklch_of(python_tokens[name]["accent"]),
                "accent_2": oklch_of(python_tokens[name]["accent_2"]),
            } for name in theme_names()},
            "radix": {name: radix_steps(name) for name in theme_names()},
        },
    }


# ------------------------------------------------------------- INV-11 proof
def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prove_byte_identity(shipped, max_diff_lines: int = 60) -> dict:
    """Compare generated output to shipped style bytes, theme by theme.

    ``shipped`` is the shipped style module (gui/style), passed in
    by the caller — GAPS-15 (2026-11-13): this module used to
    import it itself, a core -> GUI import.

    For each of the 12 themes this reports:

      * ``tokens_identical`` — resolved DTCG tokens vs the shipped
        resolver's output for the shipped theme dict;
      * ``sheet_identical`` — generated themed stylesheet bytes vs
        ``build_stylesheet(name)`` bytes;
      * both SHA-256 digests + lengths, token diffs, and a unified
        sheet diff (capped at ``max_diff_lines`` lines).

    Plus a ``house`` row (generated house sheet vs the shipped house
    sheet) reported separately from the 9-theme verdict.

    ``identical_all`` covers the 12 themes only (tokens AND sheets).
    ``generated_in_tree`` is always False: this function never writes
    anything, and this module never writes generated output anywhere.

    If ``identical_all`` is False, the gate action is: DO NOT re-pin,
    DO NOT edit shipped style files, keep generated output out of the
    tree, and surface the diff + the explicit re-pin decision. The
    proof output itself is the handoff artefact — it is not, by
    itself, a pass.
    """
    _shipped = shipped

    themes: dict[str, dict] = {}
    all_identical = True
    for name in theme_names():
        generated_tokens = build_python_tokens(name)
        shipped_tokens = _shipped._resolve_theme(_shipped.THEMES[name])
        token_diffs = [
            f"{key}: generated={generated_tokens.get(key)!r} "
            f"shipped={shipped_tokens.get(key)!r}"
            for key in RESOLVED_KEYS
            if generated_tokens.get(key) != shipped_tokens.get(key)
        ]
        generated_sheet = generate_stylesheet(name, _shipped)
        shipped_sheet = _shipped.build_stylesheet(name)
        sheet_diff: list[str] = []
        if generated_sheet != shipped_sheet:
            full = list(difflib.unified_diff(
                shipped_sheet.splitlines(),
                generated_sheet.splitlines(),
                fromfile="shipped", tofile="generated",
                lineterm=""))
            sheet_diff = full[:max_diff_lines]
            if len(full) > max_diff_lines:
                sheet_diff.append(
                    f"... diff truncated: {len(full)} lines total, "
                    f"showing first {max_diff_lines}")
        tokens_identical = not token_diffs
        sheet_identical = generated_sheet == shipped_sheet
        if not (tokens_identical and sheet_identical):
            all_identical = False
        themes[name] = {
            "tokens_identical": tokens_identical,
            "sheet_identical": sheet_identical,
            "identical": tokens_identical and sheet_identical,
            "generated_sha256": _sha256(generated_sheet),
            "shipped_sha256": _sha256(shipped_sheet),
            "generated_len": len(generated_sheet),
            "shipped_len": len(shipped_sheet),
            "token_diffs": token_diffs,
            "sheet_diff": sheet_diff,
        }
    house_generated = generate_stylesheet(None, _shipped)
    house_shipped = _shipped.PREMIUM_STYLE
    return {
        "identical_all": all_identical,
        "generated_in_tree": False,
        "themes": themes,
        "house": {
            "identical": house_generated == house_shipped,
            "generated_sha256": _sha256(house_generated),
            "shipped_sha256": _sha256(house_shipped),
            "generated_len": len(house_generated),
            "shipped_len": len(house_shipped),
        },
        "gate_action_if_not_identical": (
            "DO NOT re-pin; DO NOT modify shipped style files; keep "
            "generated output OUT of the tree; report the diff and "
            "surface the explicit re-pin decision for the gate."
        ),
    }


def proof_table_text(proof: dict | None = None, shipped=None) -> str:
    """Human-readable per-theme proof table (for the handoff).

    With no ``proof`` given, the proof is computed from
    ``shipped`` (the shipped style module, passed in — GAPS-15);
    without either, there is nothing to tabulate and this raises
    ValueError rather than reaching into GUI itself.
    """
    if proof is None:
        if shipped is None:
            raise ValueError(
                "proof_table_text needs a proof dict or the shipped "
                "style module")
        proof = prove_byte_identity(shipped)
    lines = [
        "v5.64 token byte-identity proof (generated vs shipped bytes)",
        f"identical_all (12 themes): {proof['identical_all']}",
        f"generated_in_tree: {proof['generated_in_tree']}",
        "",
        f"{'theme':<14} {'tokens':<8} {'sheet':<8} "
        f"{'gen_len':>8} {'ship_len':>8}",
    ]
    for name, row in proof["themes"].items():
        lines.append(
            f"{name:<14} "
            f"{'IDENT' if row['tokens_identical'] else 'DIFF':<8} "
            f"{'IDENT' if row['sheet_identical'] else 'DIFF':<8} "
            f"{row['generated_len']:>8} {row['shipped_len']:>8}"
        )
    house = proof["house"]
    lines.append(
        f"{'house':<14} {'-':<8} "
        f"{'IDENT' if house['identical'] else 'DIFF':<8} "
        f"{house['generated_len']:>8} {house['shipped_len']:>8}"
    )
    for name, row in proof["themes"].items():
        for diff in row["token_diffs"]:
            lines.append(f"  TOKEN DIFF {name}: {diff}")
        for diff in row["sheet_diff"]:
            lines.append(f"  SHEET DIFF {name}: {diff}")
    return "\n".join(lines) + "\n"
