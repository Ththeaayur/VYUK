"""Second-pass classifier (split from core/injection.py, B9).

Heuristic scorer alongside the regex screen. Depends on the screen
module only (INTENTIONAL imports below); the second-pass flag and its
setter/readers all live here, in one module.
"""

from __future__ import annotations

import base64
import re

from . import config_spec

# INTENTIONAL: cross-part names from the defining module (functions,
# never mutable state). _SCAN_CAP is the screen's scan bound.
from .injection_scan import (
    _SCAN_CAP,
    looks_like_injection,
    normalize_for_screen,
)

# Second-pass classifier (v5.49, programme item 37) — a small LOCAL
# heuristic scorer that runs alongside the regex screen on the same
# note/wrap path. Its powers are deliberately BOUNDED:
#   (a) it may escalate provenance-only content to SOFT taint when
#       its score reaches SECOND_PASS_THRESHOLD (see note_untrusted)
#       — this is its only taint power, and it matters most for the
#       machine-state sources the v5.46 provenance tier excludes,
#       which otherwise get no caution at all without a pattern hit;
#   (b) it may annotate the envelope with a marker line noting the
#       score (see _classifier_marker / wrap_untrusted), and only
#       for content it escalates that the screen did NOT already
#       hard-taint — pattern-hit and benign wraps stay byte-identical
#       to the pre-classifier format even while it is enabled.
# It must NEVER hard-taint on its own, and NEVER untaint or
# downgrade anything: a pattern hit is hard regardless of the
# classifier, and the classifier is not consulted on that path.
#
# The highest-value signal is decode-and-rescreen: a base64 blob
# whose decoded text trips the EXISTING patterns scores decisively
# (the classifier adds no new judgement there — it just unwraps).
# The rest is keyword/phrase density across languages plus
# secrecy/exfil co-occurrence — heuristic suspicion, nothing more,
# which is exactly why its ceiling is soft.
#
# Config: enabled by default; disable entirely with the env var
# VYUK_INJECTION_SECOND_PASS=0/false/off/no (read at import) or
# set_second_pass_enabled(False). Disabled behaviour is
# byte-identical to pre-v5.49: no marker, no escalation.
# --------------------------------------------------------------------- #

SECOND_PASS_THRESHOLD = 6


def _env_second_pass_disabled() -> bool:
    # The spec value (bool, default True = enabled): disabled when
    # the var carries a falsy spelling (0/false/off/no).
    return not config_spec.env("VYUK_INJECTION_SECOND_PASS")


_second_pass_enabled = not _env_second_pass_disabled()


def second_pass_enabled() -> bool:
    """Whether the second-pass classifier is active."""
    return _second_pass_enabled


def set_second_pass_enabled(enabled: bool) -> None:
    """Runtime switch for the second-pass classifier (tests and
    config plumbing); the env var is the import-time default."""
    global _second_pass_enabled
    _second_pass_enabled = bool(enabled)


# English signal groups, scored on the NORMALISED lowercased text.
_SP_OVERRIDE_WORDS = (
    "ignore", "disregard", "forget", "override", "bypass", "discard",
)
_SP_OVERRIDE_PHRASES = (
    "set aside", "cast aside", "supersede", "superseded", "supersedes",
    "revoked", "revoke", "no longer applies", "follow only",
    "only follow", "as void",
)
_SP_SET_ASIDE_RE = re.compile(r"\bset\b[^.\n]{0,30}?\baside\b")
_SP_NOUNS = (
    "instructions", "instruction", "prompt", "prompts", "rules",
    "guidelines", "guidance", "directions", "directives", "briefing",
    "configuration", "tasking",
)
_SP_AUTHORITY = (
    ("developer", 2), ("your configuration", 2), ("from now on", 2),
    ("obey", 2), ("you were told", 1), ("hidden", 1),
)
_SP_SECRECY = (
    ("quietly", 2), ("secretly", 2), ("don't mention", 2),
    ("do not mention", 2), ("no need to mention", 2),
    ("without mentioning", 2), ("slip in", 1),
)
_SP_EXFIL_VERBS = (
    "send", "forward", "upload", "attach", "embed", "post", "copy",
    "encode", "leak", "email",
)
_SP_EXFIL_OBJECTS = (
    ("conversation history", 2), ("chat history", 2),
    ("api key", 2), ("api keys", 2), ("clipboard contents", 2),
    ("password", 1), ("documents", 1), ("files", 1), ("inbox", 1),
    ("messages", 1), ("address", 1), ("balance", 1),
    ("conversation", 1), ("email", 1),
)
# Meta-discussion framing: content ABOUT attacks (a security blog,
# training material) quotes the same vocabulary it describes. Two
# or more framing markers dampen the surface keyword score — the
# classifier's ceiling is only soft, so this costs little, and it
# keeps benign lookalikes from escalating on vocabulary alone.
# (It never dampens a decoded-rescreen hit.)
_SP_META_MARKERS = (
    "blog", "article", "research", "attack", "example", "training",
    "defenders", "security", "quoted", "journal",
)

