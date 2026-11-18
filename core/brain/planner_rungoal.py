# planner_rungoal.py — planner family split out of
# core/brain/planner.py (skills split B41, part 2 of 2).
# Bodies below are AST-verbatim from the old module; only the
# import block above is new. Cross-family names are imported
# with INTENTIONAL comments, never redefined.

import hashlib
import json
import time
import uuid
from .planner_storage import (
    _short,
)
# INTENTIONAL (skills split B41): plans family lives in
# core/brain/planner_plans.py; imported, never redefined.
from .planner_plans import (
    _execute_tool,
    _execution_succeeded,
    _failure_reason,
    _is_confirm_denial,
    _is_transient_failure,
    _known_tools,
    _normalise_check,
    _verify_async,
    check_result,
)
# INTENTIONAL (skills split B41): checkpoint family lives in
# core/brain/planner_checkpoint.py; imported, never redefined.
from .planner_checkpoint import (
    _failsafe_tripped,
)
# INTENTIONAL (B17): learning family lives in
# core/brain/task_learning.py; imported, never redefined.
from .task_learning import (
    record_task_lesson,
    strike_lesson,
)
# INTENTIONAL (skills split B41): goalhelpers family lives in
# core/brain/planner_goalhelpers.py; imported, never redefined.
from .planner_goalhelpers import (
    _apply_j1,
    _control_gate,
    _control_snapshot_for,
    _default_observe,
    _epoch_changed,
    _epoch_stamp,
    _goal_budget_int,
    _goal_budget_seconds,
    _j1_context,
    _ladder_hint,
    _maybe_await,
    _observation_changed,
    _parse_goal_action,
    _picker_observation,
)
# INTENTIONAL (R7-3): the bounded beam search over candidate next
# actions lives in core/brain/goal_search.py; imported, never
# redefined. The search only CHOOSES among the picker's own
# candidates — it never executes, never bypasses a gate.
from .goal_search import normalise_search, report_line, search_pick
from ..state_bus_producers import emit_step
from ..state_bus_producers import emit_tool_finished
from ..state_bus_producers import emit_tool_started




