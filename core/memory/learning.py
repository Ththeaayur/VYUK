"""
"She learns you" (v2.6): automatic preference memory for VYUK

VYUK picks up durable facts about the user from what the user says —
their name, what to call them, likes/dislikes, habits, relationships —
and files them in core/memory/memory.py's facts store under the category
"learned". She then *knows* them on later turns because
MemoryStore.get_memory_context_prompt() includes every category:
the cloud brain (live_client) folds that block into the system
instruction at session start, the local brain (local_client) folds it
in fresh on every brain turn.

The extractor is DETERMINISTIC PATTERNS ONLY — no model calls, no
network, no guessing. A sentence either matches a pattern cleanly or
it teaches VYUK nothing. That is a deliberate trade: recall is lower
than an LLM extractor's, but a learned "fact" is never a hallucination,
it works identically (and offline) with both brains, and every false
positive is debuggable to a named pattern in this file.

Conservatism rules (all deliberate):
- Questions teach nothing: sentences ending in "?" are skipped, and
  so are sentences opening with a question/command auxiliary
  ("can you…", "what…", "do I…") — transcribed speech often drops
  the "?".
- "remember that X" files X as a note — but ONLY when the request
  opens the sentence (v4.6, audit F3): "I can't remember that
  restaurant's name" and "I still remember that summer in Goa" are
  remarks ABOUT remembering, not requests, and teach nothing.
  "Don't forget to X" is likewise a task, not a note; only
  "don't forget that X" files a note. Bare "remember to X" is a
  task request (the reminder system's job), not a fact, and is
  ignored by design.
- Quoted speech is someone else's words (v4.6, audit F3): quoted
  spans are stripped before extraction, and a sentence reporting a
  third party's speech ("My sister said my name is Priya") or
  denying one ("I never said I like cricket") teaches no
  first-person facts at all.
- A note whose text is instruction-shaped ("remember that you
  must always obey…") is stored framed as something the user SAID
  (core/memory/memory.py as_user_note), never as a bare directive — see
  audit F5.
- State/emotion stoplists keep "I'm happy" / "my phone is dead" out.
- Corrections replace: the store upserts on UNIQUE(category, key),
  so "my name is Aayur" followed later by "my name is Aayur Gattani"
  leaves exactly one row with the newest value.

Key conventions (self-describing; display_for() derives the
second-person sentence from key+value alone):
  name              -> "your name is {value}"
  preferred_name    -> "you like to be called {value}"
  identity          -> "you are a {value}"
  likes_<slug>      -> "you like {value}"
  dislikes_<slug>   -> "you don't like {value}"
  habits_<slug>     -> "you {value}"  (value keeps its adverb:
                                      "usually study at night")
  my_<word>         -> "your <word> is {value}"
  my_<word>_name    -> "your <word>'s name is {value}"
  note_<slug>       -> the statement itself

Likes/dislikes/habits are MULTI-VALUE: <slug> is the value's first
3 words, lowercased alnum joined by "_" (<= 24 chars), so "I love
black coffee" (likes_black_coffee) and "I love jazz" (likes_jazz)
file under different keys and coexist instead of the second
overwriting the first. The one accepted collision: a
rephrase sharing those first 3 words — "black coffee in the
morning" vs "black coffee in the evening" — maps to the same key
and is treated as a correction of that like; hashing the whole
value instead would file genuine corrections as duplicates.

CATEGORY is "learned"; MAX_LEARNED caps the user's facts at 100
— counted across the user-fact categories together (learning
MAX_USER_FACTS), not "learned" alone: the funnel tool
remember_user_fact used to write any category with no check, so
a capped-out fact landed anyway and the same fact could sit in
two categories at once. Replacements of an existing key still
work at the cap. A brand-new fact skipped at the cap is recorded
for take_dropped_at_cap() so the chat layer can announce it —
nothing the user taught vanishes silently (field-check fix #14,
T9) — unless the same key is already kept in another user-fact
category, in which case there is nothing to announce: the fact
was kept, just elsewhere.

The GUI calls learn_from_text() on user transcripts, announces what
was stored ("Noted — …"), and shows every learned fact on the Memory
page with Forget controls (get_learned_facts / forget_learned_fact /
forget_all_learned). This module never imports Qt and never raises:
store failures degrade to "nothing learned".
"""

