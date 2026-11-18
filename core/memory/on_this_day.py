"""Wave 9 (v5.64) — On This Day resurfacing (surface model only).

Reads the Wave 0 milestone log (``data/milestones.jsonl``) and
answers one question: what firsts happened on this calendar
day in earlier years? The log is EVENT-ONLY — its format is
taken from ``core/milestones.py``, not invented here:

    {"ts": "<local ISO seconds>", "event": <name>, ...fields}

Only ``ts`` and ``event`` are relied on. ``key`` is used when
present (it discriminates repeats, e.g. one line per version
for ``version_first_launch``); every other field is carried
through opaquely for display and never interpreted.

SURFACE MODEL ONLY: this module computes and formats
resurfacings. No GUI wiring reads it yet — wiring is the
closing step. Nothing here writes, edits, or backfills the log:
a milestone not written on the day it happened can never be
recovered, and inventing one would falsify the record. There
is deliberately NO write function in this module at all.

Readers skip malformed lines instead of failing, matching the
Wave 0 reader. Stdlib only. No telemetry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

MODULE_VERSION = "5.64.0"

__all__ = [
    "MODULE_VERSION",
    "Resurfacing",
    "read_events_from_path",
    "read_events",
    "on_this_day",
    "format_resurfacing",
    "summarise_day",
]

# Human renderings for the known Wave 0 event names. Unknown
# events are NOT dropped: they render from their raw name, so a
# future event type surfaces instead of vanishing.
_EVENT_LABELS = {
    "first_launch": "first launch",
    "version_first_launch": "first launch of a version",
    "first_live_connect": "first Live connection",
    "first_completed_task": "first completed task",
}


@dataclass(frozen=True)
class Resurfacing:
    """One milestone that happened on this month-day, years ago."""

    event: str
    happened: date
    years_ago: int
    key: str | None = None
    fields: dict | None = None

    @property
    def label(self) -> str:
        base = _EVENT_LABELS.get(self.event, self.event.replace("_", " "))
        if self.key:
            return f"{base} ({self.key})"
        return base


def read_events_from_path(path: str | Path) -> list[dict]:
    """Milestone events from a JSONL file. Missing file reads as
    no milestones; malformed lines are skipped, never fatal."""
    events: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — no file yet is a normal state
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:  # noqa: BLE001 — skip the malformed line
            continue
        if isinstance(item, dict) and item.get("event"):
            events.append(item)
    return events


def read_events() -> list[dict]:
    """Events from the live log, via the Wave 0 reader (same
    skip-malformed semantics). Kept import-local so importing
    this module never pulls in app config at module scope."""
    from .. import milestones as _milestones

    return _milestones.read_events()


def _parse_ts(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def on_this_day(events: list[dict],
                today: date | None = None) -> list[Resurfacing]:
    """Milestones whose month-day matches ``today``, in earlier
    years only — today's own firsts are not 'on this day' yet.
    Oldest first. Same (event, key, year) lines dedupe to one:
    the log is append-only and ``record_once`` guards repeats,
    but a hand-edited log must not double-surface."""
    today = today or date.today()
    seen: set[tuple[str, str | None, int]] = set()
    out: list[Resurfacing] = []
    for item in events:
        happened = _parse_ts(item.get("ts"))
        if happened is None:
            continue
        if happened.month != today.month or happened.day != today.day:
            continue
        if happened.year >= today.year:
            continue
        key = item.get("key")
        key = str(key) if key is not None else None
        dedupe = (str(item["event"]), key, happened.year)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        extra = {k: v for k, v in item.items()
                 if k not in ("ts", "event", "key")}
        out.append(Resurfacing(event=str(item["event"]),
                               happened=happened,
                               years_ago=today.year - happened.year,
                               key=key,
                               fields=extra or None))
    out.sort(key=lambda r: (r.happened, r.event, r.key or ""))
    return out


def format_resurfacing(item: Resurfacing) -> str:
    """One plain-language line. '1 year ago' is singular; the
    original date is always shown so the claim is checkable."""
    years = "1 year ago" if item.years_ago == 1 else \
        f"{item.years_ago} years ago"
    return (f"{years} — {item.label} "
            f"({item.happened.isoformat()}).")


def summarise_day(events: list[dict],
                  today: date | None = None) -> list[str]:
    """Formatted lines for today, or [] when nothing happened on
    this day in an earlier year. Empty is a normal answer, not
    an error — most days have no firsts."""
    return [format_resurfacing(item) for item in on_this_day(events, today)]
