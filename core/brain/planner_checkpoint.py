# planner_checkpoint.py — planner family split out of
# core/brain/planner.py (skills split B41, part 2 of 2).
# Bodies below are AST-verbatim from the old module; only the
# import block above is new. Cross-family names are imported
# with INTENTIONAL comments, never redefined.

import time
from .planner_storage import (
    MAX_ROUTINE_ACTIONS,
    _PLANS_PATH,
    _damaged_note,
    _load_plans,
    _load_routines,
    _now,
    _save_list,
    _short,
)
# INTENTIONAL (skills split B41): plans family lives in
# core/brain/planner_plans.py; imported, never redefined.
from .planner_plans import (
    _EXPECT_STRIKE_LIMIT,
    _RESUMABLE_STATUSES,
    _eval_when,
    _execute_tool,
    _execution_succeeded,
    _expect_text,
    _failure_reason,
    _find_plan,
    _is_unknown_outcome,
    _probe_snapshot,
    _progress,
    _record_expect_verdict,
    _runtime_on_fail,
    _runtime_when,
    _verify_async,
    check_result,
)
# INTENTIONAL (skills split B41): routines family lives in
# core/brain/planner_routines.py; imported, never redefined.
from .planner_routines import (
    _find_routine,
)



# ---------------------------------------------------------------------------
# B12 auto-complete + B15 run_plan driver (v5.45)
#
# observe_tool_execution() is the seam the brain clients call
# with every tool outcome they execute (local builtin + Ollama,
# Groq, Gemini Live's tool worker, lite intents): when a PENDING
# plan step declared exactly that tool and the execution
# SUCCEEDED (status success, no refusal marker, the step's own
# B12 check — when declared — passing), the step is marked done
# and an announcement line is returned for the transcript /
# result text. Matching is tool-name + success ONLY — arguments
# are never guessed at.
#
# ---------------------------------------------------------------------------
# Conversion pass, Wave 8 wiring (v5.63 modules into live paths).
# These are thin planner-level entry points over core/w8_wiring.py
# (the coordinator); they add NO tool declaration and change NO
# existing driver. run_routine / run_plan / run_goal keep their
# own semantics untouched — the checkpointed path is a separate
# entry point for straight-line step lists, and its step
# executor IS the live funnel (_execute_tool / registry
# execute), never a direct tool call.
# ---------------------------------------------------------------------------

def verify_steps_against_live_tools(steps):
    """Validate steps against the LIVE tool set before execution
    (core/w8_wiring.py). FAIL-CLOSED refuse on any deterministic
    failure or CoVe disagreement; irreversible steps are flagged
    (needs_gate) and still pass through the registry confirm
    gate at execution. Never auto-repairs a plan."""
    from .. import w8_wiring

    return w8_wiring.verify_steps_against_live_tools(steps)



def execute_step_with_reflection(step, executor=None, reflect_fn=None,
                                 task_type=""):
    """One funnel step with at most ONE reflection-retry when
    reflect_fn is supplied (core/w8_wiring.py + reflection).
    The retry is audited (retries_used / tainted / structure);
    a second failure ends the step as failed. Without
    reflect_fn this is a plain funnel execution."""
    from .. import w8_wiring

    return w8_wiring.execute_step_with_reflection(
        step, executor=executor, reflect_fn=reflect_fn,
        task_type=task_type)



def run_steps_checkpointed(steps, run_id, *, store_dir=None,
                           epoch="session", gate_callback=None,
                           executor=None, reflect_fn=None,
                           task_type="", verify=True):
    """Run fixed steps through the TC-1 checkpoint loop
    (core/w8_wiring.py + checkpoint): verified against the
    live tool set first (fail-closed), then executed through
    the funnel executor with a checkpoint AFTER each step.
    A confirm step pauses durably (gate_callback invoked);
    resume via resume_checkpointed_run(). Crash-mid-tool =
    UNKNOWN, human re-run / skip / abort — the loop never
    auto-retries or auto-skips (retry policy, when supplied,
    comes from the reflection layer, not the loop)."""
    from .. import w8_wiring

    if executor is None:
        # The funnel seam, exactly as run_goal/run_routine use
        # it: _execute_tool IS registry.execute with the full
        # gate stack. The adapter reshapes its dict into
        # CheckpointRun's outcome shape (ok / result / legs /
        # verdict); a refusal-shaped success is NOT ok.
        def _funnel_adapter(step, _execute=_execute_tool):
            result = _execute(step.get("tool", ""),
                              step.get("args") or {})
            ok = _execution_succeeded(result)
            # Audit A5: an outcome-unknown result (timeout) propagates
            # "unknown": True so no layer above may auto-retry it —
            # the call may still complete in the background, and a
            # retry risks double execution. Unknown outcomes halt
            # for a human decision, never for an automatic re-issue.
            unknown = _is_unknown_outcome(result)
            return {"ok": ok,
                    "unknown": unknown,
                    "result": (result.get("result")
                               if isinstance(result, dict) else result),
                    "legs": [],
                    "verdict": None,
                    "error": (None if ok else _failure_reason(result))}

        executor = _funnel_adapter
    return w8_wiring.run_steps_checkpointed(
        steps, run_id, store_dir=store_dir, epoch=epoch,
        gate_callback=gate_callback, executor=executor,
        reflect_fn=reflect_fn, task_type=task_type, verify=verify)