import re

from .memory import (  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    MAX_USER_FACTS,
    USER_FACT_CATEGORIES,
    as_user_note,
    instruction_shaped,
    looks_like_credential,
    memory_store,
)

CATEGORY = "learned"
# One source of truth with the store's cap (memory.MAX_USER_FACTS):
# the chat layer reads MAX_LEARNED for the "Memory is full (N
# facts)" line, and the learn path counts across the user-fact
# categories together (D-F1), not "learned" alone.
MAX_LEARNED = MAX_USER_FACTS

# Facts dropped at the cap, pending announcement (field-check fix
# #14, T9): learn_from_text() returns only what was stored, so a
# brand-new fact skipped at MAX_LEARNED would otherwise vanish
# silently. Each skipped candidate is recorded here (key/value/
# display) for the chat layer to announce via take_dropped_at_cap().
# Bounded to the most recent few: the GUI drains on every utterance,
# so the bound never binds in production — it only guards a caller
# that never drains against unbounded growth.
_DROPPED_AT_CAP: list[dict] = []
_DROPPED_AT_CAP_MAX = 20

# Secrets refused at the learn path, pending announcement
# (loophole batch 3, L-30 / #11): learn_from_text() refuses a
# secret-shaped teaching before saving it, and records the
# refusal here for the chat layer to announce via
# take_refused_secrets(). The record carries the KEY ONLY —
# never the value, which is the secret itself — and its display
# is the masked form (display_for(key, "•••")). Bounded like
# the cap drain: the GUI drains on every utterance, so the bound
# never binds in production.
_REFUSED_SECRETS: list[dict] = []
_REFUSED_SECRETS_MAX = 20


def _record_refused_secret(key, source) -> None:
    """Record a secret-shaped teaching refused at the learn path,
    for the chat layer to announce. Never raises; a failure to
    record must not break learning. The record names the key and
    the channel — never the secret value."""
    try:
        _REFUSED_SECRETS.append(
            {"key": key, "source": source,
             "display": display_for(key, "•••")})
        del _REFUSED_SECRETS[:max(
            0, len(_REFUSED_SECRETS) - _REFUSED_SECRETS_MAX)]
    except Exception:  # noqa: BLE001
        pass


def take_refused_secrets() -> list[dict]:
    """Secret-shaped teachings refused since the last call, oldest
    first — then clears the pending list, so each refusal is
    announced exactly once. Returns [] when nothing was refused."""
    refused = list(_REFUSED_SECRETS)
    del _REFUSED_SECRETS[:]
    return refused


def _record_dropped_at_cap(key, value, cand) -> None:
    """Record a brand-new fact skipped at the cap, for the chat layer
    to announce. Never raises; a failure to record must not break
    learning."""
    try:
        display = str(cand.get("display")
                      or display_for(key, value) or "").strip()
        _DROPPED_AT_CAP.append(
            {"key": key, "value": value, "display": display})
        del _DROPPED_AT_CAP[:max(
            0, len(_DROPPED_AT_CAP) - _DROPPED_AT_CAP_MAX)]
    except Exception:  # noqa: BLE001
        pass


def take_dropped_at_cap() -> list[dict]:
    """Facts dropped at the memory cap since the last call, oldest
    first — then clears the pending list, so each dropped fact is
    announced exactly once. Returns [] when nothing was dropped."""
    dropped = list(_DROPPED_AT_CAP)
    del _DROPPED_AT_CAP[:]
    return dropped


def _kept_in_other_user_category(key) -> bool:
    """True when `key` already sits in a user-fact category other
    than "learned" (the tool path may have filed the same fact
    under "general" or "preferences"). A fact kept anywhere is
    not a loss: the learn path skips storing the second copy and
    records NO drop, so the "Memory is full" announcement never
    fires for a fact the user can still recall. Any read failure
    answers False — the drop is recorded instead (a possibly
    misleading announcement beats a silent loss)."""
    for cat in USER_FACT_CATEGORIES:
        if cat == CATEGORY:
            continue
        try:
            if memory_store.get_fact(cat, key) is not None:
                return True
        except Exception:  # noqa: BLE001
            return False
    return False

