"""Measure group of the Words pack (moved verbatim from
core/skills_words.py): reading_time, acronym_finder, citation_format,
signature_block, slug_maker, word_frequency, sentence_stats,
filler_word_count, passive_voice_check.
"""

import html
import re
import unicodedata

# INTENTIONAL: cross-part names — the measure functions resolve these
# bare names in this module's globals, so they are imported from the
# module that now owns them (skills_words_helpers), not redefined here.
from .skills_words_helpers import (
    _quote_snippet,
    _resolve_text,
    _sentences,
    _with_note,
    _words,
)


# ---------------------------------------------------------------------------
# READING TIME — words into minutes, both rates stated
# ---------------------------------------------------------------------------

_SILENT_WPM = 200
_ALOUD_WPM = 130


def _fmt_minutes(minutes: float) -> str:
    """A spoken-friendly duration for a fractional minute count."""
    if minutes < 1:
        s = max(1, round(minutes * 60))
        return f"about {s} second" + ("s" if s != 1 else "")
    if minutes < 10:
        return f"about {minutes:.1f} minutes"
    total = int(round(minutes))
    hours, mins = divmod(total, 60)
    if hours:
        out = f"about {hours} hour" + ("s" if hours != 1 else "")
        if mins:
            out += f" {mins} minute" + ("s" if mins != 1 else "")
        return out
    return f"about {total} minutes"


def reading_time(text: str = "", path: str = "") -> str:
    """How long a text takes to read silently and aloud."""
    content, source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    count = len(_words(content))
    if count == 0:
        return "Nothing to time — the text has no words in it."
    silent = count / _SILENT_WPM
    aloud = count / _ALOUD_WPM
    where = f" in {source_name}" if source_name else ""
    body = (f"That's {count:,} word{'s' if count != 1 else ''}"
            f"{where} — {_fmt_minutes(silent)} to read silently "
            f"(at {_SILENT_WPM} words per minute) and "
            f"{_fmt_minutes(aloud)} read aloud (at {_ALOUD_WPM} "
            "words per minute). Averages, not a promise — dense "
            "or technical text runs slower.")
    return _with_note(body, note)


# ---------------------------------------------------------------------------
# ACRONYM FINDER — defined first, then used-but-undefined
# ---------------------------------------------------------------------------

_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")
_FUNC_WORDS = frozenset({"of", "the", "and", "for", "&", "de",
                         "la", "in", "on", "an", "a"})
_MAX_UNDEFINED_LISTED = 25


def _initials_match(words: list, acronym: str) -> bool:
    """Do the words' initials spell the acronym, skipping small
    function words (of/the/and…)?"""
    initials = "".join(
        w[0].upper() for w in words
        if w and w.lower().strip(".,;:") not in _FUNC_WORDS)
    return initials == acronym


def _defined_acronyms(text: str) -> dict:
    """Acronym -> expansion for the two defining shapes:
    'Expansion Words (ACR)' and 'ACR (Expansion Words)'.
    An expansion only counts when its initials really spell
    the acronym, so '(established 1951)'-style parentheses are
    not mistaken for definitions."""
    defined = {}
    # Shape 1: words before a parenthesised acronym.
    for m in re.finditer(r"\(([A-Z]{2,6})\)", text):
        acronym = m.group(1)
        if acronym in defined:
            continue
        before = _words(text[:m.start()])
        # Shortest capitalised-start suffix whose initials match.
        for start in range(len(before) - 1, -1, -1):
            span = before[start:]
            if len(span) > 10:
                break
            if not span[0][:1].isupper():
                continue
            if _initials_match(span, acronym):
                defined[acronym] = " ".join(span)
                break
    # Shape 2: acronym, then its expansion in parentheses.
    for m in re.finditer(r"\b([A-Z]{2,6}) \(([^)]{2,120})\)", text):
        acronym = m.group(1)
        if acronym in defined:
            continue
        expansion = " ".join(m.group(2).split())
        exp_words = expansion.split()
        if _initials_match(exp_words, acronym):
            defined[acronym] = expansion
    return defined


