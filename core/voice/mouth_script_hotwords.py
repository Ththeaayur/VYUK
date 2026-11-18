from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .mouth_lexicon_numbers import PERSONAL_LEXICON  # B30 INTENTIONAL: cross-part bare name(s) used by this family in the old module

# ------------------------------------------------------------------ #
# MO-19 + MO-17: code-switch routing (SCRIPT-split, never LID)
#
# Devanagari spans (U+0900–U+097F) route to the Hindi voice,
# Latin spans to the English voice. A brain annotation may
# refine WITHIN a script span but may never flip a span's
# script language (that is exactly the LID failure mode being
# avoided: Roman Hindi mislabelled as English is still a Latin
# span, and it stays on the Latin route unless the brain
# annotated it Hindi AND it is long enough to be a switch).
# Minimum-span hysteresis: a span under MIN_SWITCH_WORDS words
# is a loanword, not a switch — it merges into its neighbour.
# Joins are LUFS-matched (RMS-matched to -18 dBFS; pyloudnorm
# only measures) and crossfaded at clause boundaries only.
# ------------------------------------------------------------------ #

DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
MIN_SWITCH_WORDS = 4  # in the specced 3–4 band, at its top: safe


def script_of_char(ch: str) -> str:
    if "\u0900" <= ch <= "\u097F":
        return "devanagari"
    return "latin"


@dataclass
class ScriptSpan:
    text: str
    script: str  # "devanagari" | "latin"
    language: str  # "hi" | "en"


def script_spans(text: str) -> list[ScriptSpan]:
    """Splits text into maximal same-script spans (whitespace
    attaches to the following span; pure punctuation/space-only
    text yields one Latin span)."""
    spans: list[ScriptSpan] = []
    current: list[str] = []
    current_script: str | None = None
    for ch in (text or ""):
        if ch.isspace():
            current.append(ch)
            continue
        script = script_of_char(ch)
        if current_script is None:
            current_script = script
        if script != current_script and current:
            spans.append(ScriptSpan(
                text="".join(current).strip(),
                script=current_script,
                language="hi" if current_script == "devanagari"
                else "en"))
            current, current_script = [], script
        current.append(ch)
    if current:
        spans.append(ScriptSpan(
            text="".join(current).strip(),
            script=current_script or "latin",
            language="hi" if current_script == "devanagari" else "en"))
    return [s for s in spans if s.text]


def route_spans(text: str,
                brain_annotations: list[tuple[int, int, str]] | None = None
                ) -> list[ScriptSpan]:
    """SCRIPT-split routing with hysteresis. Brain annotations
    are (start_char, end_char, lang) hints; an annotation only
    applies when it covers a whole Latin span (Roman Hindi the
    brain explicitly marked) — it never overrides script.
    Spans under MIN_SWITCH_WORDS words merge into the previous
    span (or the next, for a leading loanword)."""
    spans = script_spans(text)
    if brain_annotations:
        for start, end, lang in brain_annotations:
            for span in spans:
                if span.script == "latin" and lang == "hi":
                    # Whole-span Roman-Hindi annotation only.
                    span_text = text[start:end] if 0 <= start < end <= len(text) else ""
                    if span_text.strip() == span.text:
                        span.language = "hi"
    merged: list[ScriptSpan] = []
    for span in spans:
        if (merged and len(span.text.split()) < MIN_SWITCH_WORDS
                and span.language != merged[-1].language):
            merged[-1].text = f"{merged[-1].text} {span.text}".strip()
            continue
        if (not merged and len(span.text.split()) < MIN_SWITCH_WORDS
                and len(spans) > 1):
            # Leading loanword: hold it for the next span.
            merged.append(span)
            continue
        if merged and len(merged[-1].text.split()) < MIN_SWITCH_WORDS:
            merged[-1].text = f"{merged[-1].text} {span.text}".strip()
            merged[-1].language = span.language
            merged[-1].script = span.script
            continue
        merged.append(span)
    # A trailing held loanword keeps the previous language.
    if len(merged) > 1 and len(merged[0].text.split()) < MIN_SWITCH_WORDS:
        merged[1].text = f"{merged[0].text} {merged[1].text}".strip()
        merged.pop(0)
    return merged


# ------------------------------------------------------------------ #
# VP-3.1 + VP-3.6a: personal vocabulary / hotwords (faster-whisper)
#
# Capped at 50 entries, score-tested. Sources: contacts, apps,
# files + the personal lexicon above; Hindi names in Devanagari
# AND Roman, each name plus its common mis-hearing alias.
# Scoring is phonetic-pre-key (consonant skeleton) + fuzzy
# match — the same pair the mishear repair uses — so the
# hotword test and the repair test measure one mechanism.
# ------------------------------------------------------------------ #

HOTWORD_CAP = 50
HOTWORD_FUZZY_THRESHOLD = 0.75