async def run_goal(goal_text, *, pick_action, observe=None, emit=None,
                   verify_expect=None, goal_context=None, playbook=None,
                   interruption_check=None, control_snapshot=None,
                   resilience_tripped=None, ledger=None,
                   resume_state=None, plan_approval=None,
                   search=None, strict_completion=False,
                   pause_on_denial=False) -> str:
    """Drive an open-ended goal, one picked action at a time
    (v5.49, programme item 29).

    pick_action(goal, observation, history) returns ONE action
    {"tool": name, "args": {...}} (optionally with a free-text
    "expect"), or {"done": summary}, or None — None is an honest
    stop. It may be a plain or async callable; `observe` likewise
    (default: core.desktop.uia.desktop_snapshot via _default_observe).
    `history` is the live list of attempt records; on a no-change
    retry the driver appends a {"event": "retry", "retry": True,
    "hint": <retry-ladder hint>} record BEFORE calling
    pick_action again, so the picker knows it is retrying and
    which ladder rung to climb.

    Verification is by change: the post-action observation must
    differ from the pre-action one (an unobservable pair never
    counts as no-change), plus the item-31 verify path when the
    action declares an expect. A step that changes nothing is
    retried ONCE, then counts a strike and the loop replans
    (pick_action is called fresh with the failure in history);
    goal_max_strikes (default 3) consecutive failed steps stop
    the goal.

    B12 (transient-failure retry): a failed tool execution whose
    funnel result is transient (status "failure", kind
    "handler_error" — the handler ran and raised) retries the
    SAME step once, sharing the step's one retry with the ladder
    above; a genuine refusal (status "refused"), a timeout or
    unknown outcome, a refusal sentence, and every persistent or
    unclassifiable failure still halt immediately and are never
    retried or bypassed. Hard budgets goal_max_steps / goal_max_seconds
    (defaults 25 / 600) stop the goal with a written report of
    what was done. Progress lines go to `emit` as they happen.

    B10 (goal-level verification): the picker's {"done": ...}
    is a claim the goal check judges, not the completion
    itself — after "done" the goal text is judged against a
    fresh snapshot through verify_expect, and "not-met"
    means the goal does NOT complete. Budget-stop and
    strike-stop reports gain the same goal verdict line
    ("Goal check: <verdict> — <reason>"); "unverifiable"
    never fakes to "met".

    B11 (ledger-backed goals): the goal text, the history,
    and the progress are written to the task ledger
    (core/brain/task_ledger.py — the ONE durable record, never
    a second store) after every completed step, so a crash
    mid-goal leaves an interrupted record instead of nothing.
    A step/time budget stop is a RESUMABLE paused record, not
    a terminal stop: resume_goal(goal_id) continues from the
    next pick with the history intact, in a FRESH budget
    window (window step count restarts at 0, window number
    + 1, resume count + 1 — all visible in the record). The
    budgets themselves are unchanged (25 / 600 / 3 defaults);
    only the stop is resumable. ``ledger`` is an explicit
    TaskLedger (tests pass a temp path); with none supplied
    the shared default ledger is used, exactly like the
    coordinator — and a ledger failure never breaks the goal,
    it just runs in-memory-only. ``resume_state`` is the
    internal restore shape from ledger.rebuild_goal (callers
    use resume_goal, never this directly).

    Scoped approval (v5.46, programme item 2): when the session
    is tainted at entry — the case that requires approval — the
    goal requests the screen scope ONCE up front, mirroring
    run_plan (grant bound to this goal run, revoked in the
    finally); when the session is not tainted no grant is needed
    and every action still passes execute()'s own gating.

    Wiring Build 2 (optional keyword arguments, all additive):
    goal_context (J1 context, merged over the live observation),
    playbook (a j9 Playbook looked up before picking),
    interruption_check (a callable consulted at each step
    boundary, driving the j7 GoalRun machine), and
    control_snapshot (a list, or a callable returning one, for
    the control-breadth revalidation gate). With none supplied,
    this function behaves exactly as before.

    Wiring Build 3 (also additive): resilience_tripped, a
    callable consulted at the same step boundary through
    _stop_checks. With none supplied, the shared Resilience
    gate (core/resilience/resilience_gate.is_tripped — the Doctor
    page's trip signal, the shared wiring latch, or the app
    failsafe) is consulted instead, so a trip recorded from
    the GUI halts this loop without any caller having to pass
    it in. A trip halts with a resilience reason and executes
    nothing further; re-arming is a human, confirmed GUI action
    (the Doctor page), never automatic, and never from here.

    Wiring Build 6 (also additive, observation only): when
    goal_context carries a "briefing", pick_action receives the
    live observation plus a new "briefing_context" field
    (_picker_observation), framed as data, never as a directive.
    Live observation fields are never overwritten, and with no
    briefing the picker receives the observation exactly as
    before. The briefing cannot approve or run anything: every
    picked action still passes execute()'s gate stack (C-14).

    B23 (approve-plan-once coverage): ``plan_approval`` is an
    optional (task id, approved StepDef list) pair — the plan
    the human already approved in the GUI plan dialog, as
    recorded by core.plan_approval.approve_plan. Each picked
    action is matched by CONTENT against the approved steps
    (tool + full args digest, through plan_approval's
    arm_for_call); a match arms the dispatch channel so the
    funnel's confirm stage sees the plan approval instead of
    popping again, and a miss asks as today. The approval is
    CONSULTED, never re-issued: a picked action the user
    never approved still pops, and the eligibility
    exclusions still apply (a consequential step is never
    in the approved list, and covering_call re-verifies
    eligibility at dispatch). With none supplied — the
    usual case — behaviour is byte-identical.

    Wiring Build 7 (also additive): ``search`` is None (OFF —
    the default, byte-identical greedy pick), True (the bounded
    beam search with defaults), or a GoalSearchConfig
    (core/brain/goal_search.py, R7-3, the llm-reasoners beam-search
    pattern as VYUK's own code — the library itself is NOT
    depended on: its closure pulls torch, and its WorldModel.step
    contract needs a simulator a desktop agent cannot have).
    When enabled, each step asks the picker for up to beam_width
    candidates, scores them with the tree's own static signals
    (tool known, args valid, declares an expect, not a repeat of
    a just-failed action), and feeds the BEST into the existing
    single pick. The picker contract does not change (one action
    per pick), the trust path does not change, the search never
    executes anything, and a search-internal error falls back to
    the greedy pick (the first candidate) with a report line —
    a broken optimisation degrades to the proven path, never
    halts a goal greedy would have completed. A picker failure
    still halts as "picker failed", exactly as greedy would.
    """
    lines: list[str] = []

    def _say(line: str):
        lines.append(line)
        if emit is not None:
            try:
                emit(line)
            except Exception:  # noqa: BLE001 — progress feed is a nicety
                pass

    goal_text = str(goal_text or "").strip()
    if not goal_text:
        _say("Cannot run a goal: no goal text was given.")
        return "\n".join(lines)
    if pick_action is None:
        _say("Cannot run a goal: no pick_action callable was supplied.")
        return "\n".join(lines)
    # Wiring Build 7: normalise the search flag now, so a bad
    # value fails fast before any step runs (TypeError, never a
    # silent degrade to greedy).
    _search = normalise_search(search)
    max_steps = _goal_budget_int("goal_max_steps", 25)
    max_seconds = _goal_budget_seconds("goal_max_seconds", 600.0)
    strike_limit = _goal_budget_int("goal_max_strikes", 3)
    if max_steps < 1:
        max_steps = 1
    if strike_limit < 1:
        strike_limit = 1
    known_tools = _known_tools()
    _say(f"Running goal: {goal_text} "
         f"(budget: {max_steps} step(s), {int(max_seconds)}s):")
    started = time.monotonic()
    stamp = _epoch_stamp()
    # B11: fresh run or resume — the resume restores the goal
    # text, the full history, and the cumulative progress from
    # the ledger record (rebuild_goal), and starts a FRESH
    # budget window (window_steps 0, window number + 1).
    # Budgets are always read fresh from config at entry (the
    # stored ones are provenance for the previous window).
    _resuming = isinstance(resume_state, dict) and bool(resume_state)
    if _resuming:
        goal_id = str(resume_state.get("goal_id", "") or uuid.uuid4().hex)
        history: list[dict] = [dict(e) for e in
                               resume_state.get("history") or []
                               if isinstance(e, dict)]
        completed: list[str] = [str(t) for t in
                                resume_state.get("completed_tools") or []]
        # B17: the executed steps (tool/args/description) ride
        # the resume, so a resumed goal's lesson covers the
        # steps run before the stop as well as after it.
        executed_steps: list[dict] = [dict(s) for s in
                                      resume_state.get("executed_steps") or []
                                      if isinstance(s, dict)]
        steps_used = int(resume_state.get("steps_used", 0) or 0)
        window_steps = 0
        budget_windows_used = int(
            resume_state.get("budget_windows_used", 2) or 2)
        resume_count = int(resume_state.get("resume_count", 1) or 1)
        step_no = max(0, int(resume_state.get("next_step", 1) or 1) - 1)
        strikes = int(resume_state.get("strikes", 0) or 0)
        elapsed_base = float(resume_state.get("elapsed_s", 0.0) or 0.0)
        _say(f"Resuming goal (resume {resume_count}, budget window "
             f"{budget_windows_used} — fresh {max_steps}-step / "
             f"{int(max_seconds)}s window, history intact "
             f"({len(history)} record(s), {steps_used} action(s) "
             f"executed before this window).")
    else:
        goal_id = uuid.uuid4().hex
        history = []
        completed = []
        # B17: the executed steps (tool/args/description) for
        # the lesson write at completion — the history events
        # carry no args, and a lesson needs them to be reusable.
        executed_steps: list[dict] = []
        steps_used = 0
        window_steps = 0
        budget_windows_used = 1
        resume_count = 0
        step_no = 0
        strikes = 0
        elapsed_base = 0.0
    # §2.5: the stored plan + the in-flight marker + the M-25
    # run mode. A fresh run builds approved_plan from the
    # plan_approval pair (task id, approved StepDef list) the
    # driver already holds — {task_id, steps: [{tool, args,
    # label, index}], approved_at} — and starts with no step
    # in-flight. A resume restores all three from the rebuilt
    # state (goal_to_record preserves approved_plan and the
    # M-25 flags across upserts; in_flight is state-only, so a
    # cleared marker is never resurrected). approved_at is the
    # driver's entry time — the dialog's own timestamp is not
    # carried in the plan_approval pair, so the run start is
    # the best provenance available.
    if _resuming:
        approved_plan = resume_state.get("approved_plan")
        if not isinstance(approved_plan, dict):
            approved_plan = None
        in_flight = resume_state.get("in_flight")
        if not isinstance(in_flight, dict):
            in_flight = None
        strict_completion = bool(
            strict_completion or resume_state.get("strict_completion"))
        pause_on_denial = bool(
            pause_on_denial or resume_state.get("pause_on_denial"))
    else:
        approved_plan = None
        in_flight = None
        if plan_approval is not None:
            try:
                _pa_task_id, _pa_steps = plan_approval
                _plan_entries = []
                for _i, _s in enumerate(_pa_steps or []):
                    if isinstance(_s, dict):
                        _plan_entries.append({
                            "tool": str(_s.get("tool", "") or ""),
                            "args": dict(_s.get("args") or {}),
                            "label": str(_s.get("label", "") or ""),
                            "index": _i})
                    else:
                        _plan_entries.append({
                            "tool": str(getattr(_s, "tool", "") or ""),
                            "args": dict(getattr(_s, "args", None) or {}),
                            "label": str(getattr(_s, "label", "") or ""),
                            "index": _i})
                approved_plan = {
                    "task_id": str(_pa_task_id),
                    "steps": _plan_entries,
                    "approved_at": time.time(),
                }
            except Exception:  # noqa: BLE001 — no stored plan: run unplanned
                approved_plan = None
    # M-25 (Batch 1): a goal whose plan includes click_anywhere
    # steps runs with strict_completion + pause_on_denial — the
    # flags ride the goal record (see _write_record below) so
    # resume preserves them. Default for non-opt-in goals is
    # unchanged: the latch only fires on a click_anywhere step.
    if approved_plan is not None:
        try:
            from .. import click_anywhere_tool as _ca
            _strict, _pause = _ca.click_anywhere_goal_flags(
                approved_plan.get("steps") or [])
            strict_completion = bool(strict_completion or _strict)
            pause_on_denial = bool(pause_on_denial or _pause)
        except Exception:  # noqa: BLE001 — unlatchable: flags stay
            pass
    # B11: the ledger. An explicit ledger wins; otherwise the
    # shared default ledger (same as the coordinator — a fresh
    # TaskLedger() reloads from disk under the ledger lock on
    # every method, so separate instances never clobber each
    # other). A ledger failure yields None and the goal runs
    # in-memory-only, exactly like the coordinator.
    _ledger = ledger
    if _ledger is None:
        try:
            from .task_coordinator_tail import _default_ledger
            _ledger = _default_ledger()
        except Exception:  # noqa: BLE001 — ledger failure is in-memory-only
            _ledger = None

    def _elapsed_total() -> float:
        return elapsed_base + (time.monotonic() - started)

    def _write_record(state: str, verdict=None, reason: str = "") -> None:
        """B11: one ledger write, best-effort. A ledger failure
        never breaks the goal — the loop runs in-memory-only
        and the next write retries. ``verdict`` is a
        (verdict, reason) pair or None."""
        if _ledger is None:
            return
        try:
            _ledger.upsert_goal({
                "goal_id": goal_id,
                "goal_text": goal_text,
                "state": state,
                "history": history,
                "completed_tools": completed,
                # B17: the executed steps ride the record, so a
                # resumed goal's lesson covers pre-stop steps.
                "executed_steps": executed_steps,
                "steps_used": steps_used,
                "window_steps": window_steps,
                "budget_windows_used": budget_windows_used,
                "resume_count": resume_count,
                "next_step": step_no + 1,
                "strikes": strikes,
                "elapsed_s": _elapsed_total(),
                "max_steps": max_steps,
                "max_seconds": max_seconds,
                "strike_limit": strike_limit,
                "goal_verdict": (
                    {"verdict": str(verdict[0]),
                     "reason": str(verdict[1] or "")}
                    if isinstance(verdict, (tuple, list)) and verdict
                    else None),
                "reason": reason,
                # §2.5: the stored plan + the in-flight marker
                # + the M-25 mode ride every record write, so a
                # crash mid-step leaves a resumable record that
                # names the dispatched step and the plan it came
                # from. _write_record reads the closure vars, so
                # the dispatch-time write below and the outcome
                # writes after it carry the current values.
                "approved_plan": approved_plan,
                "in_flight": in_flight,
                "strict_completion": strict_completion,
                "pause_on_denial": pause_on_denial,
            })
        except Exception:  # noqa: BLE001 — ledger failure is in-memory-only
            pass

    def _end(state: str, verdict=None, reason: str = "") -> str:
        """B11: every exit writes its terminal/paused record,
        then returns the report. The budget stops write
        "paused" (resumable); everything else writes its
        terminal state."""
        _write_record(state, verdict=verdict, reason=reason)
        # B17: the lesson write — one place, best-effort. Only
        # terminal outcomes teach (the helper filters); a
        # lesson failure never breaks the goal or the report.
        try:
            record_task_lesson(
                playbook, name=goal_text, steps=executed_steps,
                outcome=state,
                goal_verdict=(
                    {"verdict": str(verdict[0]),
                     "reason": str(verdict[1] or "")}
                    if isinstance(verdict, (tuple, list)) and verdict
                    else None),
                proof_job_id=f"goal:{goal_id}", failure_reason=reason)
        except Exception:  # noqa: BLE001 — a lesson failure never breaks a goal
            pass
        return "\n".join(lines)

    # B17: the lesson in play (if the lookup below matched
    # one) and whether the current step came from it. The
    # strike fires only for a lesson-sourced step that
    # definitively failed (see _strike_out and the no-change
    # branch) — a gate refusal or an unknown outcome never
    # strikes. Initialised here so the closures below can read
    # them at call time.
    lesson_name = None
    lesson_proof = ""
    cur_from_playbook = False
    # Wiring Build 2 — J9: look up a playbook before acting. A
    # proven hit becomes the action queue (each step still runs
    # through the funnel, budgets, and strikes below); a miss
    # changes nothing and the picker runs as today. Matching is
    # the goal text, exactly or case-insensitively — never a
    # different playbook guessed from a similar name.
    # B11: a RESUMED goal never re-runs the lookup — its
    # proven steps may already have run (the history says so),
    # and re-queueing them would repeat completed work.
    playbook_queue: list = []
    if not _resuming and playbook is not None and hasattr(playbook, "reuse"):
        lookup = None
        matched_name = None
        try:
            lookup = playbook.reuse(goal_text)
            if lookup.get("ok"):
                matched_name = goal_text
            elif hasattr(playbook, "names"):
                for name in playbook.names():
                    if str(name).lower() == goal_text.lower():
                        lookup = playbook.reuse(name)
                        matched_name = name
                        break
        except Exception:  # noqa: BLE001 — a playbook failure never breaks a goal
            lookup = None
            matched_name = None
        if lookup and lookup.get("ok") and lookup.get("steps"):
            lesson_name = matched_name
            lesson_proof = str(lookup.get("proof_job_id") or "")
            playbook_queue = list(lookup["steps"])
            # M-24 (Batch 1): a playbook step for an opted-out
            # tool (click_anywhere) is never replayed — a
            # replayed click from a stale lesson is the failure
            # the opt-out exists to prevent. The check reads the
            # tool's registry record (playbook_opt_out: True).
            try:
                from .. import click_anywhere_tool as _ca
                _kept = [s for s in playbook_queue
                         if not (isinstance(s, dict) and _ca.is_playbook_opted_out(
                             str(s.get("tool", "") or "")))]
                _dropped = len(playbook_queue) - len(_kept)
                if _dropped:
                    playbook_queue = _kept
                    history.append({"event": "playbook-opt-out",
                                    "dropped": _dropped})
                    _say(f"Playbook: {_dropped} opted-out step(s) "
                         f"(click_anywhere) will not be replayed.")
            except Exception:  # noqa: BLE001 — a filter failure
                # never breaks a goal; the queue runs as matched.
                pass
            _say(f"Playbook matched for this goal: "
                 f"{len(playbook_queue)} proven step(s) will run first "
                 f"through the normal gate stack "
                 f"(proof job {lookup.get('proof_job_id') or 'unrecorded'}).")
            history.append({"event": "playbook", "steps": len(playbook_queue),
                            "proof_job_id": lookup.get("proof_job_id")})

    # Wiring Build 2 — J7: the interruption state machine for
    # this run. Created only when a check is supplied; with no
    # check, interruption adds nothing and changes nothing.
    goal_run = None
    if interruption_check is not None:
        try:
            from .. import interruption_budget as _j7

            goal_run = _j7.InterruptionBudget().new_run(goal_text)
        except Exception:  # noqa: BLE001
            goal_run = None

    # Scoped approval, once, before the loop (see docstring).
    scope_label = None
    try:
        tainted_now = False
        try:
            from .. import injection as _injection

            tainted_now = bool(_injection.tainted())
        except Exception:  # noqa: BLE001
            tainted_now = False
        if tainted_now:
            from ..funnel.tools_gate import grant_screen_scope  # Phase 4 S1b INTENTIONAL: gate split to funnel

            scope_label = f"goal:{goal_text[:48]}"
            grant_screen_scope(plan=scope_label)
            _say("Scoped approval: the session is tainted, so the v5.46 "
                 "screen scope was granted once for this goal run "
                 "(navigation freed; high-impact actions still gate "
                 "individually through execute()).")
    except Exception:  # noqa: BLE001 — execute()'s own gating still applies
        scope_label = None

    async def _observe_now():
        try:
            if observe is not None:
                return await _maybe_await(observe())
            return _default_observe()
        except Exception:  # noqa: BLE001 — an unobservable moment is None
            return None

    def _resilience_tripped() -> bool:
        """Wiring Build 3: the shared Resilience trip signal.

        A caller-supplied callable wins; otherwise the shared
        gate is consulted, so the Doctor page's trip and this
        loop's halt are the same signal. A broken probe never
        un-trips: it reports False only when no trip source
        answers at all, and the failsafe check above still
        applies independently.
        """
        if resilience_tripped is not None:
            try:
                return bool(resilience_tripped())
            except Exception:  # noqa: BLE001 — a broken probe is not a trip
                return False
        try:
            from ..resilience import resilience_gate as _gate  # Phase 3 R1: moved to core/resilience/ (relative: the absolute voice_assistant form does not resolve under the PYTHONPATH=. test layout and the except below would silently read the gate as untripped)

            return bool(_gate.is_tripped())
        except Exception:  # noqa: BLE001 — no gate module, no trip state
            return False

    def _stop_checks() -> str | None:
        """Halt reason when a hard stop fired, else None. Checked
        before EVERY step (and every retry) per the item spec."""
        if _failsafe_tripped():
            return "the failsafe is tripped (emergency stop)"
        if _resilience_tripped():
            return ("resilience tripped (control paused until a human "
                    "re-arms it)")
        if _epoch_changed(stamp):
            return "the session epoch changed (the live session ended or was swapped)"
        if max_seconds > 0 and time.monotonic() - started > max_seconds:
            return f"the time budget was exhausted ({int(max_seconds)}s)"
        return None

    def _progress_summary() -> str:
        if completed:
            return (f"Progress so far: {len(completed)} action(s) completed "
                    f"({', '.join(completed)}); {steps_used} action(s) executed.")
        return f"Progress so far: no actions completed; {steps_used} action(s) executed."

    async def _check_goal():
        """B10 — ONE goal check: the goal text judged against a
        fresh snapshot through the EXISTING verifier
        (verify_expect — the same callback the step checks use;
        production callers supply core.desktop.uia.
        verify_expectation). Returns (verdict, reason) in
        {"met", "not-met", "unverifiable"}.

        Same rules as the coordinator's check
        (TurnCoordinatorExecutionMixin._check_goal): the goal
        STATE is judged now (no "before" snapshot — a goal
        whose end state is already true is achieved); the
        completed steps' results ride along as result text;
        no callback, or a callback/snapshot failure, degrades
        to "unverifiable", never a guessed verdict. The
        picker's {"done": ...} is a claim this check judges,
        not the completion itself. Rejected (do not re-open):
        asking the picker "are we done?" (self-certification);
        re-running the plan on not-met (B22 re-planning); a
        second verification phase.
        """
        after = await _observe_now()
        result_text = "; ".join(
            str(h.get("result", "") or "")
            for h in history
            if h.get("event") == "completed" and h.get("result"))
        verdict, reason = await _verify_async(
            verify_expect, goal_text, None, after, result_text)
        return verdict, reason

    def _goal_verdict_line(verdict, reason):
        return f"Goal check: {verdict}" + (f" — {reason}" if reason else "")

    async def _strike_out(why: str):
        """B12: the ONE shared strike-out path for a step that
        failed after its one retry — the no-change/not-met ladder
        and the transient-execution retry both land here, so the
        strike budget, the ledger write, and the goal verdict
        line are identical for both. Returns the terminal report
        when the strike limit ended the goal (the caller returns
        it), else None (the caller breaks to replan)."""
        nonlocal strikes
        strikes += 1
        history.append({"event": "step_failed", "step": step_no,
                        "tool": cur_tool, "reason": why,
                        "strikes": strikes})
        # B17: a lesson-sourced step that definitively failed
        # (its one retry spent) strikes the lesson — never
        # trusted again on the same evidence. A gate refusal
        # never reaches here (it halts, it does not strike),
        # and a timeout/unknown outcome never reaches here
        # either (B12: they halt, never retry, never strike).
        # Best-effort: a strike failure never breaks the goal.
        if cur_from_playbook and lesson_name and playbook is not None:
            try:
                strike_lesson(playbook, name=lesson_name,
                              reason=f"step {step_no} [{cur_tool}] "
                                     f"failed on reuse: {why}")
            except Exception:  # noqa: BLE001 — a strike failure never breaks a goal
                pass
        if strikes >= strike_limit:
            # B10: a strike stop still gets its goal verdict
            # line — the goal was checked at the stop, and the
            # line says what it said.
            verdict, greason = await _check_goal()
            _say(f"  ✗ Goal step {step_no} [{cur_tool}] — {why}; "
                 f"the retry failed too. Goal stopped: strike "
                 f"limit reached ({strikes}/{strike_limit} "
                 f"consecutive failed steps). {_progress_summary()}")
            _say(_goal_verdict_line(verdict, greason))
            return _end("failed", (verdict, greason),
                        "strike limit reached")
        _say(f"  ✗ Goal step {step_no} [{cur_tool}] — {why}; the "
             f"retry failed too (strike {strikes}/{strike_limit}) "
             f"— replanning.")
        # B11: per-step durability — the failed step's strike is
        # in the ledger before replanning, so a crash here does
        # not grant a fresh strike budget.
        _write_record("running")
        return None

    # B11: the opening record — the goal exists durably from
    # its first step, so a crash before any step completes
    # still leaves a "running" record to resume from. (A goal
    # that never started — empty text, no picker — returns
    # above and writes nothing.)
    _write_record("running")

    try:
        while True:
            if window_steps >= max_steps:
                # B10: a budget stop still gets its goal verdict
                # line — the goal was checked at the stop, and
                # the line says what it said.
                # B11: a budget stop is a RESUMABLE paused
                # record, not a terminal stop — resume_goal
                # continues from the next pick in a fresh window.
                verdict, greason = await _check_goal()
                _say(f"Goal stopped: step budget exhausted "
                     f"({window_steps} step(s) used in budget window "
                     f"{budget_windows_used}; {steps_used} total). "
                     f"{_progress_summary()}")
                _say(_goal_verdict_line(verdict, greason))
                _say(f"Resumable: resume_goal('{goal_id}') continues "
                     f"this goal from the next pick with the history "
                     f"intact, in a fresh {max_steps}-step / "
                     f"{int(max_seconds)}s budget window.")
                return _end("paused", (verdict, greason),
                            "budget_exhausted (step)")
            halt_reason = _stop_checks()
            if halt_reason:
                # B10: only a TIME-budget halt gets a goal
                # verdict line here — a failsafe / resilience /
                # epoch halt is an emergency stop, not a budget
                # report, and runs no extra work.
                if halt_reason.startswith("the time budget was exhausted"):
                    verdict, greason = await _check_goal()
                    _say(f"Goal halted: {halt_reason}. {_progress_summary()}")
                    _say(_goal_verdict_line(verdict, greason))
                    _say(f"Resumable: resume_goal('{goal_id}') continues "
                         f"this goal from the next pick with the history "
                         f"intact, in a fresh {max_steps}-step / "
                         f"{int(max_seconds)}s budget window.")
                    return _end("paused", (verdict, greason),
                                "budget_exhausted (time)")
                else:
                    _say(f"Goal halted: {halt_reason}. {_progress_summary()}")
                    return _end("failed", None, halt_reason)
            step_no += 1
            observation = await _observe_now()
            # Wiring Build 2 — J7, at the step boundary (never
            # mid-tool-call, so a step pauses cleanly): pause,
            # then resume only on an explicit resume=True, else
            # abandon with the held-back list. A budget-exceeded
            # pause abandons even on resume=True, per GoalRun.
            if interruption_check is not None and goal_run is not None:
                try:
                    request = await _maybe_await(interruption_check())
                except Exception:  # noqa: BLE001
                    request = None
                if isinstance(request, dict):
                    pause = goal_run.interrupt(
                        str(request.get("reason") or "interruption requested"),
                        urgent=bool(request.get("urgent")),
                        held_item=str(request.get("held_item") or ""))
                    _say(f"  ⏸ Goal step {step_no}: {pause['report']}")
                    history.append({"event": "interrupted", "step": step_no,
                                    "report": pause["report"],
                                    "state": pause["state"]})
                    if pause["state"] != "paused" or not request.get("resume"):
                        if pause["state"] == "paused":
                            abandon = goal_run.abandon(
                                str(request.get("reason") or "interruption requested"))
                            _say(f"  ⏹ Goal step {step_no}: {abandon['report']}")
                        _say(f"Goal stopped at step {step_no}: interrupted. "
                             f"{_progress_summary()}")
                        return _end("cancelled", None, "interrupted")
                    resumed = goal_run.resume()
                    _say(f"  ▶ Goal step {step_no}: {resumed['report']}")
                    history.append({"event": "resumed", "step": step_no,
                                    "report": resumed["report"]})
            # M-24 (Batch 1): defense in depth — an opted-out tool
            # (click_anywhere) is never replayed, even if it
            # reached the queue past the lookup-time filter.
            # Dropped steps are skipped, never executed; when the
            # queue drains, the picker runs as normal.
            action = None
            if playbook_queue:
                while playbook_queue:
                    _candidate = playbook_queue.pop(0)
                    _opted_out = False
                    try:
                        from .. import click_anywhere_tool as _ca
                        _opted_out = (
                            isinstance(_candidate, dict)
                            and _ca.is_playbook_opted_out(
                                str(_candidate.get("tool", "") or "")))
                    except Exception:  # noqa: BLE001 — unreadable:
                        # fail-safe: an unreadable check drops a
                        # click_anywhere-shaped step only.
                        _opted_out = (
                            isinstance(_candidate, dict)
                            and str(_candidate.get("tool", "") or "")
                            == "click_anywhere")
                    if _opted_out:
                        history.append(
                            {"event": "playbook-opt-out",
                             "step": step_no,
                             "tool": _candidate.get("tool")})
                        continue
                    action = _candidate
                    break
            if action is not None:
                # B17: this step came from the lesson — a
                # definitive failure of it strikes the lesson.
                cur_from_playbook = True
            else:
                cur_from_playbook = False
                try:
                    if _search is not None:
                        # Wiring Build 7: the bounded beam search
                        # chooses among the picker's own candidates
                        # and feeds the BEST into the existing
                        # single pick below — the pick site, the
                        # gates, and the execution path are
                        # unchanged.
                        action, _search_report = await search_pick(
                            _search, pick_action, goal_text,
                            _picker_observation(observation,
                                                goal_context),
                            history)
                        _say(report_line(_search_report))
                    else:
                        action = await _maybe_await(
                            pick_action(
                                goal_text,
                                _picker_observation(observation, goal_context),
                                history))
                except Exception as e:  # noqa: BLE001
                    _say(f"Goal halted at step {step_no}: the action picker "
                         f"failed ({e}). {_progress_summary()}")
                    return _end("failed", None, f"picker failed: {e}")
            if action is None:
                _say(f"Goal stopped at step {step_no}: the picker returned "
                     f"no action (honest stop). {_progress_summary()}")
                return _end("failed", None, "picker returned no action")
            if isinstance(action, dict) and "done" in action:
                # B10: the picker's "done" is a claim the goal
                # check judges, not the completion itself.
                verdict, greason = await _check_goal()
                _say(_goal_verdict_line(verdict, greason))
                if verdict == "not-met":
                    _say(f"Goal NOT complete: the goal {goal_text!r} "
                         f"was checked and is not done ({greason}). "
                         f"{_progress_summary()}")
                    return _end("failed", (verdict, greason),
                                "goal check not-met")
                if verdict == "unverifiable" and strict_completion:
                    # M-25 (strict completion): an unverifiable
                    # check never completes the goal — the claim
                    # is refused, not granted on a guess.
                    _say(f"Goal NOT complete: the goal {goal_text!r} "
                         f"could not be verified "
                         f"({greason or 'no check available'}); strict "
                         f"completion refuses to complete on an "
                         f"unverifiable check. {_progress_summary()}")
                    return _end("failed", (verdict, greason),
                                "goal check unverifiable under strict "
                                "completion")
                _say(f"Goal complete: {action.get('done')}")
                _say(f"Goal finished: {len(completed)} action(s) completed "
                     f"in {steps_used} executed action(s).")
                return _end("completed", (verdict, greason), "")
            tool, args, expect_text, words = _parse_goal_action(action)
            if not tool:
                _say(f"Goal halted at step {step_no}: the picker returned "
                     f"a malformed action ({action!r}). {_progress_summary()}")
                return _end("failed", None, "malformed action")
            # M-25 (Batch 1): the first picked click_anywhere step
            # latches strict completion + pause-on-denial for the
            # rest of this goal run (plan-less goals included);
            # the flags ride the record via _write_record, so
            # resume preserves them.
            if tool == "click_anywhere":
                strict_completion = True
                pause_on_denial = True
            # Wiring Build 2 — J1: resolve a reference against
            # the current context before acting; unresolved means
            # proceed exactly as picked, never with a guess.
            if isinstance(action, dict):
                resolved_action, resolution, reference = _apply_j1(
                    action, words, _j1_context(observation, goal_context))
                if reference is not None:
                    if resolution is not None and resolution.resolved \
                            and resolution.candidate is not None:
                        action = resolved_action
                        tool, args, expect_text, words = _parse_goal_action(action)
                        # M-25 (Batch 1): a J1 resolution that lands
                        # on click_anywhere latches the flags too.
                        if tool == "click_anywhere":
                            strict_completion = True
                            pause_on_denial = True
                        _say(f"  resolved '{reference}' to "
                             f"'{resolution.candidate.label}' "
                             f"(from {resolution.candidate.source})")
                        history.append({"event": "resolved", "step": step_no,
                                        "reference": reference,
                                        "label": resolution.candidate.label,
                                        "control_id": resolution.candidate.control_id})
                    else:
                        _say(f"  reference '{reference}' could not be "
                             f"resolved from context; proceeding with the "
                             f"action as picked (no guessed target).")
            if tool in ("run_routine", "run_plan", "run_goal"):
                _say(f"Goal halted at step {step_no}: goals cannot nest "
                     f"routines/plans/goals ('{tool}'). {_progress_summary()}")
                return _end("failed", None,
                            f"nested {tool} refused")
            if known_tools is not None and tool not in known_tools:
                _say(f"Goal halted at step {step_no}: '{tool}' is not a "
                     f"registered tool. {_progress_summary()}")
                return _end("failed", None, f"unknown tool: {tool}")
            _say(f"Goal step {step_no}: {words or tool} [{tool}]")
            cur_action, cur_tool, cur_args, cur_expect = action, tool, args, expect_text
            before_obs = observation
            # Command Deck Phase D: the picked action is a real
            # plan step — it feeds the bus trace. m stays 0: an
            # open-ended goal has no known total, and none is
            # invented for it.
            emit_step(n=step_no, m=0, label=cur_expect or cur_tool)
            attempt = 0
            while True:
                halt_reason = _stop_checks()
                if halt_reason:
                    # B10: only a TIME-budget halt gets a goal
                    # verdict line here — see the loop top.
                    if halt_reason.startswith("the time budget was exhausted"):
                        verdict, greason = await _check_goal()
                        _say(f"Goal halted: {halt_reason}. {_progress_summary()}")
                        _say(_goal_verdict_line(verdict, greason))
                        _say(f"Resumable: resume_goal('{goal_id}') continues "
                             f"this goal from the next pick with the history "
                             f"intact, in a fresh {max_steps}-step / "
                             f"{int(max_seconds)}s budget window.")
                        return _end("paused", (verdict, greason),
                                    "budget_exhausted (time)")
                    else:
                        _say(f"Goal halted: {halt_reason}. {_progress_summary()}")
                        return _end("failed", None, halt_reason)
                if window_steps >= max_steps:
                    # B10: same budget-stop verdict line as the
                    # loop top (see there).
                    verdict, greason = await _check_goal()
                    _say(f"Goal stopped: step budget exhausted "
                         f"({window_steps} step(s) used in budget window "
                         f"{budget_windows_used}; {steps_used} total). "
                         f"{_progress_summary()}")
                    _say(_goal_verdict_line(verdict, greason))
                    _say(f"Resumable: resume_goal('{goal_id}') continues "
                         f"this goal from the next pick with the history "
                         f"intact, in a fresh {max_steps}-step / "
                         f"{int(max_seconds)}s budget window.")
                    return _end("paused", (verdict, greason),
                                "budget_exhausted (step)")
                # Wiring Build 2 — control breadth: a control
                # action is revalidated through the hardened path
                # before it executes; a refusal halts like any
                # refused action and is never retried or bypassed.
                gate_ok, gate_reason, gate_note = _control_gate(
                    cur_tool, cur_args,
                    _control_snapshot_for(cur_action, control_snapshot, before_obs))
                if gate_note:
                    _say(f"  {gate_note}")
                if not gate_ok:
                    history.append({"event": "halted", "step": step_no,
                                    "tool": cur_tool, "reason": gate_reason})
                    _say(f"  ✗ Goal step {step_no} [{cur_tool}] — halted: "
                         f"{gate_reason}. The goal stops here; a gated or "
                         f"refused action is never retried or bypassed. "
                         f"{_progress_summary()}")
                    return _end("failed", None, gate_reason)
                # Command Deck Phase D: one Started/Finished pair
                # per actual execution attempt (a retried step
                # executes again, so it emits again — honestly).
                emit_tool_started(kind=cur_tool)
                # B20: the step runs inside a live token (same
                # design as the coordinator's _run_step) — a
                # Stop pressed while the tool runs ends it at
                # its next yield point. The goal loop honours
                # the failsafe trip at its step boundaries
                # (_stop_checks above), so its token honours
                # stop signals; the resilience trip and the
                # epoch change still act at boundaries only.
                # The token is passed ONLY when the callee
                # declares it: the suites fake _execute_tool
                # with two-argument executors, and an
                # unconditional third argument would break
                # every one of them (the coordinator's
                # _executor_accepts_token rule, same reason —
                # inspected per call, never cached, because
                # the callee is patched per test).
                from .task_cancel import live_token as _live_token
                with _live_token(goal_id, True) as _goal_token:
                    try:
                        import inspect as _inspect
                        _takes_token = (
                            "cancel_token" in _inspect.signature(
                                _execute_tool).parameters)
                    except (TypeError, ValueError):
                        _takes_token = False
                    # B23 (approve-plan-once coverage): a picked
                    # action covered by a plan approval the user
                    # already gave runs without a fresh popup —
                    # arm the channel by content match before
                    # the dispatch, exactly as the coordinator's
                    # _run_step does positionally. No approval
                    # context (the usual case) leaves behaviour
                    # byte-identical: the funnel pops as today.
                    # The token resets right after the dispatch,
                    # so it can never leak to a later pick, a
                    # retry of a different action, or any other
                    # call.
                    _plan_token = None
                    if plan_approval is not None:
                        try:
                            from .. import plan_approval as _pa
                            _pa_task_id, _pa_steps = plan_approval
                            _plan_token = _pa.arm_for_call(
                                _pa_task_id, _pa_steps,
                                cur_tool, cur_args)
                        except Exception:  # noqa: BLE001 — no plan context: ask normally
                            _plan_token = None
                    # §2.5: the dispatch-time in-flight marker —
                    # written to the ledger BEFORE the tool runs,
                    # so a crash mid-step leaves a record naming
                    # the dispatched step. step_no here is the
                    # 0-based plan index (the loop's step_no is
                    # 1-based during execution), so the re-verify
                    # can look the step up as approved_plan[
                    # step_no] and check its digest before
                    # trusting the args. The digest is
                    # plan_approval's canonical SHA-256 over the
                    # args. The marker is cleared the moment the
                    # dispatch resolves (below); only a process
                    # death in between leaves it standing, and
                    # that is exactly what
                    # _resume_verify_in_flight settles.
                    try:
                        from .. import plan_approval as _pa_d
                        _in_flight_digest = _pa_d._digest(cur_args)
                    except Exception:  # noqa: BLE001 — local canonical fallback
                        try:
                            _in_flight_digest = hashlib.sha256(
                                json.dumps(cur_args, sort_keys=True,
                                           default=str).encode("utf-8")
                            ).hexdigest()
                        except Exception:  # noqa: BLE001 — undigestible args
                            _in_flight_digest = ""
                    in_flight = {"tool": cur_tool,
                                 "digest": _in_flight_digest,
                                 "step_no": step_no - 1,
                                 "dispatched_at": time.time()}
                    _write_record("running")
                    try:
                        if _takes_token:
                            result = _execute_tool(
                                cur_tool, cur_args, cancel_token=_goal_token)
                        else:
                            result = _execute_tool(cur_tool, cur_args)
                    finally:
                        if _plan_token is not None:
                            try:
                                from .. import plan_approval as _pa2
                                _pa2.end_step_dispatch(_plan_token)
                            except Exception:  # noqa: BLE001 — teardown never breaks a step
                                pass
                # §2.5: the dispatch resolved — the step is no
                # longer in-flight, whatever the outcome was
                # (success, failure, refusal, cancellation all
                # flow through here). The outcome writes below
                # carry in_flight None; only a process death
                # between the dispatch write above and this line
                # leaves the marker standing, by design.
                in_flight = None
                _result_status = (result.get("status", "")
                                  if isinstance(result, dict) else "")
                emit_tool_finished(
                    status=("success" if _execution_succeeded(result)
                            else "cancelled"
                            if _result_status == "cancelled"
                            else "failed"),
                    kind=cur_tool)
                steps_used += 1
                window_steps += 1
                after_obs = await _observe_now()
                if _result_status == "cancelled":
                    # B20: the user said stop while this step
                    # ran — the goal stops as "cancelled", never
                    # "failed" (nothing failed, and the B12
                    # transient path below must not retry it —
                    # a retry would do the thing the user just
                    # stopped). The B11 ledger record is written
                    # by _end; the B10 goal check does not run
                    # on a cancelled goal; B17 writes no lesson
                    # for "cancelled" (record_task_lesson writes
                    # only for "completed" / "failed").
                    _cancel_reason = (
                        _failure_reason(result)
                        or "cancelled at the user's request")
                    history.append({"event": "cancelled",
                                    "step": step_no, "tool": cur_tool,
                                    "reason": _cancel_reason})
                    _say(f"  ⦸ Goal step {step_no} [{cur_tool}] — "
                         f"cancelled: {_cancel_reason}. "
                         f"{_progress_summary()}")
                    return _end("cancelled", None, _cancel_reason)
                if not _execution_succeeded(result):
                    reason = _failure_reason(result)
                    if _is_transient_failure(result):
                        if attempt == 0:
                            # B12: a transient execution failure
                            # retries the SAME step once — the
                            # action was right, the execution
                            # blipped. The retry shares the step's
                            # one retry (the `attempt` flag) with
                            # the no-change/not-met ladder below, so
                            # a step gets one retry total, whichever
                            # mechanism uses it first. The
                            # re-execution re-enters the loop top,
                            # so the gate and the stop checks run
                            # again first; before_obs stays the
                            # pre-step observation (the step has not
                            # completed). A gated/refused action
                            # never reaches here —
                            # _is_transient_failure is False for
                            # every refusal, timeout and unknown
                            # outcome.
                            attempt = 1
                            history.append({"event": "retry",
                                            "step": step_no,
                                            "tool": cur_tool,
                                            "reason": reason,
                                            "retry": True,
                                            "kind": "transient"})
                            _say(f"  ↻ Goal step {step_no} [{cur_tool}] — "
                                 f"{reason}; transient failure, retrying "
                                 f"the same step once.")
                            # B11: the retry is in the ledger before
                            # the re-execution, so a crash here does
                            # not grant a fresh retry.
                            _write_record("running")
                            continue
                        # Retry spent: strike like not-met through
                        # the shared path — never halt outright.
                        stopped = await _strike_out(reason)
                        if stopped is not None:
                            return stopped
                        break
                    if pause_on_denial and _is_confirm_denial(result):
                        # M-23 / §2.5: a confirm denial (an
                        # explicit No, or a confirm timeout's
                        # default-deny) PAUSES the goal instead of
                        # failing it — the human said no to this
                        # step, and the goal resumes from the next
                        # pick when he re-issues it. Narrow: only
                        # the confirm gate's not_confirmed kind
                        # pauses; every other refusal (failsafe,
                        # kill-chain, validation, budget) still
                        # halts below as before.
                        history.append({"event": "denied",
                                        "step": step_no, "tool": cur_tool,
                                        "reason": reason})
                        _say(f"  ⏸ Goal step {step_no} [{cur_tool}] — "
                             f"paused: the confirm was denied ({reason}). "
                             f"Resumable: resume_goal('{goal_id}') continues "
                             f"this goal from the next pick. "
                             f"{_progress_summary()}")
                        return _end("paused", None,
                                    f"confirm denied: {reason}")
                    history.append({"event": "halted", "step": step_no,
                                    "tool": cur_tool, "reason": reason})
                    _say(f"  ✗ Goal step {step_no} [{cur_tool}] — halted: "
                         f"{reason}. The goal stops here; a gated or "
                         f"refused action is never retried or bypassed. "
                         f"{_progress_summary()}")
                    return _end("failed", None, reason)
                passed, reason = check_result(
                    _normalise_check(cur_action) if isinstance(cur_action, dict) else None,
                    result)
                if not passed:
                    _say(f"  ✗ Goal step {step_no} [{cur_tool}] — halted: "
                         f"result check failed ({reason}). {_progress_summary()}")
                    return _end("failed", None,
                                f"result check failed: {reason}")
                changed = _observation_changed(before_obs, after_obs)
                verdict, vreason = "unverifiable", ""
                if cur_expect:
                    verdict, vreason = await _verify_async(
                        verify_expect, cur_expect, before_obs, after_obs, result)
                result_text = str(result.get("result", "")
                                  if isinstance(result, dict) else result or "")
                if changed is not False and verdict != "not-met":
                    strikes = 0
                    history.append({"event": "completed", "step": step_no,
                                    "tool": cur_tool,
                                    "result": _short(result_text, 120),
                                    "expect_verdict": verdict if cur_expect else None})
                    completed.append(cur_tool)
                    # B17: the executed step, with its args, for
                    # the lesson write at completion (the history
                    # event above carries no args — the lesson
                    # needs them to be reusable).
                    executed_steps.append({
                        "tool": cur_tool, "args": dict(cur_args),
                        "description": words or cur_expect or cur_tool})
                    extra = f" [expect {verdict}]" if cur_expect else ""
                    _say(f"  ✓ Goal step {step_no} [{cur_tool}] — "
                         f"{_short(result_text)}{extra}")
                    # B11: per-step durability — the completed
                    # step is in the ledger before the next
                    # pick, so a crash here loses nothing.
                    _write_record("running")
                    break
                why = ("no visible change was observed"
                       if changed is False else
                       f"verification not met: {vreason}")
                if attempt == 0:
                    attempt = 1
                    hint = _ladder_hint()
                    history.append({"event": "retry", "step": step_no,
                                    "tool": cur_tool, "reason": why,
                                    "retry": True, "hint": hint})
                    # B17: the lesson step itself failed (no
                    # change / expectation not met) — its effect
                    # did not land, so the lesson is struck now,
                    # before the retry pick (the retry is a
                    # different action, picked fresh — it is not
                    # the lesson's step, and the flag is cleared
                    # when it is adopted below).
                    if cur_from_playbook and lesson_name and playbook is not None:
                        try:
                            strike_lesson(playbook, name=lesson_name,
                                          reason=f"step {step_no} [{cur_tool}] "
                                                 f"failed on reuse: {why}")
                        except Exception:  # noqa: BLE001 — a strike failure never breaks a goal
                            pass
                    _say(f"  ↻ Goal step {step_no} [{cur_tool}] — {why}; "
                         f"retrying once. {hint}")
                    try:
                        retry_action = await _maybe_await(
                            pick_action(goal_text, after_obs, history))
                    except Exception as e:  # noqa: BLE001
                        _say(f"Goal halted at step {step_no}: the action "
                             f"picker failed on the retry ({e}). "
                             f"{_progress_summary()}")
                        return _end("failed", None,
                                    f"picker failed on retry: {e}")
                    if retry_action is None:
                        _say(f"Goal stopped at step {step_no}: the picker "
                             f"returned no action on the retry (honest "
                             f"stop). {_progress_summary()}")
                        return _end("failed", None,
                                    "picker returned no action on retry")
                    if isinstance(retry_action, dict) and "done" in retry_action:
                        # B10: same rule as the main loop — the
                        # retry picker's "done" is a claim the
                        # goal check judges, not the completion
                        # itself.
                        verdict, greason = await _check_goal()
                        _say(_goal_verdict_line(verdict, greason))
                        if verdict == "not-met":
                            _say(f"Goal NOT complete: the goal "
                                 f"{goal_text!r} was checked and is "
                                 f"not done ({greason}). "
                                 f"{_progress_summary()}")
                            return _end("failed", (verdict, greason),
                                        "goal check not-met")
                        _say(f"Goal complete: {retry_action.get('done')}")
                        _say(f"Goal finished: {len(completed)} action(s) "
                             f"completed in {steps_used} executed action(s).")
                        return _end("completed", (verdict, greason), "")
                    r_tool, r_args, r_expect, _r_words = _parse_goal_action(retry_action)
                    if not r_tool or r_tool in ("run_routine", "run_plan", "run_goal") \
                            or (known_tools is not None and r_tool not in known_tools):
                        _say(f"Goal halted at step {step_no}: the retry "
                             f"action was not usable ({retry_action!r}). "
                             f"{_progress_summary()}")
                        return _end("failed", None,
                                    "retry action not usable")
                    # J1 on the retry action too, same rule as
                    # the first pick: resolve, or proceed as picked.
                    if isinstance(retry_action, dict):
                        r_action, r_resolution, r_reference = _apply_j1(
                            retry_action, _r_words,
                            _j1_context(after_obs, goal_context))
                        if r_reference is not None and r_resolution is not None \
                                and r_resolution.resolved \
                                and r_resolution.candidate is not None:
                            retry_action = r_action
                            r_tool, r_args, r_expect, _r_words = \
                                _parse_goal_action(retry_action)
                    cur_action, cur_tool, cur_args, cur_expect = \
                        retry_action, r_tool, r_args, r_expect
                    # B17: the retry is a picked action, not the
                    # lesson's step — clear the flag (the lesson
                    # step already failed above) and take the
                    # retry's own words for the lesson record.
                    words = _r_words
                    cur_from_playbook = False
                    before_obs = after_obs
                    continue
                stopped = await _strike_out(why)
                if stopped is not None:
                    return stopped
                break
    finally:
        if scope_label is not None:
            try:
                from ..funnel.tools_gate import revoke_screen_scope  # Phase 4 S1b INTENTIONAL: gate split to funnel

                revoke_screen_scope(plan=scope_label)
            except Exception:  # noqa: BLE001
                pass


