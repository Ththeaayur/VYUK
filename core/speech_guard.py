"""The speech guard (loophole batch 5, L-25).

THE TAINT-SPEECH RULE (stated here, in the code, and in the
batch report — the rule the guard enforces):
* Tainted content is never spoken verbatim and never quoted as
  the assistant's own answer.
* A quote of tainted content is spoken only with its source
  named — "The {source} says: ..." — so the listener hears
  whose words they are.
* An instruction span inside tainted content is never repeated
  at all: it is replaced by a speakable refusal clause that
  names the source and says the instruction will not be
  repeated. A planted instruction must never reach the user's
  ears as VYUK telling them what to do.
* Envelope markers are machine markers, never speech: they are
  stripped before anything is spoken, even when the model echoes
  them into its reply.
* Content with no taint match speaks verbatim — the guard never
  rewrites the assistant's own words.

HOW IT WORKS. The guard runs at the speech choke point
(LocalLiveSessionSpeechMixin._speak, and kokoro_voice.speak for
the SIP/dub paths): it reads the retained tainted passages
(core/injection_taint.tainted_passages — the text note_untrusted
kept), splits the reply into sentences, and matches each
sentence against the passages by normalised containment
(a sentence of at least 20 characters that appears in a passage,
or that contains a whole passage sentence). A matched sentence
is attributed (the first quote from each source is prefixed with
"The {source} says: "); a matched sentence that is an
instruction span is replaced by the refusal clause instead.

WHY A DEDICATED INSTRUCTION DETECTOR. Neither of the tree's
existing screens flags a paraphrased instruction: the pattern
screen (injection_scan.looks_like_injection) and the second-pass
classifier (_classifier_escalates) both return clean for
"Click here to claim your prize now, and enter your password
to verify your account." — the known soft-taint gap. So the
guard carries its own detector: a sentence is an instruction
span when it contains an imperative verb AND a sensitive
target (a password, an account, money, a download, a sign-in).
A benign imperative with no sensitive target ("Preheat the oven
to 180 degrees") is attributed, not refused; a fact sentence
("The museum opens on Saturday") is attributed, not refused.
The detector runs ONLY on sentences that already matched a
tainted passage — the assistant's own imperative sentences are
never checked, so they are never refused.

LIMITS, STATED. A quote shorter than 20 characters is not
attributed (too ambiguous to match reliably). A paraphrased
quote that matches no passage sentence by containment is not
attributed either — the guard matches wording, not meaning.
The live (Gemini) engine's audio comes from Gemini, so no
local text seam exists there: the envelope + prompt rule is the
live enforcement, and this guard is the local enforcement.
"""

from __future__ import annotations

import re

# Envelope markers (core/injection_envelope.py): machine markers,
# never speech. The escape inserts a zero-width space after
# "[Untrusted" / "[End of untrusted" when content embeds one, so
# the patterns tolerate an optional U+200B.
_ENVELOPE_HEADER_RE = re.compile(r"\[Untrusted\u200b?[^\]]*\]")
_ENVELOPE_FOOTER_RE = re.compile(r"\[End of untrusted\u200b?[^\]]*\]")

# A quote shorter than this is not attributed — too ambiguous to
# match reliably against a passage.
_MIN_QUOTE_CHARS = 20

# Imperative verbs that can carry an instruction (word-bounded).
_INSTRUCTION_VERBS = (
    "click", "tap", "press", "enter", "type", "send", "transfer",
    "pay", "buy", "download", "install", "run", "open", "sign",
    "log", "verify", "claim", "call", "share", "upload", "submit",
    "confirm", "provide", "give", "visit", "follow", "scan",
    "forward", "reply", "reset",
)