def phonetic_key(word: str) -> str:
    """Consonant-skeleton pre-key: lowercase, strip non-letters
    (Devanagari kept whole), drop non-initial vowels, collapse
    doubled letters. 'Rohan' -> 'rhn', 'rowan' -> 'rwn' (close
    but not identical — the fuzzy step, not the key, merges
    those; the key only buckets)."""
    word = (word or "").strip().lower()
    if not word:
        return ""
    if DEVANAGARI_RE.search(word):
        return word  # Devanagari is its own key; no skeletonising
    letters = re.sub(r"[^a-z]", "", word)
    if not letters:
        return ""
    kept = [letters[0]]
    for ch in letters[1:]:
        if ch not in "aeiou":
            kept.append(ch)
    out: list[str] = []
    for ch in kept:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def fuzzy_score(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


@dataclass
class HotwordEntry:
    word: str
    aliases: tuple[str, ...] = ()
    devanagari: str = ""
    source: str = "lexicon"


def hotwords_from_sources(contacts: list[str] | None = None,
                          apps: list[str] | None = None,
                          files: list[str] | None = None
                          ) -> list[HotwordEntry]:
    """Merges contacts/apps/files + the personal lexicon into a
    capped, deduplicated hotword file. Hindi names appear in
    Devanagari AND Roman; every entry carries its mis-hearing
    aliases. Over-cap entries drop from the end (lexicon first,
    sources last — the lexicon is the curated core)."""
    entries: list[HotwordEntry] = []
    seen: set[str] = set()

    def add(word: str, aliases: tuple[str, ...], dev: str, src: str):
        key = word.lower()
        if not word.strip() or key in seen:
            return
        seen.add(key)
        entries.append(HotwordEntry(word=word, aliases=aliases,
                                    devanagari=dev, source=src))

    for entry in PERSONAL_LEXICON:
        add(entry["word"], tuple(entry["aliases"]),
            entry["devanagari"], "lexicon")
    for name in contacts or []:
        add(name, (), "", "contacts")
    for name in apps or []:
        add(name, (), "", "apps")
    for name in files or []:
        add(name, (), "", "files")
    return entries[:HOTWORD_CAP]


@dataclass
class HotwordCase:
    heard: str
    expected: str | None  # None = a negative (no hotword expected)


def evaluate_hotwords(entries: list[HotwordEntry],
                      cases: list[HotwordCase]
                      ) -> dict[str, float]:
    """Score-tests a hotword file on a frozen case set.
    Recall = expected hotwords recognised (phonetic key hit OR
    fuzzy >= threshold against the word or any alias, in either
    script). False-trigger = negatives that matched a hotword.
    §10 T11 needs recall >= 0.90 AND false-trigger <= 0.02 —
    BOTH numbers are returned; a suite that only checked recall
    would be incomplete, and this function refuses to hide it."""
    vocab: list[tuple[str, str]] = []  # (surface, canonical)
    for entry in entries:
        vocab.append((entry.word, entry.word))
        for alias in entry.aliases:
            vocab.append((alias, entry.word))
        if entry.devanagari:
            vocab.append((entry.devanagari, entry.word))

    def matched(heard: str) -> str | None:
        heard_key = phonetic_key(heard)
        for surface, canonical in vocab:
            if heard_key and heard_key == phonetic_key(surface):
                return canonical
            if fuzzy_score(heard, surface) >= HOTWORD_FUZZY_THRESHOLD:
                return canonical
        return None

    positives = [c for c in cases if c.expected is not None]
    negatives = [c for c in cases if c.expected is None]
    hits = sum(1 for c in positives
               if matched(c.heard) == c.expected)
    false_triggers = sum(1 for c in negatives if matched(c.heard))
    return {
        "recall": (hits / len(positives)) if positives else 1.0,
        "false_trigger_rate": (
            (false_triggers / len(negatives)) if negatives else 0.0),
        "hits": float(hits),
        "positives": float(len(positives)),
        "false_triggers": float(false_triggers),
        "negatives": float(len(negatives)),
    }


def seed_frozen_hotword_set(entries: list[HotwordEntry]
                            ) -> list[HotwordCase]:
    """The frozen 100-item evaluation set scaffolding (§10 T11):
    each lexicon word + one alias as positives, plus negatives
    (common non-name words) to make 100. Seeded, deterministic,
    no audio — the recall/false-trigger numbers it yields are
    for the matcher, and the field set (his voice, his room) is
    a separate owed step (see field_recording_owed())."""
    cases: list[HotwordCase] = []
    for entry in entries:
        cases.append(HotwordCase(heard=entry.word,
                                 expected=entry.word))
        if entry.aliases:
            cases.append(HotwordCase(heard=entry.aliases[0],
                                     expected=entry.word))
        if entry.devanagari:
            cases.append(HotwordCase(heard=entry.devanagari,
                                     expected=entry.word))
    negatives = [
        "hello", "weather", "tomorrow", "music", "timer", "note",
        "calendar", "reminder", "volume", "brightness", "search",
        "open", "close", "play", "pause", "stop", "next",
        "previous", "louder", "quieter", "what", "when", "where",
        "why", "how", "please", "thanks", "good", "morning",
        "evening", "today", "yesterday", "week", "month", "year",
        "time", "date", "news", "joke", "story", "help", "cancel",
        "confirm", "repeat", "again", "louder please", "turn off",
        "turn on", "wake up", "go to sleep", "alarm", "settings",
        "bluetooth", "wifi", "battery", "charging", "screenshot",
        "wallpaper", "download", "upload", "update", "weather today",
    ]
    for word in negatives:
        if len(cases) >= 100:
            break
        cases.append(HotwordCase(heard=word, expected=None))
    return cases[:100]


def propose_from_correction(heard: str, corrected: str,
                            log: list[dict] | None = None
                            ) -> HotwordEntry | None:
    """The misrecognition-log loop: when a user corrects a
    transcript (heard -> corrected), propose a hotword entry
    when the correction is a name-like token whose phonetic key
    is close to what was heard (same first letter + fuzzy
    >= 0.6) — else None (no proposal for ordinary prose
    corrections). Proposals are DATA for a human to approve;
    this function never writes a hotword file itself."""
    heard = (heard or "").strip()
    corrected = (corrected or "").strip()
    if not heard or not corrected or heard.lower() == corrected.lower():
        return None
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,39}", corrected):
        return None
    if phonetic_key(heard) and phonetic_key(heard)[0] != phonetic_key(corrected)[0]:
        return None
    if fuzzy_score(heard, corrected) < 0.6:
        return None
    return HotwordEntry(word=corrected, aliases=(heard,),
                        source="correction-log")