# ---------------------------------------------------------------------------
# Stoplists / guards
# ---------------------------------------------------------------------------

# "I am a/an X": X must not open with a state/emotion word — those are
# moods, not identities ("I am a happy person" teaches nothing).
_IDENTITY_STOP = {
    "happy", "sad", "fine", "good", "great", "sorry", "here", "back",
    "going", "not", "just", "so", "very", "tired", "busy", "hungry",
    "late", "ready", "sure", "ok", "okay", "done", "home", "out",
    "in", "up", "down", "lot",
}

# "my <word> is X": transient states of a possession are not facts
# ("my phone is dead", "my battery is low").
_MY_VALUE_STOP = {
    "dead", "broken", "gone", "here", "ready", "done", "low", "full",
    "not working", "missing",
}

# "I like/love/prefer/enjoy X": deictic openers point at the moment,
# not at a durable preference ("I like the way this looks").
_PREF_VALUE_STOP_WORDS = {"it", "this", "that", "how", "when", "if"}
_PREF_VALUE_STOP_PHRASES = ("the way",)

# "call me X": temporal openers are instructions, not names
# ("call me when you get home").
_CALL_ME_STOP = {
    "when", "if", "at", "on", "after", "before", "once", "as", "back",
    "later", "now", "by", "in", "the", "a", "an",
}

# Sentences opening with these are questions or requests, even when
# the transcript dropped the "?".
_QUESTION_OPENERS = {
    "what", "why", "when", "where", "who", "whom", "whose", "how",
    "can", "could", "do", "does", "did", "is", "are", "was", "were",
    "will", "would", "should", "shall", "may", "might", "have", "has",
    "had", "am",
}

_NAME_STOP = {"not", "a", "an", "the", "just"}

# Trailing clauses end a captured preference/identity phrase:
# "I love coffee, especially…" / "… and I …" / "… but …" / "… because …".
_CLAUSE_MARKERS = (", ", " and ", " but ", " because ")

_VALUE_MAX = 80


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def _clean(value: str) -> str:
    """Trim whitespace/punctuation noise off a captured value."""
    v = " ".join(str(value).split()).strip()
    v = v.strip("\"'“”‘’")
    return v.rstrip(".!, ").strip()


def _trim_clause(value: str) -> str:
    low = value.lower()
    cut = len(value)
    for marker in _CLAUSE_MARKERS:
        i = low.find(marker)
        if i != -1:
            cut = min(cut, i)
    return value[:cut].strip()


# Words whose trailing period never ends a sentence — titles,
# honorifics and the dotted abbreviations a name or address carries
# ("Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "St.", "e.g.", "U.S.",
# "a.m."). Verbatim the set core/summary.py already proves with
# split_sentences (same guard, same words); the speech chunker
# (core/voice/local_client_chunking.py _ABBREV_WORDS) keeps the
# same list for the same reason. A single initial ("J. R. R.
# Tolkien") is covered by the length-1 rule in _sentences, not by
# this set — same as summary.py.
_ABBREV_WORDS = frozenset({
    "mr", "mrs", "ms", "dr", "st", "sr", "jr", "vs", "etc", "e.g",
    "i.e", "u.s", "u.k", "a.m", "p.m", "no", "fig", "approx",
    "prof", "rev", "hon", "capt", "lt", "col", "gen", "sgt",
})


