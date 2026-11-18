"""
Text skills pack for VYUK (v5.12) — four small tools that work on
text itself: statistics, case conversion, contact-pattern
extraction, and one careful in-place find-and-replace.

The first three follow the skills_smart_encode rules: plain functions
returning human-readable strings, handlers never raise, every cap
enforced in the open and disclosed in the result. They are pure
READS — they compute text and return it, changing nothing on the
machine. replace_in_file is the pack's one WRITE: it edits a file
in place, but only after counting exactly what it will change,
refusing files it cannot decode cleanly, and writing back through
a temp file + os.replace so the original is never half-written.

Input convention for the three readers: each takes `text` and/or
`path`. If a path is given, the file is read as UTF-8 with
undecodable characters replaced (and that replacement is stated in
the result); files over 2 MB are refused with the cap named.
Exactly one source — both or neither gets an honest line.

No diff tool lives here on purpose: compare_files already diffs.

Stdlib-only (os, re, tempfile, pathlib) and identical on Linux
and Windows. Wiring: the parent (core/funnel/tools_registry.py) calls
register_all(registry) — this module never touches the registry
singleton itself.
"""

import os
import re
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared input resolution — exactly one of text / path
# ---------------------------------------------------------------------------

_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_RETURN_CHARS = 4000


def _resolve_text(text, path):
    """Resolve the text/path input convention.

    -> (content, source_name, note, error): on success error is
    None, content is the text to work on, source_name is the file's
    display name (None when the text was given directly) and note
    carries any read caveat (e.g. replaced characters) or None.
    On failure content is None and error is the honest line.
    """
    raw_text = "" if text is None else (
        text if isinstance(text, str) else str(text))
    raw_path = "" if path is None else str(path).strip()
    if raw_text and raw_path:
        return None, None, None, (
            "You gave me both text and a file path — I need "
            "exactly one source. Drop one of them and ask again.")
    if raw_text:
        return raw_text, None, None, None
    if not raw_path:
        return None, None, None, (
            "There's nothing to work on — give me some text, or "
            "the path to a text file.")
    p = Path(raw_path).expanduser()
    if not p.exists():
        return None, None, None, (
            f"I couldn't find a file at '{raw_path}'.")
    if not p.is_file():
        return None, None, None, (
            f"'{raw_path}' is a folder, not a file — point me at "
            "a text file.")
    try:
        size = p.stat().st_size
    except OSError as e:
        return None, None, None, f"Couldn't inspect '{p.name}': {e}"
    if size > _MAX_FILE_BYTES:
        return None, None, None, (
            f"'{p.name}' is {size / 1048576:.1f} MB — the "
            "file-reading cap for these text tools is 2 MB, so I "
            "didn't read it.")
    try:
        raw = p.read_bytes()
    except OSError as e:
        return None, None, None, f"Couldn't read '{p.name}': {e}"
    content = raw.decode("utf-8", errors="replace")
    note = None
    replaced = content.count("\ufffd")
    if replaced:
        note = (f"Note: '{p.name}' wasn't clean UTF-8 — "
                f"{replaced} character"
                f"{'s' if replaced != 1 else ''} couldn't be "
                "decoded and were replaced with �, so the result "
                "covers the whole file but those spots are "
                "approximate.")
    return content, p.name, note, None


def _with_note(body: str, note) -> str:
    """Append a read-caveat note (from _resolve_text) if present."""
    return body if not note else body + "\n" + note


# ---------------------------------------------------------------------------
# TEXT STATS — counts, time estimates, frequent words
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?=\s|$)")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

# A deliberately small stop list for the frequent-words view —
# enough to keep "the/and/of" from crowding out the real words,
# not a linguistics project.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "than",
    "so", "such", "is", "are", "was", "were", "be", "been",
    "being", "in", "on", "at", "to", "of", "for", "with", "by",
    "from", "as", "into", "over", "under", "it", "its", "this",
    "that", "these", "those", "i", "you", "he", "she", "we",
    "they", "them", "his", "her", "their", "our", "your", "my",
    "me", "him", "us", "not", "no", "all", "any", "both",
    "each", "few", "more", "most", "other", "some", "only",
    "own", "same", "too", "very", "can", "will", "just",
    "should", "now", "do", "does", "did", "have", "has", "had",
    "there", "here", "again", "once",
})


