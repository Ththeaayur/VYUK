"""Durable tool-execution receipts (fourth audit C10, Phase 2).

Every funnel dispatch leaves a receipt: an attempt record
written BEFORE execution and an outcome record AFTER it, in an
append-only JSONL file. A turn that dies mid-action (lost model
reply, disconnect, user cancellation, process death) therefore
leaves an attempt with NO outcome — status "unknown" — and the
system must not blindly re-run that side effect: the funnel's
unknown-outcome guard refuses an identical non-read-only call
until the state is verified, instead of auto-repeating it.

Store discipline (mirrors core/groq_action_journal.py):
append-only lines, flushed per record (durable against a
process crash; power-loss durability would need fsync per
record and is deliberately not paid for on every dispatch —
EXCEPT the CRITICAL_RECEIPT_TIER below, whose records fsync
before dispatch proceeds / before the result is reported).
Corrupt line fails LOUDLY (ValueError on load) and all later
writes are REFUSED — never silently rewritten, never silently
dropped. Funnel-side helpers never raise, and they still log
once per process — but a failure is now also STICKY: every
failure kind keeps a count + last error queryable via
receipt_degraded_state(), and the funnel FAILS CLOSED for
non-read tools when the guard lookup or the dispatch-point
attempt record fails (refused, nothing run), instead of
silently disabling the unknown-outcome guard. Read tools keep
the best-effort path (re-running a read is safe) and carry
the degraded flag on their result via the funnel's
observability field. The Live pre-dispatch cancellation path
stays best-effort: a call cancelled before dispatch never
ran, so a lost cancelled-record cannot hide an effect.

Guard keys (audit fix Batch 3, REL-02): the exact-argument
signature is still the primary key, and every attempt also
registers NORMALISED effect signatures (effect_signatures):
file paths (platform case/separator rules + trailing-slash
strip + ~ expansion, never symlink resolution), URLs
(scheme/host case, default-port strip, trailing-slash strip,
fragment dropped, query order PRESERVED), recipient
identifiers (strip + lowercase) and hosts (strip + lowercase
+ trailing-dot strip), applied only to named argument keys.
A query matches an unknown attempt when ANY of its effect
signatures intersects the attempt's — so a re-spelled
equivalent is refused, while a genuinely different effect
stays distinct. Pre-existing receipts (written before this
batch) carry no normalised signatures and still guard exact
matches only; normalised guarding applies to receipts written
after it. Deliberately NOT normalised: symlink-resolved
paths, URL query order, percent-encoding, numeric
1 vs "1" vs 1.0 spellings, and argument-key case — each is
either ambiguous or platform-dependent, so those spellings
remain distinct guard keys.

Outcome vocabulary: "succeeded" / "failed" / "refused" /
"unknown" / "cancelled" (Phase 4, C04). Funnel "timeout" maps
to "unknown" — a timed-out action's effects are genuinely
unknown, which is exactly the case the guard exists for.
"cancelled" carries a dispatched flag: a call cancelled
BEFORE dispatch never ran, so it is safe to re-run (the
guard ignores it, exactly like a resolved outcome); a call
cancelled DURING dispatch may have run, so the guard treats
it as unknown (refuse re-execution until verified). The
funnel itself never emits "cancelled" — a funnel dispatch
that is cancelled lands in the worker thread's true outcome;
"cancelled" receipts are written by the cancellation paths
that decide NOT to dispatch (live batch pre-dispatch skip,
and the try_record_cancelled helper) and by the live batch's
mid-execution cancellation path (field-check fix #2, T7 #17,
dispatched=True). Read-only tools never
trigger the guard (re-running a read is always safe) and are
recorded like any other dispatch.

Size bound: the file is pruned on load to the most recent
_MAX_LINES lines (atomic rewrite) — but the dropped rows are
first archived to a dated file under tool_receipts_archive/
next to the live file (D-F2), and the prune event is kept in
sticky state the Activity feed renders, so the loss is never
silent; the in-memory unknown set is the guard's working
state, so the guard never scans the file per dispatch.

Stdlib-only, except the lazy config import for the default
path and the lazy sanitizer import for args previews — both
inside functions, so importing this module never cycles.
"""

from __future__ import annotations  # INTENTIONAL (skills split B17): old module carried it (B1 lesson).

# INTENTIONAL (skills split B17): the old module's stdlib imports,
# re-exported so the facade namespace matches the old module's
# (files3/files2 leaked-import rule). time/Path/Any are also real
# uses of the state section below.
import hashlib  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import tempfile  # noqa: F401
import time  # noqa: F401
import urllib.parse  # noqa: F401
import uuid  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

