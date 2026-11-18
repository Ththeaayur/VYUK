"""RL-1.1 crash-consistent state (Resilience track, wave 25, plan §5.18).

Pattern helpers for a GIVEN SQLite database path. This module never migrates
``assistant.db`` itself: :func:`apply_durable_pragmas` refuses that filename
outright, and every other helper takes an explicit path. Stdlib ``sqlite3``
only. APSW is NOT taken (its licence check stays owed; the pattern does not
need it). DiskCache is NOT used anywhere in this track (its PyPI-Apache vs
GitHub-NOASSERTION licence conflict blocks ship, not pilot; see
``resilience_power`` for the native checkpoint store).

Stores rule (INV-8): every store this track creates gets its own file under
``data/``; nothing here writes to ``assistant.db``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from .. import store_base

SQLITE_MIN_VERSION = (3, 51, 3)
# The WAL-reset corruption range named by the wave report: versions inside
# it must fail the audit with a plain verdict, never silently proceed.
SQLITE_CORRUPTION_RANGE = ((3, 7, 0), (3, 51, 2))

SNAPSHOT_PREFIX = "snapshot-"


def parse_version(version_text: str) -> tuple:
    """'3.51.3' -> (3, 51, 3); tolerates suffixes like '3.51.3-rc1'."""
    parts = []
    for chunk in str(version_text).split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def audit_sqlite_version(version_text: str | None = None) -> dict:
    """Verdict on the packaged SQLite version.

    PASS iff version >= 3.51.3. FAIL with a plain reason when inside the
    WAL-reset corruption range 3.7.0-3.51.2, or below the minimum. Never
    silently proceeds: callers must check ``ok`` before opening a store.
    """
    raw = version_text if version_text is not None else sqlite3.sqlite_version
    version = parse_version(raw)
    dotted = ".".join(str(n) for n in version)
    if version >= SQLITE_MIN_VERSION:
        return {"ok": True, "version": dotted, "reason": "meets minimum 3.51.3"}
    low, high = SQLITE_CORRUPTION_RANGE
    if low <= version <= high:
        return {"ok": False, "version": dotted,
                "reason": "inside WAL-reset corruption range 3.7.0-3.51.2"}
    return {"ok": False, "version": dotted,
            "reason": "below minimum 3.51.3"}


def apply_durable_pragmas(db_path: str) -> dict:
    """Apply WAL + synchronous=FULL to the database at ``db_path``.

    Refuses ``assistant.db`` outright: this helper patterns a given store,
    it is not a migration of the Wave 6 schema. Returns the pragmas as
    read back from SQLite, so the caller verifies rather than assumes.
    """
    if store_base.is_assistant_db(db_path):
        raise ValueError("apply_durable_pragmas never targets assistant.db")
    con = sqlite3.connect(db_path)
    try:
        # Set-then-read-back: PRAGMA synchronous=FULL returns no row in
        # this Python sqlite3 build, so the value must be queried back.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        journal_mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = con.execute("PRAGMA synchronous").fetchone()[0]
        return {"journal_mode": journal_mode, "synchronous": synchronous}
    finally:
        con.close()


def _fsync_file(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create_snapshot(db_path: str, snapshot_dir: str, keep: int = 3) -> dict:
    """Consistent rotated snapshot of ``db_path`` into ``snapshot_dir``.

    Uses SQLite's backup API (a consistent copy even with a live WAL),
    fsyncs the copy, then prunes older snapshots keeping the newest
    ``keep``. Returns the snapshot path and the pruned list.
    """
    os.makedirs(snapshot_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final = os.path.join(snapshot_dir, "%s%s-%d.sqlite3"
                         % (SNAPSHOT_PREFIX, stamp, os.getpid()))
    tmp = final + ".tmp"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    _fsync_file(tmp)
    os.replace(tmp, final)
    snaps = sorted(
        (os.path.join(snapshot_dir, name) for name in os.listdir(snapshot_dir)
         if name.startswith(SNAPSHOT_PREFIX) and name.endswith(".sqlite3")),
        key=lambda p: os.path.getmtime(p))
    pruned = []
    while len(snaps) > keep:
        victim = snaps.pop(0)
        os.remove(victim)
        pruned.append(victim)
    return {"snapshot": final, "pruned": pruned, "kept": snaps}


def restore_snapshot(snapshot_path: str, db_path: str) -> dict:
    """Restore ``snapshot_path`` over ``db_path`` atomically.

    Copies to a temp file in the destination directory, fsyncs, then
    renames over the target, so a crash mid-restore never leaves a torn
    database behind. Returns the restored path.
    """
    if not os.path.isfile(snapshot_path):
        raise FileNotFoundError("snapshot not found: %s" % snapshot_path)
    directory = os.path.dirname(os.path.abspath(db_path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".restore-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as out:
            with open(snapshot_path, "rb") as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, db_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return {"restored": db_path, "from": snapshot_path}
