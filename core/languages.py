"""Languages: her home preference + automatic voice switching (v5.41).

ONE canonical table (LANGUAGES) drives everything language-shaped:

* the first-run onboarding step ("Which language do you prefer?")
  and the Settings "Preferred language" combo — both list exactly
  these nine entries, with native names;
* the home voice: each entry carries the CURATED Kokoro voice for
  its language (picked 2026-10-22 from the Kokoro-82M VOICES.md
  quality grades + the owner's ear — see
  code-lab/VOICE_SWITCH_CURATION.md; do not re-curate casually);
* the prompt directive: a non-English home makes every brain reply
  in that language by default (reply_language_directive);
* automatic voice switching on the Kokoro path: detect_reply_
  language reads the REPLY text and kokoro_voice_for_text picks
  the voice for that utterance, so a Hindi answer is spoken by
  the Hindi voice and she returns home for English.

Setting the language (either surface) goes through
set_preferred_language, which sets preferred_language AND
kokoro_voice (to the curated voice) together. Afterwards the user
may pick a different voice in Settings — that changes kokoro_voice
only, and their explicit voice wins for home until the language
itself is changed again.

Scope note: automatic switching lives ONLY on the Kokoro path
(core/local_client._kokoro_synthesize). The Piper and SAPI rungs
are emergency fallbacks and never switch — a degraded voice
staying predictable beats a degraded voice guessing languages.
Switching is per UTTERANCE, by dominant language: a mixed reply
is spoken whole by the majority language's voice. Sentence-level
switching is deliberately out of scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import config as _config_module
from .config import config


@dataclass(frozen=True)
class Language:
    """One speakable language: its kokoro-onnx synthesis code
    (the table key), the picker label with the native name, the
    plain English name the prompt directive speaks in, and the
    curated Kokoro voice that is this language's home voice."""

    code: str
    label: str
    display: str
    voice: str


# The order is the picker order: the two Englishes first (GB is
# the product default home), then the rest of the Kokoro roster.
LANGUAGES: tuple[Language, ...] = (
    Language("en-us", "English (US)", "English", "am_michael"),
    Language("en-gb", "English (GB)", "English", "bm_george"),
    Language("hi", "Hindi — हिन्दी", "Hindi", "hm_omega"),
    Language("ja", "Japanese — 日本語", "Japanese", "jf_alpha"),
    Language("cmn", "Mandarin — 中文", "Mandarin", "zm_yunjian"),
    Language("es", "Spanish — Español", "Spanish", "em_alex"),
    Language("fr-fr", "French — Français", "French", "ff_siwis"),
    Language("it", "Italian — Italiano", "Italian", "im_nicola"),
    Language("pt-br", "Portuguese (BR) — Português", "Portuguese",
             "pm_alex"),
)

_BY_CODE = {entry.code: entry for entry in LANGUAGES}


# Mouth track (v5.1): the curated Kokoro voice roster for the Settings
# voice picker — (voice id, friendly label). Power users can still type
# any other Kokoro voice id in the line edit under the combo; a saved id
# outside this roster is preserved as a "Custom: <id>" combo entry.
# GAPS-15: moved verbatim from gui/window_widgets_helpers.py — it is
# voice data, so it lives in core; the GUI re-exports it from the
# facade (gui/window_widgets_helpers.py) and core/app_settings.py
# reads it from here instead of from the GUI.
_KOKORO_VOICE_ROSTER: tuple[tuple[str, str], ...] = (
    ("af_heart", "Heart — warm"),
    ("af_bella", "Bella — bright and friendly"),
    ("af_nicole", "Nicole — soft and calm"),
    ("af_sarah", "Sarah — clear and confident"),
    ("bf_emma", "Emma — British, composed"),
    ("bf_isabella", "Isabella — British, warm"),
    # v5.17 (Mouth M1–M6): six more, every one verified present in
    # the Kokoro model and preview-synthesized on the build machine.
    ("af_sky", "Sky — light and airy"),
    ("am_michael", "Michael — American male, steady"),
    ("am_puck", "Puck — American male, lively"),
    ("bf_alice", "Alice — British, bright"),
    ("bf_lily", "Lily — British, gentle"),
    ("bm_george", "George — British male, warm, the current default"),
)