def _in_flight_landed(tool, args):
    """§2.5 (M-22): can a stale in-flight step's effect be
    observed, and did it land? Returns (landed, checkable).

    checkable is False when the effect cannot be observed
    from live state — the step is NEVER re-executed on an
    unknown outcome (a run_command probe, a submit / pay /
    send / download: the goal pauses and asks instead).
    checkable is True for the two observable shapes:

    * write_file — the file's current bytes are compared
      against the declared bytes in the DETECTED encoding
      (Batch 0 core.file_bytes: BOM sniff, else UTF-8);
      overwrite compares the whole file, append compares
      the tail. A file that cannot be read is uncheckable,
      not "not landed".
    * read-class tools (the funnel's own _is_read_class) —
      they leave no effect to verify, and re-executing a
      read is safe, so the step is treated as not landed
      (re-pick) rather than unknown.

    Everything else is uncheckable by design.
    """
    from pathlib import Path as _Path

    if tool == "write_file":
        path = str((args or {}).get("filepath", "") or
                   (args or {}).get("path", "") or "")
        content = (args or {}).get("content")
        if not path or not isinstance(content, str):
            return False, False
        try:
            current = _Path(path).read_bytes()
        except Exception:  # noqa: BLE001 — unreadable is unobservable
            return False, False
        try:
            from ..file_bytes import detect_encoding, encode_text
            encoding = detect_encoding(current)
            declared = encode_text(content, encoding,
                                   append=bool((args or {}).get("append")))
        except Exception:  # noqa: BLE001 — fall back to plain UTF-8 bytes
            declared = content.encode("utf-8", errors="strict") \
                if isinstance(content, str) else b""
        if (args or {}).get("append"):
            return current.endswith(declared), True
        return current == declared, True
    try:
        from ..funnel.tools_gate_readclass import _is_read_class
        if _is_read_class(tool, args or {}):
            return False, True
    except Exception:  # noqa: BLE001 — no read classifier: uncheckable
        pass
    return False, False