# Multilingual signals, scored on the RAW lowercased text (the
# screen normaliser folds Cyrillic homoglyphs, which would mangle
# genuine Russian words). Verb +3, noun/object +2, capped as a group.
_SP_MULTI: tuple[tuple[str, int], ...] = tuple(
    (term, weight) for term, weight in (
        # Chinese: 忽略/无视/忘掉/忘记 (ignore/forget), 指令/指示/命令/
        # 规则/提示词 (instructions/rules/prompt), 发送/对话记录 (send/log)
        ("忽略", 3), ("无视", 3), ("忘掉", 3), ("忘记", 3),
        ("指令", 2), ("指示", 2), ("命令", 2), ("规则", 2),
        ("提示词", 2), ("系统提示", 2), ("发送", 2), ("对话记录", 2),
        ("遵循", 1), ("从现在开始", 2),
        # Arabic: تجاهل (ignore), تعليمات/أوامر/قواعد, أرسل/سجل المحادثة
        ("تجاهل", 3), ("تعليمات", 2), ("أوامر", 2), ("قواعد", 2),
        ("أرسل", 2), ("سجل المحادثة", 2),
        # Russian: игнорируй/забудь, инструкции/правила/команды,
        # отправь/историю
        ("игнорируй", 3), ("забудь", 3), ("инструкции", 2),
        ("правила", 2), ("команды", 2), ("отправь", 2),
        ("историю", 2),
        # Japanese: 無視/忘れ, 指示/命令/プロンプト, 送信/会話履歴
        ("無視", 3), ("忘れ", 3), ("指示", 2), ("命令", 2),
        ("プロンプト", 2), ("送信", 2), ("会話履歴", 2),
        # Korean: 무시/잊어, 지시/명령/프롬프트, 보내/대화 기록
        ("무시", 3), ("잊어", 3), ("지시", 2), ("명령", 2),
        ("프롬프트", 2), ("보내", 2), ("대화 기록", 2),
    )
)

_B64_BLOB_RE = re.compile(r"[A-Za-z0-9+/=_-]{40,}")


def _decode_b64_blob(blob: str) -> str | None:
    """Decodes one candidate blob to plausible TEXT, or None.
    Binary/gibberish decodes are rejected (mostly-printable,
    minimum length) so hashes and encoded images score nothing."""
    candidate = blob.strip()
    if len(candidate) < 40:
        return None
    try:
        padded = candidate + "=" * (-len(candidate) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=False)
        decoded = raw.decode("utf-8")
    except Exception:  # noqa: BLE001 — a bad blob is just no signal
        return None
    if len(decoded) < 8:
        return None
    printable = sum(1 for ch in decoded if ch.isprintable() or ch in " \n\t")
    if printable / max(1, len(decoded)) < 0.7:
        return None
    return decoded