# Both Englishes are "home": the switcher keys on LANGUAGE, not
# accent — US-flavoured text under a British home voice (or the
# reverse) never forces the other English voice.
ENGLISH_CODES = frozenset({"en-us", "en-gb"})

DEFAULT_LANGUAGE = "en-gb"


def language_by_code(code: str | None) -> Language | None:
    """The table entry for a synthesis code, or None."""
    if not code:
        return None
    return _BY_CODE.get(str(code).strip().lower())


def apply_language_session(code: str) -> Language | None:
    """The session-only language apply (round-3 wizard expansion):
    the language page is FIRST in the wizard, so the pick must
    drive the rest of the flow (voice page, sample sentence, Done
    greeting) while the wizard is still open — but Skip records
    nothing, so the durable write stays at Finish (the existing
    set_preferred_language call in the flow's _next). Sets
    config.preferred_language AND config.kokoro_voice in-process
    only; writes nothing to .env. Unknown codes change nothing."""
    entry = language_by_code(code)
    if entry is None:
        return None
    config.preferred_language = entry.code
    config.kokoro_voice = entry.voice
    return entry


def set_preferred_language(code: str) -> Language | None:
    """The one write path for the home language (onboarding +
    Settings both come through here): sets config.preferred_
    language AND config.kokoro_voice (the entry's curated voice)
    and persists both keys to .env. A later manual voice pick in
    Settings changes kokoro_voice only — see the module docstring.
    Unknown codes change nothing and return None."""
    entry = language_by_code(code)
    if entry is None:
        return None
    config.preferred_language = entry.code
    config.kokoro_voice = entry.voice
    for key, value in (("PREFERRED_LANGUAGE", entry.code),
                       ("KOKORO_VOICE", entry.voice)):
        try:
            # Resolved through the module at call time (the same
            # seam onboarding's _persist_brain_choice uses), so the
            # write stays patchable/observable in tests.
            _config_module.save_env_setting(key, value)
        except Exception:  # noqa: BLE001 — persistence must not break the pick
            pass
    return entry