def _reverify_unverifiable(state, step_no, tool, why):
    """The in-flight step cannot be verified — no stored plan,
    or the plan does not match the marker — so its args are
    unrecoverable and it can never be appended as landed
    (U-2: never invent args). Whether the resume may proceed
    depends on whether re-executing the step is safe:
      * consequential (the never-set, or _is_consequential on
        the tool NAME — no args are available to check) →
        pause + ask (M-22: a submit / pay / send / download
        whose outcome is unknown is never re-executed);
      * anything else → not-landed: the step is re-picked
        (the resume index stays on it, the marker clears).
        The re-pick still goes through the picker's choice
        and execute()'s own gates — the re-verify only
        settles the index, it never forces the step.
    Returns (state, block_reason) like _resume_verify_in_flight.
    """
    from .. import plan_approval as _pa
    if tool in _pa._NEVER_PLAN_KINDS or _pa._is_consequential(tool, {}):
        return state, (
            f"in-flight step {step_no} ({tool or 'unknown tool'}) has an "
            f"unknown outcome ({why}) and re-executing it is unsafe — "
            "it was not re-run; confirm whether it landed and "
            "re-issue the goal")
    state["in_flight"] = None
    if isinstance(step_no, int) and not isinstance(step_no, bool) \
            and step_no >= 0:
        state["next_step"] = step_no + 1
    return state, None