def acronym_finder(text: str = "", path: str = "") -> str:
    """Find acronyms and the expansions the text itself gives."""
    content, _source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    counts: dict[str, int] = {}
    for m in _ACRONYM_RE.finditer(content):
        counts[m.group(0)] = counts.get(m.group(0), 0) + 1
    if not counts:
        return _with_note(
            "I didn't find any acronyms in that — nothing that "
            "looks like 2–6 capital letters standing alone.", note)
    defined = _defined_acronyms(content)
    lines = []
    if defined:
        lines.append("Defined in the text:")
        for acronym in sorted(defined):
            lines.append(
                f"- {acronym} — {defined[acronym]} "
                f"(used {counts.get(acronym, 0)} time"
                f"{'s' if counts.get(acronym, 0) != 1 else ''})")
    undefined = {a: c for a, c in counts.items() if a not in defined}
    if undefined:
        ordered = sorted(undefined.items(),
                           key=lambda kv: (-kv[1], kv[0]))
        shown = ordered[:_MAX_UNDEFINED_LISTED]
        lines.append("Used but never defined here:")
        lines.append(", ".join(f"{a} ×{c}" for a, c in shown))
        if len(ordered) > len(shown):
            lines.append(
                f"(and {len(ordered) - len(shown)} more — only "
                f"the first {_MAX_UNDEFINED_LISTED} are listed.)")
    if not defined and undefined:
        lines.insert(
            0, "No acronym is actually defined in the text — "
               "here is everything that looks like one:")
    return _with_note("\n".join(lines), note)


# ---------------------------------------------------------------------------
# CITATION FORMAT — APA or MLA, missing fields simply omitted
# ---------------------------------------------------------------------------


def citation_format(authors: str = "", year: str = "",
                    title: str = "", source: str = "",
                    style: str = "apa") -> str:
    """Format one reference as an APA or MLA citation."""
    style = (style or "apa").strip().lower()
    if style not in ("apa", "mla"):
        return ("Citation styles here are 'apa' and 'mla' — "
                "which would you like?")
    authors = " ".join(str(authors or "").split())
    year = " ".join(str(year or "").split())
    title = " ".join(str(title or "").split())
    source = " ".join(str(source or "").split())
    if not (authors or title or source):
        return ("There's nothing to cite yet — give me at least "
                "an author, a title or a source (a journal, site "
                "or publisher), plus a year if you have one.")
    if style == "apa":
        head = (authors + (f" ({year})" if year else "")).strip()
        parts = [head, title.rstrip("."), source.rstrip(".")]
        body = ". ".join(p for p in parts if p)
        return "APA citation: " + body + "."
    # MLA: Authors. "Title." Source, Year.
    parts = []
    if authors:
        parts.append(authors if authors.endswith(".")
                     else authors + ".")
    if title:
        clean = title.rstrip()
        punct = "" if clean[-1:] in (".", "?", "!") else "."
        parts.append(f'"{clean}{punct}"')
    tail = ", ".join(p for p in (source.rstrip("."), year) if p)
    if tail:
        parts.append(tail + ".")
    return "MLA citation: " + " ".join(parts)


# ---------------------------------------------------------------------------
# SIGNATURE BLOCK — plain text + compact HTML
# ---------------------------------------------------------------------------


def signature_block(name: str = "", role: str = "", org: str = "",
                    phone: str = "", website: str = "") -> str:
    """An email signature block in plain text and in HTML."""
    name = " ".join(str(name or "").split())
    if not name:
        return ("A signature needs at least a name — the rest "
                "(role, organisation, phone, website) can be "
                "left blank and are simply left out.")
    role = " ".join(str(role or "").split())
    org = " ".join(str(org or "").split())
    phone = " ".join(str(phone or "").split())
    website = " ".join(str(website or "").split())
    role_line = " | ".join(p for p in (role, org) if p)

    plain_lines = [name]
    if role_line:
        plain_lines.append(role_line)
    if phone:
        plain_lines.append(phone)
    if website:
        plain_lines.append(website)

    html_parts = [f"<strong>{html.escape(name)}</strong>"]
    if role_line:
        html_parts.append(html.escape(role_line))
    if phone:
        html_parts.append(html.escape(phone))
    if website:
        href = (website if website.startswith(("http://", "https://"))
                else "https://" + website)
        display = re.sub(r"^https?://", "", website).rstrip("/")
        html_parts.append(
            f'<a href="{html.escape(href, quote=True)}">'
            f"{html.escape(display)}</a>")
    html_block = ('<div class="signature">'
                  + "<br>".join(html_parts) + "</div>")
    return ("Plain text:\n" + "\n".join(plain_lines)
            + "\n\nHTML:\n" + html_block)


