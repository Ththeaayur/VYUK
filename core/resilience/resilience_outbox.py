"""RL-1.2/1.3 durable outbox (Resilience track, wave 25, plan §5.18).

SQLite-backed outbox in its OWN file (INV-8: never ``assistant.db``).
Native, superseding the LiteQueue / persist-queue dependencies: no
LiteQueue import (its packaged-Python >=3.12 check is moot) and no
persist-queue import — pickle-default queues are EXCLUDED, this outbox
serialises JSON only, never pickle.

Semantics (documented, tested):
- enqueue: JSON payload only (non-serialisable input raises TypeError).
- ack: removes an entry from the pending set (marks it acked, never
  deletes the row — the row stays auditable).
- nack: requeues with an attempts counter and a delay. ``retry_expired``
  semantics: an entry that stays pending past ``expire_after_s`` since
  enqueue is swept to ``expired`` by :meth:`Outbox.sweep_expired` — still
  stored, never silently dropped.
- max attempts reached on nack: the entry goes to ``dead`` (dead-lettered,
  still stored and listable), never dropped.
- breaker-open means QUEUE, don't drop: :func:`deliver_due` dispatches
  nothing while the breaker is open and leaves every entry pending.
- Fence 1 (INV-7): delivery goes to an injected funnel callable and the
  failsafe predicate is checked before EVERY dispatch. A tripped failsafe
  means zero dispatches, including mid-replay (delivery stops at once).
  The funnel is injected, never an engine: there is no workflow loop
  here, only a single pass over due entries.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time

from .. import store_base

OUTBOX_FILENAME = "rl_outbox.sqlite3"

STATUS_PENDING = "pending"
STATUS_ACKED = "acked"
STATUS_DEAD = "dead"
STATUS_EXPIRED = "expired"


def _apply_pragmas(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")


def _breaker_open(breaker) -> bool:
    """Duck-typed breaker check: ``is_open()`` wins, else ``allow()``."""
    if breaker is None:
        return False
    if hasattr(breaker, "is_open"):
        return bool(breaker.is_open())
    if hasattr(breaker, "allow"):
        return not bool(breaker.allow())
    return False


class Outbox:
    """A durable JSON outbox in one SQLite file."""

    def __init__(self, db_path: str):
        store_base.refuse_assistant_db(db_path, what="outbox")
        self.db_path = str(db_path)
        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        try:
            _apply_pragmas(con)
            con.execute(
                "CREATE TABLE IF NOT EXISTS outbox ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "payload TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'pending', "
                "attempts INTEGER NOT NULL DEFAULT 0, "
                "max_attempts INTEGER NOT NULL DEFAULT 5, "
                "available_at REAL NOT NULL, "
                "created_at REAL NOT NULL, "
                "expire_after_s REAL NOT NULL DEFAULT 86400.0)")
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        _apply_pragmas(con)
        return con

    def enqueue(self, payload: dict, max_attempts: int = 5,
                delay_s: float = 0.0,
                expire_after_s: float = 86400.0) -> int:
        """Store a JSON payload as pending. Returns the entry id."""
        try:
            blob = json.dumps(payload)
        except (TypeError, ValueError) as exc:
            raise TypeError("outbox payloads must be JSON-serialisable") from exc
        now = time.time()
        con = self._connect()
        try:
            cur = con.execute(
                "INSERT INTO outbox (payload, status, attempts, "
                "max_attempts, available_at, created_at, expire_after_s) "
                "VALUES (?, 'pending', 0, ?, ?, ?, ?)",
                (blob, max_attempts, now + delay_s, now, expire_after_s))
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()

    def get(self, entry_id: int) -> dict | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT id, payload, status, attempts, max_attempts, "
                "available_at, created_at, expire_after_s FROM outbox "
                "WHERE id = ?", (entry_id,)).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {"id": row[0], "payload": json.loads(row[1]), "status": row[2],
                "attempts": row[3], "max_attempts": row[4],
                "available_at": row[5], "created_at": row[6],
                "expire_after_s": row[7]}

    def claim_due(self, limit: int = 100, now: float | None = None) -> list:
        """Due pending entries, oldest first. Never returns dead/expired."""
        stamp = time.time() if now is None else now
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT id, payload, status, attempts, max_attempts, "
                "available_at, created_at, expire_after_s FROM outbox "
                "WHERE status = 'pending' AND available_at <= ? "
                "AND attempts < max_attempts ORDER BY id LIMIT ?",
                (stamp, limit)).fetchall()
        finally:
            con.close()
        return [{"id": r[0], "payload": json.loads(r[1]), "status": r[2],
                 "attempts": r[3], "max_attempts": r[4],
                 "available_at": r[5], "created_at": r[6],
                 "expire_after_s": r[7]} for r in rows]

    def ack(self, entry_id: int) -> bool:
        con = self._connect()
        try:
            cur = con.execute(
                "UPDATE outbox SET status = 'acked' WHERE id = ? "
                "AND status = 'pending'", (entry_id,))
            con.commit()
            return cur.rowcount == 1
        finally:
            con.close()

    def nack(self, entry_id: int, delay_s: float = 60.0) -> str:
        """Requeue with attempts+1; at max attempts the entry goes dead
        (dead-lettered, still stored). Returns the new status."""
        entry = self.get(entry_id)
        if entry is None or entry["status"] != "pending":
            raise ValueError("only a pending entry can be nacked")
        attempts = entry["attempts"] + 1
        if attempts >= entry["max_attempts"]:
            status = STATUS_DEAD
            available_at = entry["available_at"]
        else:
            status = STATUS_PENDING
            available_at = time.time() + delay_s
        con = self._connect()
        try:
            con.execute(
                "UPDATE outbox SET status = ?, attempts = ?, "
                "available_at = ? WHERE id = ?",
                (status, attempts, available_at, entry_id))
            con.commit()
        finally:
            con.close()
        return status

    def sweep_expired(self, now: float | None = None) -> int:
        """Mark overdue pending entries expired. Returns how many moved.

        Expired entries stay in the file (auditable, never silently
        dropped); they are simply no longer claimed as due.
        """
        stamp = time.time() if now is None else now
        con = self._connect()
        try:
            cur = con.execute(
                "UPDATE outbox SET status = 'expired' WHERE status = "
                "'pending' AND created_at + expire_after_s <= ?",
                (stamp,))
            con.commit()
            return int(cur.rowcount)
        finally:
            con.close()

    def list_by_status(self, status: str) -> list:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT id, payload, status, attempts, max_attempts, "
                "available_at, created_at, expire_after_s FROM outbox "
                "WHERE status = ? ORDER BY id", (status,)).fetchall()
        finally:
            con.close()
        return [{"id": r[0], "payload": json.loads(r[1]), "status": r[2],
                 "attempts": r[3], "max_attempts": r[4],
                 "available_at": r[5], "created_at": r[6],
                 "expire_after_s": r[7]} for r in rows]


def deliver_due(outbox: Outbox, funnel, is_failsafe_tripped,
                breaker=None) -> dict:
    """Deliver due entries through the injected funnel (Fence 1, INV-7).

    ``funnel(entry_dict)`` returns True on success. The failsafe
    predicate is checked before the first dispatch AND before every
    later dispatch: a trip mid-replay stops delivery at once with zero
    further dispatches. A breaker-open verdict queues everything: zero
    dispatches, every entry left pending. A funnel exception counts as
    a failure (nack), never a crash of the delivery pass. This is one
    pass over due entries, not a retry loop: failures requeue with a
    delay for a LATER pass.
    """
    if _breaker_open(breaker):
        return {"dispatched": 0, "acked": 0, "nacked": 0,
                "reason": "breaker-open-queued"}
    if is_failsafe_tripped():
        return {"dispatched": 0, "acked": 0, "nacked": 0,
                "reason": "failsafe-tripped"}
    dispatched = acked = nacked = 0
    for entry in outbox.claim_due():
        if is_failsafe_tripped():
            return {"dispatched": dispatched, "acked": acked,
                    "nacked": nacked,
                    "reason": "failsafe-tripped-mid-replay"}
        dispatched += 1
        try:
            ok = bool(funnel(entry))
        except Exception:  # noqa: BLE001 — funnel failure is a nack
            ok = False
        if ok:
            outbox.ack(entry["id"])
            acked += 1
        else:
            outbox.nack(entry["id"])
            nacked += 1
    return {"dispatched": dispatched, "acked": acked, "nacked": nacked,
            "reason": "complete"}
