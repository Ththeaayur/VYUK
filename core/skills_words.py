"""
WORDS skills pack for VYUK — ten small tools for people who
work with text: timing it, dissecting it, citing it, signing it
and keeping the good lines from it.

Nine of the ten are pure readers in the skills_text mould: plain
functions returning human-readable strings, handlers never
raise, and every cap is enforced in the open and disclosed in
the result. They share skills_text's input convention — each
text tool takes `text` OR `path`, never both; a file is read as
UTF-8 with undecodable characters replaced (and the replacement
stated). One difference from the text pack: rather than refusing
long input, these tools work on the first 200,000 characters and
say so, because a word count of the opening of a long document
is still useful while a refusal is not.

The pack's one WRITE is quote_vault: saved quotes persist as
memory facts (core/memory/memory.py) — small JSON payloads under the
fixed category `quotes` — exactly like the expense and reading
logs in skills_productivity_logs, so they survive restarts and show
up in the user's memory. quote_vault never invents: find and
list report only what is actually stored.

Two honesty rules worth naming: passive_voice_check is a
HEURISTIC pattern-match, not grammar parsing, and says so in
its answer, its description and here; and word_frequency counts
common words like any others — no stopword filtering — and says
that too.

Stdlib only (json, re, unicodedata, html, datetime, pathlib)
and identical on Linux and Windows. Wiring: the parent
(core/funnel/tools_registry.py) calls register_all(registry) — this module never
touches the registry singleton itself.
"""

# INTENTIONAL: stdlib imports the old module's namespace carried,
# re-exported so the dir diff is empty in both directions.
import datetime
import html
import json
import re
import unicodedata
from pathlib import Path

