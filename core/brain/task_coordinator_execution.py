"""Split of task_coordinator: TurnCoordinator execution mixin."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

# INTENTIONAL (skills split B43): run_task/_run_verification_phase emit
# mission/step events through the state-bus producers.
from ..state_bus_producers import (
    emit_mission,
    emit_step,
    emit_tool_finished,
    emit_tool_started,
)
# INTENTIONAL (skills split B43): _run_verification_phase reads the
# retry strike limit from the classify module (constants home).
from . import task_budget as _tb
from .task_budget import (
    begin_window,
    close_window,
    record_cloud_call as _record_cloud_call,
    record_local_call as _record_local_call,
    time_budget_reason,
)
from .task_coordinator_classify import _RETRY_STRIKE_LIMIT
# INTENTIONAL (skills split B43): _second_opinion_check uses the opinion
# helpers; _run_step uses the refusal rewrites + StepOutcome.
from .task_coordinator_opinion import (
    _menu_leaf,
    _second_opinion_pause_sentence,
    _second_opinion_question,
)
from .task_coordinator_refusal import (
    _detect_s7_confirmation_stop,
    _detect_wallpaper_monitor_gone,
    _rewrite_s7_refusal,
)
from .task_coordinator_steps import StepOutcome, max_jumps, parallel_groups


# B19 (parallel steps): fallback for the per-coordinator spend
# lock (TurnCoordinatorLifecycleMixin.__init__ always sets
# self._step_lock; this is the total fallback for a mixin
# used without that __init__ — the lock only ever guards the
# B14 spend counters, so a shared fallback degrades to
# serializing spend accounting, never to wrong accounting).
_FALLBACK_STEP_LOCK = threading.Lock()

# B22 (sub-goal decomposition + re-planning): the re-plan
# budget — at most this many re-plans per task. It IS the
# strike limit (_RETRY_STRIKE_LIMIT, 3), not a second number:
# one shared bounded-attempt budget, so a re-plan is a bounded
# attempt like a retry, never an infinite loop wearing a plan.
# A re-plan does not reset strikes, and strikes do not reset
# the re-plan count — the two counters are separate, the LIMIT
# is one. The 4th not-met verdict fails honestly (the B10
# failure, with the re-plan budget named in the reason).
_REPLAN_LIMIT = _RETRY_STRIKE_LIMIT


def _check_result(out: dict, check: dict | None) -> tuple[bool, str]:
    """Evaluate a B12 result check against one executor outcome.

    Fix 9: a local mirror of planner.check_result
    (core/brain/planner_plans.py:66) — the same rule, kept
    in the coordinator so a check-bearing routine step keeps
    its legacy meaning (a declared expectation the result
    does not meet is a failure, never a silent success).
    Parity against check_result is pinned in
    tests/test_complex_task.py, so the two copies cannot drift
    silently. No check -> pass.
    """
    if not check:
        return True, ""
    status = str((out or {}).get("status") or "")
    text = str((out or {}).get("result")
               or (out or {}).get("message") or "")
    expected_status = check.get("status")
    if expected_status and status.lower() != str(expected_status).lower():
        return False, (f"expected status '{expected_status}', "
                       f"got '{status or 'unknown'}'")
    contains = check.get("contains")
    if contains and str(contains).lower() not in text.lower():
        return False, (f"expected the result to contain "
                       f"'{contains}'; it said: {text[:100]}")
    return True, ""


def _pending_crop_for_step(step):
    """C7 crop producer: stored region grid for a step, or None.

    The dialog slot (gui/pending_approval) shows the crop when
    the pending record carries one. This feeds it ONLY when
    the step names the retained capture and has one primary
    point; every other case is None, so pending records
    without a crop behave exactly as before. Function-local
    imports (the C3 pattern) keep the brain/desktop edge lazy.
    """
    try:
        from ..desktop import point_crop as _point_crop
        from ..desktop import vision as _vision
        return _point_crop.stored_crop_for_step(
            step.tool, step.args or {}, _vision.last_sent_frame())
    except Exception:  # noqa: BLE001 — no crop never blocks a pause
        return None


class TurnCoordinatorExecutionMixin:
    # -- execution ---------------------------------------------------------
    def _stop_reason(self, task) -> str | None:
        """Why this task must CANCEL now, or None to keep running.

        Fix 9 (voice routines run as coordinator tasks): the
        coordinator's own cancel flag always applies. A task
        the voice-routine handler marked with
        ``_honor_stop_signals`` ALSO cancels on the failsafe
        trip (goal-pill Stop — the planner loop's own stop
        signal, core/brain/planner_rungoal.py). A session-
        epoch change is NOT a cancel: it pauses the task
        instead (see _pause_reason below) — a drop must leave
        a resumable record, and a cancelled record is
        terminal in the ledger (rebuild_task refuses it), so
        cancelling on a drop would lose the task exactly
        the way the old orphaned run did. Unmarked tasks
        (every existing caller, including typed turns)
        behave exactly as before — this check costs them
        nothing but the flag read.
        """
        if getattr(task, "_cancelled", False):
            return ""
        if not getattr(task, "_honor_stop_signals", False):
            return None
        try:
            from .. import failsafe as _failsafe  # lazy: leaf module
            if _failsafe.tripped():
                return ("stopped from the goal pill — the failsafe "
                        "was tripped while the task was running")
        except Exception:  # noqa: BLE001 — an unreadable trip signal
            pass  # never stops a task on its own
        return None

    def _pause_reason(self, task) -> str | None:
        """Why this task must PAUSE now, or None to keep running.

        Fix 9: a marked task (``_honor_stop_signals``) whose
        session epoch moved since run_task entry — the voice
        session ended and started again, including a
        reconnect after a drop (the epoch bumps at the start
        seam). The task stops at this step boundary with its
        completed steps kept and a "paused" ledger record,
        so the human's Connect resumes it from the boundary
        instead of restarting it — the old run kept going
        orphaned after a drop and its results went nowhere;
        cancelling here would be the same loss wearing a
        receipt. The epoch is captured at run_task entry, so
        a resumed task measures against the session it
        resumes into, never a stale one. Unmarked tasks
        never pause on an epoch change.
        """
        if not getattr(task, "_honor_stop_signals", False):
            return None
        try:
            from .. import session_epoch as _epoch  # lazy: leaf module
            start = getattr(task, "_epoch_at_start", None)
            if start is not None and _epoch.current_epoch() != start:
                return ("paused — the voice session restarted "
                        "while the task was running; resume to "
                        "continue from the next step")
        except Exception:  # noqa: BLE001 — an unreadable epoch
            pass  # never pauses a task on its own
        return None

    def _executor_accepts_token(self) -> bool:
        """True when the executor declares a `cancel_token`
        parameter. Cached per coordinator: executor fakes in
        tests are lambdas with 2-arg signatures, and the real
        funnel executor is the 3-arg _funnel_execute — calling
        a 2-arg executor with a third argument would break
        every fake. Inspected once, never per step."""
        flag = getattr(self, "_executor_accepts_token_flag", None)
        if flag is None:
            try:
                import inspect
                flag = ("cancel_token"
                        in inspect.signature(self._executor).parameters)
            except (TypeError, ValueError):
                flag = False
            self._executor_accepts_token_flag = flag
        return flag

    def _execute(self, tool: str, args: dict,
                 cancel_token=None) -> dict:
        """Execute one tool through the executor.

        B20: when the executor declares `cancel_token`, the
        step's cooperative token is passed through; otherwise
        the call is byte-identical to today (no third argument
        is ever forced onto a fake or a legacy executor).
        """
        try:
            if cancel_token is not None and self._executor_accepts_token():
                out = self._executor(tool, args, cancel_token=cancel_token)
            else:
                out = self._executor(tool, args)
        except Exception as exc:  # noqa: BLE001 — executor failure is a step failure
            return {"status": "failure", "kind": "handler_error",
                    "message": f"executor failed for {tool}: {exc}"}
        if not isinstance(out, dict):
            return {"status": "failure", "kind": "handler_error",
                    "message": f"executor for {tool} returned non-dict"}
        return out

    # -- verification-phase defaults (lazy, never breaking) ------------
    @staticmethod
    def _default_snapshot():
        """One desktop snapshot for verification, via uia.

        Mirrors planner._probe_snapshot: None when the probe has
        no eyes here (non-Windows, probe failure) — never raises.
        """
        try:
            from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/
            return uia.desktop_snapshot()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _default_verify(expect_text, before, after, result_text):
        """The existing uia.verify_expectation producer, guarded.

        Mirrors planner._verify_sync: verdicts normalised to met
        / not-met / unverifiable; any failure yields
        "unverifiable", never a guessed verdict, never a break.
        """
        try:
            from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/
            verdict, reason = uia.verify_expectation(
                expect_text, before, after, result_text)
        except Exception as exc:  # noqa: BLE001
            return ("unverifiable",
                    f"verification callback failed: {exc}")
        verdict = str(verdict or "").strip().lower()
        if verdict not in ("met", "not-met", "unverifiable"):
            verdict = "unverifiable"
        return (verdict, str(reason or ""))

    @staticmethod
    def _default_menu_verify(expect_text, before, after, invoke_result):
        """The menu consequence verifier, guarded.

        Mirrors _default_verify: verdicts normalised to met /
        not-met / unverifiable; any failure yields
        "unverifiable", never a guessed verdict, never a break.
        """
        try:
            from ..desktop import s7_menu_consequence as _mc  # Phase 3 S7a-2: moved to core/desktop/
            verdict, reason = _mc.verify_menu_consequence(
                expect_text, before, after, invoke_result)
        except Exception as exc:  # noqa: BLE001
            return ("unverifiable",
                    f"verification callback failed: {exc}")
        verdict = str(verdict or "").strip().lower()
        if verdict not in ("met", "not-met", "unverifiable"):
            verdict = "unverifiable"
        return (verdict, str(reason or ""))

    @staticmethod
    def _outcome(step: StepDef, out: dict) -> StepOutcome:
        status = str(out.get("status", "") or "failure")
        kind = str(out.get("kind", "") or "")
        if not kind:
            # B13: the seam Outcome kind rides the funnel dict as
            # outcome_kind — carry it onto the receipt so a step
            # that succeeded-but-unverified reads as such, not as
            # a bare success. Tools without a seam Outcome keep
            # the empty kind they always had.
            kind = str(out.get("outcome_kind", "") or "")
        message = str(out.get("message", "") or "")
        if not message and status == "success":
            message = str(out.get("result", "") or "")
        return StepOutcome(tool=step.tool, label=step.label,
                           status=status, kind=kind, message=message)

    def run_task(self, task: Task, *, on_step=None) -> TaskReceipt:
        """Run a task synchronously to completion, pause, or cancel.

        Pause/cancel flags are read before EVERY step, so a
        cancelled task's remaining steps genuinely never run, and
        a paused task resumes from the next uncompleted step.
        A step callback (per-run or coordinator-level) may pause
        or cancel mid-run; callback errors never break the task.
        """
        if task.state in ("completed", "failed", "cancelled"):
            return task.receipt()
        # §7 consumers batch 3: task-scoped handle/stamp
        # threading — begins here, ends in the finally below
        # (all exits: completed, failed, cancelled, paused,
        # and unexpected exceptions). A resumed task re-begins,
        # so implicit threading restarts while explicitly
        # declared step handles survive via the step itself.
        try:
            from ..desktop import s7_threading as _s7t  # Phase 3 S7a-2: moved to core/desktop/

            _s7t.begin_task(task.id)
        except Exception:  # noqa: BLE001 — threading never breaks tasks
            pass
        try:
            task.state = "running"
            self._ledger_write(task)
            # Fix 9: a voice-routine task measures stop signals
            # against the session it starts in (captured here,
            # so a resume re-measures against the session it
            # resumes into, never a stale one).
            if getattr(task, "_honor_stop_signals", False):
                try:
                    from .. import session_epoch as _epoch  # lazy
                    task._epoch_at_start = _epoch.current_epoch()
                except Exception:  # noqa: BLE001 — no epoch, no
                    task._epoch_at_start = None  # stop-signal rule
            # Command Deck Phase D: the mission bus is fed from this
            # loop — every event below is observed here, never staged.
            # The bus is cosmetic (producers never raise for it), so
            # these emits sit outside every try/except that matters.
            emit_mission(goal=task.title, phase="act", step=task._index,
                         steps_total=len(task.steps), outcome="running")
            callback = on_step or self._on_step
            # B22: the re-plan loop. One run_task call may run
            # several plans (the initial plan, then up to
            # _REPLAN_LIMIT re-plans) — but the flag below is
            # the only way back in: a re-plan that lands sets
            # it, everything else (pause, cancel, fail, a
            # completed goal) leaves it False and the call ends
            # at the receipt below. The inner while-else is
            # unchanged: a break (pause/cancel/fail) skips the
            # else and ends the call; only a clean finish
            # reaches the goal check, and only a not-met
            # verdict with budget left re-plans. A re-plan
            # never opens a fresh budget window (B14): the
            # window opened on the first iteration stays open
            # across the re-plan — begin_window only opens
            # when none is open.
            _replanned = True
            while _replanned:
                _replanned = False
                while task._index < len(task.steps):
                    _stop = self._stop_reason(task)
                    if _stop is not None:
                        # Fix 9: a stop signal (cancel flag, failsafe
                        # trip) halts the task at this step boundary
                        # — remaining steps genuinely never run, and
                        # the receipt names the signal instead of a
                        # bare "cancelled".
                        task._cancelled = True
                        if _stop:
                            task._cancel_reason = _stop
                        task.state = "cancelled"
                        break
                    _pause = self._pause_reason(task)
                    if _pause is not None:
                        # Fix 9: a session restart pauses, never
                        # cancels — the ledger keeps a resumable
                        # "paused" record (a cancelled record is
                        # terminal and could never resume).
                        task._paused = True
                        task._pause_reason = _pause
                        task.state = "paused"
                        break
                    if task._paused:
                        task.state = "paused"
                        break
                    # B14: wall-clock budget, checked at the step
                    # boundary — the same boundary where stop/pause
                    # signals are judged, so a budget pause lands
                    # between steps, never mid-step, and a step
                    # already running is never killed. The window
                    # begins lazily on the first iteration of each
                    # run_task entry (a no-op re-entry on a paused
                    # task breaks above before any window opens),
                    # so a resumed task is measured from the resume
                    # (B11 shape: fresh window, windows + 1,
                    # visible in the record). A spent budget pauses
                    # resumably — never completes, never silently
                    # kills — and the receipt says what it spent.
                    try:
                        begin_window(task)
                        _over = time_budget_reason(task)
                    except Exception:  # noqa: BLE001 — a broken budget check never breaks a task
                        _over = None
                    if _over is not None:
                        task._paused = True
                        task._pause_reason = _over
                        task.state = "paused"
                        try:
                            self._log_activity_line(
                                "task_budget",
                                f"task {task.title!r}: {_over}")
                        except Exception:  # noqa: BLE001 — logging never breaks a task
                            pass
                        break
                    # B19: a step whose outcome is already recorded
                    # (a parallel-group sibling that completed
                    # before the group paused) never re-runs —
                    # advance past it. In the sequential flow this
                    # never fires (outcomes are dense and _index
                    # always points at the next uncompleted step);
                    # it fires only on resume after a group pause.
                    # The retry-resume marker step is exempt: it
                    # carries a stale success outcome and MUST
                    # re-run (the marker IS the instruction to
                    # re-run it — skipping it would lose the jump
                    # to verification).
                    while task._index < len(task.steps) \
                            and getattr(task, "_retry_resume_step", None) \
                            != task._index:
                        _done = self._recorded_outcome(task, task._index)
                        if _done is None or _done.status not in (
                                "success", "skipped"):
                            break
                        try:
                            task._jump_strikes.pop(task._index, None)
                        except Exception:  # noqa: BLE001 — strike bookkeeping never breaks a skip
                            pass
                        task._index += 1
                    if task._index >= len(task.steps):
                        continue
                    # B19: a parallel group starting here runs
                    # together (see _run_parallel_group). A group
                    # never forms around the retry-resume marker
                    # step (that step re-runs alone, then jumps to
                    # verification — the marker's own rule), and a
                    # group with fewer than 2 runnable members
                    # runs sequentially (a singleton re-run is just
                    # a step; the group cannot gain members — a
                    # member becomes runnable only by completing).
                    _group = self._parallel_group_starting_at(
                        task, task._index)
                    if _group is not None \
                            and getattr(task, "_retry_resume_step", None) \
                            != task._index:
                        _runnable = [i for i in _group
                                     if self._group_branch_runnable(
                                         task, i)]
                        if len(_runnable) >= 2:
                            if not self._run_parallel_group(
                                    task, _group, _runnable,
                                    callback=callback):
                                break
                            continue
                    # B21 (crash-safe re-run): a step whose intent
                    # is recorded but whose outcome is missing is
                    # reconciled BEFORE it runs again — "applied"
                    # records a reconciled success and skips the
                    # step (the loop's skip-past advances over it);
                    # "unknown" pauses with _pending_reconcile and
                    # the human decides; "not-applied" runs fresh
                    # below with a NEW step-run id. A retry-resume
                    # marker IS the instruction to re-run (the
                    # marker names the step to retry), so it skips
                    # reconciliation — the marker's step is the one
                    # step the resume is told to re-run, not a
                    # crash to reconcile.
                    if getattr(task, "_retry_resume_step", None) \
                            != task._index:
                        _rec_v, _rec_r, _rec_p = \
                            self._reconcile_pending_intent(
                                task, task._index)
                        if _rec_v == "applied":
                            self._apply_reconciled(
                                task, task._index, _rec_r)
                            continue
                        if _rec_v == "unknown":
                            task._pending_reconcile = _rec_p
                            task._paused = True
                            task.state = "paused"
                            self._ledger_write(task)
                            break
                    step = task.steps[task._index]
                    emit_step(n=task._index + 1, m=len(task.steps),
                              label=step.label)
                    emit_tool_started(kind=step.tool)
                    outcome = self._run_step(task, step)
                    emit_tool_finished(status=outcome.status, kind=step.tool)
                    if outcome.status == "pending":
                        # §7 consumers batch 6: engine confirm stop
                        # — the task pauses for approval. The step
                        # is NOT appended to outcomes (it has not
                        # succeeded, failed, or skipped; it awaits
                        # a decision), _index still points at it,
                        # and the pending record on the task names
                        # it. _paused is set so a later run_task
                        # without resume_task stays put instead of
                        # re-executing the step; resume_task
                        # clears it and the step re-runs (and
                        # re-stops, since no approval was given).
                        # No step callback fires (failures don't
                        # callback either); the caller learns via
                        # the paused receipt + pending_approval.
                        task._paused = True
                        task.state = "paused"
                        self._ledger_write(task)
                        break
                    # B4: outcomes stay positionally aligned with
                    # steps. A step re-run after a jump replaces
                    # its previous outcome in place (the
                    # retry-resume precedent — the same rule, now
                    # general: a re-run step's outcome belongs at
                    # its own position); a first run appends.
                    # Without this, a re-run step would shift every
                    # later step's verification onto the wrong
                    # outcome. The jump itself truncates outcomes
                    # past its target (see _take_jump), so in the
                    # normal flow this fires only for the
                    # retry-resume marker — it is the safety net
                    # that keeps alignment when a re-run lands on
                    # a position that already holds an outcome.
                    if task._index < len(task.outcomes) \
                            and task.outcomes[task._index].tool == step.tool \
                            and task.outcomes[task._index].label == step.label:
                        task.outcomes[task._index] = outcome
                    else:
                        task.outcomes.append(outcome)
                    self._ledger_write(task)
                    if outcome.status == "success" or outcome.status == "skipped":
                        # B4: the step did not fail — its jump
                        # strike streak resets (the planner's
                        # reset-on-met rule: only consecutive
                        # failures count toward the strike limit).
                        try:
                            task._jump_strikes.pop(task._index, None)
                        except Exception:  # noqa: BLE001 — strike reset never breaks a task
                            pass
                        task._index += 1
                        # Retry-on-not-met resume: the step just
                        # completed is a retry-pending step resolved
                        # through pending approval — later steps
                        # already ran (their outcomes are intact),
                        # so the loop must NOT continue into them.
                        # Jump to the verification phase instead:
                        # setting _index past the end ends the loop
                        # without a break, so the else branch below
                        # runs verification in full.
                        if getattr(task, "_retry_resume_step", None) \
                                == task._index - 1:
                            task._retry_resume_step = None
                            task._index = len(task.steps)
                    elif outcome.status == "cancelled":
                        # B20: the user said stop while this step
                        # ran — the task stops here, as "cancelled"
                        # (never "failed": nothing failed, and a
                        # failure would strike the step and offer a
                        # jump — both wrong for a stop). The
                        # outcome is already recorded at its own
                        # position above, so partial progress stays
                        # in the ledger (B11); no jump is taken, no
                        # retry is owed (B12: a cancelled step is
                        # never retried), no lesson is written
                        # (B17: a stopped task teaches nothing).
                        # The receipt names the step (see
                        # _cancel_reason below, and Task.receipt).
                        task._cancel_reason = (
                            f"stopped during step {task._index + 1} "
                            f"({step.label}): "
                            f"{outcome.message or outcome.status}")
                        task._cancelled = True
                        task.state = "cancelled"
                        self._ledger_write(task)
                        break
                    else:
                        # B4: the step failed — halt, or jump to
                        # the named recovery step. _take_jump
                        # decides (strikes, budget, D4/C-14) and
                        # records the decision; True means the
                        # jump was taken and _index already points
                        # at the target, so the loop continues
                        # there. False means refused — halt
                        # honestly on this step, remaining
                        # includes it.
                        if getattr(step, "goto", None) is not None \
                                and self._take_jump(task, step, outcome):
                            continue
                        # Halted on this step: remaining includes it.
                        task.state = "failed"
                        break
                    if callback is not None:
                        try:
                            callback(task, outcome)
                        except Exception:  # noqa: BLE001 — callbacks never break tasks
                            pass
                else:
                    # All steps success/skipped: the verification phase
                    # runs here, after action and before report.
                    # B22: on a re-plan pass the phase verifies only
                    # the steps the re-plan appended (from
                    # _replan_base) — the steps that just ran.
                    # Older steps were verified when they ran, and
                    # re-verifying them could retry a step that
                    # already succeeded (the B21 rule: a
                    # recorded-outcome step never re-runs). The
                    # goal check below still judges the WHOLE goal
                    # against a fresh snapshot, so a regressed old
                    # step is caught there, not by re-running it.
                    task.state = "completed"
                    if any(getattr(s, "expect", "") for s in task.steps):
                        self._run_verification_phase(
                            task,
                            from_index=getattr(task, "_replan_base", 0)
                            or 0)
                    # B10: the goal check runs after the step phase
                    # (whether it ran or not — a task with no step
                    # expects still gets its goal judged), and only
                    # while the task still stands completed. "met"
                    # completes as today; "unverifiable" completes
                    # with the verdict recorded, never faked to
                    # "met"; "not-met" re-plans (B22, below) while
                    # budget remains, and fails honestly once it
                    # does not — the goal state was observable and
                    # absent — naming the goal and the reason.
                    # The _verify_error path carries the reason
                    # into the receipt (no failed outcome exists —
                    # every action succeeded).
                    if task.state == "completed":
                        # B22: the goal-check marker — the
                        # state reads "completed" during the
                        # check, transiently, and cancel_task
                        # honours the marker so a Stop that
                        # lands here cancels instead of being
                        # refused. Cleared in a finally: the
                        # window is the check call only.
                        task._in_goal_check = True
                        try:
                            verdict, reason = self._check_goal(task)
                        finally:
                            task._in_goal_check = False
                        task._goal_verdict = {"verdict": verdict,
                                              "reason": reason}
                        self._ledger_write(task)
                        if verdict == "not-met":
                            _replan_ok, _replan_err = \
                                self._try_replan(task, reason)
                            if _replan_ok:
                                _replanned = True
                            elif task.state == "cancelled" \
                                    or self._stop_reason(task) is not None:
                                # B22: the task was cancelled
                                # between the goal check and the
                                # re-plan (a Stop during the
                                # check, or a failsafe trip) —
                                # the user said stop, so it ends
                                # cancelled, never failed, and
                                # never re-planned. Mirrors the
                                # action loop's stop handling
                                # (flag + reason + state).
                                _stop = self._stop_reason(task)
                                task._cancelled = True
                                if _stop:
                                    task._cancel_reason = _stop
                                task.state = "cancelled"
                                self._ledger_write(task)
                            else:
                                task._verify_error = (
                                    f"goal not met: {task.title!r} — "
                                    f"{reason}"
                                    + (f"; re-plan failed: {_replan_err}"
                                       if _replan_err else
                                       f"; re-plan budget spent "
                                       f"({_REPLAN_LIMIT} re-plans)"))
                                task.state = "failed"
            receipt = task.receipt()
            # B17 (learning across tasks): ONE lesson write, at
            # the receipt path, for terminal outcomes only — a
            # completed task with a verified goal verdict writes
            # a proven lesson; a failed task writes a failure
            # lesson naming what failed; a completed task
            # without a verified verdict writes nothing. Paused
            # and cancelled tasks have not finished, so they
            # teach nothing. The tried steps are the steps with
            # recorded outcomes (positionally aligned — a re-run
            # step's outcome sits at its own position); the
            # proof is the ledger record task:<id> (receipt +
            # goal verdict). Best-effort, like the ledger write:
            # a lesson failure never breaks a task.
            if task.state in ("completed", "failed"):
                try:
                    from .task_learning import record_task_lesson
                    # B22: a lesson for a re-planned task records
                    # the re-plan — a step that came from a
                    # re-plan carries its attempt in its
                    # description ("label (re-plan 2)"), so a
                    # lesson that only works after a re-plan says
                    # so. The proof (the ledger record) carries
                    # the full re-plan history; the description
                    # is the visible part. No store format change
                    # (the playbook entry format stays v2).
                    _tried = []
                    for i in range(min(len(task.outcomes),
                                       len(task.steps))):
                        _desc = (task.steps[i].label
                                 or task.steps[i].tool)
                        _attempt = self._replan_attempt_for_step(task, i)
                        if _attempt is not None:
                            _desc = f"{_desc} (re-plan {_attempt})"
                        _tried.append({
                            "tool": task.steps[i].tool,
                            "args": dict(task.steps[i].args or {}),
                            "description": _desc,
                        })
                    record_task_lesson(
                        getattr(self, "_playbook", None),
                        name=task.title, steps=_tried,
                        outcome=task.state,
                        goal_verdict=(dict(task._goal_verdict)
                                      if isinstance(task._goal_verdict, dict)
                                      else None),
                        proof_job_id=f"task:{task.id}",
                        failure_reason=receipt.reason)
                except Exception:  # noqa: BLE001 — a lesson failure never breaks a task
                    pass
            if self._mission_sink is not None:
                try:
                    self._mission_sink(self.to_mission_receipt(task))
                except Exception:  # noqa: BLE001 — sink failure never breaks tasks
                    pass
            self._ledger_write(task)
            # Terminal mission event: completed -> success, failed ->
            # failed, cancelled -> cancelled, paused -> still running
            # (a paused task resumes; it has not ended).
            _terminal = {
                "completed": ("done", "success"),
                "failed": ("failed", "failed"),
                "cancelled": ("cancelled", "cancelled"),
                "paused": ("paused", "running"),
            }.get(task.state, ("done", "unknown"))
            emit_mission(goal=task.title, phase=_terminal[0],
                         step=task._index, steps_total=len(task.steps),
                         outcome=_terminal[1])
            return receipt
        finally:
            # B14: bank this window's seconds (a paused task's
            # resume opens a fresh window — never charged twice
            # for the stopped one) and close it, on every exit
            # including an unexpected exception. The receipt
            # above already carries the live elapsed, so the
            # close changes no reported number.
            try:
                close_window(task)
            except Exception:  # noqa: BLE001 — a broken close never breaks a task
                pass
            try:
                _s7t.end_task()
            except Exception:  # noqa: BLE001 — see above
                pass
            # Fix 8: a terminal task's plan approval dies with
            # it — completed / failed / cancelled never re-run,
            # so the record must not outlive the task. A paused
            # task keeps its approval: resume continues the
            # same plan in the same process.
            if task.state in ("completed", "failed", "cancelled"):
                try:
                    from .. import plan_approval as _pa
                    _pa.clear(task.id)
                except Exception:  # noqa: BLE001 — teardown never breaks a task
                    pass
                # R5: a terminal task's monitor pause dies with
                # it too — it is a resume instruction, and a
                # terminal task never resumes.
                task._pending_monitor = None
                # B15: a terminal task's question pause dies
                # with it too — same rule, same reason.

    # -- B22: sub-goal decomposition + re-planning --------------------
    def _replan_attempt_for_step(self, task: Task, index: int):
        """The re-plan attempt a step index came from, or None.

        B22: the lesson's provenance rule — a step appended by
        re-plan N carries "(re-plan N)" in its lesson
        description, so a lesson that only works after a
        re-plan says so. The initial plan's steps carry no
        attempt (None). Pure: reads the re-plan records only.
        """
        for event in getattr(task, "_replans", []) or []:
            if not isinstance(event, dict):
                continue
            try:
                base = int(event.get("first_step_index", 0) or 0)
                count = int(event.get("steps", 0) or 0)
                attempt = int(event.get("attempt", 0) or 0)
            except (TypeError, ValueError):
                continue
            if base <= int(index) < base + count and attempt:
                return attempt
        return None

    def _plan_candidates(self, goal_text: str) -> list:
        """Ranked candidate tools for a goal, or [] on any failure.

        B22: the picker's own hint (toolbox.rank_tools, the
        same call the run_goal picker makes for one step) —
        candidates are a hint, never a break: a ranking
        failure reads as no candidates, and the brain may
        still name any registered tool. The tool-name check
        in _plan_from_brain is the enforcement, not this list.
        """
        try:
            from ..funnel import toolbox as _toolbox
            from ..funnel.tools_registry import tools as _tools
            ranked = _toolbox.rank_tools(
                _tools, str(goal_text or ""), limit=12)
            return [str(t or "").strip() for t in (ranked or [])
                    if str(t or "").strip()]
        except Exception:  # noqa: BLE001 — candidates never break a plan
            return []

    def _ask_plan_brain(self, task: Task, prompt: str):
        """Ask the plan brain for a plan. (text, brain, seconds).

        B22: the brain is the SAME pair the second-opinion
        check uses (self._second_opinion_brains — the
        companion-session pair, never a new brain). brain_a is
        the cloud leg (Groq), brain_b the local leg. brain_a
        is asked first; only when it returns nothing usable
        is brain_b asked — and the re-plan record names which
        brain planned, so the fallback is recorded, never
        silent (the second-opinion rule is "never a SILENT
        fallback"). A brain that raises returns nothing usable
        (the pair's own rule: an erroring brain is
        unavailable, never a fake answer). No pair at all
        reads as ("", "", 0.0) with the reason carried by the
        caller — a task with no text brain fails honestly,
        never on a guessed plan. The call is recorded on the
        task's B14 spend (one cloud call for the cloud leg,
        one local call for the local leg, seconds measured,
        cost honest-unknown — the brain returns text only,
        no usage payload), whichever leg answered; a call
        that raised records nothing (which legs ran is
        unknowable from the exception — the second-opinion
        rule, applied here too).
        Returns (text, "cloud" | "local" | "", seconds) — an
        empty text means no usable plan, never a plan.
        """
        import time as _time

        brains = getattr(self, "_second_opinion_brains", None)
        if brains is None:
            return "", "", 0.0
        try:
            brain_a, brain_b = brains
        except Exception:  # noqa: BLE001 — an unpackable pair is no pair
            return "", "", 0.0
        for brain, name in ((brain_a, "cloud"), (brain_b, "local")):
            if not callable(brain):
                continue
            _t0 = _time.monotonic()
            try:
                text = brain(prompt)
            except Exception:  # noqa: BLE001 — an erroring brain is unavailable
                continue
            seconds = max(0.0, _time.monotonic() - _t0)
            if not isinstance(text, str) or not text.strip():
                continue
            try:
                if name == "cloud":
                    self.record_task_cloud_call(
                        task, leg="decompose", model="", seconds=seconds)
                else:
                    self.record_task_local_call(task)
            except Exception:  # noqa: BLE001 — accounting never breaks a plan
                pass
            return text, name, seconds
        return "", "", 0.0

    def _plan_from_brain(self, task: Task, prompt: str):
        """One brain plan, validated end to end. (plan, brain, error).

        B22: ask (see _ask_plan_brain), parse strictly
        (task_decompose.parse_plan_text — the shape is the
        contract), then validate every step through the
        EXISTING StepDef.from_mapping + the plan-level
        validators (validate_step_jumps, validate_parallel_groups
        — the same rules a declared plan must pass), and check
        every tool name against the registry's declarations
        (the picker's own rule: an unknown tool name is a
        failed plan, never a step that runs and fails at
        dispatch). Returns ({"subgoals": [...]}, brain, "") on
        success; (None, "", error) on ANY failure — a failed
        plan is a failed decomposition, never a guessed one.
        The returned steps are StepDef objects (from_mapping
        already ran); the sub-goal names ride alongside for
        the record.
        """
        from .task_coordinator_steps import (
            StepDef,
            validate_parallel_groups,
            validate_step_jumps,
        )
        from .task_decompose import parse_plan_text

        text, brain, _seconds = self._ask_plan_brain(task, prompt)
        if not text.strip():
            return None, "", (
                "no text brain answered — the companion pair is "
                "not real (a Groq key and a landed local model "
                "are both needed); the goal cannot be decomposed")
        plan, error = parse_plan_text(text)
        if plan is None:
            return None, "", f"the brain's plan was unusable: {error}"
        try:
            mappings = []
            for subgoal in plan["subgoals"]:
                mappings.extend(subgoal["steps"])
            steps = [StepDef.from_mapping(m) for m in mappings]
            validate_step_jumps(steps)
            validate_parallel_groups(steps)
        except ValueError as exc:
            return None, "", f"the brain's plan is invalid: {exc}"
        try:
            from ..funnel.tools_registry import tools as _tools
            valid = {str(d.get("name", "") or "")
                     for d in _tools.get_declarations()}
        except Exception:  # noqa: BLE001 — unreadable declarations skip the check (the picker rule)
            valid = None
        if valid is not None:
            for step in steps:
                if step.tool not in valid:
                    return None, "", (
                        f"the brain's plan names unknown tool "
                        f"{step.tool!r} — registered tool names only")
        return ({"subgoals": plan["subgoals"], "steps": steps}, brain, "")

    def _decompose_goal(self, task: Task) -> str:
        """Decompose a free-text goal into steps. "" or an error.

        B22: the initial decomposition — the brain breaks the
        goal into sub-goals, each into a small step list, and
        the steps land on the task as its plan (task.steps)
        with the decomposition recorded (task._decomposition:
        sub-goal names, which brain planned, step count).
        The ledger is written before returning, so a crash
        after the decomposition leaves the plan on the
        record. "" means the plan landed; anything else is
        the failure reason, and the caller fails the task
        honestly on it (a task with no plan never runs).
        """
        from .task_decompose import (
            build_decompose_prompt,
            plan_subgoal_names,
        )

        prompt = build_decompose_prompt(
            task.title, self._plan_candidates(task.title))
        plan, brain, error = self._plan_from_brain(task, prompt)
        if plan is None:
            return error
        task.steps = list(plan["steps"])
        task._decomposition = {
            "subgoals": plan_subgoal_names(plan),
            "brain": brain,
            "steps": len(plan["steps"]),
        }
        task._replan_base = 0
        self._ledger_write(task)
        return ""

    def run_decomposed_goal(self, title: str, *, on_step=None,
                            time_budget_s=None) -> TaskReceipt:
        """Run a free-text goal as a coordinator task, decomposed by the brain.

        B22: the decompose-and-run entry. The goal text is
        decomposed into sub-goals and steps through the text
        brain (the companion-session pair — the same brain
        run_goal's picker uses), validated like any declared
        plan, and run as a coordinator task (ledger, receipt,
        verification phase, goal check, re-plans). A goal the
        brain cannot decompose fails honestly as a failed
        task with the reason named — never on a guessed plan,
        never silently passed to the live session.

        D1 is still owed: NO caller wires this into the typed
        turn path — handle_turn / handle_typed_turn are
        untouched, and a typed free-text goal still flows to
        the live session exactly as before. This method is a
        coordinator capability invocable from the existing
        typed-plan path (the same object the typed path uses),
        not a new entry point. The voice free-form loop is
        likewise untouched (Stage 1 hold — H1): this method
        is sync, like run_task, and no voice path calls it.
        """
        task = self.new_task(title, "multi_step", [],
                             turn_text=title,
                             time_budget_s=time_budget_s)
        error = self._decompose_goal(task)
        if error:
            task._load_error = (
                f"could not decompose goal {title!r}: {error}")
            task._error_kind = "unavailable"
            task.state = "failed"
            self._ledger_write(task)
            return task.receipt()
        return self.run_task(task, on_step=on_step)

    def _try_replan(self, task: Task, failed_reason: str):
        """Re-plan after a B10 not-met verdict. (True, "") or (False, error).

        B22: the re-plan attempt. False with "" means the
        budget is spent OR the task was cancelled — the
        caller tells them apart itself (_stop_reason: a
        stop ends the task cancelled, a spent budget fails
        it honestly on the B10 verdict alone, the 4th
        not-met). False with an error means the re-plan
        itself failed (no brain, an unusable plan, an
        invalid step, an unknown tool) — the caller fails
        honestly naming both the unmet goal and the re-plan
        failure. True means a fresh step list for the
        REMAINING work landed and the task runs it:
        the new steps are appended (never prepended — the
        old steps already ran), goto targets in the new plan
        are re-based to the merged list (the brain numbers
        its own plan from 1), the merged list is validated
        whole (a group that merges across the boundary, or
        a goto that lands outside it, fails the re-plan —
        the appended steps are rolled back, never left
        half-appended), the re-plan is recorded (attempt,
        what was tried, the goal-check reason, the new
        sub-goals, the step count, where they start, which
        brain planned), _replan_base points at the first new
        step (the verification phase verifies only those on
        this pass), and the run loop continues from them.

        A re-plan never re-runs a step that already succeeded:
        only the new steps run (the run loop starts at the
        first new index, and the B19 skip-past rule advances
        over recorded outcomes). A re-plan does not reset
        strikes (the retry history is untouched — a step that
        struck out still struck out), and it does not open a
        fresh budget window (B14 — the window stays open
        across the re-plan, so the new steps are charged to
        the same window). A cancelled task never re-plans
        (the guard below — the user said stop).
        """
        from .task_coordinator_steps import (
            validate_parallel_groups,
            validate_step_jumps,
        )
        from .task_decompose import (
            build_replan_prompt,
            plan_subgoal_names,
        )

        # B22: a cancelled task never re-plans (the user said
        # stop). The guard is _stop_reason — the same check
        # the action loop and the verification phase use — so
        # it covers the cancel flag AND the failsafe trip on
        # an _honor_stop_signals task, not just the flag.
        if task.state == "cancelled" \
                or self._stop_reason(task) is not None:
            return False, ""
        replans = getattr(task, "_replans", None)
        if replans is None:
            replans = task._replans = []
        if len(replans) >= _REPLAN_LIMIT:
            return False, ""
        tried = []
        for i, step in enumerate(task.steps):
            status = ""
            if i < len(task.outcomes):
                status = task.outcomes[i].status
            tried.append({"label": step.label or step.tool,
                          "tool": step.tool, "status": status})
        prompt = build_replan_prompt(
            task.title, tried, failed_reason,
            self._plan_candidates(task.title))
        plan, brain, error = self._plan_from_brain(task, prompt)
        if plan is None:
            return False, error
        base = len(task.steps)
        new_steps = list(plan["steps"])
        for step in new_steps:
            if step.goto is not None:
                step.goto = base + step.goto
        task.steps.extend(new_steps)
        try:
            validate_step_jumps(task.steps)
            validate_parallel_groups(task.steps)
        except ValueError as exc:
            del task.steps[base:]
            return False, (f"the re-planned steps do not fit the "
                           f"running task: {exc}")
        replans.append({
            "attempt": len(replans) + 1,
            "tried": [t.get("label", "") or t.get("tool", "")
                      for t in tried],
            "failed_reason": str(failed_reason or ""),
            "subgoals": plan_subgoal_names(plan),
            "steps": len(new_steps),
            "first_step_index": base,
            "brain": brain,
        })
        task._replan_base = base
        self._ledger_write(task)
        try:
            self._log_activity_line(
                "task_replan",
                f"task {task.title!r}: re-planned (attempt "
                f"{len(replans)}) after the goal check said "
                f"not-met — {len(new_steps)} new step(s) from "
                f"{brain or 'the plan brain'}")
        except Exception:  # noqa: BLE001 — logging never breaks a task
            pass
        return True, ""

    def _take_jump(self, task: Task, step: StepDef,
                   outcome: StepOutcome) -> bool:
        """Decide a goto jump for a failed step. True when the
        jump is taken (_index already moved to the target),
        False when refused (the caller halts honestly).

        B4 — ONE jump rule, three refusals, in order:
          1. D4/C-14: the target step is a send / delete /
             purchase / money / irreversible step
             (plan_approval.is_jump_refused — the tree's own
             set). A jump must never smuggle a destructive
             action past the human; the target never runs.
          2. Strikes: this step has now failed
             _RETRY_STRIKE_LIMIT times in a row (the same
             limit as verification retry-on-not-met, and the
             same reset-on-success rule). A step that keeps
             failing and jumping strikes out instead of
             looping on itself.
          3. Budget: the task has spent max_jumps jumps
             (the legacy formula len(steps)*3+10, one home).
             The per-step bound above catches loops that
             repeat one step; this one catches long chains
             whose steps keep succeeding between failures
             (a success resets strikes, so strikes alone
             cannot bound them).
        A taken jump records one event (the failure's only
        record when the outcomes list rewinds past it),
        writes one Activity line, ledger-writes, truncates
        outcomes to the target (positions past it belong to
        the pass being rewound — they are re-produced if
        those steps re-run, so outcomes stay positionally
        aligned with steps), and moves _index. The target
        step then runs through the SAME _run_step as any
        step — funnel gates, confirm stops, second-opinion
        checks and plan-approval digest checks all apply;
        a jump confers no coverage and bypasses nothing.
        The retry-resume marker is cleared: the
        retry-resume path (jump to verification) is
        abandoned in favour of the rewind.
        """
        index = task._index
        target = int(getattr(step, "goto", -1))
        target_step = task.steps[target]
        target_label = str(getattr(target_step, "label", "")
                           or target_step.tool)
        failure_reason = str(outcome.message or outcome.status or "failed")
        if outcome.kind:
            failure_reason = f"{failure_reason} ({outcome.kind})"
        strikes = int(getattr(task, "_jump_strikes", {}).get(index, 0) or 0) + 1
        try:
            task._jump_strikes[index] = strikes
        except Exception:  # noqa: BLE001 — strike count never breaks a jump
            pass

        def _refuse(reason: str) -> bool:
            event = {"from_index": index, "from_tool": step.tool,
                     "from_label": step.label, "reason": reason,
                     "to_index": target, "to_label": target_label,
                     "strike": strikes, "jumped": False}
            task._jumps.append(event)
            try:
                self._log_activity_line(
                    "plan_jump",
                    f"task {task.title!r}: step {step.label!r} failed; "
                    f"jump to step {target + 1} "
                    f"({target_label!r}) refused — {reason}")
            except Exception:  # noqa: BLE001 — logging never breaks a jump
                pass
            self._ledger_write(task)
            outcome.message = (f"{failure_reason}; jump to step "
                               f"{target + 1} refused: {reason}")
            return False

        # 1. D4/C-14: the target must never be a destructive
        # step. Lazy import (plan_approval is stdlib-only at
        # import time, but the coordinator keeps its tree
        # reads function-local like the rest of this module).
        try:
            from .. import plan_approval as _pa
            refused, refuse_reason = _pa.is_jump_refused(
                target_step.tool, getattr(target_step, "args", {}) or {})
        except Exception:  # noqa: BLE001 — undecidable: refused
            refused, refuse_reason = True, "jump target undecidable"
        if refused:
            return _refuse(refuse_reason)
        # 2. Strikes: the step failed _RETRY_STRIKE_LIMIT
        # times in a row — it strikes out instead of
        # jumping again.
        if strikes >= _RETRY_STRIKE_LIMIT:
            return _refuse(
                f"step {step.label!r} failed {strikes} times in a row — "
                f"strike limit ({_RETRY_STRIKE_LIMIT}) reached, "
                f"refusing to jump again")
        # 3. Budget: the task spent its jumps.
        taken = sum(1 for e in getattr(task, "_jumps", []) or []
                    if isinstance(e, dict) and e.get("jumped"))
        budget = max_jumps(len(task.steps))
        if taken >= budget:
            return _refuse(
                f"jump budget spent ({taken} jumps in a "
                f"{len(task.steps)}-step plan) — refusing to loop")
        task._jumps.append({
            "from_index": index, "from_tool": step.tool,
            "from_label": step.label, "reason": failure_reason,
            "to_index": target, "to_label": target_label,
            "strike": strikes, "jumped": True})
        try:
            self._log_activity_line(
                "plan_jump",
                f"task {task.title!r}: step {step.label!r} failed "
                f"({failure_reason}); jumping to step {target + 1} "
                f"({target_label!r})")
        except Exception:  # noqa: BLE001 — logging never breaks a jump
            pass
        # Rewind: outcomes past the target belong to the pass
        # being rewound — they are re-produced if those steps
        # re-run. The failure itself survives in the jump
        # event above (its only record); the outcomes list
        # keeps exactly one entry per step, latest outcome
        # each, so verification keeps indexing by position.
        del task.outcomes[target:]
        task._retry_resume_step = None
        task._index = target
        self._ledger_write(task)
        return True

    # -- B21: crash-safe re-run (audit L14) ------------------
    #
    # Loophole family: "did it apply or not?" — a step
    # interrupted by a crash mid-execution has no durable
    # record of whether it applied, so the resume re-runs
    # it blindly and it can apply twice.
    #
    # Gap, proved from the tree before building: the ledger
    # caps args at 4000 chars (task_ledger._MAX_ARG_BLOB)
    # and a truncated blob is replaced on rebuild, while
    # _advance_index (task_ledger.py:201) advances past
    # success/skipped only — "a step with no outcome (crash
    # mid-step) likewise never advances". run_task writes
    # the ledger at start (state running) and per step only
    # AFTER the outcome is appended, so a crash between the
    # tool applying and that write leaves no outcome and the
    # step re-runs blindly on resume.
    #
    # ONE design: a durable per-step intent + outcome record
    # ("step-runs") on the same ledger record — before a step
    # executes, the intent (step, tool, args digest, unique
    # step-run id) is ledger-written; after it executes, the
    # outcome is recorded against the same id. On resume, a
    # step whose intent is recorded but whose outcome is
    # missing is reconciled first (re-read the world through
    # the EXISTING verifier — the B10 goal-check shape: the
    # expected end state is judged true now, with no before
    # snapshot) and only re-run if the reconciliation says
    # it did not apply. A step whose outcome IS recorded is
    # never re-run — the outcome record carries the verdict,
    # not the args, so a truncated args blob changes nothing.
    # And a step whose outcome was recorded but never placed
    # — the crash landed between the outcome write and the
    # run loop's append, or aborted a parallel-group join
    # before it placed the branch's outcome — is recovered,
    # not re-run: its recorded success stands, with its
    # recorded kind (never re-judged, never re-executed).
    #
    # Rejected: re-running blindly on resume (today's
    # behaviour — the double-apply itself); never re-running
    # an interrupted step (a step that never applied would
    # be skipped, work undone — the opposite failure);
    # raising the args cap (a bigger cap still truncates,
    # and the cap is not the gap); a second ledger for
    # intents (one home — the intent rides the same record,
    # like B11's goal records); trusting the funnel return
    # (there is none — the crash ate it); auto-answering
    # "unknown" as not-applied (a guess that re-runs an
    # effect that may have landed — the exact hole).
    #
    # When the world cannot be re-read, the step pauses
    # with _pending_reconcile and the human decides
    # (resolve_reconcile) — never guessed, never
    # auto-answered, never burning budget or strikes (the
    # step never ran; reconciliation itself is read-only:
    # a snapshot + a verifier call, no effects).
    #
    # Residual, named plainly: a crash DURING a cancelled
    # step (between the stop and the outcome write) is
    # indistinguishable in the ledger from a normal crash —
    # the cancel flag is in-memory only. Reconciliation is
    # read-only, and the unknowable case pauses for the
    # human, so a cancelled step is never silently re-run;
    # the "not-applied" re-run after a crash-during-cancel
    # is the one window this batch cannot close from the
    # ledger alone.
    #
    # A retried step gets a NEW step-run id: every dispatch
    # mints one, so a verification-phase retry, a jump
    # re-run, and a resume re-run are each a new attempt,
    # never the same attempt twice. Each parallel-group
    # branch mints its own id at its own index and
    # reconciles on its own — a branch never re-runs its
    # siblings. A cancelled step's outcome is recorded as
    # cancelled and is never reconciled (a resolved run is
    # not a pending intent).

    def _mint_step_run(self, task, step, step_args, index) -> str:
        """Record a step's intent durably, before it executes.

        Mints a unique step-run id, appends the intent
        {step_run_id, step_index, tool, label, args_digest,
        status "started", resolution None} to task._step_runs
        under the step lock, and ledger-writes — the write
        lands BEFORE the dispatch, so a crash mid-step
        leaves the intent in the file. The args digest is
        the confirm gate's _canonical_args_digest over the
        DISPATCH args (the copy _run_step built, with the
        answer injected and the re-issue flag set) — the
        digest names what was actually attempted, not the
        plan's declared args. The step lock is held across
        the append + the write, so a parallel-group branch
        never serializes the record mid-append (the B19
        "branches never write the ledger" rule gets one
        documented exception here: intent writes are the
        only branch ledger writes, and they are
        lock-serialized by the ledger lock). Never raises:
        a ledger failure leaves the intent in-memory-only,
        like every other ledger write — the step still runs.
        """
        import uuid

        run_id = uuid.uuid4().hex
        try:
            from ..confirm_gate_helpers import _canonical_args_digest
            digest = _canonical_args_digest(step_args)
        except Exception:  # noqa: BLE001 — a missing digest is not a break
            digest = ""
        entry = {
            "step_run_id": run_id,
            "step_index": int(index),
            "tool": str(step.tool),
            "label": str(step.label),
            "args_digest": str(digest),
            "status": "started",
            "kind": "",
            "message": "",
            "resolution": None,
        }
        try:
            _lock = getattr(self, "_step_lock", None) \
                or _FALLBACK_STEP_LOCK
            with _lock:
                runs = getattr(task, "_step_runs", None)
                if runs is None:
                    task._step_runs = runs = []
                runs.append(entry)
                self._ledger_write(task)
        except Exception:  # noqa: BLE001 — intent failure never breaks a step
            pass
        return run_id

    def _record_step_run_outcome(self, task, run_id, outcome,
                                 resolution: str) -> bool:
        """Record one attempt's outcome against its step-run id.

        Finds the run by id, sets status/kind/message from
        the StepOutcome and the resolution ("recorded" |
        "reconciled" | "superseded"), and ledger-writes — the
        pair (intent + outcome) is what makes the resume
        answerable. Returns False when no run carries the id
        (a step that never minted one — a probe, a pre-
        dispatch return — records nothing). Never raises.
        """
        try:
            _lock = getattr(self, "_step_lock", None) \
                or _FALLBACK_STEP_LOCK
            with _lock:
                found = None
                for entry in getattr(task, "_step_runs", []) or []:
                    if isinstance(entry, dict) and entry.get(
                            "step_run_id") == run_id:
                        found = entry
                        break
                if found is None:
                    return False
                found["status"] = str(outcome.status)
                found["kind"] = str(outcome.kind or "")
                found["message"] = str(outcome.message or "")
                found["resolution"] = str(resolution)
                self._ledger_write(task)
                return True
        except Exception:  # noqa: BLE001 — recording never breaks a step
            return False

    def _pending_step_run(self, task, index):
        """The latest unresolved step-run for one step, or None.

        Unresolved means status "started" with resolution
        None: the intent is recorded and the outcome is not.
        A resolved run (recorded, reconciled, superseded) is
        not pending — it was decided already. The latest
        entry wins: a new attempt supersedes the old one for
        its index.
        """
        try:
            for entry in reversed(
                    getattr(task, "_step_runs", []) or []):
                if not isinstance(entry, dict):
                    continue
                # (0 is a valid index — never `or -1` here: a
                # falsy 0 would be mistaken for "missing".)
                try:
                    _idx = int(entry.get("step_index", -1))
                except (TypeError, ValueError):
                    continue
                if _idx != int(index):
                    continue
                if str(entry.get("status", "")) == "started" \
                        and entry.get("resolution") is None:
                    return entry
        except Exception:  # noqa: BLE001 — a bad entry is not a run
            return None
        return None

    def _resolve_step_run(self, task, run, resolution, status,
                          kind, message) -> bool:
        """Resolve one step-run in memory (no ledger write).

        The caller owns the write (the run loop's write, the
        group's join write, or the resolve path's write) — so
        a resolve never writes twice for one decision.
        Returns False when there is no run to resolve.
        """
        if not isinstance(run, dict):
            return False
        try:
            run["resolution"] = str(resolution)
            run["status"] = str(status)
            run["kind"] = str(kind or "")
            run["message"] = str(message or "")
            return True
        except Exception:  # noqa: BLE001 — resolving never breaks a task
            return False

    def _supersede_step_run(self, task, run) -> bool:
        """Mark a stale intent superseded, durably.

        The old attempt is never re-decided: the step is
        about to run again with a NEW step-run id, and only
        the new attempt can reconcile. Ledger-writes so the
        supersede survives a crash before the re-run.
        """
        if not isinstance(run, dict):
            return False
        try:
            _lock = getattr(self, "_step_lock", None) \
                or _FALLBACK_STEP_LOCK
            with _lock:
                run["resolution"] = "superseded"
                self._ledger_write(task)
                return True
        except Exception:  # noqa: BLE001 — superseding never breaks a task
            return False

    def _latest_step_run(self, task, index):
        """The latest step-run for one step, any state, or None.

        Unlike _pending_step_run (unresolved only), this one
        also finds a RESOLVED attempt — the case where the
        outcome was recorded against its step-run id but the
        crash landed between that write and the run loop's
        outcome append (or the group's join), so the outcomes
        list has no entry at this position. The recorded
        success stands: the step is "applied", never re-run.
        """
        try:
            for entry in reversed(
                    getattr(task, "_step_runs", []) or []):
                if not isinstance(entry, dict):
                    continue
                try:
                    _idx = int(entry.get("step_index"))
                except (TypeError, ValueError):
                    continue
                if _idx == int(index):
                    return entry
        except Exception:  # noqa: BLE001 — a bad entry is not a run
            return None
        return None

    def _reconciled_outcome(self, task, index, reason):
        """Build the success outcome for a reconciled-as-applied step.

        Returns (outcome, run). A pending run resolves as
        kind "reconciled" (the verifier judged the world);
        an already-resolved success run keeps its recorded
        kind (its success was recovered, not judged — the
        step ran to success before the crash, and the outcome
        was recorded, even though the crash ate the loop's
        append). Returns (None, None) when the step has no
        step-run to stand on — the caller never records a
        reconciliation out of nothing.
        """
        step = task.steps[index]
        run = self._pending_step_run(task, index)
        if run is not None:
            kind = "reconciled"
        else:
            latest = self._latest_step_run(task, index)
            if latest is None \
                    or str(latest.get("status", "")) not in (
                        "success", "skipped") \
                    or str(latest.get("resolution", "")) not in (
                        "recorded", "reconciled"):
                return None, None
            kind = str(latest.get("kind", "") or "")
        outcome = StepOutcome(
            tool=step.tool, label=step.label, status="success",
            kind=kind, message=str(reason or ""))
        return outcome, run

    def _reconcile_pending_intent(self, task, index):
        """Decide whether an interrupted step applied, or not.

        Returns (verdict, reason, pending_record). Verdicts:
        - "none": no pending intent for this step — a fresh
          run, or an already-decided attempt. Run normally.
        - "applied": the world says the step's effect is
          true now — record a reconciled success, do NOT
          re-run it.
        - "not-applied": the world says the effect is absent
          (or the step is a pure local read, which has no
          effects to duplicate) — the old intent is
          superseded here, and the step runs fresh with a
          NEW step-run id.
        - "unknown": the world cannot be re-read — the step
          pauses with _pending_reconcile (the pending record
          carries the question + the reason) and the human
          decides. Never guessed, never auto-answered.

        Decision order, each fail-closed: (0) a recorded
        success stands even when the outcomes list has no
        entry at the step's position — the outcome was
        recorded against its step-run id, but the crash
        landed between that write and the run loop's outcome
        append (or the group's join, which never placed it),
        so the step looks unfinished to the loop while its
        attempt record says it succeeded; (1) a recorded
        outcome at the step's position stands — the step
        already ran to a verdict, so there is nothing to
        reconcile; (2) a pure local read is safe to re-run
        (the remaining_all_reads predicates — read-class
        AND locally-only, network reads excluded); (3) a
        declared expectation is judged against a fresh
        snapshot through the EXISTING verifier (_verify_one
        with a synthetic outcome: before None, result text
        "" — the B10 goal-check shape, "is the expected end
        state true now?"); (4) anything else is unknown.

        Decision only — it never mutates the step-run
        except superseding a "not-applied" intent (so no
        later call can re-decide it). The caller acts on
        the verdict.
        """
        step = task.steps[index]
        recorded = self._recorded_outcome(task, index)
        if recorded is None:
            # (0) The step already ran to success — its
            # outcome was recorded against its step-run id,
            # but the crash ate the loop's append (or the
            # group's join). The recorded success stands,
            # never re-run: re-running a step that already
            # applied is the double-apply itself.
            _latest = self._latest_step_run(task, index)
            if _latest is not None \
                    and str(_latest.get("status", "")) in (
                        "success", "skipped") \
                    and str(_latest.get("resolution", "")) in (
                        "recorded", "reconciled"):
                # B4 JUMP GUARD (neighbour-caught, B21): a
                # jump target re-runs ON PURPOSE. _take_jump
                # truncates the outcomes list to its target,
                # so the target's previous-pass success is
                # still in its step-run while no outcome sits
                # at its position — exactly the shape this
                # check recovers. But the jump IS the
                # instruction to re-run it: the resolved
                # success is the PREVIOUS pass's attempt, not
                # this pass's. The latest TAKEN jump event
                # (ledger-serialized, so it survives a crash
                # between the jump and the re-run) names the
                # step to re-run — reconciliation must not
                # skip it. The guard fires only when the
                # latest run is a resolved success (a crash
                # MID-RE-RUN leaves a PENDING run instead,
                # and that reconciles normally below).
                _jump_target = False
                try:
                    for _event in reversed(
                            getattr(task, "_jumps", []) or []):
                        if not isinstance(_event, dict):
                            continue
                        if not _event.get("jumped"):
                            continue
                        _jump_target = (
                            int(_event.get("to_index", -1))
                            == int(index))
                        break
                except (TypeError, ValueError):
                    _jump_target = False
                if _jump_target:
                    return ("none",
                            "jump target — the previous pass's "
                            "success does not stand for this pass; "
                            "the jump re-runs it on purpose",
                            None)
                return ("applied",
                        "recorded success stands — the step ran "
                        "to success before the crash and its "
                        "outcome was recorded",
                        None)
        run = self._pending_step_run(task, index)
        if run is None:
            return ("none", "", None)
        if recorded is not None:
            # The step already ran to a verdict — the
            # outcome stands, and the stale intent is
            # superseded so it never reconciles again.
            self._supersede_step_run(task, run)
            return ("none",
                    "recorded outcome stands; nothing to reconcile",
                    None)
        # (2) A pure local read has no effects to duplicate:
        # re-running it cannot double-apply anything.
        try:
            from ..funnel.tools_gate import (
                _is_egress_read, _is_read_class)
            _is_read = _is_read_class(step.tool, dict(step.args or {})) \
                and not _is_egress_read(step.tool)
        except Exception:  # noqa: BLE001 — predicate failure is not a read
            _is_read = False
        if _is_read:
            self._supersede_step_run(task, run)
            return ("not-applied",
                    f"step {step.label!r} is a pure local read — "
                    "re-running it cannot double-apply anything",
                    None)
        # (3) A declared expectation is judged against the
        # world as it stands now, through the existing
        # verifier. No before snapshot (post-rebuild there
        # is none — the B10 goal-check shape) and no result
        # text (the interrupted attempt produced none).
        expect = str(getattr(step, "expect", "") or "")
        if expect:
            try:
                _synth = StepOutcome(
                    tool=step.tool, label=step.label,
                    status="success", kind="", message="")
                verdict, v_reason = self._verify_one(
                    task, index, step, _synth)
            except Exception as exc:  # noqa: BLE001 — verifier failure is unknown
                verdict, v_reason = (
                    "unverifiable", f"reconciliation check failed: {exc}")
            verdict = str(verdict or "").strip().lower()
            if verdict == "met":
                return ("applied",
                        f"reconciled: the expected state is true now "
                        f"({v_reason})",
                        None)
            if verdict == "not-met":
                self._supersede_step_run(task, run)
                return ("not-applied",
                        f"reconciled: the expected state is absent "
                        f"({v_reason})",
                        None)
            _unknown_reason = (
                "the expectation could not be judged — the world "
                f"could not be re-read ({v_reason})")
        else:
            _unknown_reason = (
                "no expectation was declared for this step, so the "
                "world cannot be re-read to tell whether it applied")
        # (4) Unknown: pause for the human. The pending
        # record carries the question + the reason; only
        # resolve_reconcile decides it, never voice (C-14).
        pending = {
            "step_index": int(index),
            "tool": str(step.tool),
            "label": str(step.label),
            "question": (
                f"Did step {step.label!r} apply before the crash?"),
            "reason": _unknown_reason,
        }
        return ("unknown", _unknown_reason, pending)

    def _apply_reconciled(self, task, index, reason) -> bool:
        """Record a reconciled-as-applied step as success.

        Appends the StepOutcome at the step's position —
        replace in place when an outcome already sits there,
        append otherwise (the run loop's own rule) — resolves
        the pending intent when one exists, and ledger-writes.
        A resolved-success run keeps its recorded kind (its
        success was recovered, not judged). The loop's
        skip-past advances over the success on the next
        iteration, so the step never runs. Returns False
        when the step has no step-run to stand on.
        """
        outcome, run = self._reconciled_outcome(task, index, reason)
        if outcome is None:
            return False
        step = task.steps[index]
        try:
            _lock = getattr(self, "_step_lock", None) \
                or _FALLBACK_STEP_LOCK
            with _lock:
                if index < len(task.outcomes) \
                        and task.outcomes[index].tool == step.tool \
                        and task.outcomes[index].label == step.label:
                    task.outcomes[index] = outcome
                else:
                    task.outcomes.append(outcome)
                if run is not None:
                    self._resolve_step_run(
                        task, run, "reconciled", "success",
                        "reconciled", str(reason or ""))
                self._ledger_write(task)
                return True
        except Exception:  # noqa: BLE001 — reconciling never breaks a task
            return False

    def _verify_one(self, task: Task, index: int, step: StepDef,
                    outcome: StepOutcome):
        """One expectation check for one step: (verdict, reason).

        The existing dispatch, extracted verbatim: a menu step's
        expectation goes to the menu consequence verifier with
        the step's raw executor output as invoke-result evidence;
        every other step goes to the generic engine with the
        step outcome's message as result text. Snapshot and
        verifier failures degrade to "unverifiable", never a
        guessed verdict, never a break; unknown verdict strings
        normalize to "unverifiable".
        """
        expect = getattr(step, "expect", "") or ""
        try:
            after = self._snapshot()
        except Exception:  # noqa: BLE001 — snapshot failure is unobserved
            after = None
        try:
            if step.tool == "menu_action":
                # §7 consumers batch 4: a menu step's
                # expectation is judged by the menu consequence
                # verifier, with the step's raw executor output
                # as invoke-result evidence (menu-opened needs
                # the opened path; the snapshot shapes still
                # delegate to the generic engine inside that
                # verifier).
                verdict, reason = self._menu_verifier(
                    expect, task._verify_before.get(index),
                    after, task._verify_result.get(index))
            else:
                verdict, reason = self._verifier(
                    expect, task._verify_before.get(index),
                    after, outcome.message)
        except Exception as exc:  # noqa: BLE001 — verifier failure is unverifiable
            verdict, reason = ("unverifiable",
                               f"verification callback failed: {exc}")
        verdict = str(verdict or "").strip().lower()
        if verdict not in ("met", "not-met", "unverifiable"):
            verdict = "unverifiable"
        return (verdict, str(reason or ""))

    def _check_goal(self, task: Task) -> tuple[str, str]:
        """B10 — ONE goal check: the goal text (task.title)
        judged against a fresh snapshot through the EXISTING
        verifier (self._verifier — the same callback the step
        phase uses, default core.desktop.uia.
        verify_expectation). Returns (verdict, reason),
        verdict in {"met", "not-met", "unverifiable"}.

        The goal check asks whether the goal STATE is true
        now, not whether the task caused it — so there is no
        "before" snapshot (a step's "appears" check needs one
        to prove the step made it appear; a goal's does not:
        a goal whose end state is already true is achieved).
        The step results ride along as result text — the
        verifier's OCR fallback — so a goal grounded in what
        the steps produced can still be judged. A snapshot or
        verifier failure degrades to "unverifiable", never a
        guessed verdict, never a break.

        Rejected alternatives (recorded, do not re-open):
        asking the picker/brain "are we done?" as the check
        (self-certification — the exact hole this closes);
        re-running all steps on not-met (that's B22
        re-planning, not verification); a second full
        verification phase (the step phase stays as it is;
        the goal check is one check, not a phase).
        """
        try:
            after = self._snapshot()
        except Exception:  # noqa: BLE001 — snapshot failure is unobserved
            after = None
        result_text = "\n".join(
            str(o.message or "") for o in task.outcomes if o.message)
        try:
            verdict, reason = self._verifier(task.title, None, after,
                                             result_text)
        except Exception as exc:  # noqa: BLE001 — verifier failure is unverifiable
            verdict, reason = ("unverifiable",
                               f"verification callback failed: {exc}")
        verdict = str(verdict or "").strip().lower()
        if verdict not in ("met", "not-met", "unverifiable"):
            verdict = "unverifiable"
        return (verdict, str(reason or ""))

    def _retry_strikes(self, task: Task, index: int) -> int:
        """Consecutive not-met verdicts for one step in this
        task's retry history (planner's reset-on-met rule: a
        met verdict resets the count, so only the trailing run
        of not-met events for this step counts). The history is
        the single source — it survives phase re-runs and
        ledger rebuilds, so a crash grants no fresh retries."""
        strikes = 0
        for event in reversed(getattr(task, "_retries", []) or []):
            if event.get("step_index") != index:
                continue
            if event.get("verdict") == "not-met":
                strikes += 1
                continue
            break
        return strikes

    def _record_retry(self, task: Task, index: int, step: StepDef,
                      verdict: str, reason: str, strike: int,
                      retried: bool) -> None:
        """One retry-history event (ledger-written with the
        next transition write at the latest — every return path
        below ledger-writes, and this write makes the event
        durable even if the caller returns right after)."""
        task._retries.append({
            "step_index": index,
            "tool": step.tool,
            "label": step.label,
            "verdict": verdict,
            "reason": str(reason or ""),
            "strike": strike,
            "retried": bool(retried),
        })
        self._ledger_write(task)

    def _run_verification_phase(self, task: Task,
                                from_index: int = 0) -> bool:
        """The verification phase: after action, before report.

        Returns True when the task stays completed, False
        otherwise (failed, cancelled, or paused for approval —
        the task state is authoritative; run_task's receipt and
        mission handling both branch on it). The task state is
        "verifying" for the phase's duration (ledger-written:
        the real producer the D9 rule requires); the mission
        bus carries phase "verify". Each executed step's
        expectation is checked with its captured
        before-snapshot and a fresh after-snapshot; skipped
        steps record a "skipped" entry (never checkable).
        A "met"/"unverifiable" verdict completes the step with
        the verdict recorded ("unverifiable" never strikes and
        never retries — an inconclusive verdict means the
        effect's state is unknown, and re-running an action
        whose effect may already have landed could
        double-apply it). A "not-met" verdict retries the SAME
        step's action through _run_step (same executor, same
        gates, same confirm-stop handling) until the
        three-strikes limit; a retried step that then meets
        its expectation completes normally, with its misses
        still in the retry history. The phase never raises: a
        verifier/snapshot failure degrades to "unverifiable".
        Cancel wins at three points (phase entry, before every
        retry re-run, before the phase completes): a cancelled
        task ends cancelled, never completed by the phase, and
        no action runs after cancel. The phase fires no step
        callbacks (it is not the action loop); a callback
        pause/cancel mid-retry is therefore not a supported
        seam — cancel_task's flag is the supported one.

        B22 ``from_index``: on a re-plan pass only steps from
        this index are verified (the steps the re-plan
        appended — the ones that just ran). Older steps were
        verified when they ran; their verdicts stand (the
        list is NOT reset when from_index > 0) and they are
        never retried here — re-verifying them could re-run
        a step that already succeeded. A resumed task always
        passes 0 (the resumed-task rule: the phase re-runs in
        full).
        """
        # Cancel wins before the phase even starts: a callback
        # after the last action step may have cancelled the
        # task, and completing it here would contradict that.
        # Fix 9: a failsafe trip wins the same way for marked
        # tasks; a session restart pauses instead (below).
        _stop = self._stop_reason(task)
        if _stop is not None:
            task._cancelled = True
            if _stop:
                task._cancel_reason = _stop
            task.state = "cancelled"
            self._ledger_write(task)
            return False
        _pause = self._pause_reason(task)
        if _pause is not None:
            task._paused = True
            task._pause_reason = _pause
            task.state = "paused"
            self._ledger_write(task)
            return False
        task.state = "verifying"
        # A resumed task re-runs the phase in full, so verdicts
        # restart here; the ledger file keeps the previous run's
        # verdicts until this phase's first verdict write. The
        # retry history does NOT restart (it lives on the task,
        # restored from the ledger on rebuild), so the strike
        # budget survives the re-run. B22: a re-plan pass keeps
        # the earlier passes' verdicts (from_index > 0 skips
        # the reset) — only the new steps are judged here.
        if from_index <= 0:
            task.verification = []
        self._ledger_write(task)
        emit_mission(goal=task.title, phase="verify",
                     step=len(task.steps), steps_total=len(task.steps),
                     outcome="running")
        for index, step in enumerate(task.steps):
            # B22: a re-plan pass verifies only the re-planned
            # steps — older steps were verified when they ran.
            if index < from_index:
                continue
            expect = getattr(step, "expect", "") or ""
            if not expect:
                continue
            outcome = (task.outcomes[index]
                       if index < len(task.outcomes) else None)
            if outcome is None or outcome.status != "success":
                task.verification.append({
                    "tool": step.tool, "label": step.label,
                    "expect": expect, "verdict": "skipped",
                    "reason": "step did not succeed; "
                              "expectation not checked"})
                self._ledger_write(task)
                continue
            # Verify this step, retrying on not-met until the
            # strike limit. Each loop pass re-checks the CURRENT
            # outcome (the initial one, then each retry's), so
            # verification never reads a stale outcome message.
            while True:
                verdict, reason = self._verify_one(
                    task, index, step, outcome)
                if verdict != "not-met":
                    # A met verdict after strikes resets them
                    # (planner's rule), recorded as a reset
                    # event so a later phase re-run counts
                    # strikes from zero, not from history.
                    if verdict == "met" \
                            and self._retry_strikes(task, index) > 0:
                        self._record_retry(task, index, step,
                                           "met", reason, 0, False)
                    task.verification.append({
                        "tool": step.tool, "label": step.label,
                        "expect": expect, "verdict": verdict,
                        "reason": reason})
                    self._ledger_write(task)
                    break
                strikes = self._retry_strikes(task, index) + 1
                _stop = self._stop_reason(task)
                if _stop is not None:
                    # Cancelled before the retry: the not-met
                    # verdict stands (it was computed), but no
                    # action runs after cancel. Fix 9: a stop
                    # signal wins the same way for marked tasks.
                    if _stop:
                        task._cancel_reason = _stop
                    task._cancelled = True
                    self._record_retry(task, index, step,
                                       "not-met", reason, strikes,
                                       False)
                    task.verification.append({
                        "tool": step.tool, "label": step.label,
                        "expect": expect, "verdict": "not-met",
                        "reason": reason})
                    task.state = "cancelled"
                    self._ledger_write(task)
                    return False
                _pause = self._pause_reason(task)
                if _pause is not None:
                    # Paused before the retry: the not-met
                    # verdict stands (it was computed), but no
                    # action runs after pause. Fix 9: a session
                    # restart pauses instead of cancelling, so
                    # the retry is still owed on resume — the
                    # strike is recorded below, so the budget
                    # survives the pause.
                    task._paused = True
                    task._pause_reason = _pause
                    self._record_retry(task, index, step,
                                       "not-met", reason, strikes,
                                       False)
                    task.verification.append({
                        "tool": step.tool, "label": step.label,
                        "expect": expect, "verdict": "not-met",
                        "reason": reason})
                    task.state = "paused"
                    self._ledger_write(task)
                    return False
                if strikes >= _RETRY_STRIKE_LIMIT:
                    self._record_retry(task, index, step,
                                       "not-met", reason, strikes,
                                       False)
                    task._verify_error = (
                        f"verification failed: step {step.label!r} "
                        f"expected {expect!r} — {reason} "
                        f"(after {_RETRY_STRIKE_LIMIT} strikes)")
                    task.verification.append({
                        "tool": step.tool, "label": step.label,
                        "expect": expect, "verdict": "not-met",
                        "reason": reason})
                    task.state = "failed"
                    self._ledger_write(task)
                    return False
                self._record_retry(task, index, step,
                                   "not-met", reason, strikes, True)
                # Retry re-run: exactly this step, through the
                # same _run_step the action loop uses (when-
                # probe, approval re-issue, confirm stops,
                # refusal rewrites all apply). _index must point
                # at the step while it runs (before-snapshot,
                # menu invoke-result, pending record, and
                # re-issue matching all key off it), then
                # returns to the phase's position — except on
                # a confirm stop, where it stays pointing at
                # the pending step for the resume path.
                saved_index = task._index
                task._index = index
                emit_step(n=index + 1, m=len(task.steps),
                          label=step.label)
                emit_tool_started(kind=step.tool)
                new_outcome = self._run_step(task, step)
                emit_tool_finished(status=new_outcome.status,
                                   kind=step.tool)
                if new_outcome.status == "pending":
                    # §7 consumers batch 6, retry edition: the
                    # retry re-run hit an engine confirm stop
                    # — a question, not a failure. The task
                    # pauses with the pending record _run_step
                    # set (step_index == index); the marker
                    # tells the run loop's resume path to
                    # replace this step's outcome in place and
                    # jump to verification, never re-running
                    # later steps. The stale success outcome
                    # stays until resolve_pending replaces it
                    # — the pending run produced no outcome,
                    # exactly like the run loop's pending branch.
                    task._retry_resume_step = index
                    task.state = "paused"
                    task._paused = True
                    self._ledger_write(task)
                    return False
                task._index = saved_index
                # Replace the step's outcome in place (same
                # positional-alignment rule as the run-loop
                # resume path); if the list does not hold this
                # step at this position (the pre-existing
                # unknown-outcome resume quirk), leave the list
                # untouched — verification below already uses
                # the new outcome directly, so the verdict stays
                # correct even then.
                if index < len(task.outcomes) \
                        and task.outcomes[index].tool == step.tool \
                        and task.outcomes[index].label == step.label:
                    task.outcomes[index] = new_outcome
                if new_outcome.status == "skipped":
                    # The retry action skipped per on_fail=skip:
                    # like any skipped step, its expectation was
                    # never checkable — record that and move on
                    # (never re-verify a step that did not
                    # succeed; the phase's entry rule for
                    # non-success outcomes applies here too).
                    task.verification.append({
                        "tool": step.tool, "label": step.label,
                        "expect": expect, "verdict": "skipped",
                        "reason": "step did not succeed; "
                                  "expectation not checked"})
                    self._ledger_write(task)
                    break
                if new_outcome.status == "cancelled":
                    # B20: the retry re-run itself was stopped
                    # by the user — the task is cancelled, not
                    # failed (nothing failed; a failure here
                    # would strike the step and name it as a
                    # retry failure, both wrong for a stop).
                    # The cancelled outcome is already in place
                    # above, so partial progress stays in the
                    # ledger (B11); no goal check runs on a
                    # cancelled task (B10).
                    task.verification.append({
                        "tool": step.tool, "label": step.label,
                        "expect": expect, "verdict": "skipped",
                        "reason": "retry action cancelled "
                                  "at the user's request; "
                                  "expectation not re-checked"})
                    task._cancel_reason = (
                        f"stopped during retry of step {index + 1} "
                        f"({step.label}): "
                        f"{new_outcome.message or new_outcome.status}")
                    task._cancelled = True
                    task.state = "cancelled"
                    self._ledger_write(task)
                    return False
                if new_outcome.status not in ("success", "skipped"):
                    # The retry action itself failed (refusal,
                    # gate refusal, when-probe halt): a refusal
                    # is a decision, not a miss — it is never
                    # retried again. The task fails honestly;
                    # the receipt's failed branch names the
                    # replaced failure outcome (or, in the
                    # quirk case, the last verification entry
                    # below names the retry failure).
                    task.verification.append({
                        "tool": step.tool, "label": step.label,
                        "expect": expect, "verdict": "skipped",
                        "reason": "retry action failed "
                                  f"({new_outcome.status}); "
                                  "expectation not re-checked"})
                    task.state = "failed"
                    self._ledger_write(task)
                    return False
                outcome = new_outcome
        # Cancel wins over completion: a cancel that landed
        # during the phase (callback, or another thread's
        # cancel_task) must not let the phase complete the task.
        # Fix 9: a failsafe trip wins the same way for marked
        # tasks; a session restart pauses instead (below).
        _stop = self._stop_reason(task)
        if _stop is not None:
            task._cancelled = True
            if _stop:
                task._cancel_reason = _stop
            task.state = "cancelled"
            self._ledger_write(task)
            return False
        _pause = self._pause_reason(task)
        if _pause is not None:
            task._paused = True
            task._pause_reason = _pause
            task.state = "paused"
            self._ledger_write(task)
            return False
        task.state = "completed"
        self._ledger_write(task)
        return True

    def _dispatch_args(self, step: StepDef) -> dict:
        """Step args as dispatched: copy + declared-handle injection.

        §7 consumers batch 3 rule, shared by _run_step and the
        batch-6 re-issue: the step's own args are never
        mutated — injection lands on a copy — and an explicit
        arg always wins over the declared handle. Tools
        outside the map (including drag_drop_action, whose two
        handle args one id cannot name without guessing) get
        no injection; a handle_id there is ignored, never
        guessed. The funnel then records the handle in the
        receipt (explicit path), and the handler's own
        production threads forward to later steps' receipts.
        """
        step_args = dict(step.args)
        try:
            from ..desktop import s7_threading as _s7t  # Phase 3 S7a-2: moved to core/desktop/

            handle_arg = _s7t.step_handle_arg(step.tool)
        except Exception:  # noqa: BLE001 — no injection, never a break
            handle_arg = None
        declared = getattr(step, "handle_id", "") or ""
        if declared and handle_arg and handle_arg not in step_args:
            step_args[handle_arg] = declared
        return step_args

    def _second_opinion_high_stakes(self, step) -> bool:
        """Would the check run for this step, if the pair existed?

        B24: the same high-stakes rule _second_opinion_check
        applies (a read-only tool never qualifies; menu_action
        qualifies only as an invoke of a destructive leaf; any
        other step qualifies when its label's first word is
        destructive — "Save" is deliberately absent from that
        set, inherited) — factored out so the OFF path can ask
        the same question without duplicating the rule. Raises
        on an import failure, exactly like the inline code
        did: the caller decides what a failure means (the
        check path pauses with an error event; the OFF path
        records nothing for a step the check could not have
        judged).
        """
        from ..desktop.menu_paths import is_destructive_leaf
        from ..funnel.tools_gate_readonly import _READ_ONLY_TOOLS
        if step.tool in _READ_ONLY_TOOLS:
            return False
        if step.tool == "menu_action":
            action = str((step.args or {}).get("action", "") or ""
                         ).strip().lower()
            if action != "invoke":
                return False
            leaf = _menu_leaf(step)
            return bool(leaf) and bool(is_destructive_leaf(leaf))
        return bool(is_destructive_leaf(step.label or ""))

    def _record_second_opinion_off(self, task) -> None:
        """Record the second-opinion OFF line once per task.

        B24: called at the first check that would have run
        when the companion pair was None. The missing legs
        come from the holder's own marker
        (companion_missing_legs — "groq key" / "groq session"
        / "local model" / "local model loading" / "companion
        holder not started"), so the line names precisely
        what is missing; the holder says the pair is ready
        but this coordinator was never filled with it, the
        line says the pair is not attached. The record is
        {"missing": [...], "line": str} on the task, and the
        ledger is written at once (the _mint_step_run
        pattern — under the step lock, so two parallel
        branches can never record twice, and durable before
        the step runs). Never raises: a ledger failure
        leaves the record in-memory-only, like every other
        ledger write — the step still runs.
        """
        if isinstance(getattr(task, "_second_opinion_off", None), dict) \
                and task._second_opinion_off.get("line"):
            return
        try:
            from . import companion_sessions as _cs
            missing = _cs.companion_missing_legs()
        except Exception:  # noqa: BLE001 — holder unconsultable
            missing = ["companion holder not started"]
        missing = [str(m or "").strip() for m in (missing or [])]
        missing = [m for m in missing if m]
        if not missing:
            # The holder says the pair is ready, but this
            # coordinator was never filled with it (a stashed
            # coordinator from before the pair was ready) —
            # the check is OFF because the pair is not
            # attached to this task, and the line says so.
            missing = ["companion pair not attached"]
        try:
            from .task_coordinator_steps import second_opinion_off_line
            line = second_opinion_off_line(missing)
        except Exception:  # noqa: BLE001 — line failure never breaks a step
            return
        try:
            _lock = getattr(self, "_step_lock", None) or _FALLBACK_STEP_LOCK
            with _lock:
                if isinstance(getattr(task, "_second_opinion_off", None),
                              dict) \
                        and task._second_opinion_off.get("line"):
                    return
                task._second_opinion_off = {"missing": list(missing),
                                            "line": line}
                self._ledger_write(task)
        except Exception:  # noqa: BLE001 — OFF recording never breaks a step
            pass

    def _second_opinion_check(self, task, step, index=None):
        """Pre-action second-opinion check for high-stakes steps.

        B19: ``index`` names the step when the caller is not
        running it as the task's current step (a parallel-group
        branch runs at its own index while task._index still
        points at the group's first step). None means "the
        task's current step", exactly as before — the
        "already checked" guard keys off the step's own index,
        so two branches never check the same step.

        Returns (sentence, event): sentence is None when the
        step proceeds (no pause), else the pending dialog
        sentence carrying both brains' views; event is None
        when the step was not checked at all (feature off,
        already checked, or not high-stakes), else the history
        event the caller appends exactly once. The caller
        appends the event and, for a pause, sets the pending
        record and returns the pending outcome — this method
        itself mutates nothing, so there is exactly one append
        point and no double-record path.

        Trigger (all must hold): the coordinator was constructed
        with a second-opinion brain pair (None means OFF — no
        check, no pause, no record); the step is not already in
        the task's second-opinion history for this index; and
        the step is high-stakes — its tool is NOT in the
        funnel's _READ_ONLY_TOOLS set AND (it is a menu_action
        invoke whose leaf is destructive per
        core.desktop.menu_paths.is_destructive_leaf, or any
        other step whose label's first word is destructive per
        the same function; "Save" is deliberately absent from
        that set, inherited). menu_action list steps never
        qualify (listing acts on nothing).

        Verdict mapping, fail-closed toward human judgment —
        brains advise, never decide: verdict "agree" with both
        normalized answers "yes" proceeds (record only);
        agreement on anything else, disagreement (including
        formatting variants — the module compares exact
        normalized text and nothing here fuzzes that), and
        "unavailable" all pause with both views in the pending
        sentence; a check failure of any kind pauses once with
        an "error" event instead of breaking the task loop.
        The vocabulary imports are lazy inside this method
        (function bodies are not import-time nodes per the
        Phase 6 guard), mirroring _funnel_execute.
        """
        brains = self._second_opinion_brains
        # B19: a branch's own index, not the task's current
        # one (see the docstring above).
        index = task._index if index is None else int(index)
        if brains is None:
            # B24 (second-opinion OFF surfacing): the check is
            # OFF — but a high-stakes step is a check that
            # WOULD have run, so the OFF state is recorded
            # once per task (never per step), naming the
            # missing leg. A step the check already covered
            # (in history, from an earlier run whose pair was
            # real) or one that is not high-stakes records
            # nothing — no check would have run for it.
            # Recording never breaks the step: any failure
            # leaves the step running exactly as before.
            try:
                _off_history = getattr(task, "_second_opinions", None)
                if _off_history is None:
                    _off_history = task._second_opinions = []
                if not any(isinstance(e, dict)
                           and e.get("step_index") == index
                           for e in _off_history):
                    if self._second_opinion_high_stakes(step):
                        self._record_second_opinion_off(task)
            except Exception:  # noqa: BLE001 — OFF recording never breaks a step
                pass
            return None, None
        history = getattr(task, "_second_opinions", None)
        if history is None:
            history = task._second_opinions = []
        if any(isinstance(e, dict) and e.get("step_index") == index
               for e in history):
            return None, None
        question = ""
        try:
            from ..second_opinion import cross_check
            if not self._second_opinion_high_stakes(step):
                return None, None
            question = _second_opinion_question(step)
            brain_a, brain_b = brains
            # B14: one check is one cloud call (brain_a — the
            # Groq leg, see second_opinion_from_sessions) plus
            # one local call (brain_b). cross_check returns
            # text only — no usage payload — so tokens and cost
            # are honest-unknown, never estimated from the
            # text; the seconds ARE measured, so the
            # local-time split stays computable. A raised check
            # records nothing: which legs ran is unknowable
            # from the exception.
            _check_t0 = _tb.monotonic()
            result = cross_check(question, brain_a, brain_b)
            _check_dt = _tb.monotonic() - _check_t0
            try:
                self.record_task_cloud_call(
                    task, leg="second-opinion", model="",
                    seconds=_check_dt)
                self.record_task_local_call(task)
            except Exception:  # noqa: BLE001 — accounting never breaks a check
                pass
            norm_a = result.answer_a.strip().casefold()
            norm_b = result.answer_b.strip().casefold()
            if (result.verdict == "agree" and norm_a == "yes"
                    and norm_b == "yes"):
                event = {"step_index": index, "tool": step.tool,
                         "label": step.label, "verdict": "agree",
                         "action": "proceeded",
                         "reason": "both brains answered YES",
                         "answer_a": result.answer_a,
                         "answer_b": result.answer_b,
                         "question": question}
                return None, event
            sentence = _second_opinion_pause_sentence(step, result)
            event = {"step_index": index, "tool": step.tool,
                     "label": step.label, "verdict": result.verdict,
                     "action": "paused",
                     "reason": result.disagreement,
                     "answer_a": result.answer_a,
                     "answer_b": result.answer_b,
                     "question": question}
            return sentence, event
        except Exception as exc:  # noqa: BLE001 — a broken check pauses once, never breaks the loop
            event = {"step_index": index, "tool": step.tool,
                     "label": step.label, "verdict": "error",
                     "action": "paused",
                     "reason": ("second-opinion check failed "
                                f"({type(exc).__name__}: {exc})"),
                     "answer_a": "", "answer_b": "",
                     "question": question}
            sentence = (
                f"Second opinion needed before "
                f"{(step.label or step.tool)!r}: the second-opinion "
                f"check itself failed ({type(exc).__name__}: {exc}). "
                "Allow once to run this step anyway, or Don't allow "
                "to refuse it.")
            return sentence, event

    # -- B14: per-task cost accounting -------------------------------------
    def record_task_cloud_call(self, task, *, leg, model="",
                               prompt_tokens=None,
                               completion_tokens=None, seconds=None,
                               cost_usd=None, rate_source=None) -> None:
        """Record one cloud call onto a task's spend (B14).

        The hook any leg with a measured provider call uses:
        pass the usage the provider returned (token counts, a
        cost computed through core.cost_ledger.estimate_cost,
        its rate_source) and the measured seconds. A call with
        no measured usage records its leg and seconds with an
        unknown cost — never an estimate, never a zero. Never
        raises: accounting must never break a task. The picker
        never sees this record: it lives on the Task, the
        receipt, and the ledger only.
        """
        try:
            spend = getattr(task, "_spend", None)
            if isinstance(spend, dict):
                # B19: the spend counters are shared task state
                # — a parallel-group branch records its calls
                # under the step lock, never as a racy
                # read-modify-write beside a sibling branch.
                _lock = getattr(self, "_step_lock", None) \
                    or _FALLBACK_STEP_LOCK
                with _lock:
                    _record_cloud_call(
                        spend, leg=leg, model=model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        seconds=seconds, cost_usd=cost_usd,
                        rate_source=rate_source)
        except Exception:  # noqa: BLE001 — accounting never breaks a task
            pass

    def record_task_local_call(self, task) -> None:
        """Record one local-leg call onto a task's spend (B14).

        Count only: a local call is time, never money — its
        wall time is inside the task's elapsed_s and it is
        never priced. Never raises.
        """
        try:
            spend = getattr(task, "_spend", None)
            if isinstance(spend, dict):
                # B19: same lock as the cloud-call record
                # above (see record_task_cloud_call).
                _lock = getattr(self, "_step_lock", None) \
                    or _FALLBACK_STEP_LOCK
                with _lock:
                    _record_local_call(spend)
        except Exception:  # noqa: BLE001 — accounting never breaks a task
            pass

    def _question_answer(self, task: Task, step: StepDef,
                         index=None) -> str | None:
        """The recorded answer for this step's ask, or None.

        B15: two sources, in order. (1) A recorded answer in
        task._question_answers for this step index — the human
        answered this question before (possibly before a
        restart; an answer is information, so it persists).
        (2) An explicit arg already carrying the step's
        answer_key — explicit args always win (the §7
        handle_id rule), so a step that already has its
        answer never asks. Anything else is unanswered: the
        caller pauses with _pending_question, never guesses.
        """
        ask = getattr(step, "ask", None)
        if not isinstance(ask, dict) or not ask.get("question"):
            return None
        key = str(ask.get("answer_key") or "answer")
        # B19: a branch's own index, not the task's current
        # one (see _second_opinion_check above).
        _idx = task._index if index is None else int(index)
        for entry in getattr(task, "_question_answers", None) or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("step_index") == _idx \
                    and isinstance(entry.get("answer"), str) \
                    and entry.get("answer", "").strip():
                return entry["answer"]
        args = getattr(step, "args", None) or {}
        if isinstance(args, dict):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _run_step(self, task: Task, step: StepDef, index=None,
                  pending_sink=None) -> StepOutcome:
        """One step through the funnel gates, to a verdict.

        B20: the step runs inside live_token — a fresh
        CancellationToken per step, registered while the step
        runs, cancelled by cancel_task or by the failsafe trip
        listener (goal pill). The token is threaded into
        _run_step_inner and from there into _execute, so a
        Stop pressed while the step runs ends it at its next
        yield point. Per step, never shared across steps: a
        deadline cancels only the token of the call that timed
        out, and the next step mints a fresh one.
        """
        from .task_cancel import live_token
        honors = getattr(task, "_honor_stop_signals", False)
        with live_token(task.id, honors) as token:
            return self._run_step_inner(
                task, step, index=index, pending_sink=pending_sink,
                cancel_token=token)

    def _run_step_inner(self, task: Task, step: StepDef, index=None,
                        pending_sink=None, cancel_token=None) -> StepOutcome:
        """One step through the funnel gates, to a verdict.

        B20: ``cancel_token`` is the step's cooperative token
        from _run_step — the when-probe and the dispatch both
        receive it, so either can end promptly on Stop.
        B19: ``index`` names the step when the caller is not
        running it as the task's current step (a parallel-group
        branch runs at its own index while task._index still
        points at the group's first step). None means "the
        task's current step", exactly as before — the two
        existing callers (run_task's loop, the verification
        phase's retry re-run, which sets task._index first)
        pass nothing and behaviour is unchanged. Every use of
        the step's position inside this method keys off the
        local ``_idx`` below, never task._index directly.

        ``pending_sink`` is a per-branch dict a parallel-group
        branch passes in: a pending pause (question /
        approval / monitor) is written into the sink instead
        of the task's single pending slots, so two branches
        pending at once never clobber each other's record —
        the group's join installs the FIRST pending branch's
        record deterministically. None means "write onto the
        task", exactly as before.
        """
        # B19: the step's own index for everything below.
        _idx = task._index if index is None else int(index)

        def _set_pending(slot: str, record: dict) -> None:
            """Record a pending pause: into the branch sink
            when running in a parallel group, else onto the
            task — the same record either way, so the join
            installs exactly what _run_step would have set."""
            if pending_sink is not None:
                pending_sink[slot] = record
            elif slot == "approval":
                task._pending_approval = record
            elif slot == "monitor":
                task._pending_monitor = record
            else:
                task._pending_question = record

        # B15 (mid-task clarifying questions): a step that
        # declares an ask needs its answer before ANYTHING
        # about the step runs — the when-probe, the
        # second-opinion check and the dispatch itself may
        # all need the answer (a probe on "the file" cannot
        # run without knowing which file), so the gate sits
        # first, before even the before-snapshot. With no
        # recorded answer and no explicit arg carrying the
        # answer key, the task pauses with _pending_question
        # (fourth pause shape, beside _pending_approval /
        # _pending_monitor / the B14 budget pause): the run
        # loop intercepts status "pending" before appending
        # (the step has not succeeded, failed, or skipped —
        # it awaits an answer), _index still points at it,
        # and the pending record names it. No default, no
        # silent fallback, no confidence-threshold guess —
        # the question exists so nothing is guessed.
        _ask = getattr(step, "ask", None)
        _answer = None
        if isinstance(_ask, dict) and _ask.get("question"):
            _answer = self._question_answer(task, step, index=_idx)
            if _answer is None:
                _set_pending("question", {
                    "step_index": _idx,
                    "tool": step.tool,
                    "label": step.label,
                    "question": str(_ask.get("question", "")),
                    "options": list(_ask.get("options") or []),
                    "answer_key": str(
                        _ask.get("answer_key") or "answer"),
                })
                return StepOutcome(
                    tool=step.tool, label=step.label,
                    status="pending", kind="pending_question",
                    message=str(_ask.get("question", "")))
        if getattr(step, "expect", ""):
            # Capture the before-snapshot while the step runs so
            # the verification phase can ground "appears"-style
            # expectations against real before/after evidence.
            # Snapshot failure is unobserved (None), never a break.
            try:
                task._verify_before[_idx] = self._snapshot()
            except Exception:  # noqa: BLE001
                task._verify_before[_idx] = None
        if step.when is not None:
            probe = self._execute(step.when["tool"], step.when["args"],
                                  cancel_token=cancel_token)
            if str(probe.get("status", "")) != "success":
                # B20: a cancelled probe is NEVER a skip — the
                # user said stop, so the step stops as
                # cancelled even when on_fail == "skip" (a skip
                # would let the task continue past the stop).
                if str(probe.get("status", "")) == "cancelled":
                    return self._outcome(step, probe)
                if step.on_fail == "skip":
                    return StepOutcome(
                        tool=step.tool, label=step.label, status="skipped",
                        kind=str(probe.get("kind", "") or ""),
                        message="when-probe did not succeed; step skipped "
                                f"per on_fail=skip ({probe.get('message', '')})")
                return self._outcome(step, probe)
        # Second-opinion auto-invoke (this batch): a high-stakes
        # step is cross-checked BEFORE it runs, after the
        # when-probe (a skipped step is never checked) and
        # before reissue consumption (so an engine approval is
        # never consumed-then-lost to a pause) and before
        # dispatch. A pause outcome returns here exactly like an
        # engine confirm stop: the run loop intercepts status
        # "pending" before appending, _index still points at
        # the step, and the pending record names it. The event
        # is appended exactly once, here, so the history gates
        # every re-check: a resolve → re-issue → re-run cannot
        # pause twice on the same step.
        _so_sentence, _so_event = self._second_opinion_check(
            task, step, index=_idx)
        if _so_event is not None:
            # B19: in a parallel group the event rides the
            # branch sink, so the join appends the group's
            # events in step order (a branch appending
            # directly would order them by completion, not
            # by step — the receipt would read differently
            # run to run).
            if pending_sink is not None:
                pending_sink["second_opinion"] = _so_event
            else:
                task._second_opinions.append(_so_event)
        if _so_sentence is not None:
            _set_pending("approval", {
                "step_index": _idx,
                "tool": step.tool,
                "label": step.label,
                "sentence": _so_sentence,
                "confirm_arg": "",
            })
            _pending_crop = _pending_crop_for_step(step)
            if _pending_crop is not None:
                if pending_sink is not None:
                    pending_sink["approval"]["stored_crop"] = _pending_crop
                else:
                    task._pending_approval["stored_crop"] = _pending_crop
            return StepOutcome(tool=step.tool, label=step.label,
                               status="pending",
                               kind="pending_approval",
                               message=_so_sentence)
        step_args = self._dispatch_args(step)
        # B15: inject the recorded answer into the step's
        # args under its answer_key — on the dispatch copy
        # only (the step's own args are never mutated, the
        # §7 handle rule), and only when the step does not
        # already carry the key (explicit args always win).
        if _answer is not None and isinstance(_ask, dict):
            _key = str(_ask.get("answer_key") or "answer")
            if _key not in step_args:
                step_args[_key] = _answer
        # §7 consumers batch 6: a one-shot approval re-issue
        # sets the engine's confirmed flag — set ONLY by
        # resolve_pending(approved=True), consumed here, never
        # from the step's own args (a model-set flag is never
        # consulted as approval). The one-shot is consumed on
        # first use even if this execution then fails another
        # way (a funnel-gate refusal, a genuine refusal) —
        # that failure is honest, and a later re-issue would
        # need a fresh approval, not a stale one.
        reissue = getattr(task, "_approval_reissue", None)
        if isinstance(reissue, dict) \
                and reissue.get("step_index") == _idx \
                and reissue.get("confirm_arg"):
            step_args[reissue["confirm_arg"]] = True
            task._approval_reissue = None
        # Fix 8 (plan approval): a step the human approved in
        # the plan dialog carries a contextvar through THIS
        # dispatch only — the funnel's confirm stage reads it
        # (and re-verifies it) instead of popping again. The
        # when-probe above ran without it, so a probe always
        # gates normally; the token resets in the finally, so
        # the approval can never leak to a later step, a retry
        # of a different step, or any other call.
        _plan_token = None
        try:
            from .. import plan_approval as _pa
            # B19: the branch's own index — the approval record
            # is keyed by (task id, step index), and a branch
            # is not the task's current step.
            _plan_token = _pa.begin_step_dispatch(
                task, step, step_args, index=_idx)
            # B23 (approve-plan-once coverage): content
            # fallback — a B22 re-plan appends steps at NEW
            # indices, so a re-planned step identical to an
            # approved one would still ask under the
            # positional check above. The approval's meaning
            # is "this tool with these args", never the
            # position, so a positional miss falls back to a
            # content match against the plan. covering_call
            # still re-verifies everything at dispatch (incl.
            # the new-file leg, so a re-run that would
            # overwrite still asks).
            if _plan_token is None:
                _plan_token = _pa.arm_for_call(
                    getattr(task, "id", ""),
                    getattr(task, "steps", []),
                    step.tool, step_args)
        except Exception:  # noqa: BLE001 — no plan context: ask normally
            _plan_token = None
        # B14: one execution, counted once, at the dispatch —
        # a retried step's re-run dispatches again, so it is a
        # second execution, never folded into the first (the
        # cost ledger counts once per execution). When-probes
        # use _execute directly and second-opinion pauses
        # return before the dispatch, so neither is an
        # execution. Never breaks a step.
        try:
            _spend = getattr(task, "_spend", None)
            if isinstance(_spend, dict):
                # B19: the counter is shared task state — a
                # parallel-group branch increments it under
                # the step lock, never as a racy
                # read-modify-write beside a sibling branch.
                _lock = getattr(self, "_step_lock", None) \
                    or _FALLBACK_STEP_LOCK
                with _lock:
                    _spend["executions"] = int(
                        _spend.get("executions", 0) or 0) + 1
        except Exception:  # noqa: BLE001 — accounting never breaks a step
            pass
        # B21 (crash-safe re-run): the intent is recorded
        # durably BEFORE the step executes — the mint
        # ledger-writes, so a crash between here and the
        # outcome write leaves the intent in the file and
        # the resume reconciles instead of re-running blindly.
        _run_id = self._mint_step_run(task, step, step_args, _idx)
        try:
            out = self._execute(step.tool, step_args, cancel_token=cancel_token)
        finally:
            if _plan_token is not None:
                try:
                    from .. import plan_approval as _pa2
                    _pa2.end_step_dispatch(_plan_token)
                except Exception:  # noqa: BLE001 — teardown never breaks a step
                    pass
        if step.tool == "menu_action":
            # §7 consumers batch 4: the raw output is the
            # verification phase's invoke-result evidence (the
            # funnel renders engine results as text, so the
            # phase cannot re-derive opened/invoked from the
            # outcome message alone).
            task._verify_result[_idx] = out
        # §7 consumers batch 6: an engine confirm stop pauses
        # the task for approval INSTEAD of failing as a
        # refusal — checked BEFORE the batch-4/5 rewrite, so
        # the stop shapes (which overlap the refusal shapes
        # for surfaces and journals) pause rather than fail.
        # The returned outcome has status "pending": the run
        # loop intercepts it before appending (the step has
        # not succeeded, failed, or skipped — it awaits a
        # decision), and the pending record on the task names
        # it. A genuine refusal still rewrites below and
        # fails exactly as batches 4/5 left it.
        stop = _detect_s7_confirmation_stop(step.tool, out)
        if stop is not None:
            sentence, confirm_arg = stop
            _set_pending("approval", {
                "step_index": _idx,
                "tool": step.tool,
                "label": step.label,
                "sentence": sentence,
                "confirm_arg": confirm_arg,
            })
            _pending_crop = _pending_crop_for_step(step)
            if _pending_crop is not None:
                if pending_sink is not None:
                    pending_sink["approval"]["stored_crop"] = _pending_crop
                else:
                    task._pending_approval["stored_crop"] = _pending_crop
            # B21: the step dispatched but the gate stopped it
            # before it applied — the attempt ends here, as a
            # pending outcome against its step-run id (the
            # outcome IS recorded, so the resume re-stops at
            # the pending record instead of reconciling).
            _pending_outcome = StepOutcome(
                tool=step.tool, label=step.label,
                status="pending", kind="pending_approval",
                message=sentence)
            self._record_step_run_outcome(
                task, _run_id, _pending_outcome, "recorded")
            return _pending_outcome
        # R5 (routine wallpaper steps): a set naming a monitor
        # that is gone pauses the task for the HUMAN to fix —
        # checked right after the §7 stop, before the batch-5
        # rewrite, so the silent false success (the funnel
        # returns the "Wallpaper not set: unknown-monitor"
        # sentence as a success payload) never reaches the
        # outcome. The returned outcome has status "pending":
        # the run loop intercepts it before appending (the
        # step has not succeeded, failed, or skipped — it
        # awaits a reconnected monitor), and the pending
        # record on the task names it. This is deliberately
        # NOT _pending_approval: there is nothing to approve
        # and resolve_pending(approved=False) would FAIL the
        # step — the fix is reconnecting the screen and
        # resuming, so the record lives in _pending_monitor
        # (ledger-serialized beside _pending_approval) and
        # resolve_pending leaves it alone. A resume re-runs
        # the step, which re-resolves the monitor fresh.
        gone = _detect_wallpaper_monitor_gone(step.tool, out)
        if gone is not None:
            _set_pending("monitor", {
                "step_index": _idx,
                "tool": step.tool,
                "label": step.label,
                "sentence": gone,
            })
            # B21: the step dispatched but the set never
            # landed (the monitor is gone) — the attempt ends
            # here, as a pending outcome against its step-run
            # id (the outcome IS recorded, so the resume
            # re-presents the monitor pause instead of
            # reconciling).
            _monitor_outcome = StepOutcome(
                tool=step.tool, label=step.label,
                status="pending", kind="pending_approval",
                message=gone)
            self._record_step_run_outcome(
                task, _run_id, _monitor_outcome, "recorded")
            return _monitor_outcome
        # §7 consumers batch 5: a refusal sentence arriving as
        # success is rewritten to a refused outcome before the
        # step completes (see _rewrite_s7_refusal; menu steps
        # keep their batch-4 rewrite) — a refused §7 path fails
        # honestly instead of recording success. Non-§7 tools
        # pass through untouched.
        out = _rewrite_s7_refusal(step.tool, out)
        outcome = self._outcome(step, out)
        # Fix 9: a declared B12 result check is judged here,
        # after refusals (a refused step halts before its
        # check, exactly like the legacy routine loop) and
        # before the outcome is recorded — a result that does
        # not meet its check is a failure (or a skip when the
        # step says so), never a silent success. Steps without
        # a check pass through untouched.
        if outcome.status == "success" and getattr(step, "check", None):
            _passed, _reason = _check_result(out, step.check)
            if not _passed:
                outcome = StepOutcome(
                    tool=step.tool, label=step.label,
                    status="failure", kind="check_failed",
                    message=f"result check failed ({_reason})")
        # B13 (hands landing): a seam Outcome whose kind is
        # "landing_failed" means the vision backend read the
        # screen after the act and the landing did not happen —
        # a failure, not a silent success. It lands here, after
        # refusals and the B12 result check and before the
        # on_fail skip, so a step with on_fail=skip still skips
        # (B4 semantics) and any other step fails honestly and
        # the run loop's jump/halt acts on it. An unverified
        # landing passes through untouched (the B1 contract: an
        # unreadable screen is honest, not a failure). NOT added
        # to _TRANSIENT_FAILURE_KINDS: a click that did not land
        # does not prove transient (a wrong target fails
        # identically); the B4 jump is the recovery path.
        if outcome.status == "success" and isinstance(out, dict) \
                and out.get("outcome_kind") == "landing_failed":
            outcome = StepOutcome(
                tool=step.tool, label=step.label,
                status="failure", kind="landing_failed",
                message=outcome.message)
        # B20: a cancelled step is NEVER converted to a skip —
        # the user said stop, so a skip would let the task
        # continue past the stop (the on_fail=skip contract is
        # for failures the plan anticipated, not for a stop).
        # A cancelled outcome reaches the run loop, which
        # stops the task as "cancelled" with partial progress
        # intact in the ledger.
        if outcome.status != "success" and outcome.status != "cancelled" \
                and step.on_fail == "skip":
            outcome.status = "skipped"
        # R5: the step genuinely ran to a verdict here, so a
        # monitor pause recorded for THIS step no longer
        # applies — clear it (a later step's own pause is a
        # different record, at a different index, and stays).
        # B19: the comparison keys off this branch's index —
        # a branch clears only its own step's record, never a
        # sibling's (the clear writes to the task, not the
        # sink: a clear is a removal, and two branches never
        # clear the same index).
        _stale = getattr(task, "_pending_monitor", None)
        if isinstance(_stale, dict) \
                and _stale.get("step_index") == _idx:
            task._pending_monitor = None
        # B15: same rule for a question pause — a step that
        # ran to a verdict was answered (or already carried
        # its answer), so a pending question for THIS step
        # no longer applies. A later step's own question is
        # a different record, at a different index, and stays.
        _stale_q = getattr(task, "_pending_question", None)
        if isinstance(_stale_q, dict) \
                and _stale_q.get("step_index") == _idx:
            task._pending_question = None
        # B21: the outcome is recorded against the same
        # step-run id the intent was minted with — the pair
        # is what makes the resume answerable. This write
        # lands here, before the run loop's own write after
        # the outcome append, so a crash between the step
        # returning and the loop's write still leaves a
        # recorded outcome (and a cancelled outcome is
        # recorded as cancelled — never reconciled).
        self._record_step_run_outcome(task, _run_id, outcome, "recorded")
        return outcome

    # -- B19: parallel groups (audit L5) ---------------------
    #
    # Loophole family: "queued work" — independent steps run
    # one after another because the loop has no way to say
    # "these do not depend on each other", so a task pays
    # the sum of all latencies instead of the longest.
    #
    # ONE design: a declared parallel group — steps the
    # plan declares independent run together through the
    # SAME _run_step (one path, not two), each step's
    # outcome judged independently (its own expect, its own
    # landing verdict, its own strike), and the group
    # completes only when every step has completed. The
    # group's outcome is the set of its steps' outcomes,
    # never a single merged verdict — a failed step hidden
    # inside a group "success" would be the B1 failure
    # again.
    #
    # Independence is DECLARED in the plan (StepDef.parallel
    # label), never inferred: a tool name does not prove two
    # steps don't touch the same file, and a step that reads
    # a file another step writes is sequential unless the
    # plan says otherwise. The default is sequential — a
    # plan with no parallel group behaves exactly as today.
    #
    # A parallel group never contains a consequential step
    # (validate_parallel_groups refuses the plan at build,
    # via plan_approval.is_jump_refused — the tree's own
    # D4/C-14 set, never copied); parallelism is never a
    # way around the confirm gate or the plan-approval
    # digest, and each step still goes through its own
    # confirm stop where one applies.
    #
    # Concurrency is a bounded ThreadPoolExecutor created
    # per group run and shut down at the join, never
    # long-lived: there is no funnel worker pool in the
    # tree (the audit's phrase is aspirational), so B19
    # creates the pool where it is used and destroys it at
    # the join — no pool can leak across tasks or survive a
    # cancel. A failed branch never cancels its siblings
    # (they ran to completion); the task then acts on the
    # first failure exactly as the sequential loop would
    # (halt, or jump when the step declares a goto).
    #
    # Rejected alternatives (see the brief): inferring
    # independence from tool names; running ALL steps in
    # parallel by default; a second dispatch path; merging
    # the group's outcomes into one verdict; guessing or
    # auto-answering a pending question; a long-lived
    # shared pool; re-running completed siblings on resume.

    def _recorded_outcome(self, task: Task, index: int):
        """The outcome recorded at a step's position, or None.

        Positional alignment: the outcome at position
        ``index`` belongs to this step only when tool +
        label match (the same rule the run loop's
        replace-in-place uses). A mismatch is the
        pre-existing unknown-outcome quirk — read as "no
        outcome", exactly like the loop does."""
        if 0 <= index < len(task.outcomes):
            outcome = task.outcomes[index]
            step = task.steps[index]
            if outcome.tool == step.tool \
                    and outcome.label == step.label:
                return outcome
        return None

    def _group_branch_runnable(self, task: Task, index: int) -> bool:
        """True when a group member needs a branch in this run:
        no recorded outcome at its position, or a pending
        placeholder (the step asked a question / waited for
        an approval and never ran to a verdict — re-run it).
        A success/skipped outcome means the step already
        ran: never re-execute it."""
        outcome = self._recorded_outcome(task, index)
        if outcome is None:
            return True
        return outcome.status == "pending"

    def _parallel_group_starting_at(self, task: Task, index: int):
        """The parallel group starting at ``index``, or None.

        parallel_groups is the one definition of "group"
        (a maximal consecutive run sharing one label); this
        only finds the run starting here."""
        for members in parallel_groups(task.steps):
            if members and members[0] == index:
                return members
        return None

    def _run_group_branch(self, task: Task, index: int, step: StepDef):
        """One parallel-group branch: _run_step at its own index.

        Returns (outcome, sink): the step's outcome and the
        branch's pending records + second-opinion event ({}
        when the step ran to a verdict). Never raises: a
        branch that raises becomes a failure outcome, never
        a crashed join — the join must see every branch's
        outcome, or a crashed branch would leave its
        siblings' outcomes unrecorded and the group without
        a join."""
        sink: dict = {}
        try:
            outcome = self._run_step(task, step, index=index,
                                     pending_sink=sink)
        except Exception as exc:  # noqa: BLE001 — a crashed branch fails, never crashes the join
            # B21: the branch's attempt ended in a failure
            # outcome — record it against its step-run id, so
            # the ledger never carries a "started" intent
            # beside a recorded failure (a crash inside the
            # branch is the one case the _run_step return
            # cannot record, because it never returned).
            _run = self._pending_step_run(task, index)
            if _run is not None:
                self._record_step_run_outcome(
                    task, _run["step_run_id"],
                    StepOutcome(
                        tool=step.tool, label=step.label,
                        status="failure", kind="handler_error",
                        message=f"parallel branch failed: {exc}"),
                    "recorded")
            outcome = StepOutcome(
                tool=step.tool, label=step.label,
                status="failure", kind="handler_error",
                message=f"parallel branch failed: {exc}")
            sink = {}
        return outcome, sink

    def _run_parallel_group(self, task: Task, members, runnable,
                            callback=None) -> bool:
        """Run one parallel group and handle its join.

        ``members`` is the group's full index list (from
        parallel_groups); ``runnable`` is the subset that
        needs a branch in this run (no outcome, or a pending
        placeholder). Returns True when the run loop should
        continue, False when it must stop (paused or
        failed). The caller owns the loop — this method
        never re-enters it.

        Join, in order: (1) place every branch outcome at
        its own position, in index order, by the same
        replace-in-place rule the sequential loop uses;
        (2) append second-opinion events in step order
        (never completion order — the receipt must read the
        same run to run); (3) ONE ledger write for the whole
        group — branches never write the ledger (concurrent
        file writes would interleave); a crash mid-group
        loses the group's outcomes, exactly like a crash
        mid-step loses a sequential step's; (4) first
        failure (lowest index) halts or jumps, exactly like
        the sequential loop; (5) else first pending
        installs its record and pauses, _index at that
        branch; (6) else advance past the group (and fire
        the retry-resume marker rule, mirroring the
        sequential path, when a member that just succeeded
        carries it). Failure wins over pending: a failed
        branch halts or jumps the task, and a pending
        question on another branch is moot — the branch
        will pend again if the loop re-reaches it (after a
        jump) or never (after a halt)."""
        label = str(
            getattr(task.steps[members[0]], "parallel", "") or "")
        # B21 (crash-safe re-run): reconcile each runnable
        # branch's pending intent BEFORE launching — a
        # branch that applied before the crash never re-runs
        # (its reconciled success is pre-filled into results
        # and the join places it like any branch outcome);
        # a branch the world cannot judge pauses the whole
        # task for the human (the group's other branches
        # never launch). A "not-applied" branch runs fresh
        # with a NEW step-run id; a branch with no pending
        # intent runs as normal. The reconcile runs on the
        # loop thread, before the pool starts, so no branch
        # is ever reconciled mid-flight.
        _reconciled = {}
        _still_runnable = []
        for index in runnable:
            _v, _r, _p = self._reconcile_pending_intent(task, index)
            if _v == "applied":
                # A pending run resolves as "reconciled" (the
                # verifier judged the world); an
                # already-resolved success run keeps its
                # recorded kind (its success was recovered,
                # not judged — the crash ate the join's
                # placement, not the outcome). Either way the
                # branch never launches: its outcome is
                # pre-filled here and the join places it
                # positionally, exactly like a branch result.
                _outcome, _run = self._reconciled_outcome(
                    task, index, _r)
                if _outcome is not None:
                    _reconciled[index] = _outcome
                    if _run is not None:
                        self._resolve_step_run(
                            task, _run, "reconciled", "success",
                            "reconciled", str(_r or ""))
                    continue
                # No step-run to stand on (defensive: the
                # verdict said "applied" but the run vanished
                # between the decision and the act) — run the
                # branch; a missing run is never a reason to
                # skip work.
                _still_runnable.append(index)
            elif _v == "unknown":
                task._pending_reconcile = _p
                task._paused = True
                task.state = "paused"
                self._ledger_write(task)
                return False
            else:  # "not-applied" or "none": run the branch
                _still_runnable.append(index)
        runnable = _still_runnable
        # The group's wall time is its longest branch:
        # branches run together in a bounded pool created
        # here and shut down at the join (never long-lived
        # — see the module note above). Emits happen on
        # THIS thread only, before the pool starts: a branch
        # never emits (the state bus is fed from the loop,
        # never staged — the same rule as the sequential
        # loop, which emits before/after _run_step).
        for index in runnable:
            step = task.steps[index]
            emit_step(n=index + 1, m=len(task.steps),
                      label=step.label)
            emit_tool_started(kind=step.tool)
        # Reconciled-as-applied branches never launch: their
        # outcomes are pre-filled here and the join places
        # them positionally, exactly like a branch result.
        results = {i: (o, {}) for i, o in _reconciled.items()}
        if runnable:
            with ThreadPoolExecutor(max_workers=len(runnable)) as pool:
                futures = {
                    pool.submit(self._run_group_branch, task, index,
                                task.steps[index]): index
                    for index in runnable
                }
                for future in futures:
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as exc:  # noqa: BLE001 — the wrapper is total; this is the last line of defence
                        step = task.steps[index]
                        results[index] = (StepOutcome(
                            tool=step.tool, label=step.label,
                            status="failure", kind="handler_error",
                            message=f"parallel branch failed: {exc}"), {})
        # Pre-existing outcomes: success/skipped members
        # were skipped above (never re-executed); a member
        # with any other recorded outcome (failure —
        # unreachable in a paused task, since a failure
        # halts or jumps, never pauses) counts as that
        # branch's result WITHOUT re-running it — re-running
        # a failed step could double-apply it, and the join
        # acts on it exactly as the sequential loop would
        # have.
        for index in members:
            if index in results:
                continue
            outcome = self._recorded_outcome(task, index)
            if outcome is None \
                    or outcome.status in ("success", "skipped"):
                continue
            results[index] = (outcome, {})
        # Place outcomes positionally, in index order.
        for index in members:
            if index not in results:
                continue
            outcome, sink = results[index]
            step = task.steps[index]
            if index < len(task.outcomes) \
                    and task.outcomes[index].tool == step.tool \
                    and task.outcomes[index].label == step.label:
                task.outcomes[index] = outcome
            else:
                task.outcomes.append(outcome)
            if isinstance(sink, dict):
                event = sink.get("second_opinion")
                if isinstance(event, dict):
                    task._second_opinions.append(event)
            if index in runnable:
                emit_tool_finished(status=outcome.status,
                                   kind=step.tool)
            if outcome.status in ("success", "skipped"):
                try:
                    task._jump_strikes.pop(index, None)
                except Exception:  # noqa: BLE001 — strike bookkeeping never breaks a join
                    pass
        self._ledger_write(task)
        try:
            self._log_activity_line(
                "parallel_group",
                f"task {task.title!r}: ran parallel group {label!r} "
                f"(steps {members[0] + 1}–{members[-1] + 1}, "
                f"{len(runnable)} branches)")
        except Exception:  # noqa: BLE001 — activity logging never breaks a join
            pass
        # The join's decisions, in order. First failure
        # wins over pending (see the docstring).
        first_failure = None
        first_pending = None
        for index in members:
            if index not in results:
                continue
            outcome, _sink = results[index]
            if outcome.status == "pending" and first_pending is None:
                first_pending = index
            elif outcome.status not in ("success", "skipped", "pending") \
                    and first_failure is None:
                first_failure = index
        # B20: a cancelled branch wins over failure and
        # pending — the user said stop; a failed branch's
        # jump/halt and a pending branch's pause must not
        # swallow the stop. Every branch's outcome is already
        # recorded at its own position above, so each branch
        # reads as cancelled on its own.
        first_cancelled = None
        for index in members:
            if index not in results:
                continue
            outcome, _sink = results[index]
            if outcome.status == "cancelled":
                first_cancelled = index
                break
        if first_cancelled is not None:
            step = task.steps[first_cancelled]
            outcome = results[first_cancelled][0]
            task._index = first_cancelled
            task._cancel_reason = (
                f"stopped during parallel step "
                f"{first_cancelled + 1} ({step.label}): "
                f"{outcome.message or outcome.status}")
            task._cancelled = True
            task.state = "cancelled"
            self._ledger_write(task)
            return False
        if first_failure is not None:
            step = task.steps[first_failure]
            outcome = results[first_failure][0]
            task._index = first_failure
            if getattr(step, "goto", None) is not None \
                    and self._take_jump(task, step, outcome):
                return True
            task.state = "failed"
            return False
        if first_pending is not None:
            # Install the FIRST pending branch's record —
            # the task can hold only one pending record, and
            # the first pending branch (lowest index) is the
            # resume point. Later pending branches keep no
            # record: they pend again when the loop re-reaches
            # them (their questions were never answered, so
            # nothing is lost — only deferred).
            _outcome, sink = results[first_pending]
            if isinstance(sink, dict):
                if isinstance(sink.get("approval"), dict):
                    task._pending_approval = dict(sink["approval"])
                if isinstance(sink.get("monitor"), dict):
                    task._pending_monitor = dict(sink["monitor"])
                if isinstance(sink.get("question"), dict):
                    task._pending_question = dict(sink["question"])
            task._paused = True
            task.state = "paused"
            task._index = first_pending
            self._ledger_write(task)
            return False
        # All branches success/skipped: advance past the
        # group. The retry-resume marker, when it names a
        # member that just succeeded, fires exactly like the
        # sequential loop's rule (re-run step done → jump to
        # verification); normally the marker equals _index
        # so the group never forms around it — this is the
        # defensive mirror of that rule, not a second path.
        task._index = members[-1] + 1
        _marker = getattr(task, "_retry_resume_step", None)
        if isinstance(_marker, int) and _marker in members:
            _m_outcome = results.get(_marker, (None, {}))[0]
            if _m_outcome is not None and _m_outcome.status in (
                    "success", "skipped"):
                task._retry_resume_step = None
                task._index = len(task.steps)
        # Step callbacks fire per fresh success/skipped
        # outcome, in index order, after the join's index
        # is set — exactly like the sequential loop, where
        # the callback fires after the step's index has
        # advanced (and after the marker rule). Failures and
        # pendings never callback (the join returned above),
        # and pre-existing outcomes never callback (they did
        # not run in this pass).
        if callback is not None:
            for index in members:
                if index not in runnable:
                    continue
                outcome = results[index][0]
                if outcome.status not in ("success", "skipped"):
                    continue
                try:
                    callback(task, outcome)
                except Exception:  # noqa: BLE001 — a callback failure never breaks a join
                    pass
        return True
