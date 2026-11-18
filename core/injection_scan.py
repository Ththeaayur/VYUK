"""Injection screen (split from core/injection.py, B9).

Patterns, normalisation, and looks_like_injection. No dependency on
the other injection families: this module is the base of the chain.
"""

from __future__ import annotations

import re
import unicodedata

# The screen. Conservative on purpose: every pattern is an
# instruction-to-assistant shape, not a topic. Ordinary prose — even
# prose ABOUT computers, email or instructions — must not trip these.
# --------------------------------------------------------------------- #

_INJECTION_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "ignore/disregard/forget/override … instructions/prompts/rules"
        # ("ignore your previous instructions", "disregard all prior rules")
        r"\b(?:ignore|disregard|forget|override|bypass|skip)\b"
        r"[^.\n]{0,40}?\b(?:instructions?|prompts?|rules?|guidelines|directions)\b",
        # "you are now …" (persona takeover)
        r"\byou are now\b",
        # meta references to the machinery
        r"\bsystem prompt\b",
        r"\bdeveloper (?:mode|instructions?)\b",
        # direct orders to run things
        r"\brun this command\b",
        r"\b(?:run|execute|open|launch|start)\b[^.\n]{0,30}?"
        r"\b(?:powershell|command prompt|cmd\.exe|terminal|shell)\b",
        # "send an email to X saying …" (act-and-report shape; the
        # span must cross the dots inside an email address, so it is
        # bounded by line, not by sentence punctuation)
        r"\bsend (?:an? )?(?:e-?mail|message) to\b[^\n]{0,80}?\bsaying\b",
        # secrecy toward the user — the classic tell
        r"\bdo not (?:tell|inform|alert|notify) the user\b",
        r"\bdon'?t (?:tell|inform|alert|notify) the user\b",
        r"\bwithout (?:telling|asking) the user\b",
        # persona jailbreak framings
        r"\bpretend (?:that )?you are\b",
        r"\bact as if you (?:are|were)\b",
        # tool-call-shaped JSON embedded in content (the local brain's
        # protocol shape or the generic function-call shape)
        r"\{\s*\"tool\"\s*:",
        r"\{\s*\"(?:function_call|tool_call|name)\"\s*:\s*\"[a-z_0-9]+\""
        r"\s*,\s*\"(?:arguments|parameters)\"\s*:",
    )
)

# Screens scan a bounded prefix: injections are placed to be read,
# and an unbounded scan of a megabyte page costs real time on the
# worker thread. 20k chars is far past any plausible placement.
_SCAN_CAP = 20_000


# --------------------------------------------------------------------- #
# Normalisation (v5.45, H12) — the screen must see CANONICAL text.
#
# An attacker who writes "ign\u200bore your instructions" or swaps
# Latin letters for Cyrillic/Greek lookalikes ("ign\u043ere") is
# writing for the MODEL's eyes, which read through the disguise —
# a regex over the raw bytes does not. So looks_like_injection
# screens normalize_for_screen(text), never the raw text:
#   * NFKC (fullwidth letters, ligatures and compatibility forms
#     fold to their plain equivalents),
#   * zero-width and bidi-control characters are stripped (they
#     exist in prose only to break matching or reorder display),
#   * common Cyrillic/Greek homoglyphs fold to the Latin letters
#     they imitate.
# Normalisation is screen-only: producers keep and display the
# original text; only the pattern match sees the canonical form.
# --------------------------------------------------------------------- #

_ZERO_WIDTH_AND_BIDI = frozenset(
    "\u200b\u200c\u200d\u2060\ufeff"   # zero-width space/joiners, BOM
    "\u202a\u202b\u202c\u202d\u202e"   # bidi embeddings/overrides
    "\u2066\u2067\u2068\u2069"         # bidi isolates
    "\u200e\u200f\u061c"               # LRM / RLM / ALM
)

_HOMOGLYPHS = {
    # Cyrillic lookalikes
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x",
    "у": "y", "ѕ": "s", "і": "i", "ј": "j", "һ": "h", "ԁ": "d",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    # Greek lookalikes
    "α": "a", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Χ": "X",
}


