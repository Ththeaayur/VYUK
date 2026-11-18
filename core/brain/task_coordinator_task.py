"""Split of task_coordinator: Task."""

from __future__ import annotations

import uuid
# INTENTIONAL (skills split B43): Task.receipt builds a TaskReceipt;
# the receipt dataclass lives in task_coordinator_steps.
from .task_budget import (
    DEFAULT_TASK_TIME_BUDGET_S,
    new_spend,
    spend_snapshot_for_task,
)
from .task_coordinator_steps import TaskReceipt


class Task:
    """One coordinator-owned task: id, steps, lifecycle state."""

    def __init__(self, title: str, classification: str, steps):
        self.id = uuid.uuid4().hex
        self.title = str(title or "").strip() or "task"
        self.classification = classification
        self.steps = list(steps)
        self.state = "pending"
        self.outcomes: list[StepOutcome] = []
        self._index = 0
        self._paused = False
        self._cancelled = False
        # B22: True only while run_task is inside the B10 goal
        # check (the state reads "completed" there, transiently
        # — the task is not truly complete until the verdict
        # lands). cancel_task honours the marker: a Stop that
        # lands during the goal check cancels the task instead
        # of being refused as "already completed", so the
        # re-plan decision after the check sees the cancel and
        # the task ends cancelled, never re-planned. Never
        # ledger-serialized — it is per-run state, like the
        # verification phase's transient fields.
        self._in_goal_check = False
        # Fix 9: why a cancelled task stopped, when the stop
        # came from a signal rather than the cancel flag (goal-
        # pill failsafe trip, session restart). Empty means the
        # generic reason below. Never ledger-serialized
        # separately — the receipt carries it, and the ledger
        # stores the receipt's reason.
        self._cancel_reason = ""
        # Fix 9: a voice-routine task honours the planner's
        # stop signals (failsafe trip cancels, session-epoch
        # change pauses); see TurnCoordinatorExecutionMixin
        # ._stop_reason / ._pause_reason. Unmarked tasks
        # behave exactly as before.
        self._honor_stop_signals = False
        self._epoch_at_start = None
        # Fix 9: why a paused task paused, when the pause
        # came from a session restart (never ledger-
        # serialized separately — the receipt carries it,
        # and the ledger stores the receipt's reason).
        self._pause_reason = ""
        self._load_error = ""
        self._error_kind = ""
        # Verification phase (transient + recorded): verdicts in
        # step order (see TaskReceipt.verification); before-
        # snapshots keyed by step index, captured while the step
        # ran (never persisted — desktop state, not record
        # content); the failure reason when the phase fails the
        # task (actions succeeded, so no failed outcome carries
        # it — this string is the receipt's reason instead).
        self.verification: list = []
        self._verify_before: dict = {}
        self._verify_error = ""
        # B10 (goal-level verification): the goal check's
        # verdict, or None when no goal check has run yet (the
        # task never reached the end of its action loop with
        # all steps passed). Shape: {"verdict": "met" |
        # "not-met" | "unverifiable", "reason": str}.
        # Serialized to the receipt and the ledger; restored
        # on rebuild (a paused task never ran the check, so
        # it restores as None). receipt() reports None as
        # "unverifiable" with reason "no goal check recorded".
        self._goal_verdict = None
        # Retry-on-not-met history (see VERIFICATION PHASE in
        # the module docstring): one event per not-met verdict
        # (plus a met-reset event when a retried step finally
        # meets its expectation), in order: {"step_index",
        # "tool", "label", "verdict", "reason", "strike",
        # "retried"}. Serialized to the receipt and the ledger
        # (task_ledger.task_to_record "retries"); restored on
        # rebuild, so the strike budget survives a phase re-run
        # and a crash grants no fresh retries. The in-memory
        # resume marker below is deliberately NOT serialized:
        # like the one-shot re-issue flag, a retry-approval
        # pause never carries across a restart (C-14 re-gating).
        self._retries: list = []
        # Second-opinion auto-invoke history (this batch): one
        # event per checked step, in order — see TaskReceipt
        # "second_opinions" for the shape. Serialized to the
        # receipt and the ledger (task_ledger.task_to_record
        # "second_opinions"); restored on rebuild, so a checked
        # step is never checked twice, including across a
        # restart. The pending record itself keeps the existing
        # shape (step, tool, label, sentence, confirm flag) —
        # the sentence carries both brains' views for the
        # dialog; this history is the machine-readable record.
        self._second_opinions: list = []
        # B24 (second-opinion OFF surfacing): the OFF record —
        # {"missing": [...], "line": str} — or None when the
        # check ran (or no step ever needed one). Written once
        # per task, at the first check that would have run
        # when the companion pair was None (see
        # TurnCoordinatorExecutionMixin._record_second_opinion_off).
        # Serialized to the receipt and the ledger
        # (task_ledger.task_to_record "second_opinion_off");
        # restored on rebuild, so a resumed task never
        # records the line twice.
        self._second_opinion_off = None
        # §7 consumers follow-up (retry-on-not-met): the step
        # index of a retry re-run that paused for approval, or
        # None. Set by the verification phase when a retry
        # re-run returns a confirm stop; consumed by the run
        # loop on resume (replace that step's outcome in place
        # and jump to verification — later steps never re-run).
        self._retry_resume_step = None
        # §7 consumers batch 4: raw executor output per step
        # index, for menu steps only — the verification phase
        # passes a menu step's raw output to the menu consequence
        # verifier as invoke-result evidence (the funnel renders
        # engine results as text, so the phase cannot re-derive
        # opened/invoked from the outcome message alone). Never
        # persisted — executor output, not record content.
        self._verify_result: dict = {}
        # §7 consumers batch 6: pending-approval record, or
        # None. Set when a step's engine confirm stop pauses
        # the task (see _run_step / resolve_pending). Shape:
        # {"step_index", "tool", "label", "sentence",
        # "confirm_arg"}. The record names the pending
        # request; it is never itself an approval — only
        # resolve_pending(approved=True), the GUI's
        # authorization outcome, re-issues the step.
        self._pending_approval = None
        # R5 (routine wallpaper steps): pending-monitor
        # record, or None. Set when a wallpaper set step
        # names a monitor that is gone (see _run_step /
        # _detect_wallpaper_monitor_gone). Shape:
        # {"step_index", "tool", "label", "sentence"}.
        # Deliberately NOT _pending_approval: there is
        # nothing to approve, and resolve_pending would
        # fail the step on a deny — the fix is reconnecting
        # the screen and resuming, so this record is only
        # ever cleared by a genuine run of the step or by
        # a terminal cancel.
        self._pending_monitor = None
        # B15 (mid-task clarifying questions): pending-
        # question record, or None. Set when a step that
        # declares an ask reaches _run_step with no recorded
        # answer (see _run_step / resolve_question). Shape:
        # {"step_index", "tool", "label", "question",
        # "options", "answer_key"}. Deliberately NOT
        # _pending_approval: there is nothing to approve —
        # a question needs an ANSWER, not a yes/no, and
        # resolve_pending(approved=False) would FAIL the
        # step. The answer feeds the step on resume; an
        # unanswered question leaves the task paused, never
        # guessed. Serialized to the ledger beside
        # _pending_approval / _pending_monitor; restored on
        # rebuild (a rebuilt task re-presents the question
        # until it is answered).
        self._pending_question = None
        # B15: answered questions, one entry per answered
        # question (see TaskReceipt "questions" for the
        # shape). Serialized to the receipt and the ledger
        # (task_ledger.task_to_record "question_answers");
        # restored on rebuild, so an answered question is
        # never re-asked — convergence is N=1 by
        # construction. Unlike the one-shot approval flag,
        # an answer DOES carry across a restart: an answer
        # is information, not permission, so keeping it
        # cannot smuggle a permission past the human.
        self._question_answers: list = []
        # B21 (crash-safe re-run): one step-run record per
        # dispatched attempt, in dispatch order — the
        # durable intent + outcome pair that makes a crash
        # mid-step answerable. Shape: {"step_run_id": str,
        # "step_index": int, "tool": str, "label": str,
        # "args_digest": str, "status": str, "kind": str,
        # "message": str, "resolution": "recorded" |
        # "reconciled" | "superseded" | None}. "started"
        # with resolution None means the intent is recorded
        # and the outcome is not: the step may or may not
        # have applied, and the resume reconciles it first
        # instead of re-running it blindly. Serialized to
        # the ledger (task_ledger.task_to_record
        # "step_runs", latest entry per step index);
        # restored on rebuild, so a crash grants no fresh
        # attempt id and no silent re-run.
        self._step_runs: list = []
        # B21: pending-reconcile record, or None. Set when
        # a resumed step's intent has no outcome and the
        # world cannot be re-read (no expectation to judge,
        # or the verifier could not judge it) — the task
        # pauses for the HUMAN to decide whether the step
        # applied, instead of guessing. Shape:
        # {"step_index", "tool", "label", "question",
        # "reason"}. Deliberately NOT _pending_approval:
        # there is nothing to approve — the decision is
        # "it applied" or "it did not", resolved only by
        # resolve_reconcile, never by voice (C-14: the
        # channel is the GUI pause surface). Serialized
        # to the ledger beside _pending_approval /
        # _pending_monitor / _pending_question; restored
        # on rebuild (a rebuilt task re-presents the
        # decision until it is made).
        self._pending_reconcile = None
        # §7 consumers batch 6: one-shot re-issue flag, set
        # ONLY by resolve_pending(approved=True) and consumed
        # by the next _run_step of the pending step. Shape:
        # {"step_index", "confirm_arg"}. In-memory only,
        # never ledger-serialized, so a prior approval never
        # carries across a restart.
        self._approval_reissue = None
        # B14 (coordinator time budget + cost accounting): the
        # per-task wall-clock budget (seconds per window; the
        # run_goal 600 s shape is the mechanism default — the
        # owner sets the value), the open window's monotonic
        # start (None when no window is open), the window count
        # (fresh window on every resume, B11 shape), the
        # finished-windows running seconds (pause time is never
        # charged), and the live spend record (elapsed, steps,
        # executions, cloud/local calls — see task_budget).
        # Serialized to the receipt and the ledger; restored on
        # rebuild (a paused task's _budget_started_at is None,
        # so a rebuilt task never inherits a stale window).
        self._time_budget_s = DEFAULT_TASK_TIME_BUDGET_S
        self._budget_started_at = None
        self._budget_windows = 0
        self._spend_elapsed_base = 0.0
        self._spend = new_spend()
        # B4: jump history + per-step jump strikes. _jumps is
        # one event per jump decision (taken or refused) — see
        # TaskReceipt "jumps" for the shape. Serialized to the
        # receipt and the ledger (task_ledger.task_to_record
        # "jumps"), restored on rebuild, so a jump is never
        # lost or double-counted across a restart. _jump_strikes
        # maps step index -> consecutive failures of that step
        # that ended in a jump decision (reset when the step
        # succeeds or skips — the planner's reset-on-met rule);
        # serialized beside the history for the same reason:
        # a crash grants no fresh strikes, and a resumed task
        # strikes out on the same count it had before.
        self._jumps: list = []
        self._jump_strikes: dict = {}
        # B22 (sub-goal decomposition + re-planning): the
        # initial decomposition, or None when the task ran a
        # declared plan. Shape: {"subgoals": [names],
        # "brain": "cloud" | "local", "steps": int}. Set by
        # run_decomposed_goal when the brain's plan lands;
        # never set for a declared plan. Serialized to the
        # receipt and the ledger; restored on rebuild (a
        # resumed task keeps its decomposition — the steps
        # already ran from it).
        self._decomposition = None
        # B22: re-plan history, one event per re-plan attempt
        # that produced a new plan, in order — see
        # TaskReceipt "replans" for the shape. Serialized to
        # the receipt and the ledger; restored on rebuild, so
        # a crash grants no fresh re-plans (the budget counts
        # attempts across the restart, never per run).
        self._replans: list = []
        # B22: the first step index of the most recent
        # re-plan's steps (0 when the task never re-planned).
        # The verification phase verifies only steps from
        # this index on a re-plan pass — the steps that just
        # ran. Older steps were verified when they ran, and
        # re-verifying them could retry a step that already
        # succeeded (the B21 rule: a recorded-outcome step
        # never re-runs). In-memory only, like the retry
        # history's resume marker: a rebuilt task re-runs
        # its phase in full (the resumed-task rule).
        self._replan_base = 0

    def remaining_labels(self) -> list:
        return [s.label for s in self.steps[self._index:]]

    def receipt(self) -> TaskReceipt:
        completed = sum(1 for o in self.outcomes if o.status == "success")
        # B19: a "pending" outcome (a parallel-group branch
        # that paused for a question / approval / monitor —
        # the step ran to a question, not to a verdict) counts
        # as attempted, never as failed: the step did not fail,
        # it waits. A failed count that included it would call
        # a paused task failed. B20: a "cancelled" outcome
        # counts the same way — the step was stopped by the
        # user, not failed; a cancelled task's receipt must
        # read failed=0 (the step that stopped is named in
        # the reason below, not in a failure count).
        failed = sum(1 for o in self.outcomes
                     if o.status not in ("success", "skipped",
                                         "pending", "cancelled"))
        skipped = sum(1 for o in self.outcomes if o.status == "skipped")
        reason = ""
        error_kind = self._error_kind
        if self.state == "cancelled":
            # Fix 9: a signal stop names its signal; a plain
            # cancel keeps the generic reason.
            reason = getattr(self, "_cancel_reason", "") or \
                "cancelled; remaining steps did not run"
        elif self.state == "paused":
            # Fix 9: a session-restart pause names its reason
            # first (the receipt is what the human reads).
            _pause_reason = getattr(self, "_pause_reason", "") or ""
            if _pause_reason:
                reason = _pause_reason
            elif isinstance(getattr(self, "_pending_monitor", None),
                            dict) \
                    and self._pending_monitor.get("tool"):
                # R5: a monitor-gone pause names its sentence
                # first — the receipt itself says which screen
                # is gone and what to do, not "awaiting
                # approval" (there is nothing to approve).
                reason = self._pending_monitor.get("sentence", "")
            elif isinstance(getattr(self, "_pending_question", None),
                            dict) \
                    and self._pending_question.get("question"):
                # B15: a question pause names the question
                # first — the receipt itself says what the
                # task waits on (the full record is on the
                # task + in the ledger). There is nothing to
                # approve; the task waits for an answer.
                _pq = self._pending_question
                reason = ("paused awaiting an answer to "
                          f"{_pq.get('question', '')!r} "
                          f"(step {_pq.get('label', '')!r})")
            elif isinstance(getattr(self, "_pending_reconcile", None),
                            dict) \
                    and self._pending_reconcile.get("question"):
                # B21: a reconcile pause names the decision
                # first — the receipt itself says which step
                # may or may not have applied before the
                # crash, and that the task waits for the
                # human to decide (the full record is on the
                # task + in the ledger). There is nothing to
                # approve and nothing to answer — the
                # decision is "it applied" or "it did not".
                _pr = self._pending_reconcile
                reason = (f"paused — did step "
                          f"{_pr.get('label', '')!r} apply "
                          f"before the crash? "
                          f"({_pr.get('question', '')})")
            else:
                # §7 consumers batch 6: a paused-for-approval
                # task names its pending step, so the receipt
                # itself says what the task waits on (the full
                # record is on the task + in the ledger).
                pending = getattr(self, "_pending_approval", None)
                if isinstance(pending, dict) and pending.get("tool"):
                    reason = ("paused awaiting approval for step "
                              f"{pending.get('label', '')!r} "
                              f"({pending.get('tool')})")
                else:
                    reason = ("paused; resume to continue from "
                              "the next step")
        elif self.state == "failed":
            # B19: a "pending" placeholder is not a failure —
            # it never names the reason a failed task failed.
            failed_outcomes = [o for o in self.outcomes
                               if o.status not in ("success", "skipped",
                                                  "pending")]
            if failed_outcomes:
                last = failed_outcomes[-1]
                reason = f"step {last.label!r} {last.status}" + (
                    f" ({last.kind})" if last.kind else "") + (
                    f": {last.message}" if last.message else "")
                error_kind = error_kind or last.kind
            elif self._verify_error:
                # Failed by the verification phase: every action
                # succeeded, so no failed outcome exists — the
                # phase's reason is the receipt's reason.
                reason = self._verify_error
                error_kind = error_kind or "verification_failed"
            elif self._load_error:
                reason = self._load_error
        elif self._load_error:
            reason = self._load_error
        return TaskReceipt(
            task_id=self.id,
            classification=self.classification,
            title=self.title,
            state=self.state,
            steps=[{"tool": o.tool, "label": o.label, "status": o.status,
                    "kind": o.kind, "message": o.message}
                   for o in self.outcomes],
            attempted=len(self.outcomes),
            completed=completed,
            failed=failed,
            skipped=skipped,
            remaining=self.remaining_labels(),
            reason=reason,
            error_kind=error_kind,
            verification=[dict(v) for v in self.verification],
            # B10: None means no goal check ran — the receipt
            # reports it as "unverifiable", never faked to
            # "met" (same rule as step verdicts).
            goal_verdict=(dict(self._goal_verdict)
                          if isinstance(self._goal_verdict, dict)
                          else {"verdict": "unverifiable",
                                "reason": "no goal check recorded"}),
            retries=[dict(r) for r in getattr(self, "_retries", [])],
            second_opinions=[dict(e) for e in getattr(
                self, "_second_opinions", [])],
            # B24: the OFF record ({} when the check ran or no
            # step ever needed one — the receipt never claims
            # the check ran, and never carries an OFF line for
            # a task whose check ran).
            second_opinion_off=(
                dict(self._second_opinion_off)
                if isinstance(self._second_opinion_off, dict)
                else {}),
            jumps=[dict(j) for j in getattr(self, "_jumps", [])],
            # B15: answered questions (see TaskReceipt
            # "questions" — one entry per answered question,
            # in answer order).
            questions=[dict(q) for q in getattr(
                self, "_question_answers", [])],
            # B22: the initial decomposition ({} when the task
            # ran a declared plan) and the re-plan history
            # (see TaskReceipt "decomposition" / "replans").
            decomposition=(dict(self._decomposition)
                           if isinstance(self._decomposition, dict)
                           else {}),
            replans=[dict(r) for r in getattr(self, "_replans", [])],
            # B14: the task's spend (live elapsed — a running
            # task's receipt reports time spent so far, not the
            # last banked value; see task_budget).
            spend=spend_snapshot_for_task(self),
        )
