"""w8_wiring part module (skills split B14): w8_wiring_steps."""
from __future__ import annotations

import re
from pathlib import Path  # FIX (skills split B14, caught by import-completeness proof): checkpoint_store_dir uses Path.
# Module-level imports travel verbatim with the units that use them.
from .brain import checkpoint, plan_verify, reflection  # Phase 3 B1a-1
# INTENTIONAL (skills split B14): cross-part names from registry/funnel (P3).
from .w8_wiring_registry import live_arg_schemas, live_tool_names
from .w8_wiring_funnel import funnel_step_executor



# ---------------------------------------------------------------------
# Plan verification against the LIVE tool set (item 4, FAIL-CLOSED)
# ---------------------------------------------------------------------


def _cove_answer_factory(plan_steps):
    """A CoVe answer function over FRESH registry evidence: for
    each template question it re-reads tool existence and arg
    names from the live registry (a second, independent read —
    not the validator's own verdict, which used an earlier read).
    Only the existence question names the tool, so the step is
    resolved by its index and the other questions are answered
    from that step's tool schema. A target question answers
    True (the validator's target check is the evidence there;
    CoVe does not second-guess a target it cannot resolve)."""

    def answer(question: str) -> bool:
        match = re.search(r"Step (\d+):", question)
        if match is None:
            return False
        index = int(match.group(1))
        if index < 0 or index >= len(plan_steps):
            return False
        tool = plan_steps[index].tool
        known = live_tool_names()
        schemas = live_arg_schemas()
        if tool not in known or tool not in schemas:
            return False
        if "arguments" in question:
            listed = re.findall(r"'([A-Za-z_][A-Za-z0-9_]*)'",
                                question.split("arguments", 1)[1])
            return all(arg in schemas[tool] for arg in listed)
        return True

    return answer




def verify_steps_against_live_tools(steps: list[dict]) -> dict:
    """Validate every step against the LIVE tool set before
    execution. Returns {"ok", "refused", "checks", "cove",
    "needs_gate", "detail"}.

    FAIL-CLOSED (owner-overturnable to repair, never
    auto-repaired here): ok is False when any step fails a
    deterministic check OR when CoVe disagrees with the
    deterministic verdict (disagreement escalates to the gate
    as a refusal, never an override). needs_gate lists steps
    whose tool is irreversible — those still run, through the
    registry's own confirm gate, when ok is True.
    """
    known = live_tool_names()
    schemas = live_arg_schemas()
    reversible: set[str] = set()  # no tool claims reversibility here;
    # the registry's confirm gate is the reversibility control.
    checks: list[dict] = []
    plan_steps: list[plan_verify.PlanStep] = []
    for index, step in enumerate(steps):
        plan_step = plan_verify.PlanStep(
            tool=step.get("tool", ""), args=dict(step.get("args") or {}),
            target=step.get("target"))
        plan_steps.append(plan_step)
        result = plan_verify.validate_plan(
            plan_step, known, schemas,
            exists=lambda _target: True, reversible_tools=reversible)
        checks.append({"step": index, "tool": plan_step.tool,
                       "all_valid": result["all_valid"],
                       "needs_gate": result["needs_gate"],
                       "name": result["name"].passed,
                       "validators": [(v.name, v.passed, v.detail)
                                      for v in result["validators"]]})
    deterministic_ok = all(c["all_valid"] for c in checks)
    cove = plan_verify.cove_check(
        plan_steps, _cove_answer_factory(plan_steps))
    # CoVe disagreement = deterministic verdict and CoVe verdict
    # differ on the plan as a whole: escalate (refuse).
    disagree = (deterministic_ok != cove["agree"])
    ok = deterministic_ok and not disagree
    needs_gate = [c["step"] for c in checks if c["needs_gate"]]
    if ok:
        detail = "plan verified against the live tool set"
        if needs_gate:
            detail += (f"; steps {needs_gate} are irreversible and "
                       "still pass through the registry confirm gate")
    elif not deterministic_ok:
        failed = [c["step"] for c in checks if not c["all_valid"]]
        detail = (f"plan REFUSED fail-closed: steps {failed} failed "
                  "deterministic validation against the live tool set")
    else:
        detail = ("plan REFUSED fail-closed: CoVe check disagrees "
                  "with deterministic validation (escalation signal; "
                  "no auto-override in either direction)")
    return {"ok": ok, "refused": not ok, "checks": checks, "cove": cove,
            "cove_agrees": not disagree, "needs_gate": needs_gate,
            "detail": detail}




# ---------------------------------------------------------------------
# Reflexion around a live step executor (item 5)
# ---------------------------------------------------------------------