def resume_checkpointed_run(run_id, decision, epoch, *, store_dir=None,
                            steps, executor=None, unknown_decision=None):
    """Resume a paused or unknown checkpointed run (human
    decision only; epoch-void semantics apply). See
    core/w8_wiring.resume_checkpointed_run."""
    from .. import w8_wiring

    return w8_wiring.resume_checkpointed_run(
        run_id, decision, epoch, store_dir=store_dir, steps=steps,
        executor=executor, unknown_decision=unknown_decision)



def run_routine_checkpointed(name, *, store_dir=None, epoch="session",
                             gate_callback=None, reflect_fn=None):
    """Run a SAVED routine's actions through the checkpoint loop
    instead of run_routine's walk: same funnel executor, plus
    live-tool verification first and a durable checkpoint after
    each action, resumable via resume_checkpointed_run() with
    the same action list. DIFFERENCE from run_routine, stated
    plainly: when/on_fail branching and expect verification do
    NOT run here — the checkpointed path is straight-line
    (tool + args per action, in order). A routine that needs
    branching still uses run_routine."""
    routines, damaged = _load_routines()
    routine = _find_routine(routines, name)
    if routine is None:
        return {"state": "refused", "detail":
                f"No routine named '{name}'."}
    actions = routine.get("actions", [])[:MAX_ROUTINE_ACTIONS]
    steps = [{"tool": a.get("tool", ""), "args": a.get("args", {}) or {},
              "expect": a.get("expect")}
             for a in actions]
    outcome = run_steps_checkpointed(
        steps, f"routine:{name}", store_dir=store_dir, epoch=epoch,
        gate_callback=gate_callback, reflect_fn=reflect_fn)
    if damaged:
        outcome = dict(outcome)
        outcome["damaged_note"] = _damaged_note(damaged)
    return outcome



def observe_tool_execution(name: str, result) -> list[str]:
    """Auto-complete plan steps after a real execution (B12).
    Returns announcement lines (possibly empty). Never raises:
    bookkeeping must not break a brain turn."""
    try:
        if not name or not _execution_succeeded(result):
            return []
        plans, _damaged = _load_plans()
        announcements: list[str] = []
        changed = False
        for plan in plans:
            for idx, step in enumerate(plan.get("steps", [])):
                if step.get("status") != "pending":
                    continue
                if step.get("tool") != name:
                    continue
                passed, _reason = check_result(step.get("check"), result)
                if not passed:
                    continue
                step["status"] = "done"
                step["verification"] = (
                    f"auto-completed: {name} succeeded "
                    f"({_short(str(result.get('result') or ''), 100)})")
                changed = True
                done, total = _progress(plan)
                announcements.append(
                    f"Plan {plan.get('id')} step {idx + 1} auto-completed "
                    f"(\"{step.get('text')}\") — {name} ran and succeeded "
                    f"({done}/{total} done).")
        if changed:
            _save_list(_PLANS_PATH, plans)
        return announcements
    except Exception:  # noqa: BLE001
        return []



def _failsafe_tripped() -> bool:
    try:
        from .. import failsafe

        return bool(failsafe.tripped())
    except Exception:  # noqa: BLE001 — no failsafe module, no stop state
        return False



