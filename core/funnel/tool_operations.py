"""Durable tool-operation ledger (audit fix A5, master P1-3).

When a tool call times out, the worker is abandoned, not killed:
the call may still complete (or stay wedged) in the background,
and until somebody learns otherwise its outcome is UNKNOWN. This
module is the durable record of that fact.

    record_timeout(tool, layer) -> op_id
        Creates an operation in state "unknown" (durable JSON
        under data/, atomic tmp + replace, process-wide lock) and
        returns its collision-proof id (uuid4 hex). The timed-out
        result carries this id, so any later report can be tied
        back to exactly this call.

    finalize_operation(op_id, outcome) -> bool
        Reconciles a timed-out operation exactly once: the first
        call flips it from "unknown" to the outcome's status and
        durably records the outcome summary; later calls return
        False and change nothing. No double-apply, no double-report.

    get_operation(op_id) / pending_operations()
        Read paths for diagnostics and reconciliation. pending()
        lists every operation still "unknown".

    list_pending_unknown() / resolve_pending_unknown()
        The SURFACED reconciliation flow (audit fix Batch 3,
        REL-03): pending() was a read-only orphan — nothing
        presented it to a caller or recorded a human's verdict.
        list_pending_unknown() is the caller-visible pending
        list; resolve_pending_unknown() records a human-verified
        outcome exactly once. "did_not_happen" additionally
        releases the receipt guard for that call (via the
        receipt store), so a verified-absent effect can be
        safely re-run; "happened" deliberately does NOT release
        it (re-running a verified effect would duplicate it).
        An operation whose args_signature was never recorded
        (older callers, ledger failure) still finalises, but
        its guard release is honestly reported unavailable.

Layers name where the timeout was observed: "funnel" (native
deadline thread), "funnel-mcp" (toolbox-server timeout), and
"live_batch" (Live wait_for abandonment). MCP operations have no
late-completion channel — the bus call returned — so they stay
"unknown" until a human reconciles them; native and Live
operations finalize when the abandoned work lands (worker thread
finally / executor-future done-callback).

No auto-retry rule: NOTHING in this module retries anything, and
no caller may re-issue a call whose operation is still "unknown"
(see core.brain.planner's funnel adapter + reflection guard, and the
timeout message itself, which says so in plain words). The
existing timeout cool-down (core/funnel/tools_handlers.py) additionally refuses
immediate re-dispatch of the same tool+target.

Outcome summaries recorded here carry a status and at most a
short error-type name — never arguments, never payloads, never
secrets.

Stdlib-only, no core imports. set_store()/reset_store() exist
for tests only: an installed in-memory store skips all file I/O.
"""

from __future__ import annotations

import datetime
import json
import threading
import uuid
from pathlib import Path
from typing import Any

_FILENAME = "tool_operations.json"

_LOCK = threading.Lock()
_STORE: dict[str, Any] | None = None
_STORE_PATH: str | None = None


def _default_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / _FILENAME  # Phase 3 F2a: file now in core/funnel/ — one more level up to the package root


def _read_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a broken ledger reads empty
        return {}


def _write_file(path: Path, data: dict[str, Any]) -> bool:
    # F-01 audit: the temp name is uuid-unique, not the old fixed
    # "<name>.json.tmp" — _persist runs under _LOCK in-process, but
    # a second process sharing data/ could still interleave in one
    # shared temp file and promote a torn ledger. Unique temp names
    # close that interleave; the in-memory store stays the single
    # source of truth in-process.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=1, sort_keys=True),
                encoding="utf-8")
            tmp.replace(path)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return True
    except Exception:  # noqa: BLE001 — ledger failure never breaks a call
        print("[Tool Operations] WARNING: ledger write failed; "
              "operation recorded in memory only.")
        return False


def _store() -> dict[str, Any]:
    global _STORE
    if _STORE is None:
        _STORE = _read_file(
            Path(_STORE_PATH) if _STORE_PATH else _default_path())
    return _STORE if isinstance(_STORE, dict) else {}


def _persist() -> None:
    """Write the in-memory store through, unless a test store is
    installed (then there is no file to write)."""
    if _STORE_PATH is None and _STORE is not None and \
            _STORE.get("__test__"):
        return
    path = Path(_STORE_PATH) if _STORE_PATH else _default_path()
    _write_file(path, _STORE if isinstance(_STORE, dict) else {})


def set_store(store: dict[str, Any] | None) -> None:
    """Install an in-memory ledger (tests only). None clears it."""
    global _STORE, _STORE_PATH
    if isinstance(store, dict):
        _STORE = dict(store)
        _STORE["__test__"] = True
        _STORE_PATH = None
    else:
        _STORE = None
        _STORE_PATH = None


def reset_store() -> None:
    """Drop any in-memory ledger; next lookup loads from disk."""
    global _STORE, _STORE_PATH
    _STORE = None
    _STORE_PATH = None


