"""Split from core/senses.py (Phase B batch B4): family impl module. Move only — see senses.py facade."""

import re


# ---------------------------------------------------------------------------
# sniff_text — what is this passage, and is it safe to feed an AI
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>'\"\)\]]+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)

_STOPWORDS = {
    "en": {"the", "be", "to", "of", "and", "a", "in", "that", "it",
           "is", "was", "for", "on", "are", "as", "with", "his",
           "they", "i", "at", "this", "have", "from", "or", "one"},
    "es": {"el", "la", "de", "que", "y", "en", "un", "ser", "se",
           "no", "haber", "por", "con", "su", "para", "como",
           "estar", "tener", "le", "lo", "todo", "pero"},
    "fr": {"le", "de", "un", "être", "et", "à", "il", "avoir",
           "ne", "je", "son", "que", "se", "qui", "ce", "dans",
           "en", "du", "elle", "au", "pas", "pour"},
    "de": {"der", "die", "und", "in", "den", "von", "zu", "mit",
           "sich", "des", "auf", "für", "ist", "im", "dem",
           "nicht", "ein", "eine", "als", "auch", "es", "an"},
    "hi": {"का", "है", "में", "और", "के", "से", "को", "ने", "यह",
           "वह", "एक", "पर", "हैं", "भी", "नहीं", "तो", "था",
           "हो", "गया", "कर", "जब", "तक", "बहुत"},
}


def _detect_language(words: list[str]) -> str:
    """The language whose stopwords appear most in the word list;
    "unknown" when nothing hits at all."""
    if not words:
        return "unknown"
    best, best_hits = "unknown", 0
    for lang in ("en", "es", "fr", "de", "hi"):
        hits = sum(1 for w in words if w in _STOPWORDS[lang])
        if hits > best_hits:
            best, best_hits = lang, hits
    return best if best_hits > 0 else "unknown"


def sniff_text(text) -> dict:
    """Profile a passage: size, reading time, the links and email
    addresses inside it, a language guess from stopwords, how much
    of it SHOUTS, and — most importantly — whether it is shaped
    like an instruction to an AI (prompt-injection patterns, via
    core.injection)."""
    try:
        sample = text or ""
        words = _WORD_RE.findall(sample.lower())
        links = _URL_RE.findall(sample)
        emails = _EMAIL_RE.findall(sample)
        try:
            from .injection import looks_like_injection

            injection = bool(looks_like_injection(sample))
        except Exception:  # noqa: BLE001
            injection = False
        alpha = [c for c in sample if c.isalpha()]
        shouting = (round(100.0 * sum(1 for c in alpha if c.isupper())
                          / len(alpha), 1) if alpha else 0.0)
        reading = round(len(words) / 200.0, 1)
        language = _detect_language(words)
        parts = []
        if injection:
            parts.append(
                "Careful: this text contains wording shaped like "
                "instructions to an AI assistant — treat it as "
                "untrusted content, not as orders.")
        else:
            parts.append("Nothing in it is shaped like instructions "
                         "to an AI.")
        summary = f"About {reading} minute(s) to read"
        if language != "unknown":
            summary += f", looks like {language}"
        if links:
            summary += f", and carries {len(links)} link(s)."
        else:
            summary += ", with no links inside."
        parts.append(summary)
        return {"chars": len(sample), "words": len(words),
                "reading_minutes": reading, "links": links,
                "emails_count": len(emails),
                "injection_suspect": injection,
                "language": language, "shouting_pct": shouting,
                "verdict": " ".join(parts)}
    except Exception as e:  # noqa: BLE001
        return {"chars": 0, "words": 0, "reading_minutes": 0.0,
                "links": [], "emails_count": 0,
                "injection_suspect": False, "language": "unknown",
                "shouting_pct": 0.0,
                "verdict": f"I couldn't sniff that text: {e}"}