def _keyword_score(norm_lower: str, raw_lower: str) -> int:
    """Weighted signal density. Group caps keep any single
    vocabulary family from escalating on its own."""
    score = 0
    override_hits = sum(1 for w in _SP_OVERRIDE_WORDS if w in norm_lower)
    score += min(6, override_hits * 2)
    phrase_hits = sum(1 for p in _SP_OVERRIDE_PHRASES if p in norm_lower)
    if _SP_SET_ASIDE_RE.search(norm_lower):
        phrase_hits += 1
    score += min(6, phrase_hits * 3)
    has_override = bool(override_hits or phrase_hits)
    noun_hits = sum(1 for n in _SP_NOUNS if n in norm_lower)
    score += min(3, noun_hits)
    if has_override and noun_hits:
        score += 3
    score += min(5, sum(w for term, w in _SP_AUTHORITY
                        if term in norm_lower))
    score += min(5, sum(w for term, w in _SP_SECRECY
                        if term in norm_lower))
    verb_hits = sum(1 for v in _SP_EXFIL_VERBS
                    if re.search(rf"\b{re.escape(v)}\b", norm_lower))
    score += min(4, verb_hits * 2)
    object_hits = sum(w for term, w in _SP_EXFIL_OBJECTS
                      if term in norm_lower)
    score += min(3, object_hits)
    if verb_hits and object_hits:
        score += 2
    multi = sum(w for term, w in _SP_MULTI if term in raw_lower)
    score += min(8, multi)
    if sum(1 for m in _SP_META_MARKERS if m in norm_lower) >= 2:
        score = max(0, score - 4)
    return score


def second_pass_assess(text: str) -> dict:
    """Scores `text` heuristically. Returns {"score": int,
    "decoded_screen_hit": bool, "decoded_keyword_score": int}.
    Pure and never raises; scores the same bounded prefix as the
    screen. A decoded base64 payload that trips the real screen
    contributes a decisive +12 (still only ever spent as SOFT —
    see the bounded-powers note above)."""
    result = {"score": 0, "decoded_screen_hit": False,
              "decoded_keyword_score": 0}
    if not text or not isinstance(text, str):
        return result
    try:
        raw = str(text)
        sample = raw[:_SCAN_CAP]
        norm_lower = normalize_for_screen(sample).lower()
        score = _keyword_score(norm_lower, sample.lower())
        seen: set[str] = set()
        for match in _B64_BLOB_RE.finditer(sample):
            blob = match.group(0)
            if blob in seen or len(seen) >= 8:
                continue
            seen.add(blob)
            decoded = _decode_b64_blob(blob)
            if decoded is None:
                continue
            if looks_like_injection(decoded):
                result["decoded_screen_hit"] = True
                score += 12
            else:
                dkw = _keyword_score(
                    normalize_for_screen(decoded).lower(),
                    decoded.lower())
                result["decoded_keyword_score"] = max(
                    result["decoded_keyword_score"], dkw)
                score += min(6, dkw)
        result["score"] = score
    except Exception:  # noqa: BLE001 — the classifier must never break
        return {"score": 0, "decoded_screen_hit": False,
                "decoded_keyword_score": 0}
    return result


def second_pass_score(text: str) -> int:
    """The classifier's heuristic score for `text` (0 when it is
    disabled — a disabled classifier has no opinion)."""
    if not _second_pass_enabled:
        return 0
    return second_pass_assess(text)["score"]


def _classifier_escalates(text: str) -> int:
    """The classifier score when it escalates `text` (enabled and
    at/above threshold), else 0. Callers must only spend this as
    SOFT escalation/annotation — never as a hard signal."""
    if not _second_pass_enabled:
        return 0
    score = second_pass_assess(text)["score"]
    return score if score >= SECOND_PASS_THRESHOLD else 0


def _classifier_marker(text: str) -> str:
    """The envelope annotation line for classifier-escalated
    content the screen did NOT already flag, else "". Pattern-hit
    content gets no marker: the classifier contributed nothing
    there, and its wrap stays byte-identical to the old format."""
    try:
        if looks_like_injection(text):
            return ""
    except Exception:  # noqa: BLE001
        return ""
    score = _classifier_escalates(text)
    if not score:
        return ""
    return (f"[Second-pass classifier score: {score} — heuristic "
            f"suspicion only; this content is soft-tainted by the "
            f"classifier, never hard-tainted by it.]")