def record_timeout(tool: str, layer: str,
                   args_signature: str | None = None) -> str:
    """Record a timed-out call as outcome-unknown. Returns the
    operation id. Never raises into a call path: on ledger failure
    the record lives in memory and the id is still returned.
    args_signature (Batch 3) is the receipt-guard key hash for
    the call — a hash only, never the arguments themselves —
    so a later human "did_not_happen" resolution can release
    exactly that call's guard. Older callers pass None, and
    their operations still finalise; only guard release is
    then unavailable."""
    op_id = uuid.uuid4().hex
    record = {
        "op_id": op_id,
        "tool": str(tool),
        "layer": str(layer),
        "status": "unknown",
        "kind": "timeout",
        "outcome": "unknown",
        "args_signature": str(args_signature) if args_signature else None,
        "created_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "finalized_at": None,
        "final": None,
    }
    try:
        with _LOCK:
            store = _store()
            store[op_id] = record
            _persist()
    except Exception:  # noqa: BLE001 — see module docstring
        print("[Tool Operations] WARNING: could not record "
              f"timeout for '{tool}'; operation {op_id} is "
              "memory-only.")
    return op_id


def finalize_operation(op_id: str, outcome: dict[str, Any] | None) -> bool:
    """Reconcile one timed-out operation exactly once.

    Returns True only for the first finalization (unknown ->
    outcome status). Unknown ids and already-finalized operations
    return False and change nothing. The outcome summary is stored
    verbatim except that only its status and an optional short
    note survive — no payloads are ever persisted."""
    summary: dict[str, Any] = {"status": "completed"}
    if isinstance(outcome, dict):
        status = outcome.get("status")
        summary["status"] = str(status) if status else "completed"
        note = outcome.get("note") or outcome.get("error")
        if isinstance(note, str) and note:
            summary["note"] = note[:120]
    try:
        with _LOCK:
            store = _store()
            record = store.get(op_id)
            if not isinstance(record, dict):
                return False
            if record.get("status") != "unknown":
                return False
            record["status"] = summary["status"]
            record["outcome"] = summary["status"]
            record["final"] = summary
            record["finalized_at"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds")
            _persist()
            return True
    except Exception:  # noqa: BLE001 — finalization never breaks a call
        return False


def get_operation(op_id: str) -> dict[str, Any] | None:
    """One operation by id, or None. Returns a copy."""
    with _LOCK:
        record = _store().get(op_id)
        return dict(record) if isinstance(record, dict) else None


def pending_operations() -> list[dict[str, Any]]:
    """Every operation still outcome-unknown, oldest first."""
    with _LOCK:
        records = [dict(r) for r in _store().values()
                   if isinstance(r, dict) and r.get("status") == "unknown"
                   and "op_id" in r]
    records.sort(key=lambda r: str(r.get("created_at") or ""))
    return records


def list_pending_unknown() -> list[dict]:
    """Caller-visible pending-unknown list (Batch 3, REL-03):
    every timed-out operation still awaiting a human verdict,
    oldest first. This is the surface the timeout message
    points at ("do not retry until reconciled") — it is the
    same data as pending_operations(), named for callers."""
    return pending_operations()


def resolve_pending_unknown(op_id: str, verified: str,
                            note: str = "") -> tuple[bool, str]:
    """Record a human's verified outcome for one pending
    operation, exactly once (Batch 3, REL-03).

    verified must be "happened" (the effect DID occur) or
    "did_not_happen" (the effect provably did NOT occur).
    Returns (True, detail) on the first resolution, (False,
    reason) for an unknown id, an already-reconciled
    operation, or a bad verified value — nothing is
    double-applied. For "did_not_happen" the receipt guard
    for the call is released when its args_signature was
    recorded, so a re-run is safe; the detail string says
    honestly when release was unavailable. For "happened"
    the guard is deliberately LEFT blocking: the effect
    already happened, and re-running the identical call
    would duplicate it.
    """
    if verified not in ("happened", "did_not_happen"):
        return False, ("verified must be 'happened' or "
                       "'did_not_happen'")
    record = get_operation(op_id)
    if record is None:
        return False, "unknown operation id"
    if record.get("status") != "unknown":
        return False, "operation already reconciled"
    status = ("verified_happened" if verified == "happened"
              else "verified_absent")
    human_note = note or f"human-verified: {verified}"
    if not finalize_operation(
            op_id, {"status": status, "note": human_note}):
        return False, "operation already reconciled"
    if verified == "did_not_happen":
        signature = record.get("args_signature")
        if not signature:
            return True, ("resolved as verified_absent; receipt "
                          "guard release unavailable (no "
                          "args_signature was recorded for this "
                          "operation)")
        try:
            from .. import tool_receipts as _receipts_mod  # Phase 3 F2a: tool_operations now in core/funnel/; tool_receipts stays flat

            released = _receipts_mod.try_release_unknown(
                tool=str(record.get("tool", "")),
                args_signature=str(signature),
                summary="human-verified: effect did not happen")
        except Exception:  # noqa: BLE001 — release never breaks
            released = False  # the resolution itself
        if released:
            return True, ("resolved as verified_absent; receipt "
                          "guard released, the call may be re-run")
        return True, ("resolved as verified_absent; receipt "
                      "guard release unavailable (no unknown "
                      "receipt for this call)")
    return True, "resolved as verified_happened; receipt guard kept blocking"


def is_reconciled(op_id: str) -> bool:
    """True once an operation has been finalized (or never existed
    as unknown). A still-unknown operation is NOT reconciled, and
    must not be auto-retried."""
    record = get_operation(op_id)
    if record is None:
        return True
    return record.get("status") != "unknown"
