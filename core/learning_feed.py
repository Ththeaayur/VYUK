"""Wave 8 (v5.63) — PF-28 learning transparency feed.

What she learned, from what, when — in user-readable form.

SHAPE AND ETHICS RULES, enforced by construction and tested
in test_v563_teach_secondopinion_feed.py:

* LEARNING EVENTS ONLY. A record is one learning event:
  ``what`` she learned, ``source`` it came from, and a
  timestamp. The feed stores no conversation log, no lifelog,
  and no behavioural trace.
* APPEND-ONLY. Records are added, never edited or deleted:
  this module exposes no update and no delete operation.
  Each record is its own file, so an old record cannot be
  silently rewritten by a later write.
* WAVE 6 FILE-STORE SHAPE. Records live as Markdown +
  frontmatter files (the Wave 6 ``memory_files`` shape:
  flat ``key: value`` frontmatter, body = the value text),
  under ``data/learning_feed/`` by default — beside, never
  inside, the frozen Wave 6 database. The frozen DB is never
  opened by this module at all (read-only rollback material
  stays untouched), and no new database is created. The
  directory is overridable (``VYUK_LEARNING_FEED_DIR`` env var
  or an explicit constructor argument) so tests — and any
  caller — can point the feed elsewhere; production callers
  pass the tree's ``data/`` root via the coordinator seam.
* WAVE 6 ETHICS RULE, CARRIED OVER. Every record carries a
  ``kind`` (the Wave 6 kinds: fact, opinion, inferred,
  person, promise). Opinions are LABELLED as opinions in
  every rendering, and ``fact_prompt_lines()`` — the only
  surface meant for fact prompts — NEVER includes opinions.
  Inferred records expire after 90 days (the Wave 6 TTL) and
  drop out of fact prompts once expired; they stay listed,
  marked expired, rather than being deleted or silently kept.
* No new dependencies (stdlib only), no weights, no
  telemetry, no lifelog, no third-party material — so no
  third-party licence is involved in this module at all.

Versioned string introduced by this module (pre-pinned for
the 5.63.0 assembly; core/version.py is NOT touched here and
still reads 5.62.0 until the coordinator assembles the wave):
``VERSION = "5.63.0"``.
"""

from __future__ import annotations

import os
from . import config_spec
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

VERSION = "5.63.0"

__all__ = [
    "VERSION",
    "KINDS",
    "INFERRED_TTL_DAYS",
    "FEED_DIRNAME",
    "LearningRecord",
    "LearningFeed",
    "default_dir",
    "render",
    "is_expired",
]

KINDS = ("fact", "opinion", "inferred", "person", "promise")
INFERRED_TTL_DAYS = 90
FEED_DIRNAME = "learning_feed"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def default_dir():
    """The feed directory: env override, else
    ``<tree>/data/learning_feed`` (this file lives in
    ``<tree>/core/``, so its parent's parent is the tree root).
    Reading never creates it; appending creates it."""
    override = config_spec.explicit("VYUK_LEARNING_FEED_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "data" / FEED_DIRNAME


def _normalize_ts(value):
    """Normalise a timestamp to 'YYYY-MM-DD HH:MM:SS'.

    Accepts a datetime, or a string in one of the formats above
    (a date alone means midnight). Unparseable values raise
    ValueError — a learning record with a garbled timestamp is
    refused, not stored with a guessed time.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "").strip().replace("Z", "")
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"unparseable timestamp: {value!r}")


def _slug(text):
    slug = _SLUG_RE.sub("_", str(text or "").lower()).strip("_")
    return (slug or "item")[:48]


def _parse_frontmatter(text):
    """Parse this module's flat frontmatter. Returns
    ``(fields_dict, body)``. A file without a frontmatter block
    is not a feed record and raises ValueError."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("record file has no frontmatter block")
    fields = {}
    index = 1
    while index < len(lines) and lines[index].strip() != "---":
        line = lines[index]
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key.strip()] = value.strip()
        elif line.strip():
            raise ValueError(f"malformed frontmatter line: {line!r}")
        index += 1
    if index >= len(lines):
        raise ValueError("record file has an unterminated frontmatter block")
    body = "\n".join(lines[index + 1:])
    return fields, body