# INTENTIONAL (skills split B17): re-exports of the moved names.
from .funnel import tool_result as _tool_result
from .tool_receipts_normalize import (
    _SCHEMA_VERSION,
    _DEFAULT_FILENAME,
    _MAX_LINES,
    _MAX_PREVIEW_CHARS,
    _MAX_SUMMARY_CHARS,
    default_receipts_path,
    _canonical_args,
    args_signature,
    _PATH_KEYS,
    _URL_KEYS,
    _RECIPIENT_KEYS,
    _HOST_KEYS,
    normalize_path_value,
    normalize_url_value,
    normalize_recipient_value,
    normalize_host_value,
    _normalized_value,
    normalized_args,
    effect_signatures,
    _attempt_signatures,
    CRITICAL_RECEIPT_TIER,
    _sanitize,
    _validate_compensations,
    _validate_manual_steps,
)
from .tool_receipts_store import (
    ReceiptStore,
    prune_archive_state,
    reset_prune_archive_state,
)

# INTENTIONAL (skills split B17): the process-wide default-store state
# section, VERBATIM from the old module (B2 seam rule). It MUST live
# in the facade, not in an impl module: test_s7_consumers3_threading
# writes/reads _default_store/_store_disabled through the facade
# module object directly, and _get_store/try_* must share those
# exact globals. A state module would silently miss the test's
# writes (proven by the first live neighbour run: 8 failures +
# 1 error, all 'no attempt recorded' / missing temp file).
# ---------------------------------------------------------------------- #
# Process-wide default store (the funnel's hook) + funnel-side helpers.
# Helpers never raise. A failure is still logged once per process
# (no log storm) AND recorded in sticky per-kind state
# (_failures): the funnel reads the counters to tell "this call's
# guard/attempt lookup failed" (fail closed for non-read tools)
# from "receipts are disabled / an old call failed" — a plain
# None return cannot tell those apart, and that ambiguity is
# exactly how the old code failed open on a corrupt store.
# ---------------------------------------------------------------------- #

_default_store: ReceiptStore | None = None
_store_disabled = False
_logged_once = False
_failures: dict[str, dict] = {}


def _log_once(message: str) -> None:
    global _logged_once
    if not _logged_once:
        _logged_once = True
        print(f"[Receipts] {message}")


def _note_failure(kind: str, exc: BaseException) -> None:
    """Sticky record of one funnel-helper failure (Batch 3,
    REL-01/OBS-01): count + last error + first/last time, plus
    the once-per-process log line. Never raises."""
    try:
        now = time.time()
        rec = _failures.get(kind)
        if rec is None:
            rec = {"count": 0, "last_error": "", "first_at": now,
                   "last_at": now}
            _failures[kind] = rec
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["last_error"] = str(exc)
        rec["last_at"] = now
    except Exception:  # noqa: BLE001 — bookkeeping never breaks
        pass
    _log_once(f"receipt {kind} failed: {exc}")


def failure_count(kind: str) -> int:
    """Failures recorded for one helper kind ("guard",
    "attempt", "outcome", "cancelled")."""
    rec = _failures.get(kind)
    return int(rec.get("count", 0)) if rec else 0


def guard_failure_count() -> int:
    """Convenience for the funnel's fail-closed guard check."""
    return failure_count("guard")


def attempt_failure_count() -> int:
    """Convenience for the funnel's dispatch-point mandate."""
    return failure_count("attempt")


def last_receipt_failure(kind: str) -> dict | None:
    """A copy of one kind's sticky failure record, or None."""
    rec = _failures.get(kind)
    return dict(rec) if rec else None


def receipt_failure_remedy(kind: str) -> str:
    """The remedy a receipt-gate refusal should name for the last
    failure of one helper kind (C3-F7, 2026-11-20).

    A full disk (ENOSPC) is not fixed by repairing or rotating
    the receipts file — the remedy is freeing disk space, and the
    refusal must say so. Any other cause (a corrupt receipts
    file, an unreadable store) keeps the repair/rotate remedy,
    which is the right one there; an unknown cause (no sticky
    failure recorded) keeps it too.
    """
    rec = last_receipt_failure(kind)
    err = str(rec.get("last_error", "")) if rec else ""
    if "No space left on device" in err or "[Errno 28]" in err:
        return ("Free up disk space and retry — the disk is full, "
                "so repairing or rotating the receipts file cannot "
                "help.")
    return "Repair or rotate data/tool_receipts.jsonl and retry."


def receipt_degraded_state() -> dict:
    """The whole sticky failure state (copies): which receipt
    sinks have failed in this process, how often, and the last
    error each. Empty dict means no receipt failure yet."""
    return {kind: dict(rec) for kind, rec in _failures.items()}


