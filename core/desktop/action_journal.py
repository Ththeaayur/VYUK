"""§7 item 8 — action journal + recovery (parent draft, NOT the owner's spec).

One journal, one truth: the funnel's receipt store (``core/tool_receipts.py``)
is the journal's spine. This module unifies its readers — it never forks a
second store, never rewrites a record, and never re-executes anything itself.

A journal entry is the definition's shape::

    {entry_id, tool, args_signature, handle_id, snapshot_stamp,
     state: attempted|dispatched|outcome, outcome, evidence,
     compensations[]}

``entry_id`` is the receipt id. ``handle_id`` (item-1 target handle) and
``snapshot_stamp`` (item-2 observation stamp) are EVIDENCE carried on the
entry, never live capabilities: a handle from an earlier session may be
evicted, so resolution always goes through item-1 ``resolve`` on a fresh
observation, never through a journal id. Entries recorded before §7 carry
``None``/``[]`` for the new fields. The journal never stores raw arguments
— only the argument signature (a hash) and the receipt store's sanitized,
truncated preview — and therefore never stores credentials.

Classification (recovery reads the journal, never memory — resume with a
FRESH ``ReceiptStore`` on the path, so a post-crash process sees the file,
not a stale in-memory table):

* ``retryable`` — never dispatched (``attempted``/``unknown`` status,
  ``dispatched=False``). The call never ran; recovery may retry it freely
  as a NEW funnel attempt (a new receipt — the old entry is left untouched).
* ``done`` — outcome known (``succeeded``/``failed``/``refused``, or
  ``cancelled`` before dispatch). Recovery never re-runs it. A
  user-cancelled-before-dispatch entry is ``done`` for recovery even
  though it never ran: re-running an action the user cancelled, without
  being asked again, would defy the cancel. A fresh user-issued call is
  always a new attempt.
* ``unknown`` — dispatched without an outcome (``attempted``/``unknown``
  with ``dispatched=True``, or ``cancelled`` during dispatch). Recovery
  NEVER auto-clears or auto-retries these: they stay blocked until
  ``reconcile`` records what the user confirmed actually happened. This
  is the known R2 follow-up — the user-reachable reconcile for
  during-dispatch unknowns.

Reconcile is a USER-CONFIRMED action: it refuses without a verification
text and a confirmer name, refuses entries that are not unknown, and
appends an outcome record (a superseding entry — the original attempt
line is never rewritten). Verified-absent records ``failed`` (the guard
then releases, so a fresh retry is safe); verified-happened records
``succeeded`` (outcome-known, so recovery never re-runs it — exactly like
any other succeeded call). The signature-based REL-03 ``resolve_unknown``
path still exists for callers without a receipt id; reconcile is the
id-precise form.

Compensations run ONLY from declared compensations, in reverse order,
each verified (executor status ``succeeded``). No declared compensation
means ``no_undo_available`` plus the manual steps — never a guessed
reversal. Effectful compensation asks first (DECISION-NEEDED D5, still
the owner's question): ``run_compensations`` refuses without a
``confirmed_by`` name, so no caller can silently auto-repair effectful
state. The executor in production is the funnel itself, so each step
runs through the gate stack with its own funnel receipt; this module
writes no duplicate records for steps (one truth). D5's draft proposal
(read-only re-observation + never-dispatched retries auto-run;
effectful compensation/repair asks first) is what is built here — silent
auto-repair of effectful state is NOT built.

A corrupt journal line fails LOUDLY (``ValueError`` naming path and
line, REL-01 stance preserved) with a manual repair path from
``check_journal``. There is deliberately NO automatic repair: rewriting
a journal to hide corruption would destroy evidence.

Deliberately NOT built here (consumer follow-ups, named not dropped):
funnel tools (journal read-out, resume, reconcile) and the GUI reconcile
surface; funnel threading of item-1 handle ids and item-2 stamps into
``record_attempt`` (callers that have them pass them today; the funnel
does not yet thread them automatically). The task-level companion is
``core/brain/task_ledger.py`` (interrupted tasks); this journal covers funnel
actions.

Lab honesty: every rule here is drivable headless with temp receipt
files and fake executors. Real crash/restart behaviour on Windows stays
field work and is not claimed here.
"""

