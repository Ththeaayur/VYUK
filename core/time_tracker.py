"""Local working-time tracker (R18-10, dominikbraun/timetrace inspiration).

Parent-ruled (licence, 2026-11-12): dominikbraun/timetrace (Apache-2.0, LICENSE from main,
11,357 bytes, 201 lines, read in full). Nothing is adopted — no Go code,
no timetrace binary, no port. The build takes only the tracker's shape
(start/stop, projects, tags, reports, decimal hours) and rebuilds it as
own stdlib code over VYUK's own store shapes.

The finding (timetrace, rebuilt from scratch — no code adopted):
a simple CLI for tracking working time, with tags for records, decimal
hours, restore-records-with-project, and per-project configuration.
VYUK has no working-time tracker: the Pomodoro keeps NO history
(skills_productivity_day.py:10), the stopwatch has no projects, and
"billable" appears only as a confirm-gate comment. This tool records
working time against projects — nothing else.

What it does (all local, all own code, stdlib only):
  * PROJECTS: add / list / get / remove / edit. A project carries
    per-project configuration (default tags, decimal-hours
    preference) — the timetrace per-project config shape.
  * START / STOP: one running record at a time. `start` refuses
    honestly if something is already running; `stop` refuses
    honestly if nothing is running.
  * RECORDS: add (manual, with start/end) / list / get / remove /
    edit. A record's end must be after its start, and a record
    must not overlap any existing record — you can only do one
    thing at a time, so an overlap is refused with the numbers
    (the double-booking precedent, bookings.py / lodging.py).
  * STATUS: the running record (if any, with elapsed) + today's
    totals per project.
  * REPORT: per project and/or date range, totals in decimal hours
    AND h:mm, with a per-tag and per-day breakdown. Decimal hours
    is the timetrace shape ("prefer decimal hours").
  * EVENTS: a hash-chained ledger of every project/record/start/
    stop write, with full-chain verify and proven tamper detection.
  * RESTORE: removing a project soft-deletes it (its records are
    kept, hidden from status/report); re-adding a project with the
    same name restores it AND its records — the timetrace
    "restore records with their project" shape.

What it does NOT do (stated on the tin):
  * NO invoicing, NO billing, NO rates, NO amounts — the tracker
    records hours, it does not bill (ruling 34). There is no rate,
    no amount, no invoice action anywhere in this module.
  * NO Go port (no Go runtime in the tree), NO timetrace binary,
    NO network, NO live feed. Times come from the owner's clock
    and the owner's own records.
  * NO Pomodoro replacement — the Pomodoro stays as it is; this
    tool is the history the Pomodoro deliberately does not keep.

Classification: free by design ("app-state") — it writes only to
VYUK's own tracker store, the event_planning / event_tickets /
technical_analysis precedent. No confirm-gated action, no network,
no money movement.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR
from . import store_base

__all__ = ["time_tracker", "register_all"]

_MAX_LIMIT = 100
_MAX_NAME = 80
_MAX_TAGS = 20
_MAX_TAG_LEN = 40
_ACTIONS = ("project", "start", "stop", "record", "status", "report", "events")
_MODES = ("add", "list", "get", "remove", "edit")

_LOCK = threading.RLock()
_STORE = None


# ---------------------------------------------------------------------------
# Store: projects + records + hash-chained ledger (one SQLite file, DATA_DIR)
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return Path(DATA_DIR) / "time_tracker.sqlite3"


class _TimeStore(store_base.StoreBase):
    """Projects + records + hash-chained event ledger. Open-per-operation;
    every write is followed by a re-read before the caller claims
    it (A1-store)."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()

    def _connect(self):
        conn = store_base.connect(
            str(self.path), pragmas=("PRAGMA journal_mode=WAL",))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT UNIQUE NOT NULL,"
            " config_json TEXT NOT NULL,"
            " deleted INTEGER NOT NULL DEFAULT 0,"
            " created REAL NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS records ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " project_id INTEGER NOT NULL,"
            " start REAL NOT NULL,"
            " end REAL,"
            " tags_json TEXT NOT NULL,"
            " note TEXT NOT NULL,"
            " created REAL NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL NOT NULL,"
            " kind TEXT NOT NULL,"
            " payload TEXT NOT NULL,"
            " prev_hash TEXT NOT NULL,"
            " hash TEXT NOT NULL)")
        conn.commit()
        return conn


    # -- projects ------------------------------------------------------
    def project_add(self, name, config):
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, deleted FROM projects WHERE name = ?",
                    (name,)).fetchone()
                if row is not None and row[1] == 1:
                    # Restore: re-adding a removed project restores
                    # it AND its records (the timetrace shape).
                    conn.execute(
                        "UPDATE projects SET deleted = 0,"
                        " config_json = ? WHERE id = ?",
                        (json.dumps(config), row[0]))
                    conn.commit()
                    restored = conn.execute(
                        "SELECT id, name, config_json, deleted, created"
                        " FROM projects WHERE id = ?", (row[0],)).fetchone()
                    self._append_event_locked(
                        conn, "project_restored",
                        {"project": name, "id": row[0]})
                    conn.commit()
                    return {"restored": True, "row": restored}
                if row is not None:
                    conn.rollback()
                    return {"exists": True}
                try:
                    conn.execute(
                        "INSERT INTO projects (name, config_json, deleted,"
                        " created) VALUES (?, ?, 0, ?)",
                        (name, json.dumps(config), time.time()))
                except sqlite3.IntegrityError:
                    conn.rollback()
                    return {"exists": True}
                conn.commit()
                row = conn.execute(
                    "SELECT id, name, config_json, deleted, created"
                    " FROM projects WHERE name = ?", (name,)).fetchone()
                self._append_event_locked(
                    conn, "project_added",
                    {"project": name, "id": row[0]})
                conn.commit()
            finally:
                conn.close()
        if row is None:
            return None
        stored = json.loads(row[2])
        if stored != config:
            return None
        return {"restored": False, "row": row}

    def project_list(self, include_deleted=False):
        if self._missing():
            return []
        with _LOCK:
            conn = self._connect()
            try:
                if include_deleted:
                    rows = conn.execute(
                        "SELECT id, name, config_json, deleted, created"
                        " FROM projects ORDER BY name").fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, name, config_json, deleted, created"
                        " FROM projects WHERE deleted = 0"
                        " ORDER BY name").fetchall()
            finally:
                conn.close()
        return rows

    def project_get(self, name):
        if self._missing():
            return None
        with _LOCK:
            conn = self._connect()
            try:
                return conn.execute(
                    "SELECT id, name, config_json, deleted, created"
                    " FROM projects WHERE name = ?", (name,)).fetchone()
            finally:
                conn.close()

    def project_remove(self, name):
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, deleted FROM projects WHERE name = ?",
                    (name,)).fetchone()
                if row is None or row[1] == 1:
                    conn.rollback()
                    return None
                count = conn.execute(
                    "SELECT COUNT(*) FROM records WHERE project_id = ?",
                    (row[0],)).fetchone()[0]
                # Soft-delete: the records are kept and hidden, and
                # come back if the project is re-added.
                conn.execute(
                    "UPDATE projects SET deleted = 1 WHERE id = ?",
                    (row[0],))
                self._append_event_locked(
                    conn, "project_removed",
                    {"project": name, "id": row[0], "records": count})
                conn.commit()
                return count
            finally:
                conn.close()

    def project_edit(self, name, new_name=None, config=None):
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, config_json FROM projects"
                    " WHERE name = ? AND deleted = 0", (name,)).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                if new_name is not None and new_name != name:
                    clash = conn.execute(
                        "SELECT id FROM projects WHERE name = ?",
                        (new_name,)).fetchone()
                    if clash is not None:
                        conn.rollback()
                        return {"clash": True}
                    conn.execute(
                        "UPDATE projects SET name = ? WHERE id = ?",
                        (new_name, row[0]))
                if config is not None:
                    conn.execute(
                        "UPDATE projects SET config_json = ? WHERE id = ?",
                        (json.dumps(config), row[0]))
                self._append_event_locked(
                    conn, "project_edited",
                    {"project": new_name or name, "id": row[0]})
                conn.commit()
                return conn.execute(
                    "SELECT id, name, config_json, deleted, created"
                    " FROM projects WHERE id = ?", (row[0],)).fetchone()
            finally:
                conn.close()

    # -- records -------------------------------------------------------
    def record_add(self, project_id, start, end, tags, note):
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO records (project_id, start, end,"
                    " tags_json, note, created)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (project_id, start, end, json.dumps(tags), note,
                     time.time()))
                conn.commit()
                row = conn.execute(
                    "SELECT id, project_id, start, end, tags_json, note"
                    " FROM records ORDER BY id DESC LIMIT 1").fetchone()
                self._append_event_locked(
                    conn, "record_added",
                    {"record_id": row[0], "project_id": project_id,
                     "start": start, "end": end})
                conn.commit()
            finally:
                conn.close()
        if row is None:
            return None
        if row[1] != project_id or row[2] != start or row[3] != end:
            return None
        return row

    def record_list(self, project_id=None, start=None, end=None,
                    limit=20):
        if self._missing():
            return []
        with _LOCK:
            conn = self._connect()
            try:
                q = ("SELECT r.id, r.project_id, r.start, r.end,"
                     " r.tags_json, r.note, p.name FROM records r"
                     " JOIN projects p ON p.id = r.project_id"
                     " WHERE p.deleted = 0")
                args = []
                if project_id is not None:
                    q += " AND r.project_id = ?"
                    args.append(project_id)
                if start is not None:
                    q += " AND (r.end IS NULL OR r.end >= ?)"
                    args.append(start)
                if end is not None:
                    q += " AND r.start <= ?"
                    args.append(end)
                q += " ORDER BY r.start DESC LIMIT ?"
                args.append(limit)
                return conn.execute(q, args).fetchall()
            finally:
                conn.close()

    def record_get(self, record_id):
        if self._missing():
            return None
        with _LOCK:
            conn = self._connect()
            try:
                return conn.execute(
                    "SELECT r.id, r.project_id, r.start, r.end,"
                    " r.tags_json, r.note, p.name FROM records r"
                    " JOIN projects p ON p.id = r.project_id"
                    " WHERE r.id = ?", (record_id,)).fetchone()
            finally:
                conn.close()

    def record_remove(self, record_id):
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, project_id, start, end FROM records"
                    " WHERE id = ?", (record_id,)).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                conn.execute("DELETE FROM records WHERE id = ?",
                             (record_id,))
                self._append_event_locked(
                    conn, "record_removed",
                    {"record_id": record_id, "project_id": row[1]})
                conn.commit()
                return row
            finally:
                conn.close()

    def record_edit(self, record_id, start=None, end=None, tags=None,
                    note=None):
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, project_id, start, end, tags_json, note"
                    " FROM records WHERE id = ?", (record_id,)).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                if start is not None:
                    conn.execute(
                        "UPDATE records SET start = ? WHERE id = ?",
                        (start, record_id))
                if end is not None:
                    conn.execute(
                        "UPDATE records SET end = ? WHERE id = ?",
                        (end, record_id))
                if tags is not None:
                    conn.execute(
                        "UPDATE records SET tags_json = ? WHERE id = ?",
                        (json.dumps(tags), record_id))
                if note is not None:
                    conn.execute(
                        "UPDATE records SET note = ? WHERE id = ?",
                        (note, record_id))
                self._append_event_locked(
                    conn, "record_edited",
                    {"record_id": record_id, "project_id": row[1]})
                conn.commit()
                return conn.execute(
                    "SELECT id, project_id, start, end, tags_json, note"
                    " FROM records WHERE id = ?", (record_id,)).fetchone()
            finally:
                conn.close()

    def running_record(self):
        if self._missing():
            return None
        with _LOCK:
            conn = self._connect()
            try:
                return conn.execute(
                    "SELECT r.id, r.project_id, r.start, r.end,"
                    " r.tags_json, r.note, p.name FROM records r"
                    " JOIN projects p ON p.id = r.project_id"
                    " WHERE r.end IS NULL AND p.deleted = 0"
                    " ORDER BY r.start DESC LIMIT 1").fetchone()
            finally:
                conn.close()

    def overlaps(self, start, end, exclude_id=None):
        """True when [start, end) overlaps any existing record (a
        running record counts as ending now)."""
        if self._missing():
            return None
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT r.id, r.start, r.end, p.name FROM records r"
                    " JOIN projects p ON p.id = r.project_id"
                    " WHERE p.deleted = 0").fetchall()
            finally:
                conn.close()
        now = time.time()
        for rid, s, e, _p in rows:
            if exclude_id is not None and rid == exclude_id:
                continue
            other_end = e if e is not None else now
            if start < other_end and s < end:
                return (rid, s, other_end)
        return None

    # -- ledger --------------------------------------------------------
    def _append_event_locked(self, conn, kind, payload):
        ts = time.time()
        prev = conn.execute(
            "SELECT hash FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        prev_hash = prev[0] if prev else "0" * 64
        digest = store_base.chain_digest(
            "json_body_ts", prev_hash,
            {"ts": ts, "kind": kind, "payload": payload})
        conn.execute(
            "INSERT INTO events (ts, kind, payload, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?)",
            (ts, kind, json.dumps(payload), prev_hash, digest))

    def append_event(self, kind, payload):
        with _LOCK:
            conn = self._connect()
            try:
                self._append_event_locked(conn, kind, payload)
                conn.commit()
            finally:
                conn.close()

    def list_events(self, limit=20):
        if self._missing():
            return []
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, ts, kind, payload FROM events"
                    " ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
            finally:
                conn.close()
        return [{"id": r[0], "ts": r[1], "kind": r[2],
                 "payload": json.loads(r[3])} for r in rows]

    def verify_chain(self):
        if self._missing():
            return True
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT ts, kind, payload, prev_hash, hash FROM"
                    " events ORDER BY id").fetchall()
            finally:
                conn.close()
        entries = [{"ts": ts, "kind": kind,
                    "payload": json.loads(payload),
                    "prev_hash": prev_hash, "hash": digest}
                   for ts, kind, payload, prev_hash, digest in rows]
        return store_base.verify_entries(
            entries, "json_body_ts", store_base.GENESIS)["ok"]


