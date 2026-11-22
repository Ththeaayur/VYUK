"""
Milestone log (v5.54, Wave 0 — WF-55's recording dependency).

A small append-only log of firsts in data/milestones.jsonl: the
first launch ever, the first launch under each new version, the
first successful Live connect, the first task a brain completed.
Wave 9's On This Day resurfacing (WF-55) reads this file; the
resurfacing design is deferred, the RECORDING is not — a
milestone not written down on the day it happened can never be
backfilled, which is why this stub ships in the first build
that touches persistence (plan §5.0).

Shape rules, all deliberate:
  * A SEPARATE store, never the memory DB — INV-8 freezes that
    schema; this file is additive and lives beside it.
  * Append-only JSON Lines, one object per event:
    {"ts": <local ISO seconds>, "event": <name>, ...fields}.
    Readers skip malformed lines instead of failing.
  * Created at RUNTIME only — data/ still ships empty (INV-14).
  * Every function never raises: a milestone is a nicety, and
    a broken log must not cost a launch, a connect, or a tool
    call. Callers still wrap the call (house style).
  * record_once's "once" is judged against the file itself,
    with an optional discriminator key (version_first_launch
    is once PER VERSION; the others are once ever). The scan
    is O(file) and the file only grows by firsts, so it stays
    tiny by construction.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .config import DATA_DIR

MILESTONES_FILE = "milestones.jsonl"

_lock = threading.Lock()
_path_override: Path | None = None  # tests point this at a temp file


def _path() -> Path:
    if _path_override is not None:
        return _path_override
    return DATA_DIR / MILESTONES_FILE


def read_events() -> list[dict]:
    """Every recorded milestone, oldest first. A missing file
    reads as no milestones; malformed lines are skipped, never
    fatal. Never raises."""
    try:
        text = _path().read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — no file yet is a normal state
        return []
    events: list[dict] = []
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


def record(event: str, **fields) -> bool:
    """Append one milestone line. Returns True when the line
    was written. Never raises."""
    if not event:
        return False
    try:
        line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "event": str(event)}
        for key, value in fields.items():
            try:
                json.dumps(value)
                line[key] = value
            except (TypeError, ValueError):
                line[key] = str(value)
        path = _path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        return True
    except Exception:  # noqa: BLE001 — a milestone never breaks its caller
        return False


def record_once(event: str, key: str | None = None, **fields) -> bool:
    """Append `event` only when no matching line exists yet —
    the firsts. `key` discriminates repeats (a version, say):
    with a key, the event is once per key value; the key is
    stored on the line as "key". Returns True when a line was
    written. Never raises.

    Loophole batch 5 (#5): private mode suppresses the
    milestone — a private turn that is the first success ever
    does not record "first_task_completed" (the next non-private
    success records it instead). Returns False: no line written.
    """
    try:
        from .private_mode import private_mode_on
    except Exception:  # noqa: BLE001 — unreadable: not private
        private_mode_on = None
    if private_mode_on is not None and private_mode_on():
        return False
    try:
        for item in read_events():
            if item.get("event") != event:
                continue
            if key is None or str(item.get("key")) == str(key):
                return False
        if key is not None:
            fields["key"] = key
        return record(event, **fields)
    except Exception:  # noqa: BLE001
        return False


def record_startup(version: str) -> None:
    """The launch milestones, called once per app start from
    main(): first_launch the first time the log is written to
    at all (the install's first-run date — WF-55's anchor), and
    version_first_launch the first time each version runs.
    Never raises."""
    try:
        if not read_events():
            record("first_launch", version=str(version))
        record_once("version_first_launch", key=str(version),
                    version=str(version))
    except Exception:  # noqa: BLE001 — startup survives a broken log
        pass