# ---------------------------------------------------------------------------
# SLUG MAKER — a title into a URL slug
# ---------------------------------------------------------------------------

_SLUG_MAX = 80


def slug_maker(title: str = "") -> str:
    """Turn a title into a lowercase ASCII URL slug."""
    raw = str(title or "")
    if not raw.strip():
        return ("Give me a title and I'll slug it — like "
                "slug_maker(title='My Trip to the Mountains!').")
    folded = (unicodedata.normalize("NFKD", raw)
              .encode("ascii", "ignore").decode("ascii"))
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    if not slug:
        return ("Nothing slug-able in that — once punctuation "
                "and non-ASCII characters are folded away, no "
                "letters or digits remain.")
    note = ""
    if len(slug) > _SLUG_MAX:
        slug = slug[:_SLUG_MAX].rstrip("-")
        note = (f" (Trimmed to the {_SLUG_MAX}-character cap "
                "for slugs.)")
    return f"Slug: {slug}{note}"


# ---------------------------------------------------------------------------
# WORD FREQUENCY — honest counting, nothing filtered but short words
# ---------------------------------------------------------------------------

_TOP_WORDS = 15
_MIN_WORD_LEN = 3


def word_frequency(text: str = "", path: str = "") -> str:
    """The 15 most frequent words, with counts and shares."""
    content, _source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    cleaned = content.replace("'", "").replace("’", "")
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9]+", cleaned)
              if len(t) >= _MIN_WORD_LEN]
    if not tokens:
        return _with_note(
            f"No countable words — nothing of {_MIN_WORD_LEN} "
            "or more letters/numbers in that.", note)
    freq: dict[str, int] = {}
    for token in tokens:
        freq[token] = freq.get(token, 0) + 1
    total = len(tokens)
    ordered = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = ordered[:_TOP_WORDS]
    lines = [f"Word frequency (top {len(shown)} of "
             f"{len(freq):,} distinct words; shares are of the "
             f"{total:,} counted words):"]
    lines += [f"- {w} ×{c} ({c / total * 100:.1f}%)"
              for w, c in shown]
    lines.append(
        "Honest counting: common words like 'the' and 'and' "
        "are counted like everything else — nothing is "
        f"stopword-filtered. Only words under {_MIN_WORD_LEN} "
        "characters and punctuation are skipped.")
    return _with_note("\n".join(lines), note)


# ---------------------------------------------------------------------------
# SENTENCE STATS — length shape of a text
# ---------------------------------------------------------------------------

_LONG_SENTENCE_WORDS = 25


def sentence_stats(text: str = "", path: str = "") -> str:
    """Sentence count, average length, extremes, long share."""
    content, _source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    sentences = _sentences(content)
    if not sentences:
        return "Nothing to measure — the text has no sentences."
    counts = [len(_words(s)) for s in sentences]
    total_words = sum(counts)
    longest_i = max(range(len(sentences)), key=lambda i: counts[i])
    shortest_i = min(range(len(sentences)), key=lambda i: counts[i])
    over = sum(1 for c in counts if c > _LONG_SENTENCE_WORDS)
    lines = [
        f"Sentences: {len(sentences)}",
        f"Average length: {total_words / len(sentences):.1f} "
        f"words per sentence ({total_words:,} words in all)",
        f"Longest ({counts[longest_i]} words): "
        f"\"{_quote_snippet(sentences[longest_i])}\"",
        f"Shortest ({counts[shortest_i]} words): "
        f"\"{_quote_snippet(sentences[shortest_i])}\"",
        f"Sentences over {_LONG_SENTENCE_WORDS} words: {over} "
        f"of {len(sentences)} "
        f"({over / len(sentences) * 100:.1f}%) — long sentences "
        "are where readers get lost, so that share is the one "
        "to watch.",
    ]
    return _with_note("\n".join(lines), note)


# ---------------------------------------------------------------------------
# FILLER WORD COUNT — a talk transcript's um/uh/like census
# ---------------------------------------------------------------------------

_FILLER_SINGLES = ("um", "uh", "like", "actually", "basically",
                   "literally")
_FILLER_PHRASES = ("you know", "sort of", "kind of")