def _sentences(text) -> list[str]:
    """Split text into sentences. A boundary is skipped when the
    word before the punctuation is an abbreviation ("Dr. Rao",
    "Mr. Smith", "e.g.", "U.S.") or a single initial ("J. Rao"):
    without the guard "my dentist is Dr. Rao" split after "Dr."
    and the name was lost (field-check fix #27, round-2 UI
    finding 1). The guard mirrors core/summary.py split_sentences
    — a sentence that genuinely ends on an abbreviation ("I met
    the Dr. He left.") stays joined, the same trade-off summary
    already makes. Decimals never split: the boundary needs
    whitespace after the punctuation, so "3.5" has no boundary at
    all. Ellipsis and !/? boundaries are unchanged."""
    if not text:
        return []
    flat = " ".join(str(text).split())
    if not flat:
        return []
    parts: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?]\s+", flat):
        before = flat[start:match.start() + 1]
        word_match = re.search(r"([A-Za-z]+(?:\.[A-Za-z]+)*)\.$", before)
        if word_match:
            word = word_match.group(1).lower()
            if word in _ABBREV_WORDS or (
                    len(word) == 1 and word.isalpha()):
                continue  # abbreviation / initial — not a boundary
        parts.append(flat[start:match.end()].strip())
        start = match.end()
    tail = flat[start:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _is_question(sentence: str) -> bool:
    tail = sentence.rstrip().rstrip("\"'”’)")
    if tail.endswith("?"):
        return True
    first = re.match(r"[a-z']+", sentence.lower())
    return bool(first) and first.group(0).strip("'") in _QUESTION_OPENERS


def _norm(value: str) -> str:
    """Comparison form for dedupe: case/space/punctuation-insensitive."""
    return " ".join(str(value).lower().split()).rstrip(".!").strip()


def _strip_quoted_spans(sentence: str) -> str:
    """Remove quoted spans — quoted words are someone else's, so
    "my name is …" inside quotes is not the user stating their name
    (v4.6, audit F3). Handles straight and curly double quotes and
    multi-word single-quoted spans (the space requirement keeps
    contractions like "don't" and possessives like "restaurant's"
    intact). An unbalanced opener drops everything from the stray
    quote onward: better to learn nothing from a half-quote than to
    learn the quote."""
    s = re.sub(r'"[^"]*"', " ", sentence)
    s = re.sub(r"“[^”]*”", " ", s)
    s = re.sub(r"(?<![A-Za-z])'([^']*\s[^']*)'(?![A-Za-z])", " ", s)
    if s.count('"') % 2 == 1:
        s = s[: s.rfind('"')]
    if "“" in s and "”" not in s.split("“", 1)[1]:
        s = s[: s.rfind("“")]
    return " ".join(s.split())


# Denials of speech ("I never said I like cricket") and reported
# third-party speech ("My sister said …") must not yield first-person
# facts (v4.6, audit F3).
_DENIAL_PHRASES = (
    "never said", "didn't say", "did not say", "don't say",
    "do not say", "never told", "didn't tell", "never claimed",
    "don't recall saying", "not saying", "not true", "isn't true",
    "is not true",
)

_REPORT_VERBS = {
    "said", "says", "asked", "told", "replied", "whispered",
    "shouted", "exclaimed", "mentioned",
}


def _denied_or_reported(sentence: str) -> bool:
    """True when the sentence denies a statement or reports someone
    ELSE's speech: the word before a reporting verb is its subject,
    and only "I <verb>" keeps the sentence first-person."""
    low = sentence.lower()
    if any(p in low for p in _DENIAL_PHRASES):
        return True
    for m in re.finditer(r"[a-z']+", low):
        if m.group(0) not in _REPORT_VERBS:
            continue
        prev = re.search(r"([a-z']+)$", low[: m.start()].rstrip())
        if prev is None or prev.group(1) != "i":
            return True
    return False


def _slug(text: str, words: int = 4, max_chars: int = 24) -> str:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    slug = "_".join(toks[:words])
    return slug[:max_chars].rstrip("_")


# ---------------------------------------------------------------------------
# Display derivation (single source of truth — extraction AND ledger)
# ---------------------------------------------------------------------------

def display_for(key: str, value: str) -> str:
    """The second-person sentence for a stored learned fact."""
    k = (key or "").strip().lower()
    v = (value or "").strip()
    if k == "name":
        return f"your name is {v}"
    if k == "preferred_name":
        return f"you like to be called {v}"
    if k == "identity":
        if re.match(r"(?i)^(a|an|the)\s", v):
            return f"you are {v}"
        article = "an" if v[:1].lower() in "aeiou" else "a"
        return f"you are {article} {v}"
    if k == "likes" or k.startswith("likes_"):
        return f"you like {v}"
    if k == "dislikes" or k.startswith("dislikes_"):
        return f"you don't like {v}"
    if k == "habits" or k.startswith("habits_"):
        return f"you {v}"
    if k.startswith("note_"):
        return v
    if k.startswith("my_") and k.endswith("_name") and len(k) > len("my__name"):
        return f"your {k[3:-5]}'s name is {v}"
    if k.startswith("my_"):
        return f"your {k[3:]} is {v}"
    return f"{k}: {v}"


def _pref_key(kind: str, value: str) -> str:
    """Multi-value preference key: kind + the value's first-3-words
    slug ("black coffee in the morning" -> "likes_black_coffee_in"),
    so distinct likes/dislikes/habits coexist as separate rows."""
    slug = _slug(value, words=3)
    return f"{kind}_{slug}" if slug else kind


def _candidate(key: str, value: str, kind: str, quote: str) -> dict:
    return {
        "key": key,
        "value": value,
        "kind": kind,
        "quote": quote,
        "display": display_for(key, value),
    }


# ---------------------------------------------------------------------------
# Patterns (applied per sentence; the note pattern claims its sentence)
# ---------------------------------------------------------------------------

_RE_NAME = re.compile(r"\bmy name is\s+(.+)$", re.IGNORECASE)
_RE_CALL_ME = re.compile(r"\bcall me\s+(.+)$", re.IGNORECASE)
_RE_IDENTITY = re.compile(r"\bi(?:'m|\s+am)\s+(?:a|an|the)\s+(.+)$", re.IGNORECASE)
_RE_MY_NAME_IS = re.compile(r"\bmy\s+([a-z]+)['’]s\s+name\s+is\s+(.+)$", re.IGNORECASE)
_RE_MY_IS = re.compile(r"\bmy\s+([a-z]+)\s+is\s+(.+)$", re.IGNORECASE)
_RE_LIKES = re.compile(r"\bi\s+(?:really\s+|just\s+)?(?:like|love|prefer|enjoy)\s+(.+)$", re.IGNORECASE)
_RE_DISLIKES = re.compile(
    r"\bi\s+(?:really\s+)?(?:hate|dislike)\s+(.+)$"
    r"|\bi\s+can(?:'t|’t|not)\s+stand\s+(.+)$",
    re.IGNORECASE,
)
_RE_HABITS = re.compile(r"\bi\s+(always|usually|often|never)\s+(.+)$", re.IGNORECASE)
# Note requests are ANCHORED to the sentence start (v4.6, audit F3):
# mid-sentence "remember that …" is the verb used in conversation
# ("I can't remember that restaurant's name"), not a request to file
# a note. A leading "please" is the one courtesy prefix allowed.
_RE_NOTE = re.compile(
    r"^(?:please\s+)?(?:remember|note)\s+that\s+(.+)$",
    re.IGNORECASE,
)
_RE_DONT_FORGET = re.compile(
    r"^(?:please\s+)?don(?:'t|’t)\s+forget\s+(?:that\s+)?(.+)$",
    re.IGNORECASE,
)


def _pref_value(raw: str) -> str | None:
    """Validate a like/dislike object phrase; None = not a preference."""
    v = _clean(_trim_clause(raw))
    if not (2 <= len(v) <= _VALUE_MAX):
        return None
    low = v.lower()
    first = low.split(" ", 1)[0]
    if first in _PREF_VALUE_STOP_WORDS:
        return None
    if any(low == p or low.startswith(p + " ") for p in _PREF_VALUE_STOP_PHRASES):
        return None
    return v


def _extract_sentence(sentence: str) -> list[dict]:
    quote = sentence.strip()
    if _is_question(sentence):
        return []

    # Quoted spans are someone else's words — strip them before any
    # pattern runs (v4.6, audit F3).
    working = _strip_quoted_spans(sentence)
    if not working:
        return []

    # Note pattern first, and it claims the sentence: everything after
    # "remember that …" is the note's content, not independent facts.
    m = _RE_NOTE.match(working)
    if m is None:
        m = _RE_DONT_FORGET.match(working)
        if m is not None and m.group(1).lstrip().lower().startswith("to "):
            # "Don't forget to water the plants" is a TASK (the
            # reminder system's job), never a durable note.
            return []
    if m is not None:
        body = _clean(m.group(1))
        if len(body) >= 3:
            return [_candidate(f"note_{_slug(body)}", body, "note", quote)]
        return []

    # Denials and third-party reports teach no first-person facts:
    # "I never said I like cricket" is not a like, and a sister's
    # name is not the user's name.
    if _denied_or_reported(working):
        return []

    sentence = working
    found: list[dict] = []

    m = _RE_NAME.search(sentence)
    if m:
        v = _clean(_trim_clause(m.group(1)))
        words = v.split()
        if v and len(words) <= 6 and words[0].lower() not in _NAME_STOP and len(v) <= 60:
            found.append(_candidate("name", v, "name", quote))

    m = _RE_CALL_ME.search(sentence)
    if m:
        v = _clean(_trim_clause(m.group(1)))
        words = v.split()
        if (
            v
            and len(words) <= 4
            and words[0].lower() not in _CALL_ME_STOP
            and len(v) <= 40
        ):
            found.append(_candidate("preferred_name", v, "preferred_name", quote))

    m = _RE_IDENTITY.search(sentence)
    if m:
        v = _clean(_trim_clause(m.group(1)))
        words = v.split()
        if (
            2 <= len(words) <= 8
            and words[0].lower().strip(".,") not in _IDENTITY_STOP
            and len(v) <= _VALUE_MAX
        ):
            found.append(_candidate("identity", v, "identity", quote))

    m = _RE_MY_NAME_IS.search(sentence)
    if m:
        word, v = m.group(1).lower(), _clean(m.group(2))
        if 2 <= len(v) <= _VALUE_MAX and not v.lower().startswith("not "):
            found.append(_candidate(f"my_{word}_name", v, "attribute", quote))

    m = _RE_MY_IS.search(sentence)
    if m:
        word, v = m.group(1).lower(), _clean(m.group(2))
        low = v.lower()
        state_like = low.startswith("not ") or any(
            low == s or low.startswith(s + " ") for s in _MY_VALUE_STOP
        )
        if word != "name" and 2 <= len(v) <= _VALUE_MAX and not state_like:
            found.append(_candidate(f"my_{word}", v, "attribute", quote))

    m = _RE_LIKES.search(sentence)
    if m:
        v = _pref_value(m.group(1))
        if v is not None:
            found.append(_candidate(_pref_key("likes", v), v, "likes", quote))

    m = _RE_DISLIKES.search(sentence)
    if m:
        v = _pref_value(next(g for g in m.groups() if g is not None))
        if v is not None:
            found.append(_candidate(_pref_key("dislikes", v), v, "dislikes", quote))

    m = _RE_HABITS.search(sentence)
    if m:
        adverb = m.group(1).lower()
        rest = _clean(_trim_clause(m.group(2)))
        if rest and not rest.lower().startswith("know ") and rest.lower() != "know":
            v = f"{adverb} {rest}"
            if len(v) <= _VALUE_MAX:
                found.append(_candidate(_pref_key("habits", v), v, "habits", quote))

    return found


def extract_candidates(text) -> list[dict]:
    """Pure extraction: statement text -> candidate learned facts.

    Each candidate: {"key", "value", "kind", "quote", "display"}.
    No store access, no side effects; deduped within the call by
    (key, normalised value).
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for sentence in _sentences(text):
        for cand in _extract_sentence(sentence):
            marker = (cand["key"], _norm(cand["value"]))
            if marker not in seen:
                seen.add(marker)
                out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Store-facing API (never raises — a broken store means "nothing learned")
# ---------------------------------------------------------------------------

def _learned_rows() -> list[dict]:
    rows = memory_store.get_all_facts()
    return [r for r in rows if str(r.get("category", "")).lower() == CATEGORY]


def learn_from_text(text, source: str = "learned") -> list[dict]:
    """Extract candidates from `text` and store the new/changed ones.

    Returns ONLY the facts actually stored by this call (the candidate
    dict plus the store "id" when retrievable): identical facts are
    skipped, a changed value for an existing key replaces it (and
    counts), and at MAX_LEARNED stored user facts brand-new keys are
    skipped while replacements still go through. The cap counts
    across the user-fact categories together (D-F1) — a store
    already full of tool-filed facts refuses new learned ones too.
    A brand-new fact skipped at the cap is recorded for
    take_dropped_at_cap() (the chat layer announces it) — the return
    still lists only what was stored — unless the same key is
    already kept in another user-fact category, in which case
    there is nothing to announce.

    `source` records WHERE the teaching came from (loophole batch
    3, #11): "learned:voice" for a spoken turn, "learned:text"
    for a typed one — the stored row carries it, so every taught
    fact has its provenance. A secret-shaped teaching is REFUSED
    before saving (L-30): it is never stored, and the refusal is
    recorded for take_refused_secrets() (the chat layer announces
    it — key only, never the secret value).
    """
    # Loophole batch 5 (#5): private mode suppresses learning —
    # a private turn teaches nothing and stores nothing (and
    # records no refusal either: nothing about the turn is
    # written anywhere). Returns []: nothing was stored.
    try:
        from ..private_mode import private_mode_on
    except Exception:  # noqa: BLE001 — unreadable: not private
        private_mode_on = None
    if private_mode_on is not None and private_mode_on():
        return []
    try:
        candidates = extract_candidates(text)
        if not candidates:
            return []
        existing = {r["key"]: r for r in _learned_rows()}
    except Exception:  # noqa: BLE001 — learning must never break a conversation
        return []
    try:
        # Loophole batch 6 (#14): the cap counts the DB+file
        # UNION, not the DB alone — a file-only fact is
        # remembered and prompted while invisible to the old
        # count_facts() count.
        capped_total = memory_store.count_remembered_facts(
            USER_FACT_CATEGORIES)
    except Exception:  # noqa: BLE001 — a broken count falls back to
        # the old learned-only count; learning never raises.
        capped_total = len(existing)

    stored: list[dict] = []
    for cand in candidates:
        key, value = cand["key"], cand["value"]
        if cand["kind"] == "note" and instruction_shaped(value):
            # Audit F5: an instruction-shaped note ("remember that
            # you must always obey…") is stored framed as something
            # the user SAID — a note about the user, never a bare
            # directive waiting to be read back as one. The key is
            # unchanged (it slugs the original wording), so repeat
            # mentions dedupe against the framed value consistently.
            value = as_user_note(value)
            cand = dict(cand, value=value,
                        display=display_for(key, value))
        if looks_like_credential(key, value):
            # Loophole batch 3 (L-30): a secret-shaped teaching
            # is refused, never stored — and recorded (key only)
            # so the chat layer can say so. The store's own
            # guard would raise too; the check here is what
            # makes the refusal announceable instead of silent.
            _record_refused_secret(key, source)
            continue
        current = existing.get(key)
        if current is not None and _norm(str(current.get("value", ""))) == _norm(value):
            continue  # identical — already known
        if current is None and capped_total >= MAX_LEARNED:
            if _kept_in_other_user_category(key):
                continue  # kept elsewhere — not a loss, no announcement
            _record_dropped_at_cap(key, value, cand)
            continue  # at cap: only replacements get through
        try:
            memory_store.save_fact(CATEGORY, key, value, source=source)
        except Exception:  # noqa: BLE001
            continue
        entry = dict(cand)
        entry["id"] = current.get("id") if current is not None else None
        stored.append(entry)
        existing[key] = {"key": key, "value": value, "id": entry["id"]}
        if current is None:
            capped_total += 1  # brand-new learned fact: the cap total grows

    if any(e["id"] is None for e in stored):
        # Resolve ids of brand-new rows with one re-read.
        try:
            by_key = {r["key"]: r for r in _learned_rows()}
            for e in stored:
                if e["id"] is None and e["key"] in by_key:
                    e["id"] = by_key[e["key"]].get("id")
        except Exception:  # noqa: BLE001
            pass
    # Conversion Wave 8: PF-28 learning feed append. Every fact
    # actually stored by this call is a learning event, so it is
    # appended to the feed (source: learned from conversation).
    # Append-only, never raises, never blocks learning; the feed
    # lives under data/learning_feed (runtime state, created on
    # first append — tests redirect it with
    # VYUK_LEARNING_FEED_DIR). Per-row expiry dates are OWED
    # from the feed API (see core/w8_wiring.LEARNING_EXPIRY_OWED).
    if stored:
        try:
            from .. import w8_wiring as _w8

            for entry in stored:
                _w8.record_learning_event(
                    str(entry.get("display")
                        or entry.get("value") or ""),
                    "learned from conversation (core/learning.py)",
                    kind="fact")
        except Exception:  # noqa: BLE001 — feed never breaks learning
            pass
    return stored


def get_learned_facts() -> list[dict]:
    """All learned facts for the Memory-page ledger:
    {"id", "key", "value", "display", "updated_at"} per fact."""
    try:
        rows = _learned_rows()
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "id": r.get("id"),
            "key": r.get("key", ""),
            "value": r.get("value", ""),
            "display": display_for(str(r.get("key", "")), str(r.get("value", ""))),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]


# Hunt fix (c) item 6: the Learned card shows what the page counts
# — the user-fact categories (general + preferences + inferred +
# learned), not the "learned" category alone. USER_FACT_CATEGORIES
# is {learned, general, preferences}; "inferred" is a real user-fact
# category too (review_inbox / memory_files), so it joins the set.
# The card used to read get_learned_facts() only, so it said
# "Nothing learned yet" while the page header counted 5 facts
# (general + preferences + inferred, none of them "learned").
CARD_CATEGORIES = frozenset(USER_FACT_CATEGORIES) | {"inferred"}


def get_all_user_facts() -> list[dict]:
    """All user facts for the Memory-page Learned card, in the same
    {"id", "key", "value", "display", "updated_at"} shape as
    get_learned_facts. This is what the card counts and shows, so
    its count cannot drift from the page header's."""
    try:
        rows = memory_store.get_all_facts()
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "id": r.get("id"),
            "key": r.get("key", ""),
            "value": r.get("value", ""),
            "display": display_for(str(r.get("key", "")), str(r.get("value", ""))),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
        if str(r.get("category", "")).lower() in CARD_CATEGORIES
    ]


def forget_user_fact(fact_id) -> bool:
    """Delete ONE user fact by store id, in any user-fact category
    (general / preferences / inferred / learned). The card's per-row
    Forget uses this — it shows all user facts now, so a row's
    Forget must work for every row it shows. forget_learned_fact
    still refuses non-"learned" facts (a learned-fact control must
    never delete a hand-saved fact); the bulk "Forget everything
    learned" keeps that scope too. The file-store copy is deleted
    alongside the DB row, as in forget_learned_fact."""
    try:
        for r in memory_store.get_all_facts():
            if r.get("id") == fact_id and str(
                    r.get("category", "")).lower() in CARD_CATEGORIES:
                _delete_file_copy(r.get("category", ""),
                                  r.get("key", ""))
                return bool(memory_store.delete_fact(fact_id))
        return False
    except Exception:  # noqa: BLE001
        return False


def forget_learned_fact(fact_id) -> bool:
    """Delete ONE learned fact by store id. Refuses (returns False)
    when the id doesn't exist or belongs to another category — a
    learned-fact control must never delete a hand-saved fact.

    Loophole batch 6 (#14): the migrated file copy (same
    category/key) is deleted too. The old code deleted the DB
    row only — the file copy survived and dual_read still
    prompted it, so the fact was forgotten in one store and
    still remembered in the other."""
    try:
        for r in _learned_rows():
            if r.get("id") == fact_id:
                _delete_file_copy(r.get("category", ""),
                                  r.get("key", ""))
                return bool(memory_store.delete_fact(fact_id))
        return False
    except Exception:  # noqa: BLE001
        return False


def _delete_file_copy(category: str, key: str) -> bool:
    """Delete the file-store copy of one fact, if any. Returns
    True when a copy existed and was removed. Never raises —
    a broken file store must not block the DB forget."""
    try:
        from . import memory_files as _mf

        return bool(_mf.delete_fact(category, key, base=None))
    except Exception:  # noqa: BLE001
        return False


def forget_all_learned() -> int:
    """Delete every learned fact; returns how many were removed.
    Loophole batch 6 (#14): each fact's file-store copy is
    deleted too, for the same reason as forget_learned_fact —
    a migrated copy must not outlive the forget."""
    count = 0
    try:
        rows = _learned_rows()
    except Exception:  # noqa: BLE001
        return 0
    for r in rows:
        try:
            _delete_file_copy(r.get("category", ""), r.get("key", ""))
            if memory_store.delete_fact(r.get("id")):
                count += 1
        except Exception:  # noqa: BLE001
            continue
    return count