def execute_step_with_reflection(step: dict, executor=None,
                                 reflect_fn=None,
                                 task_type: str = "") -> dict:
    """Execute one step through the funnel, with at most ONE
    reflection-retry when reflect_fn is supplied.

    B17: the reflection path is RETIRED (see
    core/brain/reflection.py — B12's retry ladder already
    provides the one bounded retry, and no live loop ever
    supplies a reflect_fn). Without reflect_fn this is a plain
    funnel execution (no reflection is invented); the function
    stays callable for the checkpoint path and tests, but no
    live loop routes through it.

    Returns the funnel outcome plus an audited reflection
    report: {"outcome", "retries_used", "reflected", "tainted",
    "structure", "reflection"}. Without reflect_fn this is a
    plain funnel execution (no reflection is invented). The
    retry cap is the module's counter — a second failure ends
    the step as failed, never a loop.
    """
    run = executor or funnel_step_executor
    if reflect_fn is None:
        outcome = run(step)
        return {"outcome": outcome, "retries_used": 0,
                "reflected": False, "tainted": False,
                "structure": None, "reflection": None}
    return reflection.reflect_and_retry(
        step, run, reflect_fn, task_type=task_type)




# ---------------------------------------------------------------------
# Checkpoint loop: planner entry points (item 1)
# ---------------------------------------------------------------------


def checkpoint_store_dir() -> Path:
    """Runtime checkpoint state lives under data/ (never shipped)."""
    from .config import DATA_DIR

    return Path(str(DATA_DIR)) / "checkpoints"




def run_steps_checkpointed(steps: list[dict], run_id: str, *,
                           store_dir=None, epoch: str = "session",
                           gate_callback=None, executor=None,
                           reflect_fn=None, task_type: str = "",
                           verify: bool = True) -> dict:
    """Run fixed steps through the checkpoint loop.

    Steps are validated against the LIVE tool set first
    (fail-closed refuse, nothing executed on refusal). Each
    step then runs through the funnel executor (or the
    reflective executor when reflect_fn is supplied — the
    checkpoint loop itself never auto-retries). A confirm
    step pauses durably; gate_callback(run_id, step_index)
    is invoked on pause. Returns the CheckpointRun outcome
    plus the verification report.
    """
    verification = (verify_steps_against_live_tools(steps)
                    if verify else {"ok": True, "detail": "skipped"})
    if not verification["ok"]:
        return {"state": "refused", "verification": verification,
                "detail": verification["detail"]}
    base_executor = executor or funnel_step_executor
    if reflect_fn is not None:
        def _reflective(step, _base=base_executor):
            report = execute_step_with_reflection(
                step, executor=_base, reflect_fn=reflect_fn,
                task_type=task_type)
            outcome = report["outcome"]
            outcome = dict(outcome)
            outcome["reflection_report"] = {
                "retries_used": report["retries_used"],
                "reflected": report["reflected"],
                "tainted": report["tainted"]}
            return outcome
        step_executor = _reflective
    else:
        step_executor = base_executor
    store = checkpoint.CheckpointStore(
        store_dir or checkpoint_store_dir())
    run = checkpoint.CheckpointRun(
        run_id, list(steps), step_executor, store, epoch=epoch)
    outcome = run.run()
    if outcome.get("state") == "paused" and gate_callback is not None:
        try:
            gate_callback(run_id, outcome.get("step_index"))
        except Exception:  # noqa: BLE001 — callback never breaks the run
            pass
    outcome["verification"] = verification
    return outcome




def resume_checkpointed_run(run_id: str, decision: str, epoch: str, *,
                            store_dir=None, steps: list[dict],
                            executor=None,
                            unknown_decision: Optional[str] = None) -> dict:
    """Resume a paused or unknown checkpointed run.

    Human decisions only: "approve"/"deny" for a gated step;
    for an unknown (crashed-mid-tool) step, pass decision=""
    and unknown_decision="rerun"/"skip"/"abort". Epoch-void
    semantics, implemented honestly: the run's epoch is read
    from its STORED records (not from the caller's argument),
    and a decision arriving under a different epoch is refused
    by the module as void. Unknown steps are NEVER auto-retried
    or auto-skipped here — the caller supplies the decision."""
    store = checkpoint.CheckpointStore(
        store_dir or checkpoint_store_dir())
    stored, _report = store.load(run_id)
    run_epoch = stored[0].epoch if stored else epoch
    run = checkpoint.CheckpointRun(
        run_id, list(steps), executor or funnel_step_executor,
        store, epoch=run_epoch)
    return run.resume(decision, epoch,
                      unknown_decision=unknown_decision)