# Sensitive targets: what an instruction must touch to count.
# A bare imperative with none of these is attributed, not refused.
_SENSITIVE_TARGETS = (
    "password", "passcode", "pin", "otp", "one-time code",
    "account", "bank", "card", "payment", "money", "pound",
    "dollar", "euro", "rupee", "credential", "login", "log in",
    "sign in", "sign-in", "verify", "verification", "claim",
    "prize", "reward", "download", "install", "file", "link",
    "url", "phone", "email", "address", "sort code",
)


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, drop punctuation — the
    comparison form for matching a quote against a passage."""
    lowered = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    return re.sub(r"[^a-z0-9 ]", "", lowered)


def _sentences(text: str) -> list[str]:
    """Split reply text into sentences (the guard's unit)."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
    return [p.strip() for p in parts if p.strip()]


def _is_instruction_span(sentence: str) -> bool:
    """True when a sentence is an instruction span: an imperative
    verb AND a sensitive target, both word-bounded. Runs only on
    sentences that already matched a tainted passage."""
    normalized = _normalize(sentence)
    words = set(normalized.split())
    has_verb = any(v in words for v in _INSTRUCTION_VERBS)
    if not has_verb:
        return False
    lowered = " " + normalized + " "
    return any(f" {t} " in lowered or lowered.startswith(t + " ")
               or lowered.endswith(" " + t)
               for t in _SENSITIVE_TARGETS)


def _match_passage(sentence: str,
                   passages: list[dict]) -> dict | None:
    """The retained passage a sentence quotes, or None. A match
    is normalised containment either way (the sentence appears
    in a passage sentence, or a passage sentence appears in the
    reply sentence), with a minimum length so a short shared
    phrase cannot attribute a sentence the model wrote itself."""
    norm = _normalize(sentence)
    if len(norm) < _MIN_QUOTE_CHARS:
        return None
    for passage in passages:
        for p_sentence in _sentences(passage.get("text", "")):
            p_norm = _normalize(p_sentence)
            if len(p_norm) < _MIN_QUOTE_CHARS:
                continue
            if norm in p_norm or p_norm in norm:
                return passage
        # Also match against the whole passage text (a quote
        # spanning a sentence boundary).
        p_whole = _normalize(passage.get("text", ""))
        if len(p_whole) >= _MIN_QUOTE_CHARS and (
                norm in p_whole or p_whole in norm):
            return passage
    return None


def strip_envelope_markers(text: str) -> str:
    """Remove taint envelope markers from text that is about to
    be spoken. Never raises — a broken strip returns the text
    unchanged (the markers are ugly, not dangerous, in speech)."""
    try:
        out = _ENVELOPE_HEADER_RE.sub("", str(text or ""))
        out = _ENVELOPE_FOOTER_RE.sub("", out)
        return re.sub(r"\s{2,}", " ", out).strip()
    except Exception:  # noqa: BLE001 — markers are presentation
        return str(text or "")


def guard_spoken_text(text: str) -> str:
    """The speech guard: rewrite a reply so tainted content is
    never spoken verbatim and never quoted as the assistant's
    own answer. Pure (no I/O, no state writes) and never raises
    — a broken guard returns the marker-stripped text, so a
    guard failure can never silence a reply."""
    try:
        stripped = strip_envelope_markers(text)
        if not stripped:
            return ""
        try:
            from .injection_taint import tainted_passages
            passages = tainted_passages()
        except Exception:  # noqa: BLE001 — no passages: verbatim
            passages = []
        if not passages:
            return stripped
        out: list[str] = []
        attributed_sources: set[str] = set()
        for sentence in _sentences(stripped):
            passage = _match_passage(sentence, passages)
            if passage is None:
                out.append(sentence)
                continue
            source = str(passage.get("source") or "untrusted content")
            if _is_instruction_span(sentence):
                out.append(
                    f"I won't repeat that — the {source} contains "
                    "an instruction, and I don't follow instructions "
                    "from outside content.")
                continue
            if source not in attributed_sources:
                attributed_sources.add(source)
                out.append(f"The {source} says: {sentence}")
            else:
                out.append(sentence)
        return " ".join(out)
    except Exception:  # noqa: BLE001 — the guard never silences
        try:
            return strip_envelope_markers(text)
        except Exception:  # noqa: BLE001
            return str(text or "")