async def _resume_verify_in_flight(state, *, observe=None):
    """§2.5 (T-5, FIRST): settle a stale in-flight marker
    before the re-gate and the re-entry. Returns
    (state, block_reason): block_reason is None when the
    resume may proceed, else the pause message — the goal
    pauses WITHOUT re-entering run_goal (the step was not
    re-run, and no resume is consumed).

    The marker carries only {tool, digest, step_no,
    dispatched_at} — no args (U-2). The args source is
    approved_plan[step_no]; the digest is checked against
    that step's declared args before the args are trusted
    (an off-plan pick drifts the index, so a mismatch is
    unverifiable, never a guessed match).

    Outcomes: landed → the step is appended to
    executed_steps exactly as the success branch appends
    ({tool, args, description: label} from the plan) and
    the resume index moves past it; not landed → the
    resume index stays on the step (re-pick); unverifiable
    (no plan, or the plan does not match the marker) →
    _reverify_unverifiable: a consequential step pauses +
    asks, anything else is re-picked. U-5: the settled
    index is written into state["next_step"] — the handoff
    channel to _re_gate_remaining (its signature takes no
    index). next_step is 1-based; the in-flight step_no is
    the 0-based plan index, so landed means next_step =
    step_no + 2 and not-landed means next_step = step_no + 1.
    """
    in_flight = state.get("in_flight")
    if not isinstance(in_flight, dict) or not in_flight:
        return state, None
    step_no = in_flight.get("step_no")
    tool = str(in_flight.get("tool", "") or "")
    digest = str(in_flight.get("digest", "") or "")
    plan = state.get("approved_plan")
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if (not isinstance(steps, list) or not isinstance(step_no, int)
            or isinstance(step_no, bool)
            or step_no < 0 or step_no >= len(steps)):
        return _reverify_unverifiable(
            state, step_no, tool, "no stored plan to check it against")
    entry = steps[step_no]
    if not isinstance(entry, dict) \
            or str(entry.get("tool", "") or "") != tool:
        return _reverify_unverifiable(
            state, step_no, tool,
            "it does not match the stored plan")
    try:
        from .. import plan_approval as _pa
        _entry_digest = _pa._digest(entry.get("args") or {})
    except Exception:  # noqa: BLE001 — no digest to check against
        _entry_digest = None
    if _entry_digest is not None and digest \
            and _entry_digest != digest:
        return _reverify_unverifiable(
            state, step_no, tool,
            "it does not match the stored plan's args")
    args = dict(entry.get("args") or {})
    label = str(entry.get("label", "") or tool)
    # A fresh observation grounds the check in live state,
    # not the stale record — best-effort; a failed observe
    # does not block the effect check, which reads live
    # state directly.
    if observe is not None:
        try:
            await _maybe_await(observe())
        except Exception:  # noqa: BLE001 — an unobservable moment is None
            pass
    landed, checkable = _in_flight_landed(tool, args)
    if not checkable:
        return state, (
            f"in-flight step {step_no} ({tool}) may or may not have landed "
            "and its outcome cannot be observed — it was not re-run; "
            "confirm whether it landed and re-issue the goal")
    if landed:
        executed = state.get("executed_steps")
        if not isinstance(executed, list):
            executed = []
            state["executed_steps"] = executed
        executed.append({"tool": tool, "args": args,
                         "description": label})
        completed = state.get("completed_tools")
        if isinstance(completed, list) and tool not in completed:
            completed.append(tool)
        state["in_flight"] = None
        state["next_step"] = step_no + 2
        return state, None
    state["in_flight"] = None
    state["next_step"] = step_no + 1
    return state, None