async def run_plan(plan_id: str, pick_tool=None, emit=None, verify_expect=None) -> str:
    """Walk one plan's pending steps in order (B15).

    pick_tool: async callable(step_dict, plan_dict) ->
    (tool_name, args_dict) | None — the active client's one
    model call per step. Consulted ONLY for steps that do not
    declare their own tool. emit: optional callable(line) that
    receives every progress line as it happens (the clients wire
    it to the transcript). Returns the full progress report.

    v5.49 (programme items 31 + 32 — legacy contract change: the
    optional verify_expect parameter and the when/on_fail/expect
    step fields are additive; plans without them behave exactly
    as before):
      * RESUME (item 32): a step is marked "running" and saved
        BEFORE it executes, so a process that dies mid-step
        leaves that trace; on the next run such a step is
        re-run — not skipped, not counted — while verified-done
        steps are skipped as always. plan_progress() reports
        the cursor this walk resumes from.
      * CONDITIONALS (item 32): a step's `when` probe runs
        through tools.execute before the step; a failed probe
        follows on_fail (default "skip"). A failed action keeps
        the halt behaviour below unless on_fail says otherwise.
      * EXPECT (item 31): a step's free-text `expect` is judged
        by verify_expect (see the item-31 helpers) and the
        verdict recorded on the step; three consecutive
        "not-met" verdicts on one step halt the plan.
    """
    lines: list[str] = []

    def _say(line: str):
        lines.append(line)
        if emit is not None:
            try:
                emit(line)
            except Exception:  # noqa: BLE001 — transcript is a nicety
                pass

    plans, damaged = _load_plans()
    plan = _find_plan(plans, plan_id)
    if plan is None:
        msg = f"No plan found with id '{plan_id}'. Use list_plans to see existing plans."
        _say(f"{msg}\n{_damaged_note(damaged)}" if damaged else msg)
        return "\n".join(lines)
    steps = plan.get("steps", [])
    _say(f"Running plan {plan.get('id')}: {plan.get('goal')} "
         f"({len(steps)} step(s)):")

    try:
        from ..funnel.tools_registry import tools as registry  # Phase 4 S1b INTENTIONAL: registry split to funnel
        known_tools = {d["name"] for d in registry.get_declarations()}
    except Exception as e:  # noqa: BLE001
        _say(f"Could not run plan: the tool registry is unavailable ({e}).")
        return "\n".join(lines)

    # v5.46 (programme item 2, D-C1): approving/running a plan
    # IS the scoped approval for the plan's duration — a plan
    # run has no user message to clear soft taint, so without a
    # grant every navigation step would popup (or fail closed
    # headless). Grant plan scope now (NOT on the early returns
    # above: no plan / registry unavailable never grants), and
    # revoke it in the finally below — every loop exit path
    # returns from inside the loop, so the whole loop rides
    # in try/finally. Tier-2 steps still gate individually;
    # the scope frees navigation only, exactly as an app
    # scope does. (Function-local import, like run_routine's:
    # the funnel registry imports this module at registration time.)
    from ..funnel.tools_gate import grant_screen_scope, revoke_screen_scope  # Phase 4 S1b INTENTIONAL: gate split to funnel

    grant_screen_scope(plan=plan.get("id"))
    try:
        done_count = sum(1 for s in steps if s.get("status") == "done")
        jumps = 0
        max_jumps = len(steps) * 3 + 10
        idx = 0
        while idx < len(steps):
            step = steps[idx]
            label = f"Step {idx + 1}/{len(steps)}: {step.get('text')}"
            status = str(step.get("status") or "pending")
            if status in ("done", "skipped"):
                _say(f"  – {label} — already {status}, skipping.")
                idx += 1
                continue
            # Failsafe gate (B15): the stop state is checked before
            # EVERY step — a trip mid-plan halts the walk here.
            if _failsafe_tripped():
                _say(f"  ✗ {label} — halted: the failsafe is tripped "
                     "(emergency stop). The plan stops here.")
                _say(f"Plan stopped at step {idx + 1} of {len(steps)} "
                     f"({done_count} step(s) completed by this run / earlier).")
                return "\n".join(lines)
            # v5.49 (programme item 32): resume — a step left in an
            # interrupted state by a dead process is re-run here.
            if status in _RESUMABLE_STATUSES:
                _say(f"  ↻ {label} — was left '{status}' by an interrupted "
                     "run; re-running it (not skipped, not counted).")
                step["status"] = "pending"
                status = "pending"
            on_fail, on_fail_error = _runtime_on_fail(step)
            if on_fail_error:
                _say(f"  ✗ {label} — halted: the step's on_fail is invalid ({on_fail_error})")
                _say(f"Plan stopped at step {idx + 1} of {len(steps)}. Steps after it did not run.")
                return "\n".join(lines)

            def _follow_on_fail(decision, why: str) -> int | None:
                """Apply an on_fail decision for this step. Returns
                the next index to walk, or None to halt the plan
                (the halt lines are already said)."""
                nonlocal jumps
                if decision == "skip":
                    step["status"] = "skipped"
                    step["verification"] = f"skipped: {why}"
                    _save_list(_PLANS_PATH, plans)
                    _say(f"  – {label} — skipped: {why}")
                    return idx + 1
                if isinstance(decision, dict):
                    jumps += 1
                    if jumps > max_jumps:
                        _say(f"  ✗ {label} — halted: {why} (and the goto jump limit was reached — refusing to loop)")
                        _say(f"Plan stopped at step {idx + 1} of {len(steps)}. Steps after it did not run.")
                        return None
                    step["status"] = "pending"
                    _save_list(_PLANS_PATH, plans)
                    _say(f"  ↪ {label} — {why}; jumping to step {decision['goto']}")
                    return decision["goto"] - 1
                step["status"] = "pending"
                _save_list(_PLANS_PATH, plans)
                _say(f"  ✗ {label} — halted: {why}")
                _say(f"Plan stopped at step {idx + 1} of {len(steps)}. Steps after it did not run.")
                return None

            # v5.49 (programme item 32): the `when` probe, through
            # tools.execute with all gates intact, before the step
            # (and before any model pick — a skipped step costs no
            # model call). A failed probe follows on_fail, whose
            # default for when-failures is "skip".
            when, when_error = _runtime_when(step)
            if when_error:
                _say(f"  ✗ {label} — halted: the step's 'when' is invalid ({when_error})")
                _say(f"Plan stopped at step {idx + 1} of {len(steps)}. Steps after it did not run.")
                return "\n".join(lines)
            if when is not None:
                passed_when, when_reason = _eval_when(when, known_tools)
                if not passed_when:
                    decision = on_fail if on_fail is not None else "skip"
                    nxt = _follow_on_fail(decision, f"'when' condition not met ({when_reason})")
                    if nxt is None:
                        return "\n".join(lines)
                    idx = nxt
                    continue
            tool = step.get("tool")
            args = step.get("args") if isinstance(step.get("args"), dict) else {}
            if not tool:
                if pick_tool is None:
                    _say(f"  ✗ {label} — halted: this step declares no tool "
                         "and no model picker is available to choose one.")
                    _say(f"Plan stopped at step {idx + 1} of {len(steps)}.")
                    return "\n".join(lines)
                try:
                    picked = await pick_tool(step, plan)
                except Exception as e:  # noqa: BLE001
                    _say(f"  ✗ {label} — halted: the model picker failed ({e}).")
                    _say(f"Plan stopped at step {idx + 1} of {len(steps)}.")
                    return "\n".join(lines)
                if not picked:
                    _say(f"  ✗ {label} — halted: the model did not pick a tool "
                         "for this step.")
                    _say(f"Plan stopped at step {idx + 1} of {len(steps)}.")
                    return "\n".join(lines)
                tool, args = picked[0], (picked[1] if len(picked) > 1
                                         and isinstance(picked[1], dict) else {})
            if tool == "run_routine" or tool == "run_plan":
                _say(f"  ✗ {label} — halted: plans cannot nest routines/plans.")
                _say(f"Plan stopped at step {idx + 1} of {len(steps)}.")
                return "\n".join(lines)
            if tool not in known_tools:
                _say(f"  ✗ {label} — halted: '{tool}' is not a registered tool.")
                _say(f"Plan stopped at step {idx + 1} of {len(steps)}.")
                return "\n".join(lines)
            expect_text = _expect_text(step)
            # v5.49 (programme item 32): mark the step "running"
            # and persist BEFORE executing — if the process dies
            # mid-step, the next run resumes by re-running exactly
            # this step (see _RESUMABLE_STATUSES / plan_progress).
            step["status"] = "running"
            step["attempt_started"] = _now()
            _save_list(_PLANS_PATH, plans)
            next_idx = None
            while True:
                before_snap = _probe_snapshot() if expect_text else None
                started = time.perf_counter()
                result = registry.execute(tool, dict(args or {}))
                elapsed = f"({time.perf_counter() - started:.1f}s)"
                after_snap = _probe_snapshot() if expect_text else None
                result_text = str(result.get("result", "")
                                  if isinstance(result, dict)
                                  else result or "")
                if not _execution_succeeded(result):
                    reason = _failure_reason(result)
                    if on_fail is not None:
                        step["verification"] = f"failed: {reason}"
                        _save_list(_PLANS_PATH, plans)
                        nxt = _follow_on_fail(on_fail, reason)
                        if nxt is None:
                            return "\n".join(lines)
                        next_idx = nxt
                        break
                    step["verification"] = f"halted: {reason}"
                    step["status"] = "pending"
                    _save_list(_PLANS_PATH, plans)
                    _say(f"  ✗ {label} [{tool}] — halted: {reason} {elapsed}")
                    _say(f"Plan stopped at step {idx + 1} of {len(steps)}. "
                         "Steps after it did not run.")
                    return "\n".join(lines)
                passed, reason = check_result(step.get("check"), result)
                if not passed:
                    if on_fail is not None:
                        step["verification"] = f"check failed: {reason}"
                        _save_list(_PLANS_PATH, plans)
                        nxt = _follow_on_fail(on_fail, f"result check failed ({reason})")
                        if nxt is None:
                            return "\n".join(lines)
                        next_idx = nxt
                        break
                    step["verification"] = f"check failed: {reason}"
                    step["status"] = "pending"
                    _save_list(_PLANS_PATH, plans)
                    _say(f"  ✗ {label} [{tool}] — halted: result check failed "
                         f"({reason}) {elapsed}")
                    _say(f"Plan stopped at step {idx + 1} of {len(steps)}. "
                         "Steps after it did not run.")
                    return "\n".join(lines)
                # v5.49 (programme item 31): free-text expect
                # verdict. "unverifiable" (including no callback
                # injected) keeps the pre-v5.49 behaviour exactly
                # and never strikes; "not-met" retries the SAME
                # step until the three-strikes limit halts it.
                if expect_text:
                    verdict, vreason = await _verify_async(
                        verify_expect, expect_text, before_snap, after_snap, result)
                    _record_expect_verdict(step, verdict, vreason)
                    if verdict == "not-met":
                        strikes = int(step.get("expect_strikes") or 0)
                        step["verification"] = (
                            f"verification not-met (strike {strikes}/{_EXPECT_STRIKE_LIMIT}): {vreason}")
                        _save_list(_PLANS_PATH, plans)
                        if strikes < _EXPECT_STRIKE_LIMIT:
                            _say(f"  ↻ {label} [{tool}] — verification not met "
                                 f"(strike {strikes}/{_EXPECT_STRIKE_LIMIT}): {vreason} — retrying {elapsed}")
                            continue
                        if on_fail is not None:
                            nxt = _follow_on_fail(
                                on_fail, f"verification not met three times (three-strikes): {vreason}")
                            if nxt is None:
                                return "\n".join(lines)
                            next_idx = nxt
                            break
                        step["status"] = "pending"
                        _save_list(_PLANS_PATH, plans)
                        _say(f"  ✗ {label} [{tool}] — halted: verification not met "
                             f"three times (three-strikes): the step's expect was "
                             f"\"{expect_text}\" — {vreason} {elapsed}")
                        _say(f"Plan stopped at step {idx + 1} of {len(steps)}. "
                             "Steps after it did not run.")
                        return "\n".join(lines)
                    step["verification"] = (
                        f"{_short(result_text, 160)} [expect {verdict}: {vreason}]")
                else:
                    # Verified: the result text IS the verification record —
                    # it carries H5's post-action probe sentence when the
                    # tool produced one.
                    step["verification"] = _short(result_text, 200)
                step["status"] = "done"
                done_count += 1
                _save_list(_PLANS_PATH, plans)
                _say(f"  ✓ {label} [{tool}] — {_short(result_text)} {elapsed}")
                next_idx = idx + 1
                break
            idx = next_idx if next_idx is not None else idx + 1
        _say(f"Plan {plan.get('id')} finished: {done_count}/{len(steps)} "
             "step(s) done.")
        return "\n".join(lines)
    finally:
        revoke_screen_scope(plan=plan.get("id"))