from __future__ import annotations

from pathlib import Path

from .. import tool_receipts  # Phase 3 S7a-2: tool_receipts stays flat in core/

# States an entry can be in (definition vocabulary).
STATE_ATTEMPTED = "attempted"
STATE_DISPATCHED = "dispatched"
STATE_OUTCOME = "outcome"

# Classification vocabulary.
CLASS_RETRYABLE = "retryable"
CLASS_DONE = "done"
CLASS_UNKNOWN = "unknown"

# Outcome statuses that mean "the outcome is known".
_KNOWN_OUTCOMES = ("succeeded", "failed", "refused")


def entry_for(attempt: dict) -> dict:
    """The journal view of one receipt-store attempt record.

    Pure function: no I/O, no mutation. Unknown/missing §7 fields
    read as ``None``/``[]`` (pre-§7 records). ``state`` follows the
    definition: ``attempted`` (never dispatched), ``dispatched``
    (dispatched, outcome still unknown — including cancelled DURING
    dispatch, whose effect is genuinely unknown), ``outcome``
    (outcome known, including cancelled BEFORE dispatch)."""
    status = str(attempt.get("status", ""))
    dispatched = bool(attempt.get("dispatched", True))
    if status in _KNOWN_OUTCOMES:
        state = STATE_OUTCOME
        outcome = status
    elif status == "cancelled":
        # Cancelled during dispatch: effect unknown, still blocked.
        # Cancelled before dispatch: outcome known, never ran.
        state = STATE_DISPATCHED if dispatched else STATE_OUTCOME
        outcome = None if dispatched else "cancelled"
    elif dispatched:
        state = STATE_DISPATCHED
        outcome = None
    else:
        state = STATE_ATTEMPTED
        outcome = None
    compensations = attempt.get("compensations")
    manual_steps = attempt.get("manual_steps")
    return {
        "entry_id": str(attempt.get("receipt_id", "")),
        "tool": str(attempt.get("tool", "")),
        "args_signature": str(attempt.get("args_signature", "")),
        "handle_id": attempt.get("handle_id"),
        "snapshot_stamp": attempt.get("snapshot_stamp"),
        "state": state,
        "outcome": outcome,
        "evidence": str(attempt.get("outcome_summary", "")),
        "args_preview": str(attempt.get("args_preview", "")),
        "compensations": [dict(c) for c in compensations]
        if isinstance(compensations, list) else [],
        "manual_steps": [str(s) for s in manual_steps]
        if isinstance(manual_steps, list) else [],
        "dispatched": dispatched,
        "created_at": attempt.get("created_at", 0),
    }


def classify(entry: dict) -> str:
    """Recovery class of a journal entry view."""
    if entry.get("state") == STATE_DISPATCHED:
        return CLASS_UNKNOWN
    if entry.get("state") == STATE_ATTEMPTED:
        return CLASS_RETRYABLE
    return CLASS_DONE


def load_entries(store: tool_receipts.ReceiptStore) -> list[dict]:
    """All journal entries, oldest first. Re-reads the store's
    attempts; callers resuming after a crash must pass a FRESH
    store on the path (a store that already loaded keeps its
    in-memory table). Raises ValueError, loudly, on a corrupt
    journal — never a partial or silently softened list."""
    return [entry_for(a) for a in store.attempts()]


def plan_resume(store: tool_receipts.ReceiptStore) -> dict:
    """Classified resume plan. READ-ONLY: it plans, it never
    executes. Retryable entries are re-executed by the CALLER
    through the funnel as new attempts (new receipts); unknown
    entries are listed for reconcile and never auto-run; done
    entries are never re-run by recovery."""
    entries = load_entries(store)
    plan = {CLASS_RETRYABLE: [], CLASS_DONE: [], CLASS_UNKNOWN: []}
    for entry in entries:
        plan[classify(entry)].append(entry)
    return {
        "retryable": plan[CLASS_RETRYABLE],
        "done": plan[CLASS_DONE],
        "unknown": plan[CLASS_UNKNOWN],
        "counts": {
            "retryable": len(plan[CLASS_RETRYABLE]),
            "done": len(plan[CLASS_DONE]),
            "unknown": len(plan[CLASS_UNKNOWN]),
        },
    }