# INTENTIONAL: re-exports of every moved name, so importers of
# the old module path keep working unchanged (hands3 discipline).
from .skills_words_helpers import (
    _MAX_CHARS,
    _READ_BYTES,
    _WORD_RE,
    _resolve_text,
    _with_note,
    _words,
    _sentences,
    _quote_snippet,
)
from .skills_words_measure import (
    _SILENT_WPM,
    _ALOUD_WPM,
    _fmt_minutes,
    reading_time,
    _ACRONYM_RE,
    _FUNC_WORDS,
    _MAX_UNDEFINED_LISTED,
    _initials_match,
    _defined_acronyms,
    acronym_finder,
    citation_format,
    signature_block,
    _SLUG_MAX,
    slug_maker,
    _TOP_WORDS,
    _MIN_WORD_LEN,
    word_frequency,
    _LONG_SENTENCE_WORDS,
    sentence_stats,
    _FILLER_SINGLES,
    _FILLER_PHRASES,
    filler_word_count,
    _BE_VERBS,
    _PARTICIPLES,
    _PARTICIPLE_RE,
    _PARTICIPLE_EXCEPTIONS,
    _WINDOW,
    _looks_passive,
    passive_voice_check,
)
from .skills_words_make import (
    _QUOTES_CATEGORY,
    _MAX_QUOTE_CHARS,
    _MAX_AUTHOR_CHARS,
    _MAX_TAG_CHARS,
    _LIST_COUNT,
    _store,
    _load_quotes,
    _quote_line,
    _save_quote,
    quote_vault,
)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Words pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="reading_time",
        description="Estimate how long a text takes to read: word count, minutes to read silently at 200 words per minute and minutes read aloud at 130 words per minute (both rates are stated in the answer). Use before a talk, a presentation, a speech or a long read — 'how long will this take to read out?' Give either text or the path to a text file (exactly one; a file is read as UTF-8, and input is worked on up to the first 200,000 characters, disclosed when capped). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to time (give this OR path)"},
                "path": {"type": "string", "description": "Path to a text file to time (give this OR text)"},
            },
            "required": [],
        },
        handler=reading_time,
    )
    registry.register(
        name="acronym_finder",
        description="Find the acronyms in a text (2–6 capital letters) and work out which ones the text itself defines. Both defining shapes are recognised — 'Indian Institute of Technology (IIT)' and 'IIT (Indian Institute of Technology)' — and a definition is only credited when the expansion's initials really spell the acronym. Defined acronyms are listed first with their expansions and use counts; acronyms used but never defined follow with their counts (first 25 listed). Use for papers, reports and specs full of unexplained initials. Give either text or path (exactly one). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to scan (give this OR path)"},
                "path": {"type": "string", "description": "Path to a text file to scan (give this OR text)"},
            },
            "required": [],
        },
        handler=acronym_finder,
    )
    registry.register(
        name="citation_format",
        description="Format one reference as a properly shaped citation in APA or MLA style (default APA), for a bibliography or reference list. Give authors, year, title and source (a journal, website or publisher); missing fields are simply omitted — nothing is invented and no placeholder text is printed. Use when a student needs a source cited correctly in an assignment.",
        parameters={
            "type": "object",
            "properties": {
                "authors": {"type": "string", "description": "Author(s) as they should appear, e.g. 'Gattani, A.' or 'Smith, J., & Lee, K.'"},
                "year": {"type": "string", "description": "Publication year, e.g. '2026'"},
                "title": {"type": "string", "description": "Title of the work"},
                "source": {"type": "string", "description": "Journal, website or publisher the work appeared in"},
                "style": {"type": "string", "enum": ["apa", "mla"], "description": "'apa' (default) or 'mla'"},
            },
            "required": [],
        },
        handler=citation_format,
    )
    registry.register(
        name="signature_block",
        description="Build an email signature block from a name plus optional role, organisation, phone and website. Returns the block twice: as plain text (for pasting anywhere) and as a compact HTML version (for mail clients that accept HTML signatures, with the website as a link). Fields left blank are omitted from both versions. Use when setting up a new email account or a fresher signature.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name (the only required field)"},
                "role": {"type": "string", "description": "Job title or role, e.g. 'B.Tech Student, IEOR'"},
                "org": {"type": "string", "description": "Organisation, e.g. 'IIT Bombay'"},
                "phone": {"type": "string", "description": "Phone number as it should appear"},
                "website": {"type": "string", "description": "Website URL or domain"},
            },
            "required": ["name"],
        },
        handler=signature_block,
    )
    registry.register(
        name="slug_maker",
        description="Turn a title into a URL slug: lowercase, accents folded to plain ASCII (é becomes e), every run of non-alphanumerics becomes a single hyphen, leading/trailing hyphens trimmed, capped at 80 characters with the trim disclosed. Use for blog posts, page URLs, file names and permalinks. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The title or heading to slug"},
            },
            "required": ["title"],
        },
        handler=slug_maker,
    )
    registry.register(
        name="word_frequency",
        description="Count the 15 most frequent words in a text, with counts and each word's share of the total. Words are case-folded with punctuation stripped; words under 3 characters are skipped. Counting is honest — common words like 'the' are NOT removed (no stopword filtering), and the answer says so. Use for spotting overused words in an essay or seeing what a document is really about. Give either text or path (exactly one). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to count (give this OR path)"},
                "path": {"type": "string", "description": "Path to a text file to count (give this OR text)"},
            },
            "required": [],
        },
        handler=word_frequency,
    )
    registry.register(
        name="sentence_stats",
        description="Measure the sentence shape of a text: how many sentences, the average words per sentence, the longest and shortest sentence (quoted, truncated at 120 characters), and what share of sentences run over 25 words — the share where readers tend to get lost. Use when tightening a draft, an essay or a report. Give either text or path (exactly one). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to measure (give this OR path)"},
                "path": {"type": "string", "description": "Path to a text file to measure (give this OR text)"},
            },
            "required": [],
        },
        handler=sentence_stats,
    )
    registry.register(
        name="filler_word_count",
        description="Count the filler words in a speech or talk transcript: um, uh, like, actually, basically, literally, and the phrases 'you know', 'sort of' and 'kind of'. Gives the per-word breakdown, the total, and fillers per 100 words with a calibrated verdict (under 2 per 100 reads as clean delivery). Use after transcribing a practice talk, lecture or interview to see how polished the delivery sounds. Give either text or path (exactly one). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The transcript to count (give this OR path)"},
                "path": {"type": "string", "description": "Path to a transcript file (give this OR text)"},
            },
            "required": [],
        },
        handler=filler_word_count,
    )
    registry.register(
        name="passive_voice_check",
        description="A heuristic check for passive voice in a text: flags sentences where a be-verb (am/is/are/was/were/be/been/being) is closely followed by a participle-shaped word, reports what percentage of sentences are flagged, and quotes up to three examples. It is explicitly a heuristic — pattern matching, not grammar parsing — so it can miss real passives and flag active sentences; the answer says so too. Use for a first pass over a draft before manual revision, not as a proofreader's verdict. Give either text or path (exactly one). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to check (give this OR path)"},
                "path": {"type": "string", "description": "Path to a text file to check (give this OR text)"},
            },
            "required": [],
        },
        handler=passive_voice_check,
    )
    registry.register(
        name="quote_vault",
        description="A small vault for treasured quotes, stored in VYUK's memory so they survive restarts. action 'save' stores a quote with an optional author and tag; action 'find' searches stored quotes by a substring matched against the quote, its author and its tag; action 'list' shows the latest 10 saved. Use for lines from books, talks and mentors that are worth keeping and finding again. This one WRITES (a memory fact per quote); the rest of the Words pack only reads.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "find", "list"], "description": "'save', 'find' or 'list' (default list)"},
                "quote": {"type": "string", "description": "The quote's words (for save)"},
                "author": {"type": "string", "description": "Who said or wrote it (for save, optional)"},
                "tag": {"type": "string", "description": "A short tag like 'focus' or 'cricket' (for save, optional)"},
                "query": {"type": "string", "description": "Substring to search for (for find)"},
            },
            "required": [],
        },
        handler=quote_vault,
    )