def _re_gate_remaining(goal_id, record, state):
    """§2.5 (T-5, SECOND): re-present the plan-approval
    dialog for the REMAINING steps only, after the
    re-verify settled the in-flight step. Returns
    (plan_approval, pause_reason): plan_approval is the
    (task_id, remaining StepDefs) pair for run_goal's
    dispatch channel, or None when the goal must pause —
    with pause_reason naming why.

    U-5: the remaining steps are filtered by the settled
    index the re-verify wrote into state["next_step"]
    (the :262 conversion — step_no = max(0, next_step - 1));
    this function takes no index parameter. A denied
    re-gate consumes no resume (the caller pauses without
    re-entering run_goal, so the resume count is never
    incremented for it). The re-gate also re-creates the
    goal-start alarm — by re-entering the goal: the
    extension observes the live goal via GET /goal and
    re-creates its 1-minute alarm (Batch 1 channel); there
    is no main-process alarm action in the lab, and none
    is faked here.

    No stored plan — the goal was never plan-backed
    (open-ended goals run on the picker alone and never
    have one) — means there is nothing to re-gate and
    nothing to invent: the goal resumes with no plan
    approval, exactly as it ran before. (Plan 9 §2.5's
    "no-plan branch pauses" sentence assumes every resumed
    goal is plan-backed; it is not — the B11 resume tests
    prove plan-less goals resume, and a goal that ran
    under a plan ALWAYS carries approved_plan in its
    record, since run_goal writes it at entry whenever
    plan_approval is given. A plan-backed goal whose plan
    is missing cannot occur through run_goal.) An empty
    remaining list → nothing to re-gate: the pair carries
    the original task id with no steps, and run_goal
    proceeds on the picker alone.
    """
    plan = state.get("approved_plan")
    if not isinstance(plan, dict) or not plan.get("steps"):
        return (str(state.get("goal_id") or goal_id), []), None
    steps = [s for s in plan.get("steps") or [] if isinstance(s, dict)]
    if not steps:
        return (str(state.get("goal_id") or goal_id), []), None
    step_no = max(0, int(state.get("next_step", 1) or 1) - 1)
    remaining = steps[step_no:]
    task_id = str(plan.get("task_id", "") or goal_id)
    try:
        from .task_coordinator_steps import StepDef
    except Exception:  # noqa: BLE001 — no StepDef: build plain step objects
        StepDef = None
    remaining_defs = []
    for entry in remaining:
        if StepDef is not None:
            remaining_defs.append(StepDef(
                tool=str(entry.get("tool", "") or ""),
                args=dict(entry.get("args") or {}),
                label=str(entry.get("label", "") or "")))
        else:
            from types import SimpleNamespace as _NS
            remaining_defs.append(_NS(
                tool=str(entry.get("tool", "") or ""),
                args=dict(entry.get("args") or {}),
                label=str(entry.get("label", "") or "")))
    if not remaining_defs:
        return (task_id, []), None
    task = type("RemainingPlan", (), {
        "task_id": task_id, "steps": remaining_defs,
        "title": str(state.get("goal_text", "") or "goal")})()
    try:
        from .. import plan_approval as _pa
        indices, outcome = _pa.request_plan_decision_ex(task)
    except Exception as exc:  # noqa: BLE001 — the dialog itself failed
        return None, f"the plan-approval dialog failed ({exc}) — re-issue it"
    if outcome == "no_handler":
        return None, (
            "no plan-approval dialog is available — re-issue the goal "
            "from the GUI")
    if outcome == "timeout":
        return None, (
            "the plan-approval dialog timed out with no answer — "
            "re-issue the goal")
    if outcome == "error":
        return None, (
            "the plan-approval dialog errored — re-issue the goal")
    if outcome == "ask_each_time" or not indices:
        # The human declined the re-gate (or approved nothing):
        # pause, consuming no resume — the remaining steps were
        # not re-approved and are not run.
        return None, (
            "the remaining plan was not re-approved — re-issue the goal")
    approved_defs = [remaining_defs[i] for i in indices
                     if isinstance(i, int) and 0 <= i < len(remaining_defs)]
    try:
        _pa.approve_plan(task_id, remaining_defs, indices)
    except Exception:  # noqa: BLE001 — the record write is best-effort
        pass
    return (task_id, approved_defs), None


