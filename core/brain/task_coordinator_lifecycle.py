"""Split of task_coordinator: TurnCoordinator lifecycle/task-ops mixin."""

from __future__ import annotations

import datetime
import pathlib

# INTENTIONAL (skills split B43): __init__ defaults the executor to
# _funnel_execute; the refusal family lives in task_coordinator_refusal.
from .task_coordinator_refusal import _funnel_execute
# INTENTIONAL (skills split B43): new_task builds Task; resolve_pending
# and handle_turn use StepOutcome/StepDef/classify_turn; to_mission_receipt
# uses _MISSION_STATUS. All live in sibling split modules.
from .task_coordinator_classify import _MISSION_STATUS, classify_turn
from .task_coordinator_steps import StepDef, StepOutcome
from .task_coordinator_task import Task


class TurnCoordinatorLifecycleMixin:
    def __init__(self, *, executor=None, mission_sink=None, on_step=None,
                 ledger=None, verifier=None, snapshot=None,
                 menu_verifier=None, second_opinion_brains=None,
                 playbook=None):
        self._executor = executor or _funnel_execute
        self._mission_sink = mission_sink
        self._on_step = on_step
        self._ledger = ledger
        # B17 (learning across tasks): the playbook store the
        # completion path writes lessons into. None (the
        # default) means no lesson is written — tests
        # constructing a coordinator directly get no write and
        # no behaviour change; production supplies the shared
        # store through _default_coordinator (the ONE
        # production construction site).
        self._playbook = playbook
        # Verification phase injection (constructor-only, like the
        # executor): None means the defaults — the existing uia
        # producer and its snapshot probe. A caller-supplied
        # verifier has signature (expect_text, before, after,
        # result_text) -> (verdict, reason); a supplied snapshot
        # is () -> snapshot dict | None. Neither is ever allowed
        # to break a task (the phase guards both).
        self._verifier = verifier or self._default_verify
        self._snapshot = snapshot or self._default_snapshot
        # Menu consequence verifier (§7 consumers batch 4):
        # signature (expect_text, before, after, invoke_result)
        # -> (verdict, reason), where invoke_result is the menu
        # step's raw executor output. Defaults to
        # core.desktop.s7_menu_consequence.verify_menu_consequence.
        self._menu_verifier = menu_verifier or self._default_menu_verify
        # Second-opinion auto-invoke (this batch): an optional
        # (brain_a, brain_b) callable pair. None (the default)
        # means OFF — no step is ever checked and behaviour is
        # exactly as before. A supplied pair is validated here,
        # loudly and early: a bad pair must never surface as a
        # mysterious task-loop pause later.
        if second_opinion_brains is not None:
            try:
                _soa, _sob = second_opinion_brains
            except Exception as exc:
                raise ValueError(
                    "second_opinion_brains must be a (brain_a, brain_b) "
                    f"pair of callables: {exc}") from exc
            if not callable(_soa) or not callable(_sob):
                raise ValueError(
                    "second_opinion_brains must be a (brain_a, brain_b) "
                    "pair of callables")
        self._second_opinion_brains = second_opinion_brains
        self._tasks: dict[str, Task] = {}
        # B19 (parallel steps): the lock guarding the task
        # state a parallel-group branch mutates — the B14
        # spend counters (executions, cloud/local calls).
        # Everything else a branch touches is either
        # branch-indexed (verify snapshots, second-opinion
        # history) or sink-carried (pending records), so this
        # lock is the ONLY shared mutation in a group run.
        # Per-coordinator, never global: two coordinators on
        # different tasks never serialize each other.
        import threading as _threading
        self._step_lock = _threading.Lock()

    def _ledger_write(self, task) -> None:
        """Persist one task transition. Ledger failure never breaks tasks."""
        if self._ledger is None:
            return
        try:
            self._ledger.upsert_task(
                task,
                turn_text=getattr(task, "_turn_text", "") or "",
                brain_id=getattr(task, "_brain_id", None))
        except Exception:  # noqa: BLE001 — ledger failure is in-memory-only
            pass

    @staticmethod
    def _log_activity_line(name: str, line: str) -> None:
        """One line in data/activity.log, in the codebase's
        ``timestamp | name | line`` convention (cf.
        core/confirm_gate._log_decision). Never raises: a
        log failure must never change a task. Used for the
        plan-timeout pause — the pause is a visible state,
        not a silent one."""
        try:
            log_path = (pathlib.Path(__file__).resolve().parent.parent.parent
                        / "data" / "activity.log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().isoformat(
                timespec="seconds")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} | {name} | {line}\n")
        except Exception:  # noqa: BLE001 — logging never breaks a task
            pass

    # -- task construction -------------------------------------------------
    def new_task(self, title: str, classification: str, steps, *,
                 turn_text: str = "", brain_id=None,
                 time_budget_s=None) -> Task:
        # B4: a goto outside the plan is rejected at build
        # time, never run (the same range rule the planner
        # applies at parse time). Raises ValueError.
        from .task_coordinator_steps import validate_step_jumps
        validate_step_jumps(steps)
        # B19: a parallel group that breaks the group rules
        # (a group of one, over MAX_PARALLEL_STEPS, or a
        # consequential member) is rejected at build time
        # too — the same plan-level validation as the jump
        # rule, called beside it everywhere a whole plan is
        # assembled (here, handle_turn, ledger rebuild).
        from .task_coordinator_steps import validate_parallel_groups
        validate_parallel_groups(steps)
        task = Task(title, classification, steps)
        # B14: the per-task wall-clock budget (seconds per
        # window). The value is the owner's to set; the
        # mechanism default is the run_goal 600 s shape —
        # anything unusable falls back to it, never to an
        # uncapped task.
        from .task_budget import validate_time_budget
        task._time_budget_s = validate_time_budget(time_budget_s) \
            if time_budget_s is not None \
            else task._time_budget_s
        task._turn_text = str(turn_text or "")
        task._brain_id = brain_id
        self._tasks[task.id] = task
        self._ledger_write(task)
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    # -- lifecycle ---------------------------------------------------------
    def pause_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.state not in ("pending", "running"):
            return False
        task._paused = True
        if task.state == "pending":
            task.state = "paused"
        return True

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        # B22: a task mid-goal-check reads "completed"
        # (transiently — the verdict has not landed yet), so
        # the terminal-state refusal below would refuse a
        # Stop that lands during the check, and the re-plan
        # after it would run against the user's stop. The
        # _in_goal_check marker exempts exactly that window:
        # the flag lands, the re-plan guard sees it, and the
        # task ends cancelled, never re-planned.
        if task is None or (task.state in ("completed", "failed", "cancelled")
                            and not getattr(task, "_in_goal_check", False)):
            return False
        task._cancelled = True
        # B20: cancel the task's LIVE tokens too — a step that
        # is running right now ends at its next yield point
        # instead of at its own timeout. Setting _cancelled
        # alone only stops the task at the next step boundary
        # (the L13 gap itself). The trip listener is the other
        # caller of the same registry (goal pill → failsafe).
        try:
            from .task_cancel import cancel_live_tokens
            cancel_live_tokens(task.id)
        except Exception:  # noqa: BLE001 — teardown never breaks cancel
            pass
        # §7 consumers batch 6: a cancelled task has no pending
        # decision and no armed re-issue — both die with it
        # (a terminal record is never resumable, so neither
        # could be honoured later anyway).
        task._pending_approval = None
        # R5: a cancelled task's monitor pause dies with it
        # too — the screen is never coming back for a task
        # that will never resume.
        task._pending_monitor = None
        # B15: a cancelled task's question pause dies with it
        # too — the question will never be answered for a
        # task that will never resume. (The recorded ANSWERS
        # stay: they are history, like outcomes — only the
        # pending request dies.)
        task._pending_question = None
        # B21: a cancelled task's reconcile decision dies
        # with it too — the decision will never be made for
        # a task that will never resume. (The step-runs
        # stay: they are history, like outcomes — only the
        # pending decision dies.)
        task._pending_reconcile = None
        task._approval_reissue = None
        # Fix 8: a cancelled task's plan approval dies with it
        # (a terminal record is never resumable, so it could
        # never be honoured later anyway).
        try:
            from .. import plan_approval as _pa
            _pa.clear(task.id)
        except Exception:  # noqa: BLE001 — teardown never breaks cancel
            pass
        if task.state in ("pending", "paused"):
            task.state = "cancelled"
        # §7 consumers follow-up (Review-flow cancel): persist
        # the transition like every other one (new_task, the
        # run-loop steps, the confirm-stop pause, both
        # resolve_pending branches). Without this write a
        # paused-task cancel left the ledger showing paused +
        # pending_approval, so Activity kept offering "Needs
        # approval" for a dead task and the cancelled state
        # never reached the record. This is persistence only:
        # no approval semantic changes (deny/approve paths,
        # the one-shot flag, the 60 s rule, and the two-layer
        # protocol are all untouched).
        self._ledger_write(task)
        return True

    def resume_task(self, task_id: str, *, on_step=None) -> TaskReceipt | None:
        task = self._tasks.get(task_id)
        if task is None or task.state != "paused":
            return None
        task._paused = False
        return self.run_task(task, on_step=on_step)

    def pending_approval(self, task_id: str) -> dict | None:
        """The pending-approval record for a paused-for-approval task, or None.

        §7 consumers batch 6: the GUI's authorization dialog
        renders this record (step, tool, label, engine
        sentence verbatim, confirm flag name) and reports the
        user's answer through resolve_pending. A copy:
        mutating it resolves nothing, and it never itself
        counts as approval.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        pending = getattr(task, "_pending_approval", None)
        if not isinstance(pending, dict) or not pending.get("tool"):
            return None
        return dict(pending)

    def resolve_pending(self, task_id: str, *, approved: bool,
                        on_step=None) -> TaskReceipt | None:
        """Resolve a paused-for-approval task. None when the task is not
        paused for approval (unknown id, wrong state, no pending
        record, or a pending step index outside the step list).

        §7 consumers batch 6: the GUI calls this with the
        outcome of its authorization dialog — the existing
        funnel confirm path (the S-1 popup, confirmed 2026-11-03
        under the code-rulings delegation: "Don't allow" default + initial
        focus, explicit "Allow once" required, Details collapsed with Action
        + masked summary, 60 s silence = deny; no four-field format, no
        visible ring, no red edge). The
        dialog's explicit DENY and its silence-timeout both
        arrive here as approved=False. The coordinator approves
        nothing itself: approved=True arms a one-shot re-issue
        of exactly the pending step with exactly its engine
        confirmed flag, and the task continues through the
        normal run loop (threading re-begins, the verification
        phase still runs at the end); the re-issue still passes
        through the funnel with every gate intact, so a
        funnel-gate refusal on re-issue fails the step honestly,
        as any gate refusal does. Control-existence C3
        (2026-11-05): a Family-C (bare-coordinate) step
        re-verifies through the verifier before its approved
        re-issue runs — a failed re-verification refuses the
        step (kind target-stale) instead, and the approval does
        not carry over to a moved target. approved=False fails the step
        honestly (on_fail=skip still skips, and the task then
        continues from the next step). A spoken "yes" never
        reaches this method, and a model-set flag on the step's
        args is never consulted here — only this call resolves,
        and only the GUI calls it. The one-shot is in-memory
        only: a task rebuilt from the ledger after a restart
        carries its pending record but no approval, so it
        re-stops on resume until it is resolved again.
        """
        task = self._tasks.get(task_id)
        if task is None or task.state != "paused":
            return None
        pending = getattr(task, "_pending_approval", None)
        if not isinstance(pending, dict) or not pending.get("tool"):
            return None
        index = pending.get("step_index")
        if not isinstance(index, int) or index < 0 \
                or index >= len(task.steps):
            return None
        step = task.steps[index]
        if not approved:
            task._pending_approval = None
            task._approval_reissue = None
            outcome = StepOutcome(
                tool=step.tool, label=step.label, status="refused",
                kind="approval_denied", message=pending.get("sentence", ""))
            if step.on_fail == "skip":
                outcome.status = "skipped"
            # Retry-on-not-met resume: when the pending step
            # already has an outcome (its stale success from
            # before the retry-pending pause — possible ONLY
            # there in the sequential flow, since a normal
            # pending step was never appended), replacing it
            # keeps outcomes positionally aligned with steps;
            # appending would leave the stale outcome in place
            # and shift every later step's verification onto
            # the wrong outcome. B19: a parallel-group branch
            # that paused ALSO already has an outcome — its
            # "pending" placeholder, placed by the group's
            # join so later siblings' outcomes stay at their
            # own positions. The replace-in-place below is the
            # same rule for both cases; only the resume index
            # differs (see the skipped branch below).
            # The pending-approval protocol itself is unchanged
            # (dialog → resolve → fail/skip); only the
            # bookkeeping for this newly reachable state differs.
            retry_case = (
                index < len(task.outcomes)
                and task.outcomes[index].tool == step.tool
                and task.outcomes[index].label == step.label)
            if retry_case:
                task.outcomes[index] = outcome
            else:
                task.outcomes.append(outcome)
            task._paused = False
            self._ledger_write(task)
            if outcome.status == "skipped":
                # Retry case: later steps already ran, so the
                # loop must NOT continue into them — jump to
                # the verification phase (index past the end
                # ends run_task's loop without a break, so its
                # else branch runs verification in full).
                # B19: the retry case is exactly the step the
                # retry-resume marker names — the marker IS the
                # record that later steps already ran. A
                # parallel-group placeholder is NOT that case:
                # later steps (after the group) have NOT run,
                # so the loop continues from the next step
                # (run_task's skip rule advances past the
                # siblings that already completed). The two
                # cases share the replace-in-place above but
                # must not share the resume index.
                if retry_case and getattr(
                        task, "_retry_resume_step", None) == index:
                    task._retry_resume_step = None
                    task._index = len(task.steps)
                else:
                    task._index = index + 1
                return self.run_task(task, on_step=on_step)
            task.state = "failed"
            self._ledger_write(task)
            return task.receipt()
        # Control-existence C3 (2026-11-05): approval is for a
        # target, not for an action in the abstract. A Family-C
        # (bare-coordinate) step re-verifies through the
        # verifier before its approved re-issue runs: when the
        # capture grid moved since the coordinates were read,
        # the step refuses here — it never re-issues against a
        # dead grid. Family-B steps re-prove in their handlers
        # at dispatch and Family-A steps re-scan at dispatch,
        # so this coordinator check covers only the Family-C
        # gap (the approval-pause staleness sequence). A
        # verification that cannot run (no capture on record,
        # unreadable world) passes through with the
        # geometry-unverified label — it never invents a
        # refusal — so only positive evidence blocks re-issue.
        step_args = step.args or {}
        if step.tool == "cursor_control" or \
                "source_capture_id" in step_args:
            try:
                from ..desktop.control_existence import (
                    verify as _cev_verify)
                from ..desktop import vision as _vision
                from ..desktop import point_crop as _point_crop
                _cev_target = {
                    "kind": "point",
                    "description": step.label or step.tool,
                    "source_capture_id": step_args.get(
                        "source_capture_id")}
                # C7 crop producer (2026-11-05): attach the
                # stored region crop when the step names the
                # retained capture and has one primary point;
                # otherwise the target carries no crop and
                # behaves exactly as before (honest absence).
                _cev_crop = _point_crop.stored_crop_for_step(
                    step.tool, step_args, _vision.last_sent_frame())
                if _cev_crop is not None:
                    _cev_target["stored_crop"] = _cev_crop
                _verdict = _cev_verify(
                    _cev_target,
                    {"capture_info": _vision.last_capture_info()})
            except Exception:  # noqa: BLE001 — unverifiable passes
                _verdict = None
            if _verdict is not None and not _verdict.get("ok"):
                _cev_refusal = _verdict.get("refusal") or {}
                _cev_text = _cev_refusal.get("text") or \
                    "the target moved since approval."
                task._pending_approval = None
                task._approval_reissue = None
                task.verification.append({
                    "tool": step.tool, "label": step.label,
                    "expect": "", "verdict": "refused",
                    "reason": _cev_text})
                outcome = StepOutcome(
                    tool=step.tool, label=step.label,
                    status="refused", kind="target-stale",
                    message=_cev_text)
                if step.on_fail == "skip":
                    outcome.status = "skipped"
                retry_case = (
                    index < len(task.outcomes)
                    and task.outcomes[index].tool == step.tool
                    and task.outcomes[index].label == step.label)
                if retry_case:
                    task.outcomes[index] = outcome
                else:
                    task.outcomes.append(outcome)
                task._paused = False
                self._ledger_write(task)
                if outcome.status == "skipped":
                    task._retry_resume_step = None
                    task._index = len(task.steps) \
                        if retry_case else index + 1
                    return self.run_task(task, on_step=on_step)
                task.state = "failed"
                self._ledger_write(task)
                return task.receipt()
        task._approval_reissue = {
            "step_index": index,
            "confirm_arg": pending.get("confirm_arg", ""),
        }
        task._pending_approval = None
        task._paused = False
        return self.run_task(task, on_step=on_step)

    def pending_question(self, task_id: str) -> dict | None:
        """The pending-question record for a paused task, or None.

        B15: the GUI's question dialog renders this record
        (step, tool, label, question verbatim, declared
        options, answer key) and reports the user's answer
        through resolve_question. A copy: mutating it
        answers nothing, and it never itself counts as an
        answer.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        pending = getattr(task, "_pending_question", None)
        if not isinstance(pending, dict) or not pending.get("question"):
            return None
        return dict(pending)

    def pending_reconcile(self, task_id: str) -> dict | None:
        """The pending crash-reconcile decision for a paused task, or None.

        B21 (crash-safe re-run): the GUI's reconcile dialog
        renders this record (step, tool, label, the question
        "Did step '<label>' apply before the crash?" and the
        reason the world could not be re-read) and reports
        the human's decision through resolve_reconcile. A
        copy: mutating it decides nothing, and it never
        itself counts as a decision.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        pending = getattr(task, "_pending_reconcile", None)
        if not isinstance(pending, dict) or not pending.get("question"):
            return None
        return dict(pending)

    def resolve_reconcile(self, task_id: str, applied,
                          on_step=None) -> TaskReceipt | None:
        """Resolve a paused-for-reconcile task with the human's decision.

        B21 (crash-safe re-run): None when the task is not
        paused for a reconcile (unknown id, wrong state, no
        pending record, or a pending step index outside the
        step list) — or when the decision is not a bool:
        a non-bool decides nothing, the task stays paused
        with the record still pending, and no outcome is
        recorded. The decision is never guessed, never
        defaulted, never auto-answered — only a human
        True/False moves the task. applied=True records a
        reconciled success outcome for the step and resumes
        through the normal run loop (the loop's skip-past
        advances over it, so the step never runs);
        applied=False supersedes the stale intent and the
        step re-runs fresh with a NEW step-run id. In both
        cases the pending record clears and the ledger is
        written before the resume, so a crash during the
        resume re-reconciles the same way instead of
        deciding twice. A spoken decision never reaches
        this method (C-14: the channel is the GUI pause
        surface, never voice), and only the GUI calls it.
        """
        task = self._tasks.get(task_id)
        if task is None or task.state != "paused":
            return None
        pending = getattr(task, "_pending_reconcile", None)
        if not isinstance(pending, dict):
            return None
        if not isinstance(applied, bool):
            return None
        index = pending.get("step_index")
        if not isinstance(index, int) or not 0 <= index < len(task.steps):
            return None
        step = task.steps[index]
        run = self._pending_step_run(task, index)
        if applied:
            # The human says it applied: record a reconciled
            # success, resolve the intent, and resume — the
            # step never runs.
            outcome = StepOutcome(
                tool=step.tool, label=step.label,
                status="success", kind="reconciled",
                message="reconciled by human decision: "
                        "the step applied before the crash")
            if index < len(task.outcomes) \
                    and task.outcomes[index].tool == step.tool \
                    and task.outcomes[index].label == step.label:
                task.outcomes[index] = outcome
            else:
                task.outcomes.append(outcome)
            self._resolve_step_run(
                task, run, "reconciled", "success", "reconciled",
                "reconciled by human decision: "
                "the step applied before the crash")
        else:
            # The human says it did not apply: supersede the
            # stale intent and resume — the step re-runs
            # fresh with a NEW step-run id.
            self._resolve_step_run(
                task, run, "superseded", "started", "",
                "human decision: the step did not apply before "
                "the crash — re-run it")
        task._pending_reconcile = None
        task._paused = False
        self._ledger_write(task)
        return self.run_task(task, on_step=on_step)

    def resolve_question(self, task_id: str, answer,
                         on_step=None) -> TaskReceipt | None:
        """Resolve a paused-for-question task with the human's answer.

        B15: None when the task is not paused for a question
        (unknown id, wrong state, no pending record, or a
        pending step index outside the step list) — or when
        the answer is empty: an empty answer resolves
        nothing, the task stays paused with the question
        still pending, and no outcome is recorded. A
        question is never auto-answered, never defaulted,
        never guessed — only a non-empty human answer moves
        the task. The answer is recorded in
        task._question_answers (one entry per step index,
        replaced — never appended twice — so an answered
        question is never re-asked: convergence is N=1 by
        construction, including across a restart, because
        the answer is ledger-serialized unlike the one-shot
        approval flag). When the answer exactly matches one
        of the step's declared options it is recorded as
        that option; any other non-empty text is recorded
        as a free-text answer with option None. The pending
        record clears, the task resumes through the normal
        run loop, and the step re-runs with the answer
        injected into its args under its answer_key (the
        step still gates normally — an answer is
        information, never approval). A spoken answer never
        reaches this method (C-14: the channel is the GUI
        pause surface, never voice), and a model-set value
        in the step's args is never consulted here — only
        this call resolves, and only the GUI calls it.
        """
        task = self._tasks.get(task_id)
        if task is None or task.state != "paused":
            return None
        pending = getattr(task, "_pending_question", None)
        if not isinstance(pending, dict) or not pending.get("question"):
            return None
        index = pending.get("step_index")
        if not isinstance(index, int) or index < 0 \
                or index >= len(task.steps):
            return None
        if not isinstance(answer, str) or not answer.strip():
            return None
        answer = answer.strip()
        step = task.steps[index]
        options = pending.get("options") or []
        option = answer if answer in options else None
        entry = {
            "step_index": index,
            "tool": step.tool,
            "label": step.label,
            "question": str(pending.get("question", "")),
            "answer": answer,
            "option": option,
        }
        answers = [a for a in (getattr(task, "_question_answers", None)
                               or [])
                   if isinstance(a, dict)
                   and a.get("step_index") != index]
        answers.append(entry)
        task._question_answers = answers
        task._pending_question = None
        task._paused = False
        self._ledger_write(task)
        return self.run_task(task, on_step=on_step)

    def resume_ledger_task(self, record, *, ledger=None,
                           on_step=None) -> TaskReceipt | None:
        """Resume an interrupted ledger record from its next uncompleted step.

        The record must be an interrupted (running/paused) ledger
        record; terminal records raise ValueError from the rebuild
        (they are history, not resumable). A supplied ledger is
        adopted for subsequent writes when this coordinator has
        none. Completed steps are never re-executed (the rebuild
        index points past them); unknown-outcome steps resume as
        not-completed. Gated steps re-gate through the funnel —
        a prior approval never carries across a restart.
        """
        from .task_ledger import TaskLedger
        if ledger is not None and self._ledger is None:
            self._ledger = ledger
        task = TaskLedger().rebuild_task(record)
        task.state = "paused"
        self._tasks[task.id] = task
        return self.resume_task(task.id, on_step=on_step)

    def receipt(self, task_id: str) -> TaskReceipt | None:
        task = self._tasks.get(task_id)
        return task.receipt() if task is not None else None

    # -- turn handling -----------------------------------------------------
    def handle_turn(self, text: str, *, plan_names=(), routine_names=(),
                    plan_steps_for=None, routine_steps_for=None,
                    brain_id=None, plan_hook=None,
                    task_flags: dict | None = None) -> dict:
        """Classify a turn; locally execute resolvable plan/routine turns.

        Returns {"handled": bool, "classification": TurnClassification,
        "task": Task|None, "receipt": TaskReceipt|None, "error": str}.
        Non-task turns (conversation, single_action, free-text
        multi_step) are NEVER handled here — they flow to the live
        session unchanged. A resolvable name whose steps cannot be
        loaded is handled as a FAILED task with a receipt naming
        the failure, never silently passed to the model.

        Capability-contract integration (P1, one decision point):
        a free-text multi_step fall-through ALSO carries a "routing"
        record — the brain id the turn is going to (when known) and
        whether THAT brain can run a free-form goal loop, read from
        core/brain_capabilities.py. Voice-brain adoption: for the
        voice brain this is the RUNTIME answer (bridge enabled +
        text brain injected), not just the static declaration — so
        in the default state (bridge OFF) gemini_live still routes
        False here, exactly as before. The routing does NOT change:
        local decomposition of free text is model work no brain
        provides through the coordinator, so the turn flows to the
        live session on every brain; the record exists so the
        routing decision is contract-checked instead of assumed,
        and so the voice-brain adoption work has a seam to read.
        brain_id=None (unknown) records None for the loop answer —
        an unknown brain claims no capability. Conversation/
        single_action returns are unchanged (no routing key);
        handled plan/routine turns are coordinator-local and
        brain-independent, so they carry none either.
        task_flags (Fix 9): per-run flags, currently only
        {"honor_stop_signals": True} (voice-routine tasks
        honour the planner's stop signals — a goal-pill
        failsafe trip cancels, a session-epoch change
        pauses); unknown flags are ignored.
        """
        cls = classify_turn(text, plan_names=plan_names,
                            routine_names=routine_names)
        if cls.kind not in ("saved_plan", "routine"):
            result = {"handled": False, "classification": cls, "task": None,
                      "receipt": None, "error": ""}
            if cls.kind == "multi_step":
                try:
                    from .. import brain_capabilities as _bc
                    _now = getattr(_bc, "can_freeform_goal_now", None)
                    if callable(_now):
                        loop = _now(brain_id)
                    else:
                        loop = _bc.can_freeform_goal(brain_id)
                except Exception:  # noqa: BLE001 — contract failure is unknown, never a break
                    loop = None
                result["routing"] = {
                    "brain_id": brain_id,
                    "live_brain_freeform_goal": loop,
                }
            return result
        resolver = plan_steps_for if cls.kind == "saved_plan" else routine_steps_for
        raw_steps = None
        load_error = ""
        if resolver is None:
            load_error = f"no {cls.kind} resolver available"
        else:
            try:
                raw_steps = resolver(cls.name)
            except Exception as exc:  # noqa: BLE001 — resolver failure is a task failure
                load_error = f"could not load {cls.kind} {cls.name!r}: {exc}"
        task = self.new_task(text, cls.kind, [], turn_text=text,
                             brain_id=brain_id)
        # Fix 9: per-run task flags from the caller. The only
        # flag today is "honor_stop_signals" (the voice-
        # routine handler marks its tasks so the coordinator
        # honours the planner's stop signals — goal-pill
        # failsafe trip, session-epoch change). Unknown flags
        # are ignored; a flag never changes a task's steps,
        # only its run-time rules. Applied before the load
        # check so even a failed-load task carries it (it
        # never runs, so the flag is inert there).
        if isinstance(task_flags, dict) \
                and task_flags.get("honor_stop_signals"):
            task._honor_stop_signals = True
        if load_error or not raw_steps:
            task._load_error = load_error or (
                f"{cls.kind} {cls.name!r} has no steps")
            task._error_kind = "unavailable"
            task.state = "failed"
            self._ledger_write(task)
            return {"handled": True, "classification": cls, "task": task,
                    "receipt": task.receipt(), "error": task._load_error}
        try:
            steps = [StepDef.from_mapping(m) for m in raw_steps]
            # B4: a goto outside the plan is a validation
            # failure, like a malformed step — the task fails
            # with the reason named, never runs a jump to
            # nowhere.
            from .task_coordinator_steps import validate_step_jumps
            validate_step_jumps(steps)
            # B19: a parallel group that breaks the group
            # rules is a validation failure too, like a
            # malformed step — the task fails with the reason
            # named, never runs a group it cannot run.
            from .task_coordinator_steps import validate_parallel_groups
            validate_parallel_groups(steps)
        except ValueError as exc:
            task._load_error = f"invalid step in {cls.kind} {cls.name!r}: {exc}"
            task._error_kind = "validation"
            task.state = "failed"
            self._ledger_write(task)
            return {"handled": True, "classification": cls, "task": task,
                    "receipt": task.receipt(), "error": task._load_error}
        task.steps = steps
        # Fix 8 (plan approval): when the caller supplied a
        # plan hook (the GUI's plan dialog), the human sees
        # the plan once — safe steps approved here run
        # without a popup each; destructive steps still ask
        # individually at their step. No hook (headless,
        # tests, resume paths) means no dialog and no
        # approval: behaviour exactly as before. A broken
        # hook or a failed record reads as "ask per step".
        # R5 (plan-dialog timeout): the hook may answer with
        # a dict {"approved": [...], "outcome": ...} (a plain
        # list keeps the old meaning). A "timeout" outcome is
        # the silent third answer — nobody was there — so the
        # task PAUSES before any step runs instead of
        # degrading to step-by-step asks into an empty room:
        # nothing ran, nothing was approved, no retry strike
        # and no drop-budget burn (a human pause is not a
        # failure), and the pause names itself in the receipt
        # + the ledger + one activity line, so the user can
        # see it and resume it. Convergence is N=1 by
        # construction: resume never re-invokes the hook, so
        # no second plan timeout is possible — a resumed task
        # asks per step, exactly like any other resumed task.
        if plan_hook is not None:
            try:
                from .. import plan_approval as _pa
                _preview = _pa.preview(task)
            except Exception:  # noqa: BLE001 — no preview: ask per step
                _preview = None
            if _preview is not None and _preview.get("approvable"):
                try:
                    _hook_result = plan_hook(task, _preview)
                except Exception:  # noqa: BLE001 — a broken hook denies the plan
                    _hook_result = None
                _hook_outcome = None
                _approved = None
                if isinstance(_hook_result, dict):
                    _approved = _hook_result.get("approved")
                    _hook_outcome = _hook_result.get("outcome")
                else:
                    _approved = _hook_result
                if _hook_outcome == "timeout":
                    task._paused = True
                    task._pause_reason = (
                        "The plan approval timed out — nobody "
                        "answered, so nothing ran yet. Resume to "
                        "see the plan again.")
                    task.state = "paused"
                    self._ledger_write(task)
                    self._log_activity_line(
                        "plan_approval",
                        f"task {task.title!r}: plan approval timed "
                        "out — paused before any step ran; resume "
                        "to see the plan again")
                    return {"handled": True, "classification": cls,
                            "task": task, "receipt": task.receipt(),
                            "error": ""}
                if _approved:
                    try:
                        from .. import plan_approval as _pa2
                        _pa2.approve_plan(task.id, task.steps,
                                          list(_approved))
                    except Exception:  # noqa: BLE001 — a failed record asks per step
                        pass
        receipt = self.run_task(task)
        return {"handled": True, "classification": cls, "task": task,
                "receipt": receipt, "error": ""}

    # -- mission bridge ----------------------------------------------------
    def to_mission_receipt(self, task: Task):
        """This task as a mission_control.Receipt (the reuse bridge).

        Status mapping: completed->completed, failed->failed,
        cancelled->stopped, anything else->not-run. Evidence refs
        are one per attempted step (detail + source); a task with
        no attempted steps carries its load error as evidence, so
        a receipt is never recorded on a bare claim. No gate
        decisions are ever attached — the coordinator approves
        nothing, ever.
        """
        from ..mission_control import Receipt as _MissionReceipt
        receipt = task.receipt()
        evidence = []
        for step in receipt.steps:
            evidence.append({
                "detail": f"{step['label']}: {step['status']}"
                          + (f" ({step['kind']})" if step["kind"] else ""),
                "source": "task_coordinator",
            })
        if not evidence:
            evidence.append({"detail": receipt.reason or
                             f"task {task.id[:8]} did not run",
                             "source": "task_coordinator"})
        # The verification verdicts are part of the record, so
        # they join the mission view as evidence (never as gate
        # decisions — the coordinator approves nothing, ever).
        for verdict in receipt.verification:
            evidence.append({
                "detail": f"verification: {verdict['label']} — "
                          f"{verdict['verdict']}"
                          + (f": {verdict['reason']}"
                             if verdict.get("reason") else ""),
                "source": "task_coordinator",
            })
        refusal_reasons = []
        if receipt.state != "completed":
            refusal_reasons.append(receipt.reason or
                                   f"task {task.id[:8]} did not complete")
        return _MissionReceipt(
            task.title or task.id[:8], task.title,
            _MISSION_STATUS[receipt.state],
            evidence,
            steps=[s["label"] for s in receipt.steps],
            gate_decisions=[],
            refusal_reasons=refusal_reasons,
        )
