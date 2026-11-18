"""Task Ledger — identity-gap audit P2 (v5.95 follow-up).

The P0 coordinator owns task lifecycle in memory and bridges
receipts into the mission binder (``to_mission_receipt``), but
that bridge is a VIEW conversion with no file writer behind it:
a coordinator task that is interrupted by a shutdown leaves no
durable record at all. This module is the durable store.

JOURNAL AUDIT (why a separate file, not a fork)
  * The mission journal (``data/mission_journal_w6.json``,
    ``core/integration_harness_tools.py``) stores DECISION records
    (decision / options / evidence_cited / approved_by) validated
    by ``mission_control.journal_entry``. Task lifecycle
    records (task id, classification, steps, per-step outcomes,
    checkpoints) do not fit that schema; forcing them in would
    either break journal validation or smuggle a parallel schema
    inside the same file — a fork in disguise.
  * The mission binder (``receipts_to_binder``) is an in-memory
    shape, not a store. There is no existing file that carries
    task-level state.
  * So the ledger is its own file in the same family
    (``data/task_ledger.json``, JSON, versioned, atomic
    tmp + ``os.replace`` like the playbook store and the
    scheduler store). CONSISTENCY RULE (single write path):
    the coordinator writes task state ONLY here; the mission
    Receipt remains a derived view (``to_mission_receipt``
    converts from a task, never the reverse). No fact has two
    independent writers: the ledger is the source of truth for
    task state, the mission binder is the evidence view.

RECORD SHAPE (schema version 1)
  One record per task: task id, classification, title, the
  creating turn text (sanitized + capped), brain id (or None
  for unknown), coordinator marker, app version, state, full
  step definitions (sanitized args — needed to resume), per-step
  outcomes as they happen, terminal reason + error kind, and
  created/updated timestamps. A record is written on task
  creation, after EVERY step, and on every terminal/pause/
  cancel transition — so a task interrupted mid-run leaves a
  record of its completed steps, not just its ending. A
  paused-for-approval task (§7 consumers batch 6) also stores
  its pending-approval record (step, tool, label, engine
  sentence, confirm flag name) — the question, never an
  answer: the one-shot re-issue flag is in-memory only and is
  never stored, so a prior approval never carries across a
  restart.

WRITE SAFETY
  * Whole-file JSON, atomic tmp + ``os.replace`` (playbook
    pattern): a torn write leaves the old file intact, so a
    half-written record can never read as a completed task.
    (Atomicity here is replace-atomicity, not power-loss
    durability — no fsync, consistent with the tree's stores.)
  * Lazy load with a once-only guard (scheduler lesson): a
    write before any load first loads the file, so a mutation
    can never wipe persisted records.
  * Corrupt file: load FAILS LOUDLY and all later writes are
    REFUSED until the file is repaired or removed — the ledger
    never overwrites a corrupt file and silently destroys the
    tasks inside it. Ledger failure never breaks tasks: the
    coordinator catches ledger errors and runs in-memory-only.
  * Cap: 200 records; overflow prunes the oldest TERMINAL
    records first. Running/paused records are never pruned.

RESUME POLICY (load-bearing, implemented exactly as stated)
  * Interrupted = a record whose stored state is ``running``,
    ``paused``, or ``verifying``. On startup, interrupted
    tasks are REPORTED (``list_interrupted``), never auto-run.
  * Resume happens only on explicit user action — EXCEPT a
    task whose remaining steps are all pure reads, which MAY
    auto-resume (reads have no effects to duplicate). The
    auto-resume check is a helper (``remaining_all_reads``);
    this module does not itself auto-run anything.
  * Completed steps are NEVER re-executed: rebuild walks
    outcomes in order and advances past success/skipped only.
    A step with any other recorded outcome (including an
    explicit ``unknown``) does NOT advance — it resumes as
    not-completed and is re-decided, never assumed done. A step
    with no outcome (crash mid-step) likewise resumes.
  * C-14 re-gating: a prior approval does NOT carry across a
    restart. Resumed tasks execute through the funnel, whose
    confirm gate asks again for every gated step.

B11 — OPEN-ENDED GOALS SHARE THIS FILE (one durable record,
one home, never a second store). A goal record (kind "goal")
carries the goal text, the picker history, the cumulative
progress, and the budget-window accounting; coordinator
readers skip goal records and goal readers skip task records,
so neither side can mistake the other. A budget stop writes
the goal "paused", not terminal: resuming starts a FRESH
budget window (window step count restarts at 0, window number
+ 1, resume count + 1, all visible in the record) with the
history intact — never an infinite loop (every resume is
explicit user action), never a silently doubled budget (the
windows are counted in the record). A crash mid-goal leaves
the last per-step write with state "running", which is an
interrupted state and resumes the same way.
  * Redaction: step args and messages are sanitized with the
    crash-report sanitizer (``core/distribution_hardening`` — the
    single source of truth for sanitization shapes, reused not
    copied) and capped. FAIL-CLOSED TRADEOFF, stated plainly:
    a step whose args carry a sensitive-keyed value resumes
    with ``[redacted]`` in that arg and will fail rather than
    leak — a redacted step never silently runs with a wrong
    secret.

WHAT THIS MODULE DOES NOT DO
  * No GUI page (shell surfaces wait for the owner's own UI
    changes). The session-layer query (``list_interrupted_tasks``
    in ``task_coordinator``) is the consumer path, tested here.
  * No anticipation/capture, no voiceprint, no payment surface,
    no secrets-fallback change, no rung-5 change.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Fix 9 (voice routines run as coordinator tasks): one
# process-wide lock for the ledger file. Two coordinator
# instances (the session's typed coordinator, a per-goal
# voice coordinator, a per-run voice-routine coordinator)
# keep SEPARATE in-memory caches of the same file — without
# this lock, interleaved upserts clobber each other (last
# write wins, the other's transitions vanish). Every public
# method reloads from disk under this lock first, so each
# write sees every other writer's records.
_LEDGER_LOCK = threading.Lock()

SCHEMA_VERSION = 1
_DEFAULT_FILENAME = "task_ledger.json"
_MAX_RECORDS = 200
_MAX_MESSAGE = 500
_MAX_TURN_TEXT = 1000
_MAX_ARG_BLOB = 4000

# Stored states that count as "interrupted" (not terminal, not
# never-started): a task that was running when the app died
# stays "running" in the file; a paused task stays "paused";
# a task that was mid-verification stays "verifying" (the
# verification phase re-runs in full on resume — verdicts are
# observations, never actions, so re-checking is safe).
INTERRUPTED_STATES = ("running", "paused", "verifying")
_TERMINAL_STATES = ("completed", "failed", "cancelled")

# B11: open-ended goals share this file with coordinator tasks
# (one durable record, one home — never a second store).
# A goal record is a task-shaped record with kind "goal" and no
# fixed steps: the goal text, the picker history, the step
# count, the elapsed time, and the B10 goal verdict ride in
# goal-shaped keys. Coordinator readers (list_interrupted,
# rebuild_task) skip goal records explicitly; goal readers
# (get_goal, list_interrupted_goals, rebuild_goal) skip task
# records the same way, so neither side can mistake the other.
GOAL_KIND = "goal"
_MAX_HISTORY_ENTRIES = 500
_MAX_HISTORY_BLOB = 2000
_MAX_COMPLETED_TOOLS = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cap(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _sanitize(value):
    """Crash-report sanitization, reused not copied.

    Lazy import (distribution_hardening is stdlib-only, but the ledger
    stays import-light and the sanitizer stays a single source
    of truth in distribution_hardening.py).
    """
    from ..distribution_hardening import _sanitize_value
    return _sanitize_value(value)


def _app_version() -> str:
    try:
        from ..version import VERSION
        return str(VERSION)
    except Exception:  # noqa: BLE001 — version is provenance, never a break
        return "unknown"


def default_ledger_path() -> Path:
    """Production ledger path: the app data directory, never a caller path."""
    from ..config import DATA_DIR
    return Path(DATA_DIR) / _DEFAULT_FILENAME


def _advance_index(outcomes, n_steps: int) -> int:
    """Steps completed before a resume point.

    Walks outcomes in order, advancing past success/skipped
    only. Any other recorded outcome (failure, refused, timeout,
    unknown, …) stops the walk: that step and everything after
    it is remaining. A step with no outcome (crash mid-step)
    likewise never advances. This is the single walk shared by
    rebuild and by the interrupted-task summary, so the two can
    never disagree about what remains.
    """
    index = 0
    for outcome in outcomes or []:
        if index >= n_steps:
            break
        if str(outcome.get("status", "")) in ("success", "skipped"):
            index += 1
        else:
            break
    return index


def task_to_record(task, *, turn_text: str = "", brain_id=None,
                   existing: dict | None = None) -> dict:
    """A live coordinator Task as a ledger record (v1).

    Step args are sanitized (sensitive-keyed values become
    [redacted] — see the fail-closed tradeoff in the module
    docstring) and the whole args blob is capped; messages are
    sanitized + capped. ``existing`` (a prior record for the same
    task id) preserves created_at; everything else is fresh.
    Retry-on-not-met history rides along as "retries" (one event
    per not-met verdict, plus a met-reset event when a retried
    step finally meets its expectation); old records without the
    key load as an empty history (getattr default), so no schema
    bump is needed for this additive key.
    """
    steps = []
    for step in task.steps:
        when = None
        if getattr(step, "when", None):
            when = {"tool": str(step.when.get("tool", "")),
                    "args": _sanitize(step.when.get("args", {}) or {})}
        args = _sanitize(getattr(step, "args", {}) or {})
        args_blob = json.dumps(args, default=str)
        if len(args_blob) > _MAX_ARG_BLOB:
            args = {"_truncated": True,
                    "note": "args blob over cap; full args not stored"}
        steps.append({
            "tool": str(step.tool),
            "args": args,
            "label": str(getattr(step, "label", "") or step.tool),
            "on_fail": str(getattr(step, "on_fail", "halt") or "halt"),
            # B4: a jump target must survive the round-trip
            # (1-based, the planner's canonical shape) or a
            # resumed task would silently lose its recovery
            # branch. Old records without the key load as no
            # jump, exactly like a step that never had one.
            "goto": (int(getattr(step, "goto", None)) + 1
                     if getattr(step, "goto", None) is not None
                     else None),
            "when": when,
            "expect": str(getattr(step, "expect", "") or ""),
            # §7 consumers batch 3: a declared step handle must
            # survive a ledger round-trip, or a resumed task
            # would silently lose it (getattr default keeps old
            # records loadable).
            "handle_id": str(getattr(step, "handle_id", "") or ""),
            # Fix 9: a declared B12 result check must survive
            # too — a resumed step whose check was lost would
            # record success where the check would have failed
            # it. Old records without the key load as no check.
            "check": (dict(getattr(step, "check", None) or {})
                      if getattr(step, "check", None) else None),
            # B15: a declared clarifying question must survive
            # too — a resumed step whose ask was lost would
            # run without the information it needs (or ask a
            # question it already answered). Old records
            # without the key load as no ask.
            "ask": ({"question": _cap(_sanitize(
                        str((getattr(step, "ask", None) or {}).get(
                            "question", "") or "")), _MAX_MESSAGE),
                     "options": [_cap(_sanitize(str(o or "")),
                                      _MAX_MESSAGE)
                                 for o in ((getattr(step, "ask", None)
                                            or {}).get("options") or [])],
                     "answer_key": str((getattr(step, "ask", None)
                                        or {}).get("answer_key", "")
                                       or "answer")}
                    if getattr(step, "ask", None) else None),
            # B19: a declared parallel-group label must survive
            # too — a resumed step whose label was lost would
            # run sequentially where the plan said together
            # (and a group that lost a member would fail its
            # own size rule on rebuild). Old records without
            # the key load as no label, exactly like a step
            # that never had one.
            "parallel": str(getattr(step, "parallel", "") or ""),
        })
    outcomes = []
    for outcome in task.outcomes:
        outcomes.append({
            "tool": str(outcome.tool),
            "label": str(outcome.label),
            "status": str(outcome.status),
            "kind": str(outcome.kind or ""),
            "message": _cap(_sanitize(str(outcome.message or "")),
                            _MAX_MESSAGE),
        })
    verification = []
    for verdict in getattr(task, "verification", []) or []:
        if not isinstance(verdict, dict):
            continue
        verification.append({
            "tool": str(verdict.get("tool", "") or ""),
            "label": str(verdict.get("label", "") or ""),
            "expect": _cap(_sanitize(str(verdict.get("expect", "") or "")),
                            _MAX_MESSAGE),
            "verdict": str(verdict.get("verdict", "") or ""),
            "reason": _cap(_sanitize(str(verdict.get("reason", "") or "")),
                            _MAX_MESSAGE),
        })
    # Retry-on-not-met history (see task_coordinator's
    # VERIFICATION PHASE): same sanitize + cap treatment as
    # verification reasons; restored on rebuild so the strike
    # budget survives a phase re-run.
    retries = []
    for event in getattr(task, "_retries", []) or []:
        if not isinstance(event, dict):
            continue
        retries.append({
            "step_index": int(event.get("step_index", 0) or 0),
            "tool": str(event.get("tool", "") or ""),
            "label": _cap(_sanitize(str(event.get("label", "") or "")),
                          _MAX_MESSAGE),
            "verdict": str(event.get("verdict", "") or ""),
            "reason": _cap(_sanitize(str(event.get("reason", "") or "")),
                            _MAX_MESSAGE),
            "strike": int(event.get("strike", 0) or 0),
            "retried": bool(event.get("retried", False)),
        })
    # PF-18 batch 1: permission flip history (same sanitize + cap
    # treatment as retries; restored on rebuild like the
    # neighbouring keys, so a flip is never lost or double-
    # counted across a restart). Old records without the key
    # load as an empty history — no schema bump.
    permission_changes = []
    for event in getattr(task, "_permission_changes", []) or []:
        if not isinstance(event, dict):
            continue
        permission_changes.append({
            "permission_id": str(event.get("permission_id", "") or ""),
            "old_state": str(event.get("old_state", "") or ""),
            "new_state": str(event.get("new_state", "") or ""),
            "source": str(event.get("source", "") or ""),
            "at": str(event.get("at", "") or ""),
        })
    # Second-opinion auto-invoke history (same sanitize + cap
    # treatment as retries; restored on rebuild so a checked
    # step is never checked twice, including across a restart).
    second_opinions = []
    for event in getattr(task, "_second_opinions", []) or []:
        if not isinstance(event, dict):
            continue
        second_opinions.append({
            "step_index": int(event.get("step_index", 0) or 0),
            "tool": str(event.get("tool", "") or ""),
            "label": _cap(_sanitize(str(event.get("label", "") or "")),
                          _MAX_MESSAGE),
            "verdict": str(event.get("verdict", "") or ""),
            "action": str(event.get("action", "") or ""),
            "reason": _cap(_sanitize(str(event.get("reason", "") or "")),
                            _MAX_MESSAGE),
            "answer_a": _cap(_sanitize(str(event.get("answer_a", "") or "")),
                             _MAX_MESSAGE),
            "answer_b": _cap(_sanitize(str(event.get("answer_b", "") or "")),
                             _MAX_MESSAGE),
            "question": _cap(_sanitize(str(event.get("question", "") or "")),
                             _MAX_MESSAGE),
        })
    # B24: the second-opinion OFF record (see Task._second_opinion_off):
    # {"missing": [...], "line": str} — written once, at the first
    # check that would have run. Old records without the key load
    # as empty (no OFF line), so no schema bump is needed for
    # this additive key.
    _off = getattr(task, "_second_opinion_off", None)
    if isinstance(_off, dict) and _off.get("line"):
        second_opinion_off = {
            "missing": [str(m or "") for m in (_off.get("missing") or [])],
            "line": _cap(_sanitize(str(_off.get("line", "") or "")),
                         _MAX_MESSAGE),
        }
    else:
        second_opinion_off = {}
    # B4: jump history (same sanitize + cap treatment as
    # retries; restored on rebuild so a jump is never lost
    # or double-counted across a restart, and the strike
    # budget survives it — a crash grants no fresh jumps).
    # jump_strikes rides beside it for the same reason.
    jumps = []
    for event in getattr(task, "_jumps", []) or []:
        if not isinstance(event, dict):
            continue
        jumps.append({
            "from_index": int(event.get("from_index", 0) or 0),
            "from_tool": str(event.get("from_tool", "") or ""),
            "from_label": _cap(_sanitize(
                str(event.get("from_label", "") or "")), _MAX_MESSAGE),
            "reason": _cap(_sanitize(str(event.get("reason", "") or "")),
                            _MAX_MESSAGE),
            "to_index": int(event.get("to_index", 0) or 0),
            "to_label": _cap(_sanitize(
                str(event.get("to_label", "") or "")), _MAX_MESSAGE),
            "strike": int(event.get("strike", 0) or 0),
            "jumped": bool(event.get("jumped", False)),
        })
    jump_strikes = []
    for index, count in sorted(
            (getattr(task, "_jump_strikes", {}) or {}).items()):
        try:
            jump_strikes.append({
                "step_index": int(index),
                "strikes": int(count or 0),
            })
        except Exception:  # noqa: BLE001 — a bad key never blocks recording
            continue
    # §7 consumers batch 6: a paused-for-approval task stores
    # its pending record (step, tool, label, engine sentence,
    # confirm flag name) so a rebuild restores the question,
    # never an answer — the one-shot re-issue flag is
    # in-memory only and is deliberately NOT stored, so a
    # prior approval never carries across a restart.
    pending = getattr(task, "_pending_approval", None)
    pending_record = None
    if isinstance(pending, dict) and pending.get("tool"):
        pending_record = {
            "step_index": int(pending.get("step_index", 0) or 0),
            "tool": str(pending.get("tool", "") or ""),
            "label": _cap(_sanitize(str(pending.get("label", "") or "")),
                           _MAX_MESSAGE),
            "sentence": _cap(_sanitize(
                str(pending.get("sentence", "") or "")), _MAX_MESSAGE),
            "confirm_arg": str(pending.get("confirm_arg", "") or ""),
        }
    # R5: a monitor-gone pause stores its record beside the
    # approval one (step, tool, label, sentence) so a
    # rebuild restores the reason, never an answer — the
    # step re-resolves its monitor fresh on resume.
    pending_monitor = getattr(task, "_pending_monitor", None)
    pending_monitor_record = None
    if isinstance(pending_monitor, dict) and pending_monitor.get("tool"):
        pending_monitor_record = {
            "step_index": int(pending_monitor.get("step_index", 0) or 0),
            "tool": str(pending_monitor.get("tool", "") or ""),
            "label": _cap(_sanitize(
                str(pending_monitor.get("label", "") or "")),
                _MAX_MESSAGE),
            "sentence": _cap(_sanitize(
                str(pending_monitor.get("sentence", "") or "")),
                _MAX_MESSAGE),
        }
    # B15: a question pause stores its record beside the
    # approval and monitor ones (step, tool, label,
    # question, options, answer key) so a rebuild restores
    # the question, never an answer — the step re-presents
    # the question on resume until it is answered.
    pending_question = getattr(task, "_pending_question", None)
    pending_question_record = None
    if isinstance(pending_question, dict) \
            and pending_question.get("question"):
        pending_question_record = {
            "step_index": int(pending_question.get("step_index", 0) or 0),
            "tool": str(pending_question.get("tool", "") or ""),
            "label": _cap(_sanitize(
                str(pending_question.get("label", "") or "")),
                _MAX_MESSAGE),
            "question": _cap(_sanitize(
                str(pending_question.get("question", "") or "")),
                _MAX_MESSAGE),
            "options": [_cap(_sanitize(str(o or "")), _MAX_MESSAGE)
                        for o in (pending_question.get("options") or [])],
            "answer_key": str(pending_question.get("answer_key", "")
                              or "answer"),
        }
    # B15: answered questions ride along as
    # "question_answers" (one entry per answered question);
    # restored on rebuild so an answered question is never
    # re-asked. Old records without the key load as an empty
    # history — no schema bump.
    question_answers = []
    for entry in getattr(task, "_question_answers", []) or []:
        if not isinstance(entry, dict):
            continue
        question_answers.append({
            "step_index": int(entry.get("step_index", 0) or 0),
            "tool": str(entry.get("tool", "") or ""),
            "label": _cap(_sanitize(
                str(entry.get("label", "") or "")), _MAX_MESSAGE),
            "question": _cap(_sanitize(
                str(entry.get("question", "") or "")), _MAX_MESSAGE),
            "answer": _cap(_sanitize(
                str(entry.get("answer", "") or "")), _MAX_MESSAGE),
            "option": (str(entry.get("option", "") or "")
                       if entry.get("option") is not None else None),
        })
    # B22: the initial decomposition (sub-goal names, which
    # brain planned, step count — {} when the task ran a
    # declared plan) and the re-plan history (one entry per
    # re-plan: attempt, what was tried, what failed, the new
    # sub-goals, the step count, where they start, which
    # brain planned). Old records without the keys load as
    # {} / [] — no schema bump.
    _dec = getattr(task, "_decomposition", None)
    decomposition = {}
    if isinstance(_dec, dict):
        decomposition = {
            "subgoals": [str(s or "") for s in
                         (_dec.get("subgoals") or [])],
            "brain": str(_dec.get("brain", "") or ""),
            "steps": int(_dec.get("steps", 0) or 0),
        }
    replans = []
    for entry in getattr(task, "_replans", []) or []:
        if not isinstance(entry, dict):
            continue
        replans.append({
            "attempt": int(entry.get("attempt", 0) or 0),
            "tried": [str(t or "") for t in (entry.get("tried") or [])],
            "failed_reason": _cap(_sanitize(
                str(entry.get("failed_reason", "") or "")), _MAX_MESSAGE),
            "subgoals": [str(s or "") for s in
                         (entry.get("subgoals") or [])],
            "steps": int(entry.get("steps", 0) or 0),
            "first_step_index": int(
                entry.get("first_step_index", 0) or 0),
            "brain": str(entry.get("brain", "") or ""),
        })
    try:
        receipt = task.receipt()
        reason = receipt.reason or ""
        error_kind = receipt.error_kind or ""
    except Exception:  # noqa: BLE001 — receipt failure never blocks recording
        reason, error_kind = "", ""
    # B10: the goal check's verdict (see task_coordinator_task
    # Task._goal_verdict): {"verdict", "reason"}. A task that
    # never ran the check stores the "no goal check recorded"
    # reason — old records without the key load the same way
    # (rebuild_task's getattr default), so no schema bump is
    # needed for this additive key.
    _goal = getattr(task, "_goal_verdict", None)
    if isinstance(_goal, dict) and _goal.get("verdict"):
        goal_verdict = {
            "verdict": str(_goal.get("verdict", "") or ""),
            "reason": _cap(_sanitize(str(_goal.get("reason", "") or "")),
                           _MAX_MESSAGE),
        }
    else:
        goal_verdict = {"verdict": "unverifiable",
                        "reason": "no goal check recorded"}
    # B14: what the task spent (see task_budget) — elapsed
    # wall time, steps run, executions, cloud calls by leg,
    # local calls, and cost where measured, honest-unknown
    # where not. Old records without the key load as an empty
    # spend (rebuild_task's default), so no schema bump is
    # needed for this additive key.
    from .task_budget import spend_for_record
    try:
        spend = spend_for_record(task)
    except Exception:  # noqa: BLE001 — spend failure never blocks recording
        spend = {"elapsed_s": 0.0, "steps_run": 0, "executions": 0,
                 "cloud_calls": [], "local_calls": 0,
                 "local_seconds": None, "cost_usd": None,
                 "cost_known": True,
                 "cost_note": "spend unavailable"}
    # B21: one step-run record per dispatched attempt (the
    # crash-safe re-run pair: intent before the step runs,
    # outcome after, against the same step-run id). The FULL
    # list is serialized, in dispatch order — the whole
    # history rides the record, so a retried step's old
    # attempt survives to prove the new attempt got a NEW
    # step-run id, and a superseded intent survives to prove
    # it was decided. Reconciliation itself consults only
    # the latest entry for its index (an unresolved entry is
    # always the latest for its index: no new attempt is
    # minted until the old one resolves). Old records
    # B21 (crash-safe re-run): the durable per-step intent +
    # outcome record ("step-runs") — the FULL list, in
    # dispatch order, sanitized + capped. The whole history
    # rides the record (not just the latest entry per step):
    # a retried step's old attempt must survive to prove the
    # new attempt got a NEW step-run id, and a superseded
    # intent must survive to prove it was decided. The
    # reconciliation itself consults only the latest entry
    # for its index (an unresolved entry is always the
    # latest for its index), but the record keeps the audit
    # trail. Malformed entries (no id, or no int index)
    # never reach the file.
    step_runs = []
    for entry in getattr(task, "_step_runs", []) or []:
        if not isinstance(entry, dict):
            continue
        if not entry.get("step_run_id"):
            continue
        try:
            _idx = int(entry.get("step_index"))
        except (TypeError, ValueError):
            continue
        step_runs.append({
            "step_run_id": str(entry.get("step_run_id", "") or ""),
            "step_index": _idx,
            "tool": str(entry.get("tool", "") or ""),
            "label": _cap(_sanitize(
                str(entry.get("label", "") or "")), _MAX_MESSAGE),
            "args_digest": str(entry.get("args_digest", "") or ""),
            "status": str(entry.get("status", "") or ""),
            "kind": str(entry.get("kind", "") or ""),
            "message": _cap(_sanitize(
                str(entry.get("message", "") or "")), _MAX_MESSAGE),
            "resolution": (str(entry.get("resolution", "") or "")
                           if entry.get("resolution") is not None
                           else None),
        })
    # B21: a reconcile pause stores its record beside the
    # approval / monitor / question ones (step, tool, label,
    # question, reason) so a rebuild restores the decision,
    # never an answer — the step is not re-run until the
    # human decides whether it applied.
    pending_reconcile = getattr(task, "_pending_reconcile", None)
    pending_reconcile_record = None
    if isinstance(pending_reconcile, dict) \
            and pending_reconcile.get("question"):
        pending_reconcile_record = {
            "step_index": int(pending_reconcile.get("step_index", 0) or 0),
            "tool": str(pending_reconcile.get("tool", "") or ""),
            "label": _cap(_sanitize(
                str(pending_reconcile.get("label", "") or "")),
                _MAX_MESSAGE),
            "question": _cap(_sanitize(
                str(pending_reconcile.get("question", "") or "")),
                _MAX_MESSAGE),
            "reason": _cap(_sanitize(
                str(pending_reconcile.get("reason", "") or "")),
                _MAX_MESSAGE),
        }
    record = {
        "schema_version": SCHEMA_VERSION,
        "task_id": str(task.id),
        "classification": str(task.classification),
        "title": str(task.title),
        "turn_text": _cap(_sanitize(str(turn_text or "")), _MAX_TURN_TEXT),
        "brain_id": brain_id,
        "coordinator": "task_coordinator/1",
        "app_version": _app_version(),
        "state": str(task.state),
        "steps": steps,
        "outcomes": outcomes,
        "verification": verification,
        "goal_verdict": goal_verdict,
        "spend": spend,
        # B14: the budget value + window count, so a rebuild
        # restores the task's own budget (not the default) and
        # its window accounting. Old records without the keys
        # load as the defaults.
        "time_budget_s": float(
            getattr(task, "_time_budget_s", 600.0) or 600.0),
        "budget_windows": int(
            getattr(task, "_budget_windows", 0) or 0),
        "retries": retries,
        "second_opinions": second_opinions,
        "second_opinion_off": second_opinion_off,
        "permission_changes": permission_changes,
        # B4: jump history + strike counts (restored on
        # rebuild, like retries) and the resume index — the
        # outcome walk alone cannot recover a jumped task's
        # position (it stops at the first failure outcome,
        # while _index points at the jump target), so a
        # record with jumps carries its index explicitly.
        # Old records without the key load via the walk.
        "jumps": jumps,
        "jump_strikes": jump_strikes,
        "index": int(getattr(task, "_index", 0) or 0),
        "pending_approval": pending_record,
        "pending_monitor": pending_monitor_record,
        "pending_question": pending_question_record,
        "pending_reconcile": pending_reconcile_record,
        "step_runs": step_runs,
        "question_answers": question_answers,
        "decomposition": decomposition,
        "replans": replans,
        # Fix 9: a voice-routine task honours the planner's
        # stop signals; the flag must survive a round-trip or
        # a resumed task would silently lose the rule. Old
        # records without the key load as False.
        "honor_stop_signals": bool(
            getattr(task, "_honor_stop_signals", False)),
        "reason": _cap(_sanitize(reason), _MAX_MESSAGE),
        "error_kind": str(error_kind),
        "created_at": (existing or {}).get("created_at") or _now(),
        "updated_at": _now(),
    }
    return record


def _cap_history_entry(entry: dict) -> dict:
    """One picker-history entry, sanitized + capped (B11).

    Entries are data, never code: strings are sanitized with
    the crash-report sanitizer and the whole blob is capped;
    an over-cap entry keeps its event/step identity and drops
    its payload rather than growing the file without bound.
    """
    clean = _sanitize(dict(entry or {}))
    if not isinstance(clean, dict):
        return {"event": "unknown"}
    try:
        blob = json.dumps(clean, default=str)
    except Exception:  # noqa: BLE001 — an unserializable entry is capped, not kept
        blob = ""
    if len(blob) <= _MAX_HISTORY_BLOB:
        return clean
    return {
        "event": str(clean.get("event", "") or "truncated"),
        "step": clean.get("step"),
        "_truncated": True,
        "note": "history entry over cap; payload not stored",
    }


def _cap_executed_step(step: dict) -> dict:
    """B17: one executed step (tool/args/description) as a ledger
    record — sanitized like task args (sensitive-keyed values
    become [redacted]) and capped at _MAX_ARG_BLOB; an over-cap
    step keeps its tool identity and drops its args payload
    rather than growing the file without bound."""
    clean = _sanitize({
        "tool": str(step.get("tool", "") or ""),
        "args": dict(step.get("args") or {}),
        "description": str(step.get("description", "") or "")})
    if not isinstance(clean, dict):
        return {"tool": "", "args": {}, "description": ""}
    clean["description"] = _cap(str(clean.get("description", "") or ""),
                                 _MAX_MESSAGE)
    try:
        blob = json.dumps(clean, default=str)
    except Exception:  # noqa: BLE001 — an unserializable step is capped, not kept
        blob = ""
    if len(blob) <= _MAX_ARG_BLOB:
        return clean
    return {
        "tool": str(clean.get("tool", "") or ""),
        "args": {},
        "description": clean["description"],
        "_truncated": True,
        "note": "executed step over cap; args not stored",
    }


def _cap_plan_step(step: dict) -> dict:
    """§2.5: one approved-plan step {tool, args, label, index}
    as a ledger record — the same sanitise-and-cap as
    _cap_executed_step, keeping the plan's label and index
    (the re-verify reads args by index, U-2)."""
    clean = _sanitize({
        "tool": str(step.get("tool", "") or ""),
        "args": dict(step.get("args") or {}),
        "label": str(step.get("label", "") or ""),
        "index": step.get("index")})
    if not isinstance(clean, dict):
        return {"tool": "", "args": {}, "label": "", "index": None}
    clean["label"] = _cap(str(clean.get("label", "") or ""), _MAX_MESSAGE)
    try:
        blob = json.dumps(clean, default=str)
    except Exception:  # noqa: BLE001 — an unserializable step is capped, not kept
        blob = ""
    if len(blob) <= _MAX_ARG_BLOB:
        return clean
    return {
        "tool": str(clean.get("tool", "") or ""),
        "args": {},
        "label": clean["label"],
        "index": clean.get("index"),
        "_truncated": True,
        "note": "approved plan step over cap; args not stored",
    }


def goal_to_record(state: dict, *, existing: dict | None = None) -> dict:
    """An open-ended goal's live state as a ledger record (B11, v1).

    ``state`` is the run_goal-side dict (goal_id, goal_text,
    history, completed_tools, executed_steps, steps_used, window_steps,
    budget_windows_used, resume_count, next_step, strikes,
    elapsed_s, max_steps, max_seconds, strike_limit, state,
    reason, goal_verdict). ``existing`` (a prior record for the
    same goal id) preserves created_at; everything else is
    fresh. History is capped at _MAX_HISTORY_ENTRIES entries,
    oldest dropped first — a goal that long is still resumable,
    it just does not carry its earliest picks verbatim.
    """
    goal_id = str((state or {}).get("goal_id", "") or "").strip()
    if not goal_id:
        raise ValueError("goal record needs a goal_id")
    goal_text = _cap(_sanitize(str((state or {}).get("goal_text", "") or "")),
                     _MAX_TURN_TEXT)
    history = []
    for entry in (state or {}).get("history") or []:
        if isinstance(entry, dict):
            history.append(_cap_history_entry(entry))
    if len(history) > _MAX_HISTORY_ENTRIES:
        history = history[-_MAX_HISTORY_ENTRIES:]
    completed_tools = []
    for tool in (state or {}).get("completed_tools") or []:
        completed_tools.append(str(tool or ""))
    completed_tools = completed_tools[:_MAX_COMPLETED_TOOLS]
    # B17: the executed steps (tool/args/description) ride the
    # record, so a resumed goal's lesson covers the steps run
    # before the stop as well as after it.
    executed_steps = []
    for step in (state or {}).get("executed_steps") or []:
        if isinstance(step, dict):
            executed_steps.append(_cap_executed_step(step))
    executed_steps = executed_steps[:_MAX_COMPLETED_TOOLS]
    _goal = (state or {}).get("goal_verdict")
    if isinstance(_goal, dict) and _goal.get("verdict"):
        goal_verdict = {
            "verdict": str(_goal.get("verdict", "") or ""),
            "reason": _cap(_sanitize(str(_goal.get("reason", "") or "")),
                           _MAX_MESSAGE),
        }
    else:
        goal_verdict = {"verdict": "unverifiable",
                        "reason": "no goal check recorded"}
    # §2.5 (click-anywhere): the stored plan-approval record
    # {task_id, steps: [{tool, args, label, index}], approved_at}
    # — the re-verify's args source (U-2) and the re-gate's
    # remaining-steps source. It is preserved across upserts
    # from `existing` when the incoming state lacks it (an old
    # goal's plan survives a resume that carries none), and its
    # steps are capped like executed_steps.
    _plan = (state or {}).get("approved_plan")
    approved_plan = None
    if isinstance(_plan, dict) and _plan.get("steps"):
        _plan_steps = [
            _cap_plan_step(s) for s in _plan.get("steps") or []
            if isinstance(s, dict)]
        approved_plan = {
            "task_id": str(_plan.get("task_id", "") or ""),
            "steps": _plan_steps[:_MAX_COMPLETED_TOOLS],
            "approved_at": _plan.get("approved_at"),
        }
    elif existing is not None and existing.get("approved_plan"):
        approved_plan = existing["approved_plan"]
    # in_flight is the dispatch-time marker {tool, digest,
    # step_no, dispatched_at} — STATE-ONLY: it is never filled
    # from `existing`, so a stale marker is never resurrected
    # after a writer clears it. Absent means no step was
    # in-flight when the record was written.
    in_flight = (state or {}).get("in_flight")
    if not isinstance(in_flight, dict):
        in_flight = None
    # M-25: the run mode flags ride the record so a resume
    # preserves the mode the goal started in (preserved from
    # `existing` when the incoming state lacks them, same as
    # approved_plan).
    _strict = (state or {}).get("strict_completion")
    if _strict is None and existing is not None:
        _strict = existing.get("strict_completion")
    _pause_denial = (state or {}).get("pause_on_denial")
    if _pause_denial is None and existing is not None:
        _pause_denial = existing.get("pause_on_denial")
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": GOAL_KIND,
        "task_id": goal_id,
        "title": goal_text,
        "goal_text": goal_text,
        "coordinator": "run_goal/1",
        "app_version": _app_version(),
        "state": str((state or {}).get("state", "running") or "running"),
        "history": history,
        "completed_tools": completed_tools,
        # B17: the executed steps (see goal_to_record above).
        "executed_steps": executed_steps,
        "steps_used": int((state or {}).get("steps_used", 0) or 0),
        "window_steps": int((state or {}).get("window_steps", 0) or 0),
        "budget_windows_used": int(
            (state or {}).get("budget_windows_used", 1) or 1),
        "resume_count": int((state or {}).get("resume_count", 0) or 0),
        "next_step": int((state or {}).get("next_step", 1) or 1),
        "strikes": int((state or {}).get("strikes", 0) or 0),
        "elapsed_s": float((state or {}).get("elapsed_s", 0.0) or 0.0),
        "max_steps": int((state or {}).get("max_steps", 25) or 25),
        "max_seconds": float((state or {}).get("max_seconds", 600.0) or 600.0),
        "strike_limit": int((state or {}).get("strike_limit", 3) or 3),
        "goal_verdict": goal_verdict,
        "reason": _cap(_sanitize(str((state or {}).get("reason", "") or "")),
                       _MAX_MESSAGE),
        "approved_plan": approved_plan,
        "in_flight": in_flight,
        "strict_completion": bool(_strict),
        "pause_on_denial": bool(_pause_denial),
        "created_at": (existing or {}).get("created_at") or _now(),
        "updated_at": _now(),
    }
    return record


class TaskLedger:
    """Durable task records: one JSON file, atomic replace.

    ``path=None`` means the production path (app data dir).
    Tests pass an explicit temp path. All writes are guarded by
    the caller (the coordinator catches ledger errors); a load
    failure (corrupt file) refuses later writes to protect the
    file's contents — see the module docstring.
    """

    def __init__(self, path=None):
        self._path = Path(path) if path is not None else None
        self._records: dict[str, dict] = {}
        self._loaded = False
        self._load_error = ""

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = default_ledger_path()
        return self._path

    @property
    def load_error(self) -> str:
        return self._load_error

    # -- load / save --------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._load_locked()

    def _load_locked(self) -> None:
        """(Re-)read the ledger file into this instance's cache.

        Caller must hold _LEDGER_LOCK. Missing file reads as
        empty; a corrupt file sets _load_error and raises,
        preserving the refusal rule (a corrupt file is never
        overwritten). Called on every public method, so no
        instance ever writes from a stale cache.
        """
        path = self.path
        if not path.exists():
            self._records = {}
            self._load_error = ""
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — corrupt file is loud, not silent
            self._load_error = f"task ledger unreadable at {path}: {exc}"
            self._records = {}
            raise ValueError(self._load_error)
        tasks = payload.get("tasks") if isinstance(payload, dict) else None
        if not isinstance(tasks, list):
            self._load_error = f"task ledger malformed at {path}: no tasks list"
            self._records = {}
            raise ValueError(self._load_error)
        records = {}
        for item in tasks:
            if isinstance(item, dict) and str(item.get("task_id", "")).strip():
                records[str(item["task_id"])] = item
        self._records = records
        self._load_error = ""

    def _save(self) -> None:
        """Atomic whole-file rewrite. Refuses when load failed."""
        self._ensure_loaded()
        if self._load_error:
            raise ValueError(
                f"refusing to overwrite unreadable task ledger: "
                f"{self._load_error}")
        records = list(self._records.values())
        if len(records) > _MAX_RECORDS:
            keep = []
            overflow = len(records) - _MAX_RECORDS
            terminal = [r for r in records
                        if str(r.get("state", "")) in _TERMINAL_STATES]
            terminal.sort(key=lambda r: str(r.get("updated_at", "")))
            drop_ids = {r["task_id"] for r in terminal[:overflow]}
            keep = [r for r in records if r["task_id"] not in drop_ids]
            if len(keep) > _MAX_RECORDS:
                keep = keep[-_MAX_RECORDS:]
            records = keep
            self._records = {r["task_id"]: r for r in records}
        payload = {"version": SCHEMA_VERSION, "tasks": records}
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1, sort_keys=True)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- writes ---------------------------------------------------------------
    def upsert_task(self, task, *, turn_text: str = "",
                    brain_id=None) -> dict:
        """Record a live task (creation, step, or transition).

        Turn text / brain id are provenance from the creating
        turn; on later upserts the caller passes the stored
        values back (the coordinator keeps them on the task),
        so provenance is never blanked by a step write.
        """
        # Fix 9: load-apply-save is one locked step — a
        # concurrent writer's records are read first, so this
        # write cannot clobber them.
        with _LEDGER_LOCK:
            self._ensure_loaded()
            self._load_locked()
            existing = self._records.get(str(task.id))
            record = task_to_record(task, turn_text=turn_text,
                                    brain_id=brain_id, existing=existing)
            self._records[str(task.id)] = record
            self._save()
            return record

    # -- goal records (B11) ---------------------------------------------------
    def upsert_goal(self, state: dict) -> dict:
        """Record an open-ended goal's live state (creation, step, or transition).

        Same locked load-apply-save as upsert_task, so a goal
        write cannot clobber a concurrent task write. The
        record is built by goal_to_record (sanitized + capped);
        created_at is preserved across upserts of the same goal.
        """
        with _LEDGER_LOCK:
            self._ensure_loaded()
            self._load_locked()
            goal_id = str((state or {}).get("goal_id", "") or "").strip()
            if not goal_id:
                raise ValueError("goal record needs a goal_id")
            existing = self._records.get(goal_id)
            if existing is not None \
                    and str(existing.get("kind", "")) != GOAL_KIND:
                raise ValueError(
                    f"record {goal_id} is a coordinator task, not a goal")
            record = goal_to_record(state, existing=existing)
            self._records[goal_id] = record
            self._save()
            return record

    def get_goal(self, goal_id: str) -> dict | None:
        """A stored goal record by id, or None (tasks never match)."""
        with _LEDGER_LOCK:
            self._ensure_loaded()
            self._load_locked()
            record = self._records.get(str(goal_id))
            if record is None \
                    or str(record.get("kind", "")) != GOAL_KIND:
                return None
            return json.loads(json.dumps(record))

    def list_interrupted_goals(self) -> list:
        """Interrupted goals as summaries (report-only, never mutates).

        One summary per goal record whose stored state is
        running or paused: what the goal is, how far it got,
        which budget window it is in, and why it stopped.
        The startup-recovery surface (B16) reads these; nothing
        here auto-runs anything.
        """
        with _LEDGER_LOCK:
            self._ensure_loaded()
            self._load_locked()
            summaries = []
            for record in self._records.values():
                if str(record.get("kind", "")) != GOAL_KIND:
                    continue
                if str(record.get("state", "")) not in INTERRUPTED_STATES:
                    continue
                summaries.append({
                    "goal_id": record.get("task_id"),
                    "goal_text": record.get("goal_text"),
                    # B16 fix (caught by test_startup_recovery):
                    # is_recoverable_goal requires kind "goal",
                    # and raw goal records carry it — but this
                    # summary did not, so NO goal was ever
                    # offered through startup_recovery_check.
                    # Additive computed key only; the stored
                    # record shape does not change.
                    "kind": GOAL_KIND,
                    "state": record.get("state"),
                    "steps_used": int(record.get("steps_used", 0) or 0),
                    "window_steps": int(record.get("window_steps", 0) or 0),
                    "budget_windows_used": int(
                        record.get("budget_windows_used", 1) or 1),
                    "resume_count": int(record.get("resume_count", 0) or 0),
                    "strikes": int(record.get("strikes", 0) or 0),
                    "elapsed_s": float(record.get("elapsed_s", 0.0) or 0.0),
                    "goal_verdict": record.get("goal_verdict"),
                    "reason": record.get("reason", ""),
                    # B16: goal records carry no B14 spend today
                    # (the per-task spend is coordinator-only),
                    # so this is {} and the offer line uses
                    # elapsed_s alone — never an invented cost.
                    "spend": record.get("spend") or {},
                    "updated_at": record.get("updated_at"),
                })
            return summaries

    def rebuild_goal(self, record: dict) -> dict:
        """A stored goal record as resume state for run_goal (B11).

        Terminal records raise (they are history, not
        resumable). The returned state carries the goal text,
        the full history, the cumulative progress (steps used,
        elapsed, strikes, completed tools, next step), and the
        budget window accounting — the resume starts a FRESH
        budget window (window_steps 0, window number + 1), so
        a resumed goal is never charged twice for work done
        before the stop, and the resume count is visible in
        the record. A resume is always explicit user action:
        nothing here auto-runs, and no resume is ever silent.
        """
        if not isinstance(record, dict):
            raise ValueError("ledger record is not a mapping")
        if str(record.get("kind", "")) != GOAL_KIND:
            raise ValueError(
                f"record {record.get('task_id', '?')} is not a goal record "
                f"(use rebuild_task for coordinator tasks)")
        state = str(record.get("state", ""))
        if state in _TERMINAL_STATES:
            raise ValueError(
                f"goal {record.get('task_id', '?')} is {state}, "
                f"not resumable (terminal records are history)")
        if state not in INTERRUPTED_STATES:
            raise ValueError(
                f"goal {record.get('task_id', '?')} has state {state!r}, "
                f"not an interrupted state")
        goal_text = str(record.get("goal_text", "") or "").strip()
        if not goal_text:
            raise ValueError(
                f"goal {record.get('task_id', '?')} has no goal text")
        history = [dict(e) for e in record.get("history") or []
                   if isinstance(e, dict)]
        _goal = record.get("goal_verdict")
        goal_verdict = None
        if isinstance(_goal, dict) and _goal.get("verdict"):
            goal_verdict = {
                "verdict": str(_goal.get("verdict", "") or ""),
                "reason": str(_goal.get("reason", "") or ""),
            }
        return {
            "goal_id": str(record.get("task_id")),
            "goal_text": goal_text,
            "history": history,
            "completed_tools": [str(t) for t in
                                record.get("completed_tools") or []],
            # B17: the executed steps ride the resume, so the
            # lesson covers pre-stop steps as well as post-stop
            # ones (old records carry none — they resume with
            # an empty list, and only post-resume steps are
            # recorded).
            "executed_steps": [dict(s) for s in
                               record.get("executed_steps") or []
                               if isinstance(s, dict)],
            "steps_used": int(record.get("steps_used", 0) or 0),
            # Fresh budget window on resume (see the module
            # note above): the window step count restarts at
            # 0 and the window number advances by one, so the
            # resumed goal is measured from the resume, never
            # charged twice for the stopped window.
            "window_steps": 0,
            "budget_windows_used": int(
                record.get("budget_windows_used", 1) or 1) + 1,
            "resume_count": int(record.get("resume_count", 0) or 0) + 1,
            "next_step": max(1, int(record.get("next_step", 1) or 1)),
            "strikes": int(record.get("strikes", 0) or 0),
            "elapsed_s": float(record.get("elapsed_s", 0.0) or 0.0),
            "max_steps": int(record.get("max_steps", 25) or 25),
            "max_seconds": float(record.get("max_seconds", 600.0) or 600.0),
            "strike_limit": int(record.get("strike_limit", 3) or 3),
            "state": state,
            "reason": str(record.get("reason", "") or ""),
            "goal_verdict": goal_verdict,
            # §2.5: the stored plan rides the resume — the
            # re-verify's args source (U-2) and the re-gate's
            # remaining-steps source. in_flight rides too (the
            # stale dispatch marker the re-verify settles).
            # NOTE (U-1): this return carries NO updated_at —
            # the S-6 clearer reads the pause time from the
            # RECORD's updated_at (list_interrupted_goals
            # summaries carry it), never from here.
            "approved_plan": (
                dict(record["approved_plan"])
                if isinstance(record.get("approved_plan"), dict)
                else None),
            "in_flight": (
                dict(record["in_flight"])
                if isinstance(record.get("in_flight"), dict)
                else None),
            # M-25: the run mode rides the resume, so a resumed
            # goal keeps the mode it started in.
            "strict_completion": bool(record.get("strict_completion")),
            "pause_on_denial": bool(record.get("pause_on_denial")),
        }

    # -- reads ------------------------------------------------------------------
    def get(self, task_id: str) -> dict | None:
        # Fix 9: reads reload under the same lock, so a query
        # never answers from a cache another writer has moved on.
        with _LEDGER_LOCK:
            self._ensure_loaded()
            self._load_locked()
            record = self._records.get(str(task_id))
            return json.loads(json.dumps(record)) \
                if record is not None else None

    def abandon_record(self, task_id: str, *,
                       reason: str = "abandoned by the user") -> bool:
        """Mark a stored interrupted record cancelled, without running it.

        B16 (startup recovery): explicit abandonment is a
        ledger write, never a task execution — the record is
        already interrupted, so there is no live task to
        cancel through the coordinator (after a restart the
        coordinator holds nothing). Only an interrupted
        record (running / paused / verifying) may be
        abandoned: terminal records are history and a missing
        record is a stale offer, so both return False and
        nothing is written. The state change is what takes
        the record out of every future recovery offer; the
        record itself stays in the file as cancelled history.
        True when the record was abandoned, False otherwise.
        """
        with _LEDGER_LOCK:
            self._ensure_loaded()
            self._load_locked()
            record = self._records.get(str(task_id))
            if not isinstance(record, dict):
                return False
            if str(record.get("state", "")) not in INTERRUPTED_STATES:
                return False
            record["state"] = "cancelled"
            record["reason"] = _cap(
                _sanitize(str(reason or "abandoned by the user")),
                _MAX_MESSAGE)
            record["updated_at"] = _now()
            self._save()
            return True

    def all_records(self) -> list:
        with _LEDGER_LOCK:
            self._ensure_loaded()
            self._load_locked()
            return json.loads(json.dumps(list(self._records.values())))

    def list_interrupted(self) -> list:
        """Interrupted tasks as summaries (report-only, never mutates).

        One summary per record whose stored state is running or
        paused: what completed, what remains (via the shared
        outcome walk), and whether the remainder is all pure
        reads (the only case that MAY auto-resume).
        """
        # Fix 9: same locked reload as the other readers.
        with _LEDGER_LOCK:
            self._ensure_loaded()
            self._load_locked()
            return self._list_interrupted_locked()

    def _list_interrupted_locked(self) -> list:
        """list_interrupted body; caller holds _LEDGER_LOCK."""
        summaries = []
        for record in self._records.values():
            # B11: goal records are not coordinator tasks —
            # they surface through list_interrupted_goals,
            # never through the task summary (no steps to
            # walk, no remaining labels to offer).
            if str(record.get("kind", "")) == GOAL_KIND:
                continue
            if str(record.get("state", "")) not in INTERRUPTED_STATES:
                continue
            steps = record.get("steps") or []
            outcomes = record.get("outcomes") or []
            # B4: a jumped task's resume point is its stored
            # index, not the outcome walk (the walk stops at
            # the failure that caused the jump, while the
            # task resumes at the jump target). Same rule as
            # rebuild_task: stored index only when the record
            # carries jump history and the value is in range.
            stored_index = record.get("index")
            if record.get("jumps") and isinstance(stored_index, int) \
                    and not isinstance(stored_index, bool) \
                    and 0 <= stored_index <= len(steps):
                index = stored_index
            else:
                index = _advance_index(outcomes, len(steps))
            completed = sum(1 for o in outcomes
                            if str(o.get("status", "")) in ("success", "skipped"))
            remaining = steps[index:]
            summaries.append({
                "task_id": record.get("task_id"),
                "title": record.get("title"),
                "classification": record.get("classification"),
                "state": record.get("state"),
                "completed": completed,
                "remaining_labels": [str(s.get("label") or s.get("tool", ""))
                                     for s in remaining],
                "remaining_tools": [str(s.get("tool", "")) for s in remaining],
                "remaining_all_reads": remaining_all_reads(remaining),
                # §7 consumers batch 6: a paused-for-approval
                # task names its pending question in the
                # startup-recovery summary, so the GUI can
                # offer the authorization dialog for it.
                "pending_approval": record.get("pending_approval"),
                # B15: a paused-for-question task names its
                # pending question in the same summary, so the
                # GUI can offer the question dialog for it.
                "pending_question": record.get("pending_question"),
                # B16: the recovery offer names why the task
                # stopped and what it spent — the stored
                # reason (receipt reason: pause reason first,
                # then monitor sentence, then question, then
                # approval) and the B14 spend, plus the
                # monitor record for a monitor-gone pause.
                # Additive computed keys only; the stored
                # record shape does not change.
                "pending_monitor": record.get("pending_monitor"),
                "reason": record.get("reason", ""),
                "spend": record.get("spend") or {},
                # B22: the recovery offer names the
                # decomposition and the re-plan history, so a
                # task interrupted mid-re-plan is offered back
                # with its re-plan history named.
                "decomposition": record.get("decomposition") or {},
                "replans": record.get("replans") or [],
                "updated_at": record.get("updated_at"),
                "brain_id": record.get("brain_id"),
            })
        return summaries

    # -- rebuild / resume -------------------------------------------------------
    def rebuild_task(self, record: dict):
        """A stored record as a live Task, paused and ready to resume.

        Terminal records raise (they are history, not resumable).
        The rebuilt task keeps its id, its completed outcomes
        (history — a retried step appends a new outcome beside
        the old one), and an index pointing at the first
        uncompleted step per the shared outcome walk. Unknown
        outcomes do not advance the index: the step resumes as
        not-completed and is re-decided.
        """
        from .task_coordinator import StepDef, StepOutcome, Task
        if not isinstance(record, dict):
            raise ValueError("ledger record is not a mapping")
        # B11: a goal record is not a coordinator task (it has
        # no fixed steps to rebuild) — use rebuild_goal.
        if str(record.get("kind", "")) == GOAL_KIND:
            raise ValueError(
                f"record {record.get('task_id', '?')} is a goal record, "
                f"not a coordinator task (use rebuild_goal)")
        state = str(record.get("state", ""))
        if state in _TERMINAL_STATES:
            raise ValueError(
                f"task {record.get('task_id', '?')} is {state}, "
                f"not resumable (terminal records are history)")
        if state not in INTERRUPTED_STATES:
            raise ValueError(
                f"task {record.get('task_id', '?')} has state {state!r}, "
                f"not an interrupted state")
        raw_steps = record.get("steps") or []
        steps = []
        for raw in raw_steps:
            if not isinstance(raw, dict):
                raise ValueError("ledger step is not a mapping")
            mapping = {"tool": raw.get("tool", ""),
                       "args": raw.get("args", {}) or {},
                       "label": raw.get("label", "") or raw.get("tool", ""),
                       "on_fail": raw.get("on_fail", "halt") or "halt"}
            # B4: restore the jump target (stored 1-based);
            # a missing key means no jump, exactly like an
            # old record. The range check runs below, once
            # the whole list is built.
            if isinstance(raw.get("goto"), int) \
                    and not isinstance(raw.get("goto"), bool) \
                    and raw.get("goto") >= 1:
                mapping["on_fail"] = {"goto": int(raw["goto"])}
            if raw.get("when"):
                mapping["when"] = raw["when"]
            if raw.get("expect"):
                mapping["expect"] = raw["expect"]
            if raw.get("handle_id"):
                mapping["handle_id"] = raw["handle_id"]
            # Fix 9: restore the B12 result check (stored by
            # task_to_record); a missing key means no check,
            # exactly like an old record.
            if isinstance(raw.get("check"), dict) and raw["check"]:
                mapping["check"] = dict(raw["check"])
            # B15: restore the declared clarifying question
            # (stored by task_to_record); a missing key means
            # no ask, exactly like an old record. The
            # from_mapping validation runs again on it, so a
            # malformed stored ask is a malformed record,
            # never a silently dropped question.
            if isinstance(raw.get("ask"), dict) and raw["ask"].get("question"):
                mapping["ask"] = {
                    "question": str(raw["ask"].get("question", "")),
                    "options": list(raw["ask"].get("options") or []),
                    "answer_key": str(raw["ask"].get("answer_key", "")
                                     or "answer"),
                }
            # B19: restore the declared parallel-group label
            # (stored by task_to_record); a missing key means
            # no label, exactly like an old record. The
            # from_mapping validation runs again on it, so a
            # malformed stored label is a malformed record,
            # never a silently dropped group.
            if isinstance(raw.get("parallel"), str) and raw["parallel"]:
                mapping["parallel"] = str(raw["parallel"])
            steps.append(StepDef.from_mapping(mapping))
        # B4: a stored goto outside the plan is a malformed
        # record, not a resumable one — the same range rule
        # as plan-level validation (validate_step_jumps).
        from .task_coordinator_steps import validate_step_jumps
        validate_step_jumps(steps)
        # B19: a stored parallel group that breaks the group
        # rules is a malformed record too — the same plan-level
        # validation as a fresh plan (a group that lost a
        # member, or gained a consequential one, never runs
        # as a group on resume).
        from .task_coordinator_steps import validate_parallel_groups
        validate_parallel_groups(steps)
        task = Task(str(record.get("title", "") or "task"),
                    str(record.get("classification", "") or "routine"),
                    steps)
        task.id = str(record.get("task_id") or uuid.uuid4().hex)
        task._turn_text = str(record.get("turn_text", "") or "")
        task._brain_id = record.get("brain_id")
        outcomes = []
        for raw in (record.get("outcomes") or []):
            if not isinstance(raw, dict):
                continue
            outcomes.append(StepOutcome(
                tool=str(raw.get("tool", "")),
                label=str(raw.get("label", "") or raw.get("tool", "")),
                status=str(raw.get("status", "")),
                kind=str(raw.get("kind", "") or ""),
                message=str(raw.get("message", "") or "")))
        task.outcomes = outcomes
        # Stored verification verdicts are history: a resumed
        # task re-runs the phase in full (observations are safe
        # to repeat), overwriting this list with fresh verdicts.
        task.verification = [dict(v) for v in record.get("verification") or []
                             if isinstance(v, dict)]
        # B10: the goal check's verdict restores with the task
        # (history — a resumed task re-runs the check on
        # completion, overwriting it); old records without the
        # key load as None, exactly like a task that never ran
        # the check (receipt() reports None as "unverifiable"
        # with reason "no goal check recorded").
        _goal = record.get("goal_verdict")
        if isinstance(_goal, dict) and _goal.get("verdict"):
            task._goal_verdict = {
                "verdict": str(_goal.get("verdict", "") or ""),
                "reason": str(_goal.get("reason", "") or ""),
            }
        else:
            task._goal_verdict = None
        # Retry-on-not-met history restores with the task, so a
        # resumed phase counts strikes from history instead of
        # granting fresh retries (a crash must not reset the
        # budget); old records without the key load empty.
        task._retries = [dict(e) for e in record.get("retries") or []
                         if isinstance(e, dict)]
        # Second-opinion history restores with the task, so a
        # checked step is never checked twice — a crash or
        # restart grants no fresh check (and no second pause
        # on the same step); old records without the key load
        # empty.
        task._second_opinions = [dict(e) for e in record.get("second_opinions") or []
                                 if isinstance(e, dict)]
        # B24: the second-opinion OFF record restores with the
        # task, so a resumed task never records the line twice
        # (and a crash between the record and the next write
        # loses nothing — the line is re-recorded at the next
        # high-stakes step only when the record has none).
        # Old records without the key load empty.
        _off = record.get("second_opinion_off")
        if isinstance(_off, dict) and _off.get("line"):
            task._second_opinion_off = {
                "missing": [str(m or "") for m in (_off.get("missing") or [])],
                "line": str(_off.get("line", "") or ""),
            }
        else:
            task._second_opinion_off = None
        # PF-18 batch 1: permission flip history restores with
        # the task, like retries/second_opinions; old records
        # without the key load empty.
        task._permission_changes = [
            dict(e) for e in record.get("permission_changes") or []
            if isinstance(e, dict)]
        # §7 consumers batch 6: a stored pending-approval
        # record restores the question (step, tool, sentence,
        # flag name) — never an answer. The one-shot re-issue
        # flag is not stored, so a rebuilt pending task
        # re-stops on resume until it is resolved again.
        pending = record.get("pending_approval")
        if isinstance(pending, dict) and pending.get("tool"):
            task._pending_approval = {
                "step_index": int(pending.get("step_index", 0) or 0),
                "tool": str(pending.get("tool", "") or ""),
                "label": str(pending.get("label", "") or ""),
                "sentence": str(pending.get("sentence", "") or ""),
                "confirm_arg": str(pending.get("confirm_arg", "") or ""),
            }
        # R5: a stored pending-monitor record restores the
        # reason (step, tool, sentence) — never an answer.
        # Old records without the key load as None.
        pending_monitor = record.get("pending_monitor")
        if isinstance(pending_monitor, dict) \
                and pending_monitor.get("tool"):
            task._pending_monitor = {
                "step_index": int(
                    pending_monitor.get("step_index", 0) or 0),
                "tool": str(pending_monitor.get("tool", "") or ""),
                "label": str(pending_monitor.get("label", "") or ""),
                "sentence": str(
                    pending_monitor.get("sentence", "") or ""),
            }
        # B15: a stored pending-question record restores the
        # question (step, tool, label, question, options,
        # answer key) — never an answer. Old records without
        # the key load as None.
        pending_question = record.get("pending_question")
        if isinstance(pending_question, dict) \
                and pending_question.get("question"):
            task._pending_question = {
                "step_index": int(
                    pending_question.get("step_index", 0) or 0),
                "tool": str(pending_question.get("tool", "") or ""),
                "label": str(pending_question.get("label", "") or ""),
                "question": str(
                    pending_question.get("question", "") or ""),
                "options": [str(o or "")
                            for o in (pending_question.get("options")
                                      or [])],
                "answer_key": str(
                    pending_question.get("answer_key", "")
                    or "answer"),
            }
        # B21: a stored pending-reconcile record restores
        # the decision (step, tool, label, question,
        # reason) — never an answer. Old records without
        # the key load as None.
        pending_reconcile = record.get("pending_reconcile")
        if isinstance(pending_reconcile, dict) \
                and pending_reconcile.get("question"):
            task._pending_reconcile = {
                "step_index": int(
                    pending_reconcile.get("step_index", 0) or 0),
                "tool": str(pending_reconcile.get("tool", "") or ""),
                "label": str(
                    pending_reconcile.get("label", "") or ""),
                "question": str(
                    pending_reconcile.get("question", "") or ""),
                "reason": str(
                    pending_reconcile.get("reason", "") or ""),
            }
        # B21: step-run records restore with the task, so a
        # resumed task reconciles the interrupted attempt
        # instead of re-running it blindly — a crash grants
        # no fresh attempt id and no silent re-run. Old
        # records without the key load empty. Malformed
        # entries are dropped, never honoured: a step-run
        # without an id or an index cannot be reconciled.
        task._step_runs = []
        for entry in record.get("step_runs") or []:
            if not isinstance(entry, dict):
                continue
            if not str(entry.get("step_run_id", "") or "").strip():
                continue
            # The index must be present and int-convertible —
            # a missing index defaults to nothing (never 0:
            # 0 is a real step, and a run filed under the
            # wrong step would reconcile the wrong attempt).
            if "step_index" not in entry:
                continue
            try:
                _sr_index = int(entry["step_index"])
            except (TypeError, ValueError):
                continue
            task._step_runs.append({
                "step_run_id": str(entry.get("step_run_id", "")),
                "step_index": _sr_index,
                "tool": str(entry.get("tool", "") or ""),
                "label": str(entry.get("label", "") or ""),
                "args_digest": str(entry.get("args_digest", "") or ""),
                "status": str(entry.get("status", "") or ""),
                "kind": str(entry.get("kind", "") or ""),
                "message": str(entry.get("message", "") or ""),
                "resolution": (str(entry.get("resolution", "") or "")
                               if entry.get("resolution") is not None
                               else None),
            })
        # B15: answered questions restore with the task, so a
        # resumed task never re-asks a question it already
        # got an answer for — a crash grants no fresh ask.
        # Old records without the key load empty.
        task._question_answers = []
        for entry in record.get("question_answers") or []:
            if not isinstance(entry, dict):
                continue
            task._question_answers.append({
                "step_index": int(entry.get("step_index", 0) or 0),
                "tool": str(entry.get("tool", "") or ""),
                "label": str(entry.get("label", "") or ""),
                "question": str(entry.get("question", "") or ""),
                "answer": str(entry.get("answer", "") or ""),
                "option": (str(entry.get("option", "") or "")
                           if entry.get("option") is not None
                           else None),
            })
        # B22: the initial decomposition and the re-plan
        # history restore with the task, so a resumed task
        # counts re-plans from history instead of granting
        # fresh ones (a crash must not reset the budget),
        # and the picker (B18) sees what was already tried.
        # Old records without the keys load as {} / [].
        # _replan_base restores as 0, not the latest
        # re-plan's base: a resumed task re-runs the
        # verification phase in full (the resumed-task
        # rule), and _replan_base is per-run state for the
        # re-plan pass inside one run_task call — the
        # record keeps each re-plan's first_step_index, so
        # no history is lost.
        _dec = record.get("decomposition")
        task._decomposition = {}
        if isinstance(_dec, dict):
            task._decomposition = {
                "subgoals": [str(s or "") for s in
                             (_dec.get("subgoals") or [])],
                "brain": str(_dec.get("brain", "") or ""),
                "steps": int(_dec.get("steps", 0) or 0),
            }
        task._replans = []
        for entry in record.get("replans") or []:
            if not isinstance(entry, dict):
                continue
            task._replans.append({
                "attempt": int(entry.get("attempt", 0) or 0),
                "tried": [str(t or "") for t in (entry.get("tried") or [])],
                "failed_reason": str(entry.get("failed_reason", "") or ""),
                "subgoals": [str(s or "") for s in
                             (entry.get("subgoals") or [])],
                "steps": int(entry.get("steps", 0) or 0),
                "first_step_index": int(
                    entry.get("first_step_index", 0) or 0),
                "brain": str(entry.get("brain", "") or ""),
            })
        task._replan_base = 0
        # B4: jump history + strike counts restore with the
        # task, so a resumed task counts jumps and strikes
        # from history instead of granting fresh ones (a
        # crash must not reset either budget); old records
        # without the keys load empty.
        task._jumps = [dict(e) for e in record.get("jumps") or []
                       if isinstance(e, dict)]
        task._jump_strikes = {}
        for entry in record.get("jump_strikes") or []:
            if not isinstance(entry, dict):
                continue
            try:
                task._jump_strikes[int(entry.get("step_index", 0) or 0)] = \
                    int(entry.get("strikes", 0) or 0)
            except Exception:  # noqa: BLE001 — a bad entry never blocks rebuild
                continue
        # B4: the resume index. The outcome walk is the rule
        # for tasks that never jumped (it stops at the first
        # failure outcome, which is exactly the resume point
        # for a forward-only task). A jumped task's stored
        # index is authoritative instead: the walk would stop
        # at the failure that caused the jump, while _index
        # points at the jump target — resuming at the walk's
        # index would re-run the failed step and lose the
        # jump. The stored index is used only when the record
        # carries jump history and the value is in range;
        # anything else falls back to the walk.
        stored_index = record.get("index")
        if task._jumps and isinstance(stored_index, int) \
                and not isinstance(stored_index, bool) \
                and 0 <= stored_index <= len(steps):
            task._index = stored_index
        else:
            task._index = _advance_index(
                record.get("outcomes") or [], len(steps))
        # Fix 9: restore the stop-signal rule (stored by
        # task_to_record); old records load as False, exactly
        # like a task that never had it.
        task._honor_stop_signals = bool(
            record.get("honor_stop_signals", False))
        # B14: restore the budget + spend (stored by
        # task_to_record). The budget value restores as set;
        # the window count restores as run (a resumed task
        # opens a fresh window — windows + 1 — so it is
        # measured from the resume, never charged twice for
        # the stopped window); the spend restores with its
        # elapsed as the finished-windows base, and
        # _budget_started_at stays None (a rebuilt task never
        # inherits a stale open window). Old records without
        # the keys load as the defaults, exactly like a task
        # that never had a budget.
        from .task_budget import (
            spend_from_record,
            validate_time_budget,
        )
        try:
            task._time_budget_s = validate_time_budget(
                record.get("time_budget_s"))
        except Exception:  # noqa: BLE001 — a bad budget restores as default
            pass
        try:
            task._budget_windows = int(
                record.get("budget_windows", 0) or 0)
        except (TypeError, ValueError):
            task._budget_windows = 0
        try:
            task._spend = spend_from_record(record)
            task._spend_elapsed_base = float(
                task._spend.get("elapsed_s", 0.0) or 0.0)
        except Exception:  # noqa: BLE001 — a bad spend restores as empty
            pass
        task._budget_started_at = None
        task.state = "paused"
        return task


def remaining_all_reads(remaining_steps) -> bool:
    """True when every remaining step is a pure local read.

    The auto-resume gate: only all-read remainders MAY resume
    without explicit user action (reads have no effects to
    duplicate). A read here means read-class AND locally-only
    per the gate's own predicates; anything else (network
    reads included) is False — a network read is an effect on
    the wire, not a pure local read. Unknown tools are False:
    an unclassifiable step never auto-resumes.
    """
    steps = list(remaining_steps or [])
    if not steps:
        return False
    try:
        from ..funnel.tools_gate import _is_read_class, _is_egress_read  # Phase 3 F1: tools_gate moved to core/funnel/
    except Exception:  # noqa: BLE001 — gate import failure is not a read
        return False
    for step in steps:
        tool = step.get("tool", "") if isinstance(step, dict) else getattr(step, "tool", "")
        tool = str(tool or "")
        if not tool:
            return False
        try:
            if not _is_read_class(tool, {}):
                return False
            if _is_egress_read(tool):
                return False
        except Exception:  # noqa: BLE001 — predicate failure is not a read
            return False
    return True