def _write_resume_paused(_ledger, record, state, reason):
    """§2.5: write the paused record for a resume that was
    blocked before re-entry (re-verify unknown, or the
    re-gate denied / no-plan / no-handler). Best-effort —
    a ledger failure leaves the goal in-memory-only, exactly
    like run_goal's _write_record. The blocked resume
    consumed NO resume and NO budget window: the record
    keeps the ORIGINAL resume_count and
    budget_windows_used, while the re-verify's settled
    next_step (and the cleared in_flight, when settled)
    is kept — the next resume starts from the settled
    index, not from before it. in_flight is NOT cleared
    here: when the re-verify blocked, the marker is the
    only evidence of the unknown dispatch, and the next
    resume must try to settle it again."""
    if _ledger is None:
        return
    paused = dict(state)
    paused["state"] = "paused"
    paused["reason"] = reason
    paused["resume_count"] = int(record.get("resume_count", 0) or 0)
    paused["budget_windows_used"] = int(
        record.get("budget_windows_used", 1) or 1)
    paused["window_steps"] = 0
    try:
        _ledger.upsert_goal(paused)
    except Exception:  # noqa: BLE001 — ledger failure is in-memory-only
        pass


async def resume_goal(goal_id, *, ledger=None, pick_action, observe=None,
                      emit=None, verify_expect=None, goal_context=None,
                      playbook=None, interruption_check=None,
                      control_snapshot=None, resilience_tripped=None,
                      plan_approval=None) -> str:
    """Resume an interrupted open-ended goal (B11).

    The clearly-named sibling of the coordinator's resume path:
    loads the goal record from the ledger (default ledger when
    none is supplied, same as run_goal), rebuilds the resume
    state (a fresh budget window, the history intact), and
    re-enters run_goal with it. Terminal records raise
    ValueError (they are history, not resumable); a missing
    record raises ValueError too. A resume is always explicit
    user action — nothing here auto-runs, and the resume count
    is visible in the record, so a goal can never silently
    loop through window after window.

    The picker is supplied by the caller (the original picker
    is not stored — it is a callable, not data), exactly like
    the coordinator's executor is injected on resume.

    §2.5 (T-5 order): the resume settles the in-flight step
    FIRST (_resume_verify_in_flight — landed / not-landed /
    unknown), then re-gates the REMAINING steps
    (_re_gate_remaining — the plan-approval dialog for what
    is left, filtered by the settled index the re-verify
    wrote into state["next_step"]), and only then re-enters
    run_goal. A blocked re-verify or a denied / impossible
    re-gate pauses the goal WITHOUT re-entering — the step
    was not re-run, and the blocked resume consumed no
    resume and no budget window. ``plan_approval`` (None by
    default) lets a caller supply an approval pair directly,
    which replaces the re-gate step only — the re-verify
    still runs first. The M-25 run mode
    (strict_completion / pause_on_denial) is forwarded from
    the rebuilt state, so a resumed goal keeps the mode it
    started in.
    """
    _ledger = ledger
    if _ledger is None:
        try:
            from .task_coordinator_tail import _default_ledger
            _ledger = _default_ledger()
        except Exception:  # noqa: BLE001 — no ledger, no resume
            _ledger = None
    if _ledger is None:
        raise ValueError("no task ledger available to resume a goal from")
    record = _ledger.get_goal(goal_id)
    if record is None:
        raise ValueError(f"no goal record {goal_id!r} in the task ledger")
    state = _ledger.rebuild_goal(record)
    state, _blocked = await _resume_verify_in_flight(
        state, observe=observe)
    if _blocked is not None:
        _write_resume_paused(_ledger, record, state, _blocked)
        return (f"Goal {goal_id!r} paused on resume: {_blocked}. "
                f"Re-issue the goal to try again.")
    _gated = plan_approval
    if _gated is None:
        _gated, _gate_blocked = _re_gate_remaining(goal_id, record, state)
        if _gated is None:
            _write_resume_paused(_ledger, record, state, _gate_blocked)
            return (f"Goal {goal_id!r} paused on resume: {_gate_blocked}. "
                    f"Re-issue the goal to try again.")
    return await run_goal(
        state["goal_text"], pick_action=pick_action, observe=observe,
        emit=emit, verify_expect=verify_expect, goal_context=goal_context,
        playbook=playbook, interruption_check=interruption_check,
        control_snapshot=control_snapshot,
        resilience_tripped=resilience_tripped, ledger=_ledger,
        resume_state=state, plan_approval=_gated,
        strict_completion=bool(state.get("strict_completion")),
        pause_on_denial=bool(state.get("pause_on_denial")))
