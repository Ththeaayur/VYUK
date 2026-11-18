"""Wave 10 (v5.65) Leaf 2 — PF-6 in-app changelog (STAGED, unwired).

Data: one plain-language line per shipped version from 5.54
through 5.64.1, each taken from that version's ship-gate record in
``code-lab/polish_notes.md``. Nothing is invented for a version
whose record lacks detail: every version below has a record-backed
headline, so no entry is omitted and none is marked as a guess.
(If a future version's record is ever thin, the rule is: headline
only, or omit with a note in the record — never fabricate.)

Logic: given a last-seen-version record, ``pending_entries``
returns exactly the entries newer than it, in ship order, none
skipped. Recording a new last-seen version is explicit — the
caller persists the dict ``mark_seen`` returns; this module
writes no files and never updates anything silently.

Version comparison is numeric-tuple based (5.10 > 5.9), the same
rule as ``core/version.py``; version strings are never compared
lexically. Product VERSION is untouched (still 5.64.1); this
module carries no product-version constant of its own.
"""

from __future__ import annotations

# version -> one honest plain-language line, from the ship records.
ENTRIES: tuple[tuple[str, str], ...] = (
    ("5.54", "Field Truth: a microphone doctor, a typing fallback, "
             "sharper display scaling, and the move to the new Gemini model."),
    ("5.54.1", "Field-fix patch: small fixes only — no new features, "
               "no tools gained or lost."),
    ("5.55", "Spine: safer tool handling, with stricter confirmations "
             "before anything irreversible."),
    ("5.56", "Trust & Truth: an audit trail for what VYUK did, a kill "
             "switch, and trust cards in Settings."),
    ("5.57", "A premium redo of the Settings, Skills and Home screens — "
             "and the core design is now frozen."),
    ("5.57.1", "Field fix: the Doctor can run again, Settings gains a "
               "Back button, and YouTube Music is now the default music choice."),
    ("5.58", "Voice work: a proper testing harness for wake words, and "
             "more natural pauses in speech."),
    ("5.59", "Hands & Web: browser and desktop control pilots, staged "
             "for testing — not yet live."),
    ("5.60", "Senses: vision and capture pieces that honestly report "
             "when a backend is unavailable, instead of guessing."),
    ("5.61", "Memory you can read: your facts are now stored as plain "
             "Markdown files, alongside the existing database."),
    ("5.62", "World & Reach: open world feeds and reach features, "
             "staged behind their licence gates."),
    ("5.63", "Brain: checkpoints, reflection and teaching tools, "
             "staged and unwired — nothing you see changes yet."),
    ("5.64", "Premium Surface: a summon window, theme sync with Windows, "
             "and new widgets and icons."),
    ("5.64.1", "Wired: everything built across the last waves that could "
               "be wired is now live, plus the latest field fixes."),
)

VERSIONS: tuple[str, ...] = tuple(v for v, _ in ENTRIES)
LATEST_VERSION: str = ENTRIES[-1][0]


def parse_version(text: object) -> tuple[int, ...] | None:
    """Dotted-numeric version -> comparable tuple, or None if unparseable."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(".")
    if not parts or any(not p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    width = max(len(a), len(b))
    return (a + (0,) * (width - len(a)), b + (0,) * (width - len(b)))


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is strictly newer than ``current``."""
    cand, cur = parse_version(candidate), parse_version(current)
    if cand is None or cur is None:
        return False
    cand, cur = _pad(cand, cur)
    return cand > cur


def pending_entries(last_seen_version) -> list[dict]:
    """Entries newer than ``last_seen_version``, in ship order.

    ``None`` means first launch (nothing seen yet) -> every entry.
    An unparseable non-None value raises ValueError: silently
    treating a garbled record as "seen nothing" or "seen
    everything" would skip entries or spam them, and neither is
    acceptable for a changelog. A last-seen version newer than
    the latest entry yields an empty list (nothing is owed).
    """
    if last_seen_version is None:
        seen = None
    else:
        seen = parse_version(last_seen_version)
        if seen is None:
            raise ValueError(
                f"unparseable last-seen version: {last_seen_version!r}")
    out = []
    for version, text in ENTRIES:
        if seen is None or is_newer(version, str(last_seen_version)):
            out.append({"version": version, "text": text})
    return out


def mark_seen(record: dict | None = None) -> dict:
    """Build the record marking everything up to LATEST as seen.

    Returns a NEW dict; the caller persists it (e.g. beside the
    settings). Nothing is written here, so an update can never
    happen silently as a side effect of merely reading the list.
    """
    new = dict(record or {})
    new["last_seen_version"] = LATEST_VERSION
    return new


def last_seen_of(record: dict | None):
    """Extract the last-seen version from a record dict, or None."""
    if not isinstance(record, dict):
        return None
    value = record.get("last_seen_version")
    return value if isinstance(value, str) and value else None


def check_no_skip() -> dict:
    """Self-check: entries strictly increasing, no duplicates, none missing.

    Returns {"ok": bool, "versions": [...], "problems": [...]}.
    """
    problems = []
    seen: set[str] = set()
    prev = None
    for version, _text in ENTRIES:
        parsed = parse_version(version)
        if parsed is None:
            problems.append(f"{version}: unparseable")
            continue
        if version in seen:
            problems.append(f"{version}: duplicate")
        seen.add(version)
        if prev is not None and not _pad(parsed, prev)[0] > _pad(parsed, prev)[1]:
            problems.append(f"{version}: not after {prev}")
        prev = parsed
    return {"ok": not problems, "versions": list(VERSIONS),
            "problems": problems}
