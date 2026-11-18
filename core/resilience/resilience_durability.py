"""RL-4.x task durability: mechanism evidence, NOT a second engine
(wave 25, plan §5.18).

Three pieces, each in its OWN file (INV-8):
- RL-4.2 in-house idempotency-key effect ledger (exactly-once by key).
  The ``exactly-once`` PyPI package's repo LICENSE read stays owed, but
  the in-house pattern is unaffected by it.
- RL-4.3 saga / compensation SPEC for reversible tool classes: a
  registry for reversible classes ONLY, plus a WAL-backed spec store.
  ``agent-saga``'s repo LICENSE read stays owed and gates any code
  pilot — this build ships the spec + registry, not the package.
- The FENCE module: a replay entry point routing every replayed step
  through an injected funnel callable with a failsafe check per step
  (Fence 1, INV-7). Steps are funnelled individually; there is no
  workflow loop, no scheduler, and no engine here.

Fence 2 (INV-5): durability lives OUTSIDE the executor. The one durable
engine in the programme is the TC-1 checkpoint build (Waves 1/8, D-27);
RL-4 is its mechanism evidence, not a second engine. Temporal /
Restate / Hatchet shipped forms are EXCLUDED (BSL / server / fit) and
appear here as spec notes only, never as code.

DBOS (RL-4.1): NO dbos import, NO dependency, NO DBOS code in this
build. The fence + ledger above are the substrate a DBOS-library pilot
would plug into. The pilot plan (documented, not built): import dbos
with its SQLite default and no server, against both fences, with
acceptance = one plan class surviving kill -9 / power-cut and resuming
without double-acting, proven by the RL-6.6 kill harness first.
DBOS-as-workflow ("agent loop = workflow") is REJECTED as a shape: it
would be a second orchestration owner (INV-13 / INV-2). RL-1.4
Huey-class schedulers are NOT built: one scheduler story is decided in
Wave 8 under BN-8 consolidation (D-27).
"""

from __future__ import annotations

import json
import os
import sqlite3

from .. import store_base

EFFECT_LEDGER_FILENAME = "rl_effects.sqlite3"
SAGA_FILENAME = "rl_sagas.sqlite3"


def _apply_pragmas(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")


class EffectLedger:
    """RL-4.2 idempotency-key effect ledger in its OWN file.

    Exactly-once semantics by key: recording an already-known key
    returns the ORIGINAL outcome and records nothing new, so a replay
    after a crash can never double-act. Outcomes are JSON only, never
    pickle.
    """

    def __init__(self, db_path: str):
        store_base.refuse_assistant_db(db_path, what="effect ledger")
        self.db_path = str(db_path)
        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        try:
            _apply_pragmas(con)
            con.execute(
                "CREATE TABLE IF NOT EXISTS effects ("
                "key TEXT PRIMARY KEY, outcome TEXT NOT NULL)")
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        _apply_pragmas(con)
        return con

    def record_effect(self, key: str, outcome: dict) -> dict:
        """Record an effect, or return the original if the key exists.

        Returns {"recorded": True, "outcome": outcome} on first write,
        {"recorded": False, "outcome": original} on a replayed key.
        """
        try:
            blob = json.dumps(outcome)
        except (TypeError, ValueError) as exc:
            raise TypeError("effect outcomes must be JSON-serialisable"
                            ) from exc
        con = self._connect()
        try:
            cur = con.execute(
                "INSERT OR IGNORE INTO effects (key, outcome) "
                "VALUES (?, ?)", (key, blob))
            con.commit()
            if cur.rowcount == 1:
                return {"recorded": True, "outcome": outcome}
            row = con.execute(
                "SELECT outcome FROM effects WHERE key = ?",
                (key,)).fetchone()
        finally:
            con.close()
        return {"recorded": False, "outcome": json.loads(row[0])}

    def get_effect(self, key: str) -> dict | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT outcome FROM effects WHERE key = ?",
                (key,)).fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else None

    def list_keys(self) -> list:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT key FROM effects ORDER BY key").fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]


class CompensationRegistry:
    """RL-4.3 compensation registry for REVERSIBLE classes only.

    A spec row names the undo action and its requirements; registering
    a non-reversible class raises ValueError. The spec store is WAL
    backed and lives in its OWN file (INV-8). This is a spec +
    registry, not an execution engine: nothing here runs compensations
    on its own — a compensation runs only as a funnelled step, through
    the same fence as any replay.
    """

    def __init__(self, db_path: str):
        store_base.refuse_assistant_db(db_path, what="saga spec")
        self.db_path = str(db_path)
        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        try:
            _apply_pragmas(con)
            con.execute(
                "CREATE TABLE IF NOT EXISTS saga_specs ("
                "tool_class TEXT PRIMARY KEY, "
                "undo_action TEXT NOT NULL, requires TEXT NOT NULL)")
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        _apply_pragmas(con)
        return con

    def register(self, tool_class: str, undo_action: str,
                 requires: dict | None = None,
                 reversible: bool = True) -> None:
        """Register a compensation spec. Non-reversible classes are
        refused outright: a saga must never promise to undo what it
        cannot undo."""
        if not reversible:
            raise ValueError(
                "refusing non-reversible class in the compensation "
                "registry: %s" % tool_class)
        try:
            blob = json.dumps(requires or {})
        except (TypeError, ValueError) as exc:
            raise TypeError("compensation requirements must be JSON"
                            ) from exc
        con = self._connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO saga_specs "
                "(tool_class, undo_action, requires) VALUES (?, ?, ?)",
                (tool_class, undo_action, blob))
            con.commit()
        finally:
            con.close()

    def get(self, tool_class: str) -> dict | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT undo_action, requires FROM saga_specs "
                "WHERE tool_class = ?", (tool_class,)).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {"tool_class": tool_class, "undo_action": row[0],
                "requires": json.loads(row[1])}

    def list_specs(self) -> list:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT tool_class, undo_action, requires FROM "
                "saga_specs ORDER BY tool_class").fetchall()
        finally:
            con.close()
        return [{"tool_class": r[0], "undo_action": r[1],
                 "requires": json.loads(r[2])} for r in rows]


def replay(entries, funnel, is_failsafe_tripped) -> dict:
    """FENCE module: replay entries through the injected funnel.

    Every entry is routed through ``funnel(entry)`` individually, and
    the failsafe predicate is checked before EVERY dispatch (Fence 1,
    INV-7). A trip at the start means zero dispatches; a trip mid
    replay stops delivery at once. A funnel exception counts as a
    failed step, never a crash of the replay. There is no workflow
    loop, no scheduler, and no engine in this function: the funnel
    owns execution, this function only routes and gates (Fence 2,
    INV-5). Callers that need durability across a crash pair this
    with the effect ledger: check the key before dispatch, record
    after, so a replayed entry returns its original outcome instead of
    acting twice.
    """
    dispatched = []
    for entry in entries:
        if is_failsafe_tripped():
            return {"dispatched": dispatched, "stopped": True,
                    "reason": "failsafe-tripped",
                    "dispatched_count": len(dispatched)}
        try:
            ok = bool(funnel(entry))
        except Exception:  # noqa: BLE001 — funnel failure is a step
            ok = False
        dispatched.append({"entry": entry, "ok": ok})
    return {"dispatched": dispatched, "stopped": False,
            "reason": "complete",
            "dispatched_count": len(dispatched)}