@dataclass(frozen=True)
class LearningRecord:
    """One learning event. ``what`` is the body text; ``source``
    and ``kind`` ride in frontmatter. ``expired`` is derived
    (inferred records older than 90 days), never stored."""

    what: str
    source: str
    timestamp: str
    kind: str = "fact"
    filename: str = ""

    def __post_init__(self):
        if not isinstance(self.what, str) or not self.what.strip():
            raise ValueError("a learning record needs non-empty 'what' text")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("a learning record needs a non-empty source")
        if "\n" in self.source or "\r" in self.source:
            raise ValueError("a learning record's source must be a single line")
        if self.kind not in KINDS:
            raise ValueError(f"unknown learning record kind: {self.kind!r}")
        object.__setattr__(self, "timestamp", _normalize_ts(self.timestamp))

    @property
    def expired(self):
        return is_expired(self)

    def to_file_text(self):
        frontmatter = "\n".join((
            "---",
            f"kind: {self.kind}",
            f"source: {self.source.strip()}",
            f"timestamp: {self.timestamp}",
            "---",
            "",
        ))
        return frontmatter + self.what

    @classmethod
    def from_file_text(cls, text, filename=""):
        fields, body = _parse_frontmatter(text)
        return cls(
            what=body,
            source=fields.get("source", ""),
            timestamp=fields.get("timestamp", ""),
            kind=fields.get("kind", "fact"),
            filename=filename,
        )


def is_expired(record, now=None):
    """True only for inferred records older than 90 days.
    Facts, people, and promises do not expire; opinions do not
    expire either — they are labelled, not time-boxed, and they
    never reach fact prompts in the first place."""
    if record.kind != "inferred":
        return False
    try:
        created = datetime.strptime(record.timestamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    moment = now if isinstance(now, datetime) else datetime.now()
    return moment > created + timedelta(days=INFERRED_TTL_DAYS)


def render(record):
    """One plain-words line for a record. Opinions are always
    labelled as opinions, never rendered as settled facts;
    expired inferred records are marked expired."""
    try:
        day = datetime.strptime(
            record.timestamp, "%Y-%m-%d %H:%M:%S").strftime("%d %b %Y")
    except ValueError:
        day = record.timestamp
    what = " ".join(record.what.split())
    if record.kind == "opinion":
        return (
            f"On {day}, I noted an opinion — not a settled fact: "
            f"{what} (from {record.source})."
        )
    if record.kind == "inferred":
        tail = " (this inference has expired)" if record.expired else \
            " (an inference, not a confirmed fact)"
        return (
            f"On {day}, I inferred: {what} (from {record.source}){tail}."
        )
    if record.kind == "person":
        return f"On {day}, I learned about a person: {what} (from {record.source})."
    if record.kind == "promise":
        return f"On {day}, I recorded a promise: {what} (from {record.source})."
    return f"On {day}, I learned: {what} (from {record.source})."


class LearningFeed:
    """Append-only learning feed backed by Markdown+frontmatter
    files in one directory. No update, no delete, no database."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None \
            else default_dir()

    def append(self, what, source, kind="fact", timestamp=None):
        """Append one learning event and return its record.

        The timestamp defaults to now. The filename embeds the
        timestamp plus a counter, so two events in the same
        second never overwrite each other — appending can never
        rewrite an existing record.
        """
        stamp = _normalize_ts(timestamp) if timestamp is not None \
            else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record = LearningRecord(what=what, source=source,
                                timestamp=stamp, kind=kind)
        self.directory.mkdir(parents=True, exist_ok=True)
        base = stamp.replace("-", "").replace(" ", "_").replace(":", "")
        counter = 0
        while True:
            filename = f"{base}_{counter:03d}_{_slug(what)}.md"
            path = self.directory / filename
            if not path.exists():
                break
            counter += 1
        path.write_text(record.to_file_text(), encoding="utf-8")
        return LearningRecord(what=record.what, source=record.source,
                              timestamp=record.timestamp, kind=record.kind,
                              filename=filename)

    def list_records(self, kind=None, include_expired=True):
        """All records, oldest first. ``kind`` filters to one
        kind (the Wave 6 kind-filtered query shape); opinions
        are retrievable only through such an explicit filter or
        the unfiltered listing — never via fact prompts."""
        if not self.directory.is_dir():
            return []
        records = []
        for path in sorted(self.directory.glob("*.md")):
            try:
                record = LearningRecord.from_file_text(
                    path.read_text(encoding="utf-8"), filename=path.name)
            except ValueError:
                continue  # a non-record file in the dir is skipped, not fatal
            if kind is not None and record.kind != kind:
                continue
            if not include_expired and record.expired:
                continue
            records.append(record)
        records.sort(key=lambda r: (r.timestamp, r.filename))
        return records

    def render_all(self, kind=None):
        """Plain-words rendering of the listing (opinions still
        labelled as opinions)."""
        return [render(record) for record in self.list_records(kind=kind)]

    def fact_prompt_lines(self):
        """Lines safe for fact prompts: opinions NEVER included,
        expired inferred records excluded. This is the only
        feed surface a fact prompt may consume."""
        lines = []
        for record in self.list_records(include_expired=False):
            if record.kind == "opinion":
                continue
            lines.append(f"- {render(record)}")
        return lines