def summarize(entry: dict) -> str:
    """One-line human read-out of an entry."""
    entry_id = str(entry.get("entry_id", ""))[:8]
    state = entry.get("state", "")
    outcome = entry.get("outcome")
    line = f"{entry_id} {entry.get('tool', '')} — {state}"
    if outcome:
        line += f" ({outcome})"
    if entry.get("evidence"):
        line += f": {entry['evidence']}"
    if entry.get("handle_id"):
        line += f" [handle {entry['handle_id']}]"
    return line


def read_out(store: tool_receipts.ReceiptStore, limit: int = 20) -> dict:
    """Journal read-out for 'what happened to my last task?' —
    newest first, capped. Read-only."""
    if limit < 1:
        raise ValueError("read_out: limit must be at least 1")
    entries = load_entries(store)
    newest = sorted(
        entries, key=lambda e: (e.get("created_at", 0), e["entry_id"]),
        reverse=True)[:limit]
    counts = {CLASS_RETRYABLE: 0, CLASS_DONE: 0, CLASS_UNKNOWN: 0}
    for entry in entries:
        counts[classify(entry)] += 1
    return {"entries": [summarize(e) for e in newest],
            "counts": counts}


def reconcile(store: tool_receipts.ReceiptStore, *, receipt_id: str,
              happened: bool, verification: str,
              confirmed_by: str) -> dict:
    """User-confirmed reconcile of one unknown entry. Refuses
    (ValueError) without a verification text or a confirmer name,
    for an unknown receipt id, and for any entry that is not
    unknown — a never-dispatched attempt is retryable, not
    reconcilable, and an outcome-known entry has nothing to
    reconcile. Appends an outcome record (superseding entry);
    the original attempt line is never rewritten.

    happened=False (verified absent) records ``failed`` — the
    guard releases, so a fresh retry is safe. happened=True
    (verified happened) records ``succeeded`` — outcome-known,
    so recovery never re-runs it. A store load failure raises
    loudly (REL-01: tools fail closed on a corrupt journal)."""
    if not isinstance(verification, str) or not verification.strip():
        raise ValueError(
            "reconcile refuses: verification text is required — "
            "what the user confirmed actually happened (or did not)")
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        raise ValueError(
            "reconcile refuses: confirmed_by is required — "
            "reconcile is a user-confirmed action, never automatic")
    attempts = {str(a.get("receipt_id", "")): a
                for a in store.attempts()}
    attempt = attempts.get(str(receipt_id))
    if attempt is None:
        raise ValueError(
            f"reconcile refuses: no journal entry for receipt "
            f"{receipt_id!r}")
    entry = entry_for(attempt)
    if classify(entry) != CLASS_UNKNOWN:
        raise ValueError(
            f"reconcile refuses: entry {receipt_id!r} is "
            f"{classify(entry)} (state {entry['state']}), not "
            f"unknown — nothing to reconcile")
    if happened:
        status = "succeeded"
        verb = "effect confirmed happened"
    else:
        status = "failed"
        verb = "effect verified absent"
    summary = (f"user-reconciled: {verb}; verified by "
               f"{confirmed_by.strip()}: {verification.strip()}")
    store.record_outcome(str(receipt_id), status=status,
                         summary=summary, dispatched=True)
    return {
        "receipt_id": str(receipt_id),
        "previous_state": entry["state"],
        "new_outcome": status,
        "summary": summary,
        "reconciled_by": confirmed_by.strip(),
    }