def reply_language_directive(code: str | None = None) -> str:
    """The system-prompt suffix for a non-English home: one shared
    helper, appended by every brain's assembly (Gemini Live, the
    cloud-text/Groq path, both local turn builders) right after
    config.system_instruction. Returns "" for English homes and
    unknown codes, so the default install's assembled prompts stay
    byte-identical to before this feature existed."""
    if code is None:
        code = str(getattr(config, "preferred_language",
                           DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)
    entry = language_by_code(code)
    if entry is None or entry.code in ENGLISH_CODES:
        return ""
    return (
        f"\n\nReply by default in {entry.display} ({entry.label.split(' — ')[-1]}), "
        "written in its native script. When the user speaks a "
        "different language, mirror them and reply in that "
        "language instead."
    )


# --------------------------------------------------------------------- #
# Reply-language detection (for automatic voice switching)
# --------------------------------------------------------------------- #

# Script buckets over Unicode ranges. hi/ja/cmn have Kokoro
# voices; the rest are scripts she cannot speak — a reply dominated
# by one of them stays on the home voice rather than being fed to
# a wrong-language voice.
def _script_bucket(ch: str) -> str | None:
    o = ord(ch)
    if 0x0900 <= o <= 0x097F:
        return "devanagari"
    if 0x3040 <= o <= 0x309F or 0x30A0 <= o <= 0x30FF:
        return "kana"
    if 0x4E00 <= o <= 0x9FFF:
        return "han"
    if (0xAC00 <= o <= 0xD7AF      # Hangul
            or 0x0400 <= o <= 0x04FF   # Cyrillic
            or 0x0590 <= o <= 0x05FF   # Hebrew
            or 0x0600 <= o <= 0x06FF   # Arabic
            or 0x0370 <= o <= 0x03FF   # Greek
            or 0x0E00 <= o <= 0x0E7F):  # Thai
        return "other"
    return None


# Stopword scoring for the Latin-script languages (the approach —
# and part of the data — follows core/senses.py's language
# guesser; the table lives here because senses covers neither
# Italian nor Portuguese, and the synthesis path should not import
# the senses toolbox). Only DISTINCTIVE function words are scored.
_STOPWORDS: dict[str, frozenset] = {
    "es": frozenset({
        "el", "la", "los", "las", "de", "que", "y", "en", "un",
        "una", "del", "es", "ser", "se", "no", "por", "con", "su",
        "para", "como", "estar", "tener", "todo", "pero", "gracias",
        "hola", "está", "muy", "también", "cuando", "donde",
    }),
    "fr-fr": frozenset({
        "le", "la", "les", "de", "des", "un", "une", "être", "et",
        "à", "il", "elle", "avoir", "ne", "je", "vous", "nous",
        "son", "que", "se", "qui", "ce", "dans", "en", "du", "au",
        "pas", "pour", "est", "merci", "bonjour", "avec", "sur",
    }),
    "it": frozenset({
        "il", "lo", "la", "di", "che", "e", "è", "un", "una",
        "uno", "in", "con", "del", "della", "dei", "per", "sono",
        "anche", "come", "ma", "più", "questo", "questa", "grazie",
        "ciao", "non", "da", "le", "gli", "nel", "alla", "essere",
    }),
    "pt-br": frozenset({
        "o", "a", "os", "as", "de", "que", "e", "do", "da", "dos",
        "das", "em", "um", "uma", "para", "é", "com", "não", "no",
        "na", "se", "por", "pelo", "pela", "ela", "são", "como",
        "mais", "obrigado", "você", "eu", "ao", "está", "muito",
    }),
    "en": frozenset({
        "the", "be", "to", "of", "and", "a", "in", "that", "it",
        "is", "was", "for", "on", "are", "as", "with", "they", "i",
        "at", "this", "have", "from", "or", "one", "you", "we",
        "can", "will", "your", "our", "would", "there", "their",
        "what", "when", "which", "who", "how", "all", "would",
    }),
}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_reply_language(text: str | None) -> str | None:
    """The canonical language code of a reply, or None when the
    reply should stay on the home voice: English of either locale,
    text too short to judge, an unspeakable script (Korean,
    Russian, Arabic…), or no clear Latin-script winner.

    Scripts are judged first (native scripts are unambiguous);
    a script only wins when it is at least ~30% of the reply's
    letters, so an English answer quoting one Hindi word stays
    English. Latin script falls to stopword scoring, which needs
    at least 3 stopword hits and a strict winner."""
    if not text:
        return None
    counts = {"devanagari": 0, "kana": 0, "han": 0, "other": 0}
    letters = 0
    for ch in text:
        if ch.isalpha():
            letters += 1
            bucket = _script_bucket(ch)
            if bucket:
                counts[bucket] += 1
    script_total = sum(counts.values())
    if script_total >= 3 and letters and script_total >= 0.3 * letters:
        if counts["other"] >= max(counts["devanagari"], counts["kana"],
                                  counts["han"]):
            return None  # a script she cannot speak — stay home
        if counts["devanagari"] >= counts["kana"] + counts["han"]:
            return "hi"
        if counts["kana"]:
            # Japanese mixes kana with han; a trace of kana in a
            # han-dominant text is Chinese, not Japanese.
            return "ja" if counts["kana"] >= 0.25 * max(
                counts["han"], 1) else "cmn"
        if counts["han"]:
            return "cmn"
        return None
    words = _WORD_RE.findall(text.lower())
    if len(words) < 3:
        return None
    hits = {code: sum(1 for w in words if w in stops)
            for code, stops in _STOPWORDS.items()}
    total = sum(hits.values())
    if total < 3:
        return None
    ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
    (best_code, best), (_, runner_up) = ranked[0], ranked[1]
    if best == 0 or best <= runner_up:
        return None  # no clear winner
    if best_code == "en":
        return None  # English always stays home (either locale)
    return best_code


# --------------------------------------------------------------------- #
# Transcription profiles (v5.43, EARS A1 + A7)
#
# Her EARS get the same one-table treatment as her voice: the
# Settings "Transcription profile" picker lists TRANSCRIPTION_
# PROFILES, and resolve_transcription_profile() is the ONE
# resolver every transcription call goes through (today that is
# core/voice/local_client.py's faster-whisper path, which the Groq and
# lite sessions also hear through). A profile decides:
#
#   * the Whisper language — locked from the user's preferred
#     language, never per-utterance auto-detect, EXCEPT the one
#     honest escape: profile Auto + a preferred language with no
#     confident Whisper mapping → language None (auto-detect,
#     exactly the pre-v5.43 behaviour);
#   * the biasing prompt terms fed to Whisper's initial_prompt;
#   * the EXTENDED hallucination phrase list the v5.19 filter
#     drops on top of its stock list (Hinglish profile only).
#
# Whisper language codes are NOT the Kokoro/locale codes above:
# Mandarin is "zh" to Whisper (not "cmn"), French "fr" (not
# "fr-fr"), Portuguese "pt" (not "pt-br"), both Englishes "en".
# --------------------------------------------------------------------- #

WHISPER_LANGUAGE_BY_LOCALE: dict[str, str] = {
    "en-us": "en", "en-gb": "en", "hi": "hi", "ja": "ja",
    "cmn": "zh", "es": "es", "fr-fr": "fr", "it": "it",
    "pt-br": "pt",
}
_WHISPER_CODES = frozenset(WHISPER_LANGUAGE_BY_LOCALE.values())


def whisper_language_for_locale(code: str | None) -> str | None:
    """The faster-whisper language code for a locale code from
    the LANGUAGES table (or an already-Whisper code), or None
    when there is no confident mapping — the caller then passes
    language=None (auto-detect) rather than guessing."""
    if not code:
        return None
    key = str(code).strip().lower()
    if key in WHISPER_LANGUAGE_BY_LOCALE:
        return WHISPER_LANGUAGE_BY_LOCALE[key]
    return key if key in _WHISPER_CODES else None


@dataclass(frozen=True)
class TranscriptionProfile:
    """One Settings-selectable transcription profile (the picker
    entry). whisper_language is the language an EXPLICIT pick of
    this profile locks; for "auto" it is None because Auto's
    language comes from the preferred language at resolve time."""

    id: str
    label: str
    whisper_language: str | None
    prompt_terms: tuple[str, ...]
    hallucination_phrases: frozenset


_STANDARD_PROMPT_TERMS = (
    "open", "close", "screenshot", "volume", "browser", "camera",
)
_HINGLISH_PROMPT_TERMS = _STANDARD_PROMPT_TERMS + (
    "khol do", "band karo", "awaaz badhao", "awaaz kam karo",
    "tasveer lo", "dikhao", "batao", "chalao",
)
# Romanised on purpose: the v5.19 filter normalises transcripts
# to [a-z0-9 ] before matching, so Devanagari entries could never
# match — the phrases Whisper actually hallucinates in Latin
# script (caption-style Hinglish) are the ones that can be caught.
_HINGLISH_HALLUCINATION_PHRASES = frozenset({
    "dhanyavaad", "shukriya", "dekhne ke liye dhanyavaad",
    "video dekhne ke liye dhanyavaad", "aapka swagat hai",
    "namaskar doston", "channel ko subscribe karein",
    "subscribe karein", "like aur subscribe karein",
})

TRANSCRIPTION_PROFILES: tuple[TranscriptionProfile, ...] = (
    TranscriptionProfile(
        "auto", "Auto", None, _STANDARD_PROMPT_TERMS, frozenset()),
    TranscriptionProfile(
        "standard", "Standard", "en", _STANDARD_PROMPT_TERMS,
        frozenset()),
    TranscriptionProfile(
        "hinglish", "Indian English / Hinglish", "hi",
        _HINGLISH_PROMPT_TERMS, _HINGLISH_HALLUCINATION_PHRASES),
)

_PROFILES_BY_ID = {entry.id: entry for entry in TRANSCRIPTION_PROFILES}


def transcription_profile_by_id(profile_id: str | None) -> TranscriptionProfile | None:
    """The picker entry for a profile id, or None."""
    if not profile_id:
        return None
    return _PROFILES_BY_ID.get(str(profile_id).strip().lower())


@dataclass(frozen=True)
class ResolvedTranscription:
    """The outcome of resolve_transcription_profile(): everything
    a transcription call needs, with no further decisions left."""

    setting: str            # the normalised configured setting
    profile_id: str         # the EFFECTIVE profile: standard|hinglish
    label: str
    whisper_language: str | None   # None = auto-detect (see header)
    prompt_terms: tuple[str, ...]
    hallucination_phrases: frozenset


def resolve_transcription_profile(
        profile_setting: str | None = None,
        preferred_language: str | None = None) -> ResolvedTranscription:
    """The ONE transcription-profile resolver (v5.43, A1 + A7).

    Resolution table:
      setting "hinglish" (explicit)  → Hinglish profile, lang "hi"
      setting "standard" (explicit)  → Standard profile, lang "en"
      setting "auto" + preferred "hi"        → Hinglish, lang "hi"
      setting "auto" + preferred en-gb/en-us → Standard, lang "en"
      setting "auto" + another known locale  → Standard terms, the
                                               locale's Whisper code
      setting "auto" + unknown locale        → Standard terms,
                                               language None
      a garbage setting                      → treated as "auto"

    With no arguments, both inputs read live config (the normal
    call); explicit arguments exist for tests and previews."""
    if profile_setting is None:
        profile_setting = getattr(config, "transcription_profile", "auto")
    if preferred_language is None:
        preferred_language = getattr(
            config, "preferred_language", DEFAULT_LANGUAGE)
    setting = str(profile_setting or "auto").strip().lower()
    if setting not in _PROFILES_BY_ID:
        setting = "auto"

    if setting in ("standard", "hinglish"):
        entry = _PROFILES_BY_ID[setting]
        return ResolvedTranscription(
            setting=setting, profile_id=entry.id, label=entry.label,
            whisper_language=entry.whisper_language,
            prompt_terms=entry.prompt_terms,
            hallucination_phrases=entry.hallucination_phrases)

    # Auto: the profile follows the preferred language.
    locale = str(preferred_language or "").strip().lower()
    if locale == "hi":
        entry = _PROFILES_BY_ID["hinglish"]
        return ResolvedTranscription(
            setting="auto", profile_id=entry.id, label=entry.label,
            whisper_language="hi",
            prompt_terms=entry.prompt_terms,
            hallucination_phrases=entry.hallucination_phrases)
    entry = _PROFILES_BY_ID["standard"]
    return ResolvedTranscription(
        setting="auto", profile_id=entry.id, label=entry.label,
        whisper_language=whisper_language_for_locale(locale),
        prompt_terms=entry.prompt_terms,
        hallucination_phrases=entry.hallucination_phrases)


def kokoro_voice_for_text(text: str | None, home_voice: str) -> str:
    """The Kokoro voice for one utterance: the detected language's
    curated voice, or `home_voice` when detection stays home
    (English, short, ambiguous, unspeakable script)."""
    code = detect_reply_language(text)
    if code is None:
        return home_voice
    entry = language_by_code(code)
    return entry.voice if entry is not None else home_voice
