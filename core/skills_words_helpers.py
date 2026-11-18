"""Shared input-resolution helpers for the Words pack.

Moved verbatim from core/skills_words.py (shared section); used by
the measure group. Dependency order: helpers <- measure/make <- facade.
"""

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared input resolution — exactly one of text / path, capped openly
# ---------------------------------------------------------------------------

_MAX_CHARS = 200_000          # the working cap for every text tool here
_READ_BYTES = 1_000_001       # > 4 bytes/char x the cap: the char cap binds first
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")


def _resolve_text(text, path):
    """Resolve the text/path input convention.

    -> (content, source_name, note, error): on success error is
    None and content is capped at _MAX_CHARS characters (the cap
    and any UTF-8 replacement are disclosed in note). On failure
    content is None and error is the honest line.
    """
    raw_text = "" if text is None else (
        text if isinstance(text, str) else str(text))
    raw_path = "" if path is None else str(path).strip()
    if raw_text and raw_path:
        return None, None, None, (
            "You gave me both text and a file path — I need "
            "exactly one source. Drop one of them and ask again.")
    if not raw_text and not raw_path:
        return None, None, None, (
            "There's nothing to work on — give me some text, or "
            "the path to a text file.")
    notes = []
    source_name = None
    content = raw_text
    if raw_path:
        p = Path(raw_path).expanduser()
        if not p.exists():
            return None, None, None, (
                f"I couldn't find a file at '{raw_path}'.")
        if not p.is_file():
            return None, None, None, (
                f"'{raw_path}' is a folder, not a file — point "
                "me at a text file.")
        try:
            with p.open("rb") as fh:
                raw = fh.read(_READ_BYTES)
        except OSError as e:
            return None, None, None, f"Couldn't read '{p.name}': {e}"
        content = raw.decode("utf-8", errors="replace")
        source_name = p.name
        replaced = content.count("\ufffd")
        if replaced:
            notes.append(
                f"Note: '{p.name}' wasn't clean UTF-8 — "
                f"{replaced} character"
                f"{'s' if replaced != 1 else ''} couldn't be "
                "decoded and were replaced with �, so those spots "
                "are approximate.")
    if len(content) > _MAX_CHARS:
        content = content[:_MAX_CHARS]
        notes.append(
            f"Note: I worked on the first {_MAX_CHARS:,} "
            "characters only — the cap for these word tools — so "
            "the result covers that part, not the whole text.")
    return content, source_name, "\n".join(notes) or None, None


def _with_note(body: str, note) -> str:
    """Append any read/cap caveats (from _resolve_text)."""
    return body if not note else body + "\n" + note


def _words(text: str) -> list:
    return _WORD_RE.findall(text)


def _sentences(text: str) -> list:
    """Split into sentences on ending punctuation; a text with
    no ending punctuation is one sentence. Same splitter style
    as skills_text's text_stats."""
    parts = re.split(r"[.!?]+(?=\s|$)", text.strip())
    return [p.strip() for p in parts if _WORD_RE.search(p)]


def _quote_snippet(sentence: str, cap: int = 120) -> str:
    """A sentence quoted for display, truncated at `cap` chars
    with the ellipsis inside the budget."""
    cleaned = " ".join(sentence.split())
    if len(cleaned) <= cap:
        return cleaned
    return cleaned[:cap - 1] + "…"
