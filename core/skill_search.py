"""Shared goal-search token index for the Capabilities Centre.

UI Phase 2 (D-P2-05): ONE index, two callers — the catalogue
widget's search box and the command palette's goal lane. A
goal phrase ("send an email", "what time is it") ranks the
tool it names first, so the user searches by what they want
to DO, not by the tool's snake_case name.

The weights match the funnel's ``keyword_scores`` (name 12 /
synonym 8 / description 2) so the GUI and the voice path
agree on what a phrase means; the index adds the friendly
name (12 — the label the user actually reads) and the group
name (4 — "desktop" should find Desktop control tools).

Qt-free and pure: build the index once from the live
catalogue + registry, then ``search`` is a lookup. No
per-call rescan, no second matcher in any widget.
"""

from __future__ import annotations

import re

from core.catalogue_groups_data import GROUP_MEMBERS
from core.funnel.toolbox_tokens import (
    TOOL_SYNONYMS,
    WORD_SYNONYMS,
    stem,
)
from core.tool_labels import tool_friendly_name

_NAME_WEIGHT = 12
_FRIENDLY_WEIGHT = 12
_SYNONYM_WEIGHT = 8
_GROUP_WEIGHT = 4
_DESCRIPTION_WEIGHT = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Words that carry no intent in a goal phrase ("send an email"
#: is "send" + "email"; "an" must not rank anything up). Filler
#: and question words only — never a content word ("more" stays:
#: it is the "More skills" group token).
_STOPWORDS = frozenset({
    "a", "an", "the", "to", "use", "with", "my", "me", "please",
    "can", "you", "i", "want", "need", "let", "do", "it", "is",
    "for", "of", "on", "in", "at", "no", "such", "any", "some",
    "what", "how", "when", "where", "are", "does", "and", "or",
    "if", "then", "than", "this", "that", "its", "there", "here",
    "not", "just", "only",
})

#: Meta-language stems that descriptions and group names use
#: about themselves ("this skill does…", "the tool…", "More
#: skills") — they carry no content, so they never rank a tool
#: up. Dropped from the description and group sources only: a
#: NAME or synonym that says "skill" still matches (nobody
#: names a tool that, but the rule is the source's, not the
#: word's). Without this, "xyzzy-no-such-skill" matches half
#: the catalogue on the word "skill" alone.
_META_TOKENS = frozenset({"skill", "tool", "vyuk"})


def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens (the same shape as the
    funnel's tokenizer — stemmed on both sides, so the two
    rankings stay aligned)."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def _stemmed(text: str) -> set[str]:
    return {stem(t) for t in _tokens(text)}


def _expand(stemmed: set[str]) -> set[str]:
    """Query stems plus their WORD_SYNONYMS expansions (mirrors
    the funnel's expansion, so "snap" reaches "screenshot")."""
    out = set(stemmed)
    for tok in list(stemmed):
        for phrase in WORD_SYNONYMS.get(tok, ()):
            out |= _stemmed(phrase)
    return out


def build_index(group_members=None, declarations=None):
    """Build the shared index.

    ``group_members`` defaults to the live GROUP_MEMBERS;
    ``declarations`` defaults to the live registry. Pass both
    explicitly in tests (a small fixture) instead of the live
    catalogue. Returns an opaque dict: {"entries": [...]} where
    each entry carries the tool name, its group, and a
    {stemmed token: weight} map holding the BEST weight per
    stem across its sources.
    """
    if group_members is None:
        group_members = GROUP_MEMBERS
    group_of = {}
    for group, names in group_members.items():
        for name in names:
            group_of[name] = group
    if declarations is None:
        from core.funnel.tools_registry import tools

        declarations = tools.get_declarations()
    descriptions = {d.get("name", ""): d.get("description", "")
                    for d in declarations}
    entries = []
    for name in sorted(group_of):
        tokens: dict[str, int] = {}

        def _add(text, weight):
            for tok in _stemmed(text):
                if weight > tokens.get(tok, 0):
                    tokens[tok] = weight

        _add(name.replace("_", " "), _NAME_WEIGHT)
        _add(tool_friendly_name(name), _FRIENDLY_WEIGHT)
        _add(" ".join(TOOL_SYNONYMS.get(name, ())), _SYNONYM_WEIGHT)
        _add(" ".join(t for t in _tokens(group_of[name])
                      if stem(t) not in _META_TOKENS), _GROUP_WEIGHT)
        _add(" ".join(t for t in _tokens(descriptions.get(name, ""))
                      if stem(t) not in _META_TOKENS),
             _DESCRIPTION_WEIGHT)
        entries.append({"name": name, "group": group_of[name],
                        "tokens": tokens})
    return {"entries": entries}


def search(index, query, limit=50):
    """Rank tool names for a goal phrase, best first.

    Scores the query's stemmed tokens (stopwords dropped)
    against each entry's token map: a token the user actually
    typed counts its full weight; a token that only arrived
    through WORD_SYNONYMS expansion counts half — the
    expansion is a guess ("send" also means "share"), so it
    must never outrank the word the user typed (a full-weight
    expansion lets "send an email" rank get_unread_email
    above send_email on its "mail"/"inbox" synonyms). A
    prefix hit (the entry token starts with a typed query
    stem of 3+ chars, e.g. "emai" -> "email") counts half,
    rounded down. Ties break by group order, then name, so
    the ranking is deterministic. An empty query scores
    nothing (the caller shows the full catalogue instead).
    """
    entries = (index or {}).get("entries") or []
    raw = _tokens(query)
    if not raw:
        return []
    core = {stem(t) for t in raw if t not in _STOPWORDS}
    if not core:
        return []
    expanded = _expand(core) - core
    group_order = {g: i for i, g in enumerate(GROUP_MEMBERS)}
    scored = []
    for entry in entries:
        tokens = entry["tokens"]
        score = 0
        for qs in core:
            w = tokens.get(qs)
            if w:
                score += w
                continue
            if len(qs) >= 3:
                best = 0
                for tok, tw in tokens.items():
                    if tok.startswith(qs) and tw > best:
                        best = tw
                score += best // 2
        for qs in expanded:
            w = tokens.get(qs)
            if w:
                score += w // 2
        if score:
            scored.append((-score, group_order.get(entry["group"], 99),
                            entry["name"]))
    scored.sort()
    if limit is None:
        return [name for _s, _g, name in scored]
    return [name for _s, _g, name in scored[:limit]]


_INDEX = None


def get_index():
    """The live index, built once and cached (the catalogue is
    static for the life of the process)."""
    global _INDEX
    if _INDEX is None:
        _INDEX = build_index()
    return _INDEX