def reset_receipt_degraded() -> None:
    """Clear sticky failure state + the once-per-process log
    flag. Tests only: production never forgets a failure."""
    global _logged_once
    _failures.clear()
    _logged_once = False


def install_store(path: Path) -> ReceiptStore:
    """Install a store (tests use temp paths). Returns it."""
    global _default_store, _store_disabled
    _default_store = ReceiptStore(path)
    _store_disabled = False
    return _default_store


def reset_default() -> None:
    """Restore the lazy default store (test cleanup): the next
    funnel access creates the production store on demand."""
    global _default_store, _store_disabled
    _default_store = None
    _store_disabled = False


def disable() -> None:
    """Disable funnel receipts for this process (tests)."""
    global _store_disabled
    _store_disabled = True


def _get_store() -> ReceiptStore | None:
    global _default_store, _store_disabled
    if _store_disabled:
        return None
    if _default_store is None:
        try:
            _default_store = ReceiptStore()
        except Exception as exc:  # noqa: BLE001 — never break a call
            _log_once(f"receipt store unavailable: {exc}")
            _store_disabled = True
            return None
    return _default_store


def try_record_attempt(*, tool: str, args: Any,
                       handle_id: str | None = None,
                       snapshot_stamp: str | None = None,
                       compensations: list | None = None,
                       manual_steps: list | None = None) -> dict | None:
    """Best-effort attempt record for the funnel. None when
    receipts are disabled or the store fails."""
    try:
        store = _get_store()
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("attempt", exc)
        return None
    if store is None:
        return None
    try:
        return store.record_attempt(
            tool=tool, args=args, handle_id=handle_id,
            snapshot_stamp=snapshot_stamp, compensations=compensations,
            manual_steps=manual_steps)
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("attempt", exc)
        return None


def try_mark_dispatched(receipt: dict | None) -> bool:
    """Best-effort dispatch mark for the funnel. True when receipts
    are disabled (nothing to mark — vacuous), when the attempt was
    already resolved/marked, or when the mark landed. False only
    when the store failed to record the mark (the funnel fails
    closed on False for non-read calls). Never raises."""
    if receipt is None:
        return True
    try:
        store = _get_store()
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("mark", exc)
        return False
    if store is None:
        return True
    try:
        return store.mark_dispatched(str(receipt.get("receipt_id", "")))
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("mark", exc)
        return False


def try_record_outcome(receipt: dict | None, *, status: str,
                       summary: str = "", dispatched: bool = True) -> bool:
    """Best-effort outcome record for the funnel. Returns True
    when the outcome landed, False when it did not (disabled
    store, or a failure the funnel surfaces as a degraded
    observability flag — never a refusal). A None receipt is
    not a failure: nothing was recorded because nothing was
    attempted, so it returns True."""
    if receipt is None:
        return True
    try:
        store = _get_store()
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("outcome", exc)
        return False
    if store is None:
        return False
    try:
        store.record_outcome(receipt["receipt_id"], status=status,
                             summary=summary, dispatched=dispatched)
        return True
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("outcome", exc)
        return False


def try_record_cancelled(*, tool: str, args: Any,
                         summary: str = "",
                         dispatched: bool = False) -> dict | None:
    """Best-effort cancelled record. By default
    (dispatched=False) for a call cancelled BEFORE dispatch
    (never ran): writes an attempt plus a "cancelled" outcome,
    so the receipt states cancelled explicitly and the guard
    does NOT block a later re-run. With dispatched=True
    (field-check fix #2, T7 #17) for a call cancelled DURING
    execution: the call ran, so the guard treats the outcome
    as unknown and refuses re-execution until verified. Never
    raises; None when receipts are disabled or the store
    fails."""
    try:
        store = _get_store()
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("cancelled", exc)
        return None
    if store is None:
        return None
    try:
        attempt = store.record_attempt(tool=tool, args=args)
        store.record_outcome(attempt["receipt_id"], status="cancelled",
                             summary=summary, dispatched=dispatched)
        return dict(attempt)
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("cancelled", exc)
        return None


def try_find_unknown(*, tool: str, args: Any) -> dict | None:
    """Best-effort unknown-prior lookup for the funnel guard.
    None when no unknown prior exists, when receipts are
    disabled, OR when the store failed — the funnel tells the
    last two apart via guard_failure_count() and FAILS CLOSED
    for non-read tools on a store failure (Batch 3, REL-01:
    the old fail-open reading of None silently disabled the
    guard on a corrupt store). The failure is sticky, not
    just logged once."""
    try:
        store = _get_store()
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("guard", exc)
        return None
    if store is None:
        return None
    try:
        return store.find_unknown(tool=tool, args=args)
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("guard", exc)
        return None