def _stats(text: str) -> dict:
    """Pure core of text_stats: every number, no formatting."""
    words = _WORD_RE.findall(text)
    word_count = len(words)
    sentences = sum(
        1 for part in _SENTENCE_SPLIT_RE.split(text)
        if _WORD_RE.search(part))
    paragraphs = sum(
        1 for part in _PARAGRAPH_SPLIT_RE.split(text)
        if part.strip())
    freq = {}
    for w in words:
        lw = w.lower()
        if lw in _STOPWORDS:
            continue
        freq[lw] = freq.get(lw, 0) + 1
    top_words = sorted(freq.items(),
                       key=lambda kv: (-kv[1], kv[0]))[:5]
    return {
        "characters": len(text),
        "characters_no_spaces": len(re.sub(r"\s", "", text)),
        "words": word_count,
        "sentences": sentences,
        "paragraphs": paragraphs,
        "lines": len(text.splitlines()) if text else 0,
        "average_word_length": (
            sum(len(w) for w in words) / word_count
            if word_count else 0.0),
        "reading_minutes": word_count / 200,
        "speaking_minutes": word_count / 130,
        "top_words": top_words,
    }


def _duration(minutes: float) -> str:
    """A spoken-friendly duration for a fractional minute count."""
    if minutes <= 0:
        return "no time at all"
    seconds = minutes * 60
    if seconds < 60:
        s = max(1, round(seconds))
        return f"about {s} second" + ("s" if s != 1 else "")
    if minutes < 120:
        m = round(minutes, 1)
        return f"about {m:g} minute" + ("s" if m != 1 else "")
    hours, rem = divmod(int(round(minutes)), 60)
    out = f"about {hours} hour" + ("s" if hours != 1 else "")
    if rem:
        out += f" {rem} minute" + ("s" if rem != 1 else "")
    return out


def _format_stats(s: dict, source_name) -> str:
    head = (f"Statistics for {source_name}:"
            if source_name else "Text statistics:")
    lines = [
        head,
        f"- Characters: {s['characters']} "
        f"({s['characters_no_spaces']} without spaces)",
        f"- Words: {s['words']}",
        f"- Sentences: {s['sentences']} (approximate — counted "
        "from ending punctuation)",
        f"- Paragraphs: {s['paragraphs']}",
        f"- Lines: {s['lines']}",
        f"- Average word length: "
        f"{s['average_word_length']:.1f} characters",
        f"- Reading time: {_duration(s['reading_minutes'])} "
        "(estimate at 200 words per minute)",
        f"- Speaking time: {_duration(s['speaking_minutes'])} "
        "(estimate at 130 words per minute)",
    ]
    if s["top_words"]:
        lines.append(
            "- Most frequent words: " + ", ".join(
                f"{w} ×{c}" for w, c in s["top_words"]))
    else:
        lines.append("- Most frequent words: none — no countable "
                     "words stood out")
    return "\n".join(lines)


def text_stats(text: str = "", path: str = "") -> str:
    """Counts and time estimates for a text or a text file."""
    content, source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    if not content.strip():
        return "Nothing to measure — the text is empty."
    return _with_note(_format_stats(_stats(content), source_name),
                      note)


# ---------------------------------------------------------------------------
# CASE CONVERT — eight modes; word-based modes share one splitter
# ---------------------------------------------------------------------------

_CASE_MODES = ("upper", "lower", "title", "sentence", "camel",
               "pascal", "snake", "kebab")
_TITLE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")
_SENTENCE_START_RE = re.compile(r"(^\s*|[.!?]\s+)([a-z])")