def compensation_plan(entry: dict) -> dict:
    """The compensation plan for one entry: declared steps in
    REVERSE declaration order, or ``no_undo_available`` with the
    manual steps when nothing was declared. Never a guessed
    reversal."""
    steps = entry.get("compensations") or []
    if not steps:
        manual = entry.get("manual_steps") or []
        if not manual:
            manual = [
                f"Inspect what {entry.get('tool', 'the action')} "
                f"(receipt {entry.get('entry_id', '')}) changed, "
                "and reverse it by hand — no automatic undo "
                "exists for this action.",
            ]
        return {"status": "no_undo_available", "steps": [],
                "manual_steps": list(manual)}
    return {"status": "declared",
            "steps": [dict(s) for s in reversed(steps)],
            "manual_steps": []}


def run_compensations(store: tool_receipts.ReceiptStore, entry: dict,
                      *, executor, confirmed_by: str) -> dict:
    """Run an entry's declared compensations in reverse order,
    each verified. Effectful compensation asks first (D5): refuses
    (ValueError) without a ``confirmed_by`` name — no silent
    auto-repair of effectful state. ``executor`` is the funnel in
    production (gate stack + its own receipts per step) and a
    fake in tests; it takes one compensation descriptor and
    returns a dict with ``status`` (``succeeded`` counts as
    verified). The first non-succeeded step STOPS the run; later
    steps never execute. No declared compensations means
    ``no_undo_available`` and the executor is never called.
    The runner itself writes no records (one truth: in
    production each step's receipt is the funnel's own)."""
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        raise ValueError(
            "run_compensations refuses: confirmed_by is required — "
            "effectful compensation asks first (D5), never "
            "auto-runs")
    if not callable(executor):
        raise ValueError("run_compensations: executor must be callable")
    plan = compensation_plan(entry)
    if plan["status"] == "no_undo_available":
        return {"status": "no_undo_available",
                "manual_steps": plan["manual_steps"],
                "ran": [], "verified": [], "stopped_at": None,
                "reason": "no declared compensation"}
    ran = []
    verified = []
    for step in plan["steps"]:
        try:
            result = executor(step)
        except Exception as exc:  # noqa: BLE001 — a failing step
            # stops the run and is reported, not hidden.
            ran.append({"step": dict(step), "status": "error",
                        "error": str(exc)})
            return {"status": "stopped", "ran": ran,
                    "verified": verified, "stopped_at": dict(step),
                    "reason": f"executor raised: {exc}"}
        status = str((result or {}).get("status", ""))
        ran.append({"step": dict(step), "status": status})
        if status != "succeeded":
            return {"status": "stopped", "ran": ran,
                    "verified": verified, "stopped_at": dict(step),
                    "reason": f"step not verified (status {status!r})"}
        verified.append(dict(step))
    return {"status": "complete", "ran": ran, "verified": verified,
            "stopped_at": None, "reason": ""}


def check_journal(store: tool_receipts.ReceiptStore) -> dict:
    """Journal health check against a FRESH read of the file
    (never the caller's possibly-stale in-memory table). ``ok``
    False carries the loud load error plus the manual repair
    path; there is NO automatic repair — rewriting a journal to
    hide corruption would destroy evidence, and tools stay
    fail-closed until the check passes."""
    path = Path(store.path)
    try:
        fresh = tool_receipts.ReceiptStore(path=path)
        entries = [entry_for(a) for a in fresh.attempts()]
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "repair_steps": [
                "Stop the assistant so nothing appends to the "
                "journal while you repair it.",
                f"Back up the journal file before touching it: {path}",
                "Open the file at the line named in the error; "
                "the corrupt line is the one that fails to parse "
                "as an attempt/outcome/dispatched record.",
                "Either repair that line back to a valid record "
                "or delete ONLY that line (deleting loses one "
                "entry's evidence — prefer repair).",
                "Re-run check_journal; tools stay fail-closed "
                "until it passes.",
            ],
        }
    counts = {CLASS_RETRYABLE: 0, CLASS_DONE: 0, CLASS_UNKNOWN: 0}
    for entry in entries:
        counts[classify(entry)] += 1
    return {"ok": True, "error": "", "repair_steps": [],
            "entries": len(entries), "counts": counts}