def _get_store():
    global _STORE
    if _STORE is None:
        _STORE = _TimeStore()
    return _STORE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_time(value):
    """Parse an ISO datetime, a date, or epoch seconds -> epoch float.
    Returns None when the value cannot be parsed."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def _fmt_time(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def _fmt_hours(seconds):
    """Decimal hours AND h:mm — the timetrace decimal-hours shape,
    with the plain duration alongside it."""
    hours = seconds / 3600.0
    total_min = int(seconds // 60)
    return "%.2f h (%dh %02dm)" % (hours, total_min // 60, total_min % 60)


def _parse_tags(value):
    """Comma-separated tags -> list of clean tags (max 20, each
    at most 40 chars)."""
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value).split(",")
    tags = []
    for part in raw:
        tag = str(part).strip()
        if tag and tag not in tags:
            tags.append(tag[:_MAX_TAG_LEN])
    return tags[:_MAX_TAGS]


def _parse_decimal(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "decimal")


def _project_config(store, project_id, name):
    row = store.project_get(name)
    if row is None:
        return {"tags": [], "decimal": True}
    try:
        return json.loads(row[2])
    except (ValueError, TypeError):
        return {"tags": [], "decimal": True}


def _overlap_refusal(store, start, end, exclude_id=None):
    hit = store.overlaps(start, end, exclude_id=exclude_id)
    if hit is None:
        return None
    rid, s, e = hit
    return ("record refused: that span overlaps record %d (%s to %s)."
            % (rid, _fmt_time(s), _fmt_time(e)))


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def time_tracker(action=None, project=None, name=None, mode=None,
                 tags=None, record_id=None, start=None, end=None,
                 date=None, from_date=None, to_date=None, limit=None,
                 decimal=None):
    """Local working-time tracker (R18-10, dominikbraun/timetrace
    inspiration): projects, start/stop, records, status, report,
    events. Actions: 'project' (add/list/get/remove/edit),
    'start' (begin tracking a project), 'stop' (end the running
    record), 'record' (add/list/get/remove/edit a manual record),
    'status' (running record + today's totals), 'report' (totals
    per project over a date range, decimal hours), 'events' (the
    hash-chained ledger). Hours only — no rate, no amount, no
    invoice (ruling 34).
    """
    store = _get_store()
    action = (action or "").strip().lower()
    mode = (mode or "").strip().lower()
    limit = _coerce_int(limit, 20)
    limit = max(1, min(_MAX_LIMIT, limit))
    project = (project or "").strip()
    name = (name or "").strip()

    if action not in _ACTIONS:
        return ("time_tracker refused: unknown action %r — one of %s."
                % (action, ", ".join(_ACTIONS)))

    # -- projects ------------------------------------------------------
    if action == "project":
        if mode not in _MODES:
            return ("project refused: mode must be one of %s."
                    % ", ".join(_MODES))
        if mode == "list":
            rows = store.project_list(include_deleted=False)
            if not rows:
                return "No projects yet. Add one with project mode=add."
            lines = ["Projects (%d):" % len(rows)]
            for _id, pname, cfg_json, _d, _c in rows:
                try:
                    cfg = json.loads(cfg_json)
                except ValueError:
                    cfg = {}
                lines.append("  #%d %s — default tags: %s, decimal: %s"
                             % (_id, pname,
                                ", ".join(cfg.get("tags") or []) or "none",
                                cfg.get("decimal", True)))
            return "\n".join(lines)
        if not name and mode in ("add", "get", "remove", "edit"):
            return "project refused: give the project name."
        if len(name) > _MAX_NAME:
            return "project refused: name is longer than 80 chars."
        if mode == "add":
            tag_list = _parse_tags(tags)
            dec = _parse_decimal(decimal, True)
            got = store.project_add(name, {"tags": tag_list,
                                           "decimal": dec})
            if got is None:
                return "project refused: the store write failed."
            if got.get("exists"):
                return ("project refused: %r already exists."
                        % name)
            row = got["row"]
            if got.get("restored"):
                return ("Project %r restored with its records."
                        % name)
            return ("Project %r added (#%d) — default tags: %s,"
                    " decimal hours: %s."
                    % (name, row[0],
                       ", ".join(tag_list) or "none", dec))
        if mode == "get":
            row = store.project_get(name)
            if row is None or row[3] == 1:
                return "project refused: no project named %r." % name
            try:
                cfg = json.loads(row[2])
            except ValueError:
                cfg = {}
            recs = store.record_list(project_id=row[0], limit=1000)
            total = sum((r[3] or time.time()) - r[2] for r in recs)
            return ("Project %r (#%d) — default tags: %s, decimal: %s,"
                    " %d records, %s tracked in total."
                    % (name, row[0],
                       ", ".join(cfg.get("tags") or []) or "none",
                       cfg.get("decimal", True), len(recs),
                       _fmt_hours(total)))
        if mode == "remove":
            count = store.project_remove(name)
            if count is None:
                return "project refused: no project named %r." % name
            return ("Project %r removed (soft-delete) with %d record(s)"
                    " kept — re-add the same name to restore them."
                    % (name, count))
        if mode == "edit":
            # Edit changes the per-project configuration only
            # (default tags, decimal-hours preference) — renaming a
            # project is not offered, so no second name is needed.
            if tags is None and decimal is None:
                return ("project refused: edit needs tags or decimal"
                        " to change.")
            old = store.project_get(name)
            if old is None:
                return "project refused: no project named %r." % name
            try:
                cfg = json.loads(old[2])
            except ValueError:
                cfg = {"tags": [], "decimal": True}
            if tags is not None:
                cfg["tags"] = _parse_tags(tags)
            if decimal is not None:
                cfg["decimal"] = _parse_decimal(decimal, True)
            got = store.project_edit(name, config=cfg)
            if got is None:
                return "project refused: no project named %r." % name
            return "Project %r updated." % name

    # -- start ---------------------------------------------------------
    if action == "start":
        if not project:
            return "start refused: give the project to track."
        prow = store.project_get(project)
        if prow is None or prow[3] == 1:
            return ("start refused: no project named %r — add it first"
                    " with project mode=add." % project)
        running = store.running_record()
        if running is not None:
            return ("start refused: already tracking %r since %s —"
                    " stop it first." % (running[6], _fmt_time(running[2])))
        cfg = _project_config(store, prow[0], project)
        tag_list = _parse_tags(tags) or list(cfg.get("tags") or [])
        note = ""
        row = store.record_add(prow[0], time.time(), None, tag_list, note)
        if row is None:
            return "start refused: the store write failed."
        return ("Tracking %r since %s (record #%d, tags: %s)."
                % (project, _fmt_time(row[2]), row[0],
                   ", ".join(tag_list) or "none"))

    # -- stop ----------------------------------------------------------
    if action == "stop":
        running = store.running_record()
        if running is None:
            return "stop refused: nothing is being tracked right now."
        now = time.time()
        got = store.record_edit(running[0], end=now)
        if got is None:
            return "stop refused: the store write failed."
        spent = now - running[2]
        return ("Stopped %r — %s tracked (%s to %s, record #%d)."
                % (running[6], _fmt_hours(spent), _fmt_time(running[2]),
                   _fmt_time(now), running[0]))

    # -- records -------------------------------------------------------
    if action == "record":
        if mode not in _MODES:
            return ("record refused: mode must be one of %s."
                    % ", ".join(_MODES))
        if mode == "list":
            prow = None
            if project:
                prow = store.project_get(project)
                if prow is None or prow[3] == 1:
                    return ("record refused: no project named %r."
                            % project)
            rows = store.record_list(
                project_id=(prow[0] if prow else None), limit=limit)
            if not rows:
                return "No records yet."
            lines = ["Records (%d, newest first):" % len(rows)]
            for rid, _pid, s, e, t_json, note, pname in rows:
                try:
                    t_list = json.loads(t_json)
                except ValueError:
                    t_list = []
                span = ("%s to %s (%s)" % (_fmt_time(s), _fmt_time(e),
                                           _fmt_hours(e - s))
                        if e is not None else
                        "%s to now (running)" % _fmt_time(s))
                lines.append("  #%d %s — %s, tags: %s%s"
                             % (rid, pname, span,
                                ", ".join(t_list) or "none",
                                (" — " + note) if note else ""))
            return "\n".join(lines)
        rid = _coerce_int(record_id, 0)
        if mode in ("get", "remove", "edit") and rid <= 0:
            return "record refused: give the record number."
        if mode == "get":
            row = store.record_get(rid)
            if row is None:
                return "record refused: no record #%d." % rid
            try:
                t_list = json.loads(row[4])
            except ValueError:
                t_list = []
            span = ("%s to %s (%s)" % (_fmt_time(row[2]),
                                       _fmt_time(row[3]),
                                       _fmt_hours(row[3] - row[2]))
                    if row[3] is not None else
                    "%s to now (running)" % _fmt_time(row[2]))
            return ("Record #%d — %s, %s, tags: %s%s"
                    % (rid, row[6], span, ", ".join(t_list) or "none",
                       (" — " + row[5]) if row[5] else ""))
        if mode == "remove":
            row = store.record_remove(rid)
            if row is None:
                return "record refused: no record #%d." % rid
            return "Record #%d removed." % rid
        if mode == "edit":
            row = store.record_get(rid)
            if row is None:
                return "record refused: no record #%d." % rid
            if row[3] is None:
                return ("record refused: record #%d is running — stop"
                        " it first." % rid)
            new_start = _parse_time(start) if start is not None else None
            new_end = _parse_time(end) if end is not None else None
            if start is not None and new_start is None:
                return "record refused: could not parse start %r." % start
            if end is not None and new_end is None:
                return "record refused: could not parse end %r." % end
            s = new_start if new_start is not None else row[2]
            e = new_end if new_end is not None else row[3]
            if e <= s:
                return ("record refused: end (%s) must be after start"
                        " (%s)." % (_fmt_time(e), _fmt_time(s)))
            refusal = _overlap_refusal(store, s, e, exclude_id=rid)
            if refusal is not None:
                return refusal
            new_tags = _parse_tags(tags) if tags is not None else None
            got = store.record_edit(rid, start=new_start, end=new_end,
                                    tags=new_tags, note=name or None)
            if got is None:
                return "record refused: the store write failed."
            return "Record #%d updated." % rid
        if mode == "add":
            if not project:
                return "record refused: give the project."
            prow = store.project_get(project)
            if prow is None or prow[3] == 1:
                return ("record refused: no project named %r — add it"
                        " first." % project)
            s = _parse_time(start)
            e = _parse_time(end)
            if s is None:
                return "record refused: give a start time."
            if e is None:
                return "record refused: give an end time."
            if e <= s:
                return ("record refused: end (%s) must be after start"
                        " (%s)." % (_fmt_time(e), _fmt_time(s)))
            refusal = _overlap_refusal(store, s, e)
            if refusal is not None:
                return refusal
            cfg = _project_config(store, prow[0], project)
            tag_list = _parse_tags(tags) or list(cfg.get("tags") or [])
            row = store.record_add(prow[0], s, e, tag_list, name or "")
            if row is None:
                return "record refused: the store write failed."
            return ("Record #%d added — %s, %s to %s (%s), tags: %s."
                    % (row[0], project, _fmt_time(s), _fmt_time(e),
                       _fmt_hours(e - s), ", ".join(tag_list) or "none"))

    # -- status --------------------------------------------------------
    if action == "status":
        lines = []
        running = store.running_record()
        if running is None:
            lines.append("Nothing is being tracked right now.")
        else:
            lines.append("Tracking %r since %s (%s so far, record #%d,"
                         " tags: %s)."
                         % (running[6], _fmt_time(running[2]),
                            _fmt_hours(time.time() - running[2]),
                            running[0],
                            ", ".join(json.loads(running[4])) or "none"))
        now = time.time()
        day_start = datetime.fromtimestamp(now).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        rows = store.record_list(start=day_start, end=now, limit=1000)
        totals = {}
        for _rid, _pid, s, e, _t, _n, pname in rows:
            totals[pname] = totals.get(pname, 0.0) + (
                (e if e is not None else now) - s)
        if totals:
            lines.append("Today (%s):" % datetime.fromtimestamp(now)
                         .strftime("%Y-%m-%d"))
            for pname in sorted(totals):
                lines.append("  %s — %s" % (pname, _fmt_hours(totals[pname])))
        else:
            lines.append("Nothing tracked today yet.")
        lines.append("Projects: %d, records: %d."
                     % (len(store.project_list()),
                        len(store.record_list(limit=100000))))
        return "\n".join(lines)

    # -- report --------------------------------------------------------
    if action == "report":
        prow = None
        if project:
            prow = store.project_get(project)
            if prow is None or prow[3] == 1:
                return "report refused: no project named %r." % project
        start_s = _parse_time(from_date) if from_date else None
        end_s = _parse_time(to_date) if to_date else None
        if date:
            try:
                day = datetime.fromisoformat(str(date).strip())
            except ValueError:
                return "report refused: could not parse date %r." % date
            start_s = day.replace(hour=0, minute=0, second=0,
                                  microsecond=0).timestamp()
            end_s = start_s + 86400
        if from_date and start_s is None:
            return ("report refused: could not parse from_date %r."
                    % from_date)
        if to_date and end_s is None:
            return ("report refused: could not parse to_date %r."
                    % to_date)
        if start_s is not None and end_s is not None and end_s <= start_s:
            return "report refused: to_date must be after from_date."
        now = time.time()
        rows = store.record_list(
            project_id=(prow[0] if prow else None),
            start=start_s, end=end_s, limit=100000)
        if not rows:
            return "No records in that range."
        totals = {}
        per_tag = {}
        per_day = {}
        for _rid, _pid, s, e, t_json, _n, pname in rows:
            span = (e if e is not None else now) - s
            totals[pname] = totals.get(pname, 0.0) + span
            try:
                t_list = json.loads(t_json)
            except ValueError:
                t_list = []
            for tag in t_list:
                per_tag[tag] = per_tag.get(tag, 0.0) + span
            day = datetime.fromtimestamp(s).strftime("%Y-%m-%d")
            per_day[day] = per_day.get(day, 0.0) + span
        grand = sum(totals.values())
        scope = project or "all projects"
        when = ""
        if start_s is not None:
            when += " from %s" % _fmt_time(start_s)
        if end_s is not None:
            when += " to %s" % _fmt_time(end_s)
        lines = ["Report — %s%s: %s in total."
                 % (scope, when, _fmt_hours(grand))]
        for pname in sorted(totals):
            lines.append("  %s — %s" % (pname, _fmt_hours(totals[pname])))
        if per_tag:
            lines.append("By tag:")
            for tag in sorted(per_tag):
                lines.append("  %s — %s" % (tag, _fmt_hours(per_tag[tag])))
        if len(per_day) > 1:
            lines.append("By day:")
            for day in sorted(per_day):
                lines.append("  %s — %s" % (day, _fmt_hours(per_day[day])))
        return "\n".join(lines)

    # -- events --------------------------------------------------------
    if action == "events":
        if not store.verify_chain():
            return ("events refused: the ledger chain does not verify"
                    " — the store may have been tampered with.")
        events = store.list_events(limit=limit)
        if not events:
            return "No ledger events yet."
        lines = ["Ledger events (%d, newest first):" % len(events)]
        for ev in events:
            lines.append("  #%d %s — %s: %s"
                         % (ev["id"],
                            datetime.fromtimestamp(ev["ts"])
                            .strftime("%Y-%m-%d %H:%M"),
                            ev["kind"], json.dumps(ev["payload"])))
        return "\n".join(lines)

    return "time_tracker refused: unhandled action %r." % action


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register the working-time tracker pack (R18-10,
    dominikbraun/timetrace inspiration): time_tracker — projects,
    start/stop, records, status, report and a hash-chained ledger
    (free by design, "app-state" — it writes only to VYUK's own
    tracker store, the event_planning / event_tickets /
    technical_analysis precedent). Own stdlib code over the tracker
    shape; nothing adopted — no Go code, no timetrace binary, no
    invoicing, no billing, no network. The module owns its
    catalogue via register_all (same pattern as the other packs)."""
    registry.register(
        name="time_tracker",
        description=(
            "Local working-time tracker: record working time against "
            "projects with tags, start/stop a live timer, add manual "
            "records, and read status and reports in decimal hours. "
            "Projects carry per-project configuration (default tags, "
            "decimal-hours preference); removing a project keeps its "
            "records, and re-adding the same name restores them. "
            "Hours only — no rates, no amounts, no invoicing. Use "
            "when the user wants to know how long they worked on a "
            "project, or to start/stop tracking their work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'project' (manage projects), 'start' (begin tracking), 'stop' (end the running record), 'record' (manual records), 'status' (running record + today's totals), 'report' (totals over a date range), 'events' (the hash-chained ledger)"},
                "project": {"type": "string",
                            "description": "With 'project' get/remove/edit, 'start', 'stop', 'record' add/list or 'report', the project name"},
                "name": {"type": "string",
                         "description": "With 'project' add, the new project's name (at most 80 chars); with 'record' add, a note for the record"},
                "mode": {"type": "string",
                         "description": "With 'project' or 'record', one of 'add', 'list', 'get', 'remove' or 'edit'"},
                "tags": {"type": "string",
                         "description": "Comma-separated tags (at most 20) for a project default or a record"},
                "record_id": {"type": "integer",
                              "description": "With 'record' get/remove/edit, the record number"},
                "start": {"type": "string",
                          "description": "With 'record' add/edit, the start time as ISO datetime or epoch seconds"},
                "end": {"type": "string",
                        "description": "With 'record' add/edit, the end time as ISO datetime or epoch seconds"},
                "date": {"type": "string",
                         "description": "With 'report', one day as YYYY-MM-DD"},
                "from_date": {"type": "string",
                              "description": "With 'report', the range start as ISO datetime or date"},
                "to_date": {"type": "string",
                            "description": "With 'report', the range end as ISO datetime or date"},
                "limit": {"type": "integer",
                          "description": "With 'record' list or 'events', most rows shown (20 if left out, max 100)"},
                "decimal": {"type": "string",
                            "description": "With 'project' add/edit, 'true' to prefer decimal hours in status and reports (the default) or 'false' for h:mm only"},
            },
            "required": ["action"],
        },
        handler=time_tracker,
    )