def _split_words(text: str) -> list:
    """Split into words on non-alphanumerics and case boundaries.

    'helloWorld' -> ['hello', 'World']; 'HTTPServer' ->
    ['HTTP', 'Server']; 'user-first_name' -> ['user', 'first',
    'name'].
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def _sentence_case(text: str) -> str:
    lowered = text.lower()
    return _SENTENCE_START_RE.sub(
        lambda m: m.group(1) + m.group(2).upper(), lowered)


def _convert_case(text: str, mode: str) -> str:
    """Pure core of case_convert (mode already validated)."""
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    if mode == "title":
        return _TITLE_TOKEN_RE.sub(
            lambda m: m.group(0)[:1].upper()
            + m.group(0)[1:].lower(), text)
    if mode == "sentence":
        return _sentence_case(text)
    words = _split_words(text)
    if not words:
        return ""
    capped = [w[:1].upper() + w[1:].lower() for w in words]
    if mode == "camel":
        return words[0].lower() + "".join(capped[1:])
    if mode == "pascal":
        return "".join(capped)
    if mode == "snake":
        return "_".join(w.lower() for w in words)
    return "-".join(w.lower() for w in words)  # kebab


def case_convert(text: str = "", path: str = "",
                 mode: str = "upper") -> str:
    """Convert text between upper/lower/title/sentence/camel/
    Pascal/snake/kebab case. A file given by path is read but
    never modified — the converted text is returned."""
    mode = (mode or "upper").strip().lower()
    if mode not in _CASE_MODES:
        return ("Case modes are: " + ", ".join(_CASE_MODES) + ".")
    content, source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    if not content.strip():
        return "The text is empty — nothing to convert."
    converted = _convert_case(content, mode)
    if not converted:
        return ("There's nothing convertible in that — no "
                "letters or digits to reshape.")
    if len(converted) > _MAX_RETURN_CHARS:
        converted = (
            converted[:_MAX_RETURN_CHARS]
            + f"\n[truncated: showing {_MAX_RETURN_CHARS:,} of "
              f"{len(converted):,} characters]")
    if source_name:
        converted = (
            f"(Converted the contents of {source_name} — the "
            "file itself was not changed. To edit a file in "
            "place, that's replace_in_file's job.)\n" + converted)
    return _with_note(converted, note)


# ---------------------------------------------------------------------------
# EXTRACT FROM TEXT — emails, URLs, phone numbers (conservative)
# ---------------------------------------------------------------------------

_MAX_PER_KIND = 50

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'()\[\]{}]+")
_URL_TRAILING = ".,;:!?"
# Phones: precision over recall, three shapes —
#  1. international: a leading + and 7–15 digits with separators
#  2. grouped local: (415) 555-0132 / 415-555-0132 / 415.555.0132
#  3. a bare run of exactly 10 digits (a typical local number)
# A long bare digit run (an order id, a serial) matches NONE of
# these on purpose — it doesn't look like a phone number.
_PHONE_RES = (
    re.compile(r"\+\d[\d\s().\-]{6,17}\d"),
    re.compile(r"(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3,4}[\s.\-]\d{4}"),
    re.compile(r"\b\d{10}\b"),
)


def _extract(text: str) -> dict:
    """Pure core of extract_from_text: full deduplicated lists
    per kind, in first-seen order. Emails and URLs are masked out
    before the phone pass so digits inside them can't pose as
    phone numbers."""
    chars = list(text)

    def blank(spans):
        for start, end in spans:
            for i in range(start, end):
                chars[i] = " "

    emails = [m.group(0) for m in _EMAIL_RE.finditer(text)]
    blank([m.span() for m in _EMAIL_RE.finditer(text)])
    masked = "".join(chars)

    urls, url_spans = [], []
    for m in _URL_RE.finditer(masked):
        value = m.group(0).rstrip(_URL_TRAILING + ")]}")
        if value in ("http://", "https://", "www."):
            continue
        urls.append(value)
        url_spans.append(m.span())
    blank(url_spans)
    masked = "".join(chars)

    candidates = []
    for rx in _PHONE_RES:
        for m in rx.finditer(masked):
            candidates.append((m.start(), m.end(), m.group(0)))
    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))
    taken, phones = [], []
    for start, end, value in candidates:
        digits = sum(ch.isdigit() for ch in value)
        if not 7 <= digits <= 15:
            continue
        if any(start < t_end and t_start < end
               for t_start, t_end in taken):
            continue  # overlaps an earlier (or longer) match
        taken.append((start, end))
        phones.append(value)

    def dedup(items):
        return list(dict.fromkeys(items))

    return {"emails": dedup(emails), "urls": dedup(urls),
            "phones": dedup(phones)}


_EXTRACT_LABELS = {"emails": "Email addresses", "urls": "URLs",
                   "phones": "Phone numbers"}
_EXTRACT_NOUNS = {"emails": "email addresses", "urls": "URLs",
                  "phones": "phone numbers",
                  "all": "email addresses, URLs or phone numbers"}


def extract_from_text(text: str = "", path: str = "",
                      kind: str = "all") -> str:
    """Pull email addresses, URLs and/or phone numbers out of a
    text or a text file, deduplicated, capped at 50 per kind."""
    kind = (kind or "all").strip().lower()
    if kind not in ("all", "emails", "urls", "phones"):
        return ("I can extract 'emails', 'urls', 'phones', or "
                "'all' of them.")
    content, _source_name, note, error = _resolve_text(text, path)
    if error:
        return error
    if not content.strip():
        return "The text is empty — nothing to extract from."
    found = _extract(content)
    kinds = (("emails", "urls", "phones") if kind == "all"
             else (kind,))
    if not any(found[k] for k in kinds):
        return _with_note(
            f"I didn't find any {_EXTRACT_NOUNS[kind]} in that.",
            note)
    sections = []
    for k in kinds:
        items = found[k]
        label = _EXTRACT_LABELS[k]
        if not items:
            sections.append(f"{label}: none found")
            continue
        shown = items[:_MAX_PER_KIND]
        if len(items) > _MAX_PER_KIND:
            header = (f"{label} ({len(items)} found, showing "
                      f"first {_MAX_PER_KIND}):")
        else:
            header = f"{label} ({len(items)} found):"
        sections.append(header + "\n" + "\n".join(
            f"- {item}" for item in shown))
    return _with_note("\n".join(sections), note)


# ---------------------------------------------------------------------------
# REPLACE IN FILE — the pack's one write: counted, strict, atomic
# ---------------------------------------------------------------------------

_FIND_PREVIEW_CHARS = 40


def _occurrences(n: int) -> str:
    return f"{n} occurrence" + ("s" if n != 1 else "")


def replace_in_file(path: str, find: str, replace: str,
                    max_replacements: int = 0) -> str:
    """Literal, case-sensitive find-and-replace inside a file.

    No regex (deliberate — what you type is exactly what gets
    matched). The file must decode as strict UTF-8; anything else
    is refused untouched, because a loose decode rewritten to disk
    would corrupt it. Occurrences are counted BEFORE anything is
    written, and the write goes through a temp file + os.replace
    in the same folder. The bytes are handled end-to-end (never
    text mode), so the file's newline style — CRLF or LF — is
    preserved exactly; nothing is translated.
    """
    find = "" if find is None else str(find)
    replace = "" if replace is None else str(replace)
    if not find:
        return ("The text to find can't be empty — an empty "
                "search string would match everywhere.")
    try:
        cap = int(max_replacements)
    except (TypeError, ValueError):
        return (f"'{max_replacements}' isn't a whole number — "
                "max_replacements is 0 (replace all) or a "
                "positive count.")
    if cap < 0:
        return ("max_replacements can't be negative — use 0 to "
                "replace every occurrence.")
    raw_path = "" if path is None else str(path).strip()
    if not raw_path:
        return "Tell me which file to edit — path is required."
    p = Path(raw_path).expanduser()
    if not p.exists():
        return f"I couldn't find a file at '{raw_path}'."
    if not p.is_file():
        return f"'{raw_path}' is a folder, not a file."
    try:
        raw = p.read_bytes()
    except OSError as e:
        return f"Couldn't read '{p.name}': {e}"
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return (f"'{p.name}' isn't readable as UTF-8 text — "
                "it's probably a binary file. I won't rewrite "
                "it, because decoding it loosely could corrupt "
                "it. Nothing was changed.")
    count = content.count(find)
    shown = find[:_FIND_PREVIEW_CHARS] + (
        "…" if len(find) > _FIND_PREVIEW_CHARS else "")
    if count == 0:
        return (f"'{shown}' doesn't appear in {p.name} — "
                "nothing was changed.")
    done = count if cap == 0 else min(count, cap)
    new_bytes = content.replace(find, replace, done).encode("utf-8")
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(new_bytes)
            os.replace(tmp_name, p)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as e:
        return (f"Couldn't write '{p.name}': {e}. The original "
                "file is unchanged.")
    result = (f"Replaced {done} of {_occurrences(count)} of "
              f"'{shown}' in {p.name} "
              f"({len(raw)} → {len(new_bytes)} bytes).")
    if done < count:
        result += (f" Stopped at the max_replacements cap of "
                   f"{cap} — {count - done} more remain.")
    # R1a: verify the atomic rewrite by re-reading the file —
    # os.replace returning quietly is not proof the new bytes
    # landed. The result is an Outcome; refusal returns above
    # stay plain strings.
    from . import tool_outcome as _outcome  # R1a: seam wiring

    ok, evidence = _outcome.file_written(p, new_bytes)
    if ok:
        return _outcome.verified(result, evidence)
    return _outcome.failed(
        f"The replacement in {p.name} could not be confirmed: "
        f"{evidence}.", evidence)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Text pack tool on a ToolRegistry-like object."""
    registry.register(
        name="text_stats",
        description="Measure a text: characters (and without spaces), words, sentences (approximate — counted from ending punctuation), paragraphs, lines, average word length, estimated reading time (200 words/minute) and speaking time (130 words/minute), and the 5 most frequent words (common stopwords excluded, ties alphabetical). Give either text or the path to a text file (read as UTF-8, files over 2 MB refused) — exactly one. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to measure (give this OR path)"},
                "path": {"type": "string", "description": "Path to a text file to measure (give this OR text)"},
            },
            "required": [],
        },
        handler=text_stats,
    )
    registry.register(
        name="case_convert",
        description="Convert text to another case: upper, lower, title, sentence, camel, pascal, snake or kebab. The word-based modes split on punctuation, spaces and case boundaries (helloWorld becomes hello_world in snake). Give either text or the path to a text file — a file is only READ, never modified; the converted text is returned (capped at 4000 characters, truncation disclosed). Editing a file in place is replace_in_file's job. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to convert (give this OR path)"},
                "path": {"type": "string", "description": "Path to a text file to convert — read only, never modified (give this OR text)"},
                "mode": {"type": "string", "enum": ["upper", "lower", "title", "sentence", "camel", "pascal", "snake", "kebab"], "description": "Target case (default upper)"},
            },
            "required": [],
        },
        handler=case_convert,
    )
    registry.register(
        name="extract_from_text",
        description="Extract email addresses, URLs and/or phone numbers from a text or a text file and list them deduplicated, in first-seen order, capped at 50 per kind (true counts disclosed when capped). Phone matching is deliberately conservative — international (+…) numbers, grouped local numbers like (415) 555-0132, and bare 10-digit numbers; long bare digit runs (order ids, serials) are NOT reported as phones. Give either text or path — exactly one. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to extract from (give this OR path)"},
                "path": {"type": "string", "description": "Path to a text file to extract from (give this OR text)"},
                "kind": {"type": "string", "enum": ["all", "emails", "urls", "phones"], "description": "What to extract (default all)"},
            },
            "required": [],
        },
        handler=extract_from_text,
    )
    registry.register(
        name="replace_in_file",
        description="Find and replace a literal string inside a file, editing it in place. Case-sensitive, plain text only — NOT a regular expression, deliberately: what you type is exactly what gets matched. Occurrences are counted first and the result reports how many of how many were replaced; max_replacements 0 (default) means all. The file must be valid UTF-8 — binary or undecodable files are refused untouched rather than risk corrupting them. The write is atomic (temp file + rename) and preserves the file's newline style (CRLF/LF) exactly.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit"},
                "find": {"type": "string", "description": "The literal text to find (case-sensitive, not a regex)"},
                "replace": {"type": "string", "description": "The replacement text (may be empty to delete the matches)"},
                "max_replacements": {"type": "integer", "description": "Maximum replacements to make; 0 (default) replaces all"},
            },
            "required": ["path", "find", "replace"],
        },
        handler=replace_in_file,
    )
