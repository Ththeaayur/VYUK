"""Wave 4 PLAN breadth — decomposition, constraints, uncertainty, confidence.

Stdlib only, no model calls, no invented calibration. Step
confidences here are caller-supplied numbers; this module combines
them by a stated rule and localises uncertainty, it never claims a
confidence it was not given.

Rules, enforced in code:
  * Decomposition turns a goal into named steps, each with its own
    constraints. An unnamed step is invalid, never guessed.
  * Constraint solving is checking, not searching: a step that
    violates a stated constraint is flagged with the constraint
    name. No constraint is silently dropped.
  * Uncertainty localisation names the step(s) whose confidence is
    lowest, so the caller knows exactly where doubt lives.
  * Calibrated confidence for a plan is the minimum step confidence
    (the weakest step bounds the plan), stated in the result. A
    confidence outside [0, 1] is invalid, never clamped silently.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanStep:
    name: str
    confidence: float
    constraints: tuple = ()

    def __post_init__(self):
        if not self.name:
            raise ValueError("plan step must have a name")
        if not isinstance(self.confidence, (int, float)) \
                or isinstance(self.confidence, bool) \
                or self.confidence < 0 or self.confidence > 1:
            raise ValueError(
                f"step '{self.name}' confidence {self.confidence} "
                "outside [0, 1]")


def decompose(goal: str, step_names: list) -> dict:
    """Turn a goal into named steps with no confidences assigned.

    Confidences must be supplied by the caller per step (see
    calibrate); this function never invents them.
    """
    if not goal:
        return {"ok": False, "reason": "goal is empty"}
    if not step_names or any(not n for n in step_names):
        return {"ok": False, "reason": "every step must have a name"}
    return {"ok": True, "goal": goal,
            "steps": [{"name": n, "confidence": None} for n in step_names]}


def check_constraints(steps: list, constraints: dict) -> dict:
    """Check steps against stated constraints.

    constraints maps a constraint name to a predicate name the
    caller already evaluated: here each step carries the constraint
    names it claims to satisfy, and a constraint is violated when
    a step lists it in its "violates" field. This is a record of
    caller-checked constraints, not a solver that invents facts.
    A step naming an unknown constraint is flagged.
    """
    violations = []
    for step in steps:
        if not isinstance(step, dict) or not step.get("name"):
            violations.append({"step": None,
                               "reason": "step has no name"})
            continue
        for unknown in step.get("constraints", []):
            if unknown not in constraints:
                violations.append({"step": step["name"],
                                   "constraint": unknown,
                                   "reason": "unknown constraint"})
        for broken in step.get("violates", []):
            violations.append({"step": step["name"], "constraint": broken,
                               "reason": f"violates constraint '{broken}'"})
    return {"ok": not violations, "violations": violations}


def localise_uncertainty(steps: list) -> dict:
    """Name the least-confident step(s) — where doubt lives."""
    if not steps:
        return {"ok": False, "reason": "no steps to localise"}
    confidences = [s.confidence for s in steps]
    lowest = min(confidences)
    weakest = [s.name for s in steps if s.confidence == lowest]
    return {"ok": True, "lowest_confidence": lowest,
            "weakest_steps": weakest}


def calibrate(steps: list) -> dict:
    """Plan confidence = minimum step confidence (stated rule)."""
    if not steps:
        return {"ok": False, "reason": "no steps to calibrate"}
    result = localise_uncertainty(steps)
    result["plan_confidence"] = result["lowest_confidence"]
    result["rule"] = "plan confidence is the minimum step confidence"
    return result
