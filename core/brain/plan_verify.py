"""Wave 8 (v5.63) — plan verification: deterministic validators first,
then CoVe-style independent check questions (brief item 4, BN-6-2 + 6-1).

Order matters: the deterministic validators are FREE (milliseconds,
no brain call) and run FIRST; the CoVe check questions run after,
as an independent second opinion on the same plan. A sampled-plan
disagreement between the two is an ESCALATION SIGNAL to the gate —
never an auto-override in either direction.

Validators are a SECOND check beside name validation, never a
replacement: :func:`validate_plan` returns the name-validation
result and the validator results as SEPARATE fields, and a plan
must pass both. Collapsing them into one verdict would let a
validator failure hide behind a name pass (or vice versa).

No judge model earns a live slot: 7-8B judges are multi-second on
CPU, so no judge runs in the live path (``JUDGE_LIVE = False``,
enforced). Prometheus 2 stays DEV-ONLY scoring: :func:`dev_score`
refuses unless explicitly dev-enabled, and its card's OpenAI-terms
taint is recorded as OWED — written clarification is required
before ANY redistribution; Prometheus 2 is never bundled here.

No new dependencies, no telemetry, stdlib only. The coordinator
supplies the tool-name set, arg schemas, target-existence predicate,
and reversibility set — this module holds no product knowledge and
executes no tool.

Versioned strings introduced by this module (pre-pinned to 5.63):
``WAVE_VERSION = "5.63"`` (module marker only — NOT the product
version; ``core/version.py`` stays 5.62.0 until coordinator assembly).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

WAVE_VERSION = "5.63"

#: No judge model earns a live slot (7-8B judges: multi-second on CPU).
JUDGE_LIVE = False

#: Prometheus 2 redistribution status: OWED — its card's OpenAI-terms
#: taint needs written clarification before any redistribution.
#: Prometheus 2 is dev-only scoring here, never bundled, never shipped.
PROMETHEUS2_REDISTRIBUTION = "OWED: written clarification of the " \
    "OpenAI-terms taint required before any redistribution"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class PlanStep:
    tool: str
    args: dict = field(default_factory=dict)
    target: Optional[str] = None  # e.g. a path the step acts on


def validate_name(step: PlanStep, known_tools: set[str]) -> CheckResult:
    """Name validation: the tool word must exist in the registry set.

    This is the FIRST check, not the only check — see module docstring.
    """
    if step.tool in known_tools:
        return CheckResult("name_valid", True, f"tool {step.tool!r} known")
    return CheckResult("name_valid", False, f"tool {step.tool!r} unknown")


def validate_tool_exists(step: PlanStep, known_tools: set[str]) -> CheckResult:
    """Deterministic validator 1: tool exists (registry membership)."""
    return CheckResult(
        "tool_exists", step.tool in known_tools,
        "exists" if step.tool in known_tools else "no such tool")


def validate_args_types(step: PlanStep,
                        schemas: dict[str, dict[str, type]]) -> CheckResult:
    """Deterministic validator 2: args type-check against the schema.

    Unknown tools and unknown args fail closed (a plan that cannot
    be type-checked is not passed by default). ``schemas`` maps tool
    name -> {arg name: expected type}; missing schema = fail.
    """
    schema = schemas.get(step.tool)
    if schema is None:
        return CheckResult("args_typecheck", False,
                           f"no schema for {step.tool!r}")
    for name, value in step.args.items():
        expected = schema.get(name)
        if expected is None:
            return CheckResult("args_typecheck", False,
                               f"unknown arg {name!r} for {step.tool!r}")
        if not isinstance(value, expected):
            return CheckResult(
                "args_typecheck", False,
                f"arg {name!r} is {type(value).__name__}, "
                f"expected {expected.__name__}")
    return CheckResult("args_typecheck", True, "args type-check")


def validate_target_exists(
        step: PlanStep,
        exists: Callable[[str], bool]) -> CheckResult:
    """Deterministic validator 3: the step's target exists.

    Steps with no target pass vacuously (nothing to check); steps
    with a target must resolve through the injected predicate.
    """
    if step.target is None:
        return CheckResult("target_exists", True, "no target to check")
    ok = exists(step.target)
    return CheckResult("target_exists", ok,
                       "target exists" if ok else
                       f"target {step.target!r} does not exist")


def validate_reversible(step: PlanStep,
                        reversible_tools: set[str]) -> CheckResult:
    """Deterministic validator 4: the step is reversible.

    Irreversible steps are not failures — they are flagged so the
    gate can demand confirmation. ``passed`` here means "no
    reversibility concern"; a False result carries the flag, and
    :func:`validate_plan` surfaces it separately from failures.
    """
    if step.tool in reversible_tools:
        return CheckResult("reversible", True, "reversible")
    return CheckResult("reversible", False,
                       f"{step.tool!r} is not in the reversible set; "
                       "gate confirmation required")


def validate_plan(step: PlanStep, known_tools: set[str],
                  schemas: dict[str, dict[str, type]],
                  exists: Callable[[str], bool],
                  reversible_tools: set[str]) -> dict:
    """Full deterministic validation for one step.

    Returns name validation AND validator results as separate fields
    — never merged. ``all_valid`` requires the name check plus every
    validator except ``reversible`` (which is a flag, not a failure);
    ``needs_gate`` is True when the step is irreversible.
    """
    name = validate_name(step, known_tools)
    validators = [
        validate_tool_exists(step, known_tools),
        validate_args_types(step, schemas),
        validate_target_exists(step, exists),
        validate_reversible(step, reversible_tools),
    ]
    hard = [v for v in validators if v.name != "reversible"]
    return {
        "name": name,
        "validators": validators,
        "all_valid": name.passed and all(v.passed for v in hard),
        "needs_gate": not validators[-1].passed,
    }


# --- CoVe-style independent check questions ---------------------------
def cove_questions(plan: list[PlanStep]) -> list[str]:
    """Independent check questions about a plan, generated WITHOUT
    re-reading the planner's reasoning: one existence, one
    type-safety, and one reversibility question per step. The
    questions are templates, not model output — CoVe's independence
    comes from answering them from fresh evidence (the injected
    answer function), not from the plan's own claims.
    """
    questions = []
    for index, step in enumerate(plan):
        questions.append(
            f"Step {index}: does tool {step.tool!r} exist and is it "
            "the right tool for this step's stated purpose?")
        questions.append(
            f"Step {index}: are the arguments {sorted(step.args)} "
            "well-typed and complete for this tool?")
        if step.target is not None:
            questions.append(
                f"Step {index}: does target {step.target!r} exist, "
                "and is acting on it reversible or gate-confirmed?")
    return questions


def cove_check(plan: list[PlanStep],
               answer: Callable[[str], bool]) -> dict:
    """Answer every CoVe question via the injected ``answer`` evidence
    function. Returns per-question answers plus the overall verdict:
    ``agree`` iff every question answers True. A False anywhere is
    disagreement with the plan as written."""
    answers = [(q, bool(answer(q))) for q in cove_questions(plan)]
    return {"questions": answers,
            "agree": all(ok for _, ok in answers)}


def sampled_disagreement(plans: list[list[PlanStep]],
                         validate: Callable[[list[PlanStep]], bool],
                         cove_answer: Callable[[str], bool],
                         escalate_threshold: float = 0.0) -> dict:
    """Sampled-plan disagreement = escalation signal to the gate.

    For each sampled plan, compare the deterministic validator verdict
    (``validate``) with the CoVe verdict (``cove_check``). Any plan
    where they disagree counts. ``escalate`` is True when the
    disagreement rate exceeds ``escalate_threshold`` (default: ANY
    disagreement escalates — disagreement is a signal, not noise).
    The signal goes to the gate; it never auto-overrides a plan.
    """
    disagreements = 0
    details = []
    for plan in plans:
        det = validate(plan)
        cove = cove_check(plan, cove_answer)
        disagree = det != cove["agree"]
        if disagree:
            disagreements += 1
        details.append({"deterministic": det, "cove_agree": cove["agree"],
                        "disagree": disagree})
    rate = disagreements / len(plans) if plans else 0.0
    return {"sampled": len(plans), "disagreements": disagreements,
            "rate": rate, "escalate": rate > escalate_threshold,
            "details": details,
            "signal": "escalate to gate" if rate > escalate_threshold
                      else "no escalation"}


# --- Judge models: no live slot; Prometheus 2 dev-only -----------------
def dev_score(prompt: str, response: str, dev_enabled: bool = False) -> dict:
    """Dev-only scoring stub (Prometheus 2's seat, not its weights).

    Refuses unless ``dev_enabled`` is explicitly True — scoring never
    runs in the live path, and no judge model is bundled, downloaded,
    or invoked here. Prometheus 2 redistribution remains OWED (see
    ``PROMETHEUS2_REDISTRIBUTION``); this stub scores nothing and
    records only that the call was dev-gated.
    """
    if not dev_enabled:
        return {"scored": False,
                "detail": "dev-only scoring: enable explicitly in a "
                          "dev harness; no judge runs live",
                "redistribution": PROMETHEUS2_REDISTRIBUTION}
    if JUDGE_LIVE:
        raise AssertionError("judge live slot must stay closed")
    return {"scored": False,
            "detail": "no judge weights available in this build; "
                      "score recorded as unavailable, not fabricated",
            "redistribution": PROMETHEUS2_REDISTRIBUTION}