def normalize_for_screen(text: str) -> str:
    """Canonical form of `text` for the injection screen (H12):
    NFKC, zero-width/bidi controls stripped, homoglyphs folded.
    Never raises; non-string input screens as empty."""
    if not text or not isinstance(text, str):
        return ""
    try:
        folded = unicodedata.normalize("NFKC", text)
    except Exception:  # noqa: BLE001 — the screen must never break
        folded = text
    out = []
    for ch in folded:
        if ch in _ZERO_WIDTH_AND_BIDI:
            continue
        out.append(_HOMOGLYPHS.get(ch, ch))
    return "".join(out)


# --------------------------------------------------------------------- #
# Multilingual coverage (v5.45, H12) — the "ignore previous
# instructions" family in Hindi and the major European languages.
# Patterns stay TIGHT (an ignore/forget verb within a short span of
# an instructions noun, or the fixed "previous instructions" phrase
# in-language): ordinary prose in these languages must not trip the
# screen, because a false positive costs the user a popup (taint).
# --------------------------------------------------------------------- #

_MULTILINGUAL_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # French: ignorez / oubliez … instructions/consignes
        r"\b(?:ignorez?|oubliez?|négligez?)\b[^.\n]{0,40}?"
        r"\b(?:instructions?|consignes|directives)\b",
        r"\binstructions?\s+(?:précédentes?|antérieures?)\b",
        # Spanish: ignora / olvida … instrucciones
        r"\b(?:ignora|ignorad|ignorar|olvida|olvidad|olvidar)\b"
        r"[^.\n]{0,40}?\b(?:instrucciones|indicaciones|órdenes)\b",
        r"\binstrucciones\s+(?:anteriores|previas)\b",
        # German: ignoriere/ignorieren / vergiss … Anweisungen
        r"\b(?:ignoriere|ignorieren|ignoriert|vergiss|vergesst|"
        r"missachte|missachten)\b[^.\n]{0,40}?"
        r"\b(?:anweisungen|anleitungen|instruktionen|anweisung)\b",
        r"\bvorherigen\s+(?:anweisungen|anleitungen|instruktionen)\b",
        # Italian: ignora / dimentica … istruzioni
        r"\b(?:ignora|ignorate|ignorare|dimentica|dimenticate)\b"
        r"[^.\n]{0,40}?\b(?:istruzioni|indicazioni|direttive)\b",
        r"\bistruzioni\s+(?:precedenti|anteriori)\b",
        # Portuguese: ignore / esqueça … instruções
        r"\b(?:ignore|ignorem|ignorar|esqueça|esqueçam|esquecer)\b"
        r"[^.\n]{0,40}?\b(?:instruções|instrucoes|indicações|ordens)\b",
        r"\binstruções\s+(?:anteriores|prévias)\b",
        # Dutch: negeer / vergeet … instructies
        r"\b(?:negeer|negeren|vergeet|vergeten)\b[^.\n]{0,40}?"
        r"\b(?:instructies|aanwijzingen|instructie)\b",
        r"\bvorige\s+(?:instructies|aanwijzingen)\b",
        # Hindi (Devanagari): अनदेखा/नज़रअंदाज़/भूल जाओ … निर्देश,
        # and the fixed phrase पिछले निर्देश ("previous instructions")
        r"(?:अनदेखा|नजरअंदाज|नज़रअंदाज़|भूल\s*जाओ|भुला\s*दो)"
        r"[^।\n]{0,40}?(?:निर्देश|आदेश|हिदायत)",
        # Hindi is SOV: the noun usually comes FIRST ("निर्देश
        # भूल जाओ" — "instructions, forget [them]").
        r"(?:निर्देश|आदेश|हिदायत)[^।\n]{0,20}?"
        r"(?:अनदेखा|नजरअंदाज|नज़रअंदाज़|भूल|भुला)",
        r"पिछले\s+(?:सभी\s+)?निर्देश",
        r"पिछली\s+(?:सभी\s+)?हिदायतें",
    )
)

_ALL_PATTERNS = _INJECTION_PATTERNS + _MULTILINGUAL_PATTERNS


def looks_like_injection(text: str) -> bool:
    """True when `text` contains an instruction-to-assistant shape.
    Conservative: ordinary prose must not trip this (see module
    docstring). The screen always runs over the NORMALISED text
    (H12), so zero-width, bidi and homoglyph disguises don't hide
    a match. Never raises."""
    if not text:
        return False
    try:
        sample = normalize_for_screen(str(text))[:_SCAN_CAP]
    except Exception:  # noqa: BLE001
        return False
    return any(p.search(sample) for p in _ALL_PATTERNS)