def filler_word_count(text: str = "", path: str = "") -> str:
    """Count filler words and phrases in a transcript."""
    content, _source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    tokens = [w.lower() for w in _words(content)]
    if not tokens:
        return "Nothing to count — the transcript has no words."
    counts: dict[str, int] = {}
    for filler in _FILLER_SINGLES:
        counts[filler] = tokens.count(filler)
    for phrase in _FILLER_PHRASES:
        pw = phrase.split()
        counts[phrase] = sum(
            1 for i in range(len(tokens) - len(pw) + 1)
            if tokens[i:i + len(pw)] == pw)
    total_fillers = sum(counts.values())
    rate = total_fillers / len(tokens) * 100
    if rate < 2:
        verdict = ("Verdict: clean delivery — under 2 fillers "
                   "per 100 words is barely noticeable.")
    elif rate <= 5:
        verdict = ("Verdict: noticeable but normal for "
                   "unscripted speech (2–5 per 100 words).")
    else:
        verdict = ("Verdict: heavy — over 5 per 100 words. "
                   "Worth a practice pass if this is for a talk "
                   "or an interview.")
    breakdown = ", ".join(
        f"{f} {counts[f]}" for f in _FILLER_SINGLES + _FILLER_PHRASES)
    body = (f"{total_fillers} filler(s) in {len(tokens):,} words "
            f"— {rate:.1f} per 100 words.\n"
            f"Breakdown: {breakdown}.\n"
            f"{verdict}\n"
            "(Each phrase — 'you know', 'sort of', 'kind of' — "
            "counts as one filler; its words still count in the "
            "word total. And 'like' is counted wherever it "
            "appears, even as a verb — this counts words, it "
            "doesn't judge grammar.)")
    return _with_note(body, note)


# ---------------------------------------------------------------------------
# PASSIVE VOICE CHECK — a labelled heuristic, not grammar parsing
# ---------------------------------------------------------------------------

_BE_VERBS = frozenset({"am", "is", "are", "was", "were", "be",
                       "been", "being"})
_PARTICIPLES = frozenset({
    "built", "made", "written", "given", "taken", "shown",
    "known", "seen", "done", "found", "held", "sent", "set",
    "put", "told", "brought", "bought", "thought", "caught",
    "taught", "chosen", "driven", "eaten", "forgotten", "hidden",
    "broken", "spoken", "stolen", "worn", "drawn", "grown",
    "thrown", "won", "lost", "paid", "read", "said", "met",
    "kept", "left", "felt", "led", "sold",
})
_PARTICIPLE_RE = re.compile(r"^[a-z]{4,}(?:ed|en)$")
_PARTICIPLE_EXCEPTIONS = frozenset({"indeed"})
_WINDOW = 3  # words after the be-verb that are searched


def _looks_passive(tokens: list) -> bool:
    """The heuristic: a be-verb with a participle-looking word
    within the next _WINDOW words."""
    for i, token in enumerate(tokens):
        if token not in _BE_VERBS:
            continue
        for nxt in tokens[i + 1:i + 1 + _WINDOW]:
            if nxt in _PARTICIPLES:
                return True
            if (nxt not in _PARTICIPLE_EXCEPTIONS
                    and _PARTICIPLE_RE.match(nxt)):
                return True
    return False


def passive_voice_check(text: str = "", path: str = "") -> str:
    """Flag sentences that look passive, heuristically."""
    content, _source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    sentences = _sentences(content)
    if not sentences:
        return "Nothing to check — the text has no sentences."
    flagged = [s for s in sentences
               if _looks_passive([w.lower() for w in _words(s)])]
    pct = len(flagged) / len(sentences) * 100
    lines = [
        f"Heuristic check: {len(flagged)} of {len(sentences)} "
        f"sentence(s) ({pct:.0f}%) look passive."]
    if flagged:
        lines.append("Examples:")
        lines += [f"- \"{_quote_snippet(s)}\"" for s in flagged[:3]]
    else:
        lines.append("No passive-looking sentences found.")
    lines.append(
        "This is a pattern heuristic — a be-verb (was/is/are…) "
        "with a participle-shaped word just after it — not "
        "grammar parsing. It can miss real passives and flag "
        "active sentences that happen to match the shape, so "
        "treat the examples as prompts to look, not verdicts.")
    return _with_note("\n".join(lines), note)