def try_release_unknown(*, tool: str, args_signature: str,
                        summary: str) -> bool:
    """Best-effort REL-03 reconciliation release: a human has
    verified the effect did NOT happen, so the unknown attempt
    for (tool, signature) is recorded as failed and the guard
    stops blocking a re-run. Never raises; False when there is
    no unknown attempt, no store, or the store failed."""
    try:
        store = _get_store()
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("outcome", exc)
        return False
    if store is None:
        return False
    try:
        return store.resolve_unknown(
            tool=tool, args_signature=args_signature, summary=summary)
    except Exception as exc:  # noqa: BLE001 — never break a call
        _note_failure("outcome", exc)
        return False


def funnel_status_to_receipt(status: str, kind: str) -> str:
    """Map a funnel result to a receipt outcome. Any timeout
    signal — genuine status "timeout" OR a timeout kind on a
    normalized refusal — maps to "unknown": a timed-out
    action's effects are genuinely unknown, which is exactly
    the case the guard exists for. This is deliberately more
    conservative than the funnel's own normalization (which
    files legacy error+timeout under "refused"): for receipts,
    unknown effects must never be recorded as refused."""
    if status == "timeout" or kind == "timeout":
        return "unknown"
    if status == "success":
        return "succeeded"
    if status == "failure":
        return "failed"
    if status == "refused":
        return "refused"
    if status == "error":
        return "refused" if kind in ("refused", "not_confirmed",
                                     "validation", "budget") else "failed"
    return "unknown"


def receipt_outcome_for_result(result: Any, *,
                              is_read: bool = False) -> tuple[str, str]:
    """(status, summary) for one funnel result dict (C2-F2).

    The outcome row records the ACT's outcome, not the dispatch:
    a failed act reads "failed" with the cause named, never
    "succeeded" with an empty summary. Non-success statuses use
    the pinned funnel_status_to_receipt mapping, falling back to
    the result text (or a dict payload's reason) when the message
    is empty — a failed act never records an empty summary when
    the cause is known (R2, 2026-11-20). A "success" status is
    refined by the act's own verdict: an explicit Outcome.failed
    verdict -> "failed" (an unverified one stays an honest
    success, B13 — the world was not re-read, so the act is not
    a failure); a dict payload whose own verdict is failure
    (``ok`` False — e.g. a ``cdp-click-failed`` click, a
    ``needs-observation`` pick-first, a route-death pause) ->
    "failed" with the reason named (R2); a plain-string failure
    sentence -> "failed"; anything else -> "succeeded". The
    summary is the result's own message, or the act's report
    string for a successful effectful call (a dict payload's
    ``reason`` / ``report``); a successful READ keeps an empty
    summary — its result is data, not a description, and copying
    it into the receipts file would leak content there.
    """
    if not isinstance(result, dict):
        return "unknown", ""
    status = result.get("status", "")
    kind = result.get("kind", "")
    message = result.get("message")
    message = message if isinstance(message, str) else ""
    payload = result.get("result")
    if isinstance(payload, str):
        text = payload
        payload_dict = None
    elif isinstance(payload, dict):
        # A dict payload carries the act's own verdict and its own
        # report: 'reason' names a failure, 'report' names a
        # landed act (R2 — the classifier used to look only at
        # string results, so a dict failure read "succeeded"
        # with an empty summary).
        text = ""
        payload_dict = payload
    else:
        text = ""
        payload_dict = None
    dict_report = ""
    if payload_dict is not None:
        for _key in ("reason", "report"):
            _value = payload_dict.get(_key)
            if isinstance(_value, str) and _value.strip():
                dict_report = _value
                break
    summary = message or text or dict_report
    if status != "success":
        return funnel_status_to_receipt(status, kind), summary
    verified = result.get("outcome_verified")
    if verified is not None:
        # An explicit Outcome verdict already said exactly what
        # happened; the string heuristic must not override it
        # (the Q1 rule's own principle).
        if verified is False and result.get("outcome_kind") == "failed":
            return "failed", summary
        return "succeeded", "" if is_read else summary
    if payload_dict is not None:
        # The act's own verdict, not the dispatch: ok False
        # means the act did not land (a killed-mid-click
        # cdp-click-failed, a needs-observation pick-first, a
        # route-death pause), and a needs-observation status
        # means the same even without an ok flag. The summary
        # names the reason; a pause reads "failed" because the
        # act definitively did not happen and the attempt must
        # resolve (so the guard allows a later re-run), with the
        # pause reason visible in the summary.
        if payload_dict.get("ok") is False or payload_dict.get(
                "status") == "needs-observation":
            return "failed", dict_report
    if text and _tool_result.is_failure_text(text):
        return "failed", text
    return "succeeded", "" if is_read else summary
