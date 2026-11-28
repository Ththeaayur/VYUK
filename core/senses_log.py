"""
Environment timeline for VYUK (v5.20, NOSE) — the senses log.

A tiny append-only JSONL store: one line per noticed event,
{"ts": <iso>, "kind": <watcher/tool kind>, "message": <sentence>},
written to senses_timeline.jsonl in the app data dir (the same
DATA_DIR core.voice.local_assets resolves from core.config). The window
appends watcher events here as they fire; the Senses dashboard
reads them back newest-first as the environment timeline.

N7 (v5.45): a line MAY also carry structured measurement fields —
"value" (a number) and "unit" (its unit, e.g. "%" or "min") —
alongside the human prose in "message". The fields are optional
and additive: lines written before N7 (no fields) and lines
written after read back through exactly the same path, and
consumers (the Senses trends, the hindsight tools) use the
fields when present and fall back to parsing the prose when not.
Writers pass the fields explicitly to append_event, or — for
watcher notices, whose (kind, message) travels through the GUI
before it is appended — register a value provider (see
register_value_provider) that append_event consults.

Deliberately boring: no database, no locking games (a lost line on
a crash is acceptable for a timeline), a hard 500-line cap kept by
rewrite-trim, and NOTHING in here ever raises — a broken timeline
must never break noticing, speaking or starting up. Corrupt lines
(hand-edits, partial writes) are skipped on read.
"""

import json
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

_FILE_NAME = "senses_timeline.jsonl"
_MAX_LINES = 500

# N7 value providers: callables (kind, message) -> (value, unit)
# | None, consulted by append_event when the caller passed no
# explicit value. Watchers register one so a notice's structured
# measurement survives the trip through the GUI's append site,
# which only ever passes (kind, message). Providers are tried in
# registration order; a provider that raises is skipped — the
# timeline must never break on enrichment.
_value_providers: list = []


def register_value_provider(fn) -> None:
    """Register a (kind, message) -> (value, unit) | None callable
    that append_event consults when no explicit value is passed.
    Never raises; a non-callable is ignored."""
    try:
        if callable(fn) and fn not in _value_providers:
            _value_providers.append(fn)
    except Exception:  # noqa: BLE001 — enrichment must never break
        pass


def _provided_value(kind: str, message: str):
    """The first provider's (value, unit) for this event, or None."""
    for fn in list(_value_providers):
        try:
            found = fn(kind, message)
        except Exception:  # noqa: BLE001
            continue
        if found is not None:
            return found
    return None


def event_value(event) -> float | None:
    """An event dict's structured measurement (N7): the "value"
    field as a float when the line carries a usable number, else
    None (old prose-only lines land here — callers fall back to
    parsing the message). Never raises."""
    try:
        if not isinstance(event, dict):
            return None
        raw = event.get("value")
        if isinstance(raw, bool) or raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _log_path() -> Path:
    return Path(DATA_DIR) / _FILE_NAME


def append_event(kind: str, message: str, ts=None,
                 value=None, unit=None) -> None:
    """Append one event line; trim the file to the cap. Never raises.

    `ts` may be a datetime (stored as ISO seconds), a pre-formatted
    string, or None for "now".

    N7: `value`/`unit` are the event's structured measurement
    (e.g. value=42, unit="%"). They are stored only when `value`
    converts to a float — a non-numeric value is dropped rather
    than written as junk. When `value` is None, registered value
    providers are consulted for this (kind, message); when they
    have nothing either, the line is written exactly as before
    N7 (three fields), so old and new lines stay one format.
    """
    try:
        if ts is None:
            stamp = datetime.now().isoformat(timespec="seconds")
        elif isinstance(ts, datetime):
            stamp = ts.isoformat(timespec="seconds")
        else:
            stamp = str(ts)
        record = {"ts": stamp, "kind": str(kind),
                  "message": str(message)}
        if value is None:
            provided = _provided_value(record["kind"], record["message"])
            if provided is not None:
                try:
                    value, unit = provided[0], provided[1]
                except (TypeError, IndexError):
                    value, unit = None, None
        if value is not None:
            try:
                record["value"] = float(value)
            except (TypeError, ValueError):
                pass  # a non-numeric measurement is not written
            else:
                if unit is not None:
                    record["unit"] = str(unit)
        line = json.dumps(record)
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        # Cap: rewrite keeping only the newest _MAX_LINES lines.
        # The file is small (<= ~501 lines) so this stays cheap.
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > _MAX_LINES:
                path.write_text(
                    "\n".join(lines[-_MAX_LINES:]) + "\n",
                    encoding="utf-8")
        except OSError:
            pass  # the append already landed; trimming is best-effort
    except Exception:  # noqa: BLE001 — the timeline must never break callers
        pass


def timeline_line_count() -> int:
    """The timeline file's line count (non-empty lines), capped at
    _MAX_LINES — the "M" in the Senses page's "N of M shown" (UI
    Phase 4, D-P4-15). A missing file reads as 0. Never raises:
    an unreadable file reads as 0, never a crash."""
    try:
        path = _log_path()
        if not path.exists():
            return 0
        count = 0
        for line in path.read_text(
                encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                count += 1
        return min(count, _MAX_LINES)
    except Exception:  # noqa: BLE001 — the count says 0
        return 0


def read_events(limit: int = 100, kind: str | None = None,
                since_hours: float | None = None) -> list[dict]:
    """Up to `limit` events, newest first. Corrupt lines are
    skipped; a missing file reads as an empty timeline. Never raises.

    S14 filters: `kind` keeps only that event kind;
    `since_hours` keeps only events whose timestamp parses and
    is within the last N hours (an unparseable timestamp cannot
    prove recency, so it is excluded while the filter is on)."""
    try:
        path = _log_path()
        if not path.exists():
            return []
        events: list[dict] = []
        for raw in path.read_text(
                encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue  # corrupt line — tolerate and move on
            if isinstance(obj, dict):
                events.append(obj)
        if kind is not None:
            events = [e for e in events if e.get("kind") == kind]
        if since_hours is not None:
            from datetime import timedelta

            cutoff = datetime.now() - timedelta(hours=float(since_hours))
            fresh = []
            for e in events:
                try:
                    stamp = datetime.fromisoformat(str(e.get("ts") or ""))
                except ValueError:
                    continue
                if stamp >= cutoff:
                    fresh.append(e)
            events = fresh
        events.reverse()
        try:
            count = max(0, int(limit))
        except (TypeError, ValueError):
            count = 100
        return events[:count]
    except Exception:  # noqa: BLE001
        return []
