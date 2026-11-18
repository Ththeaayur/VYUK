"""Wave 1 (v6 build flow, D-22) — K1 measurement comparison rig.

K1's value is exactly that it is decided by measurement: this rig
produces comparison DATA, not a K1 verdict. There is deliberately
NO verdict field and NO winner field anywhere in its output. A
caller that wants a verdict must earn it from this data plus field
evidence; this module never supplies one.

Inputs are injected brains: any object (or callable) with an async
``run_goal(goal_text) -> str`` — the Groq/local client entries, or
test stubs with that shape. The rig itself makes no model call, no
API call, and no tool execution; it only drives the injected brains
and records what their reports say.

Outcome classification comes from the planner's own terminal lines:
"Goal complete:" -> complete; "Goal stopped:" -> stopped;
"Goal halted:" -> halted; anything else -> unknown. A report that
does not say complete is never counted as complete, however
confident its wording sounds — that is the false-done guard at
this level (the R4-C false-done rate itself is a bench/field
number, not something this rig invents).

Fail-closed goal filter: goals naming a consequential verb
(send / delete / purchase / buy / pay) are REFUSED by the rig,
not run. K1 trials are non-blocking goals; a trial that could move
money, mail, or files is not a trial goal. The refusal is recorded
in the output, not silently skipped.

Each goal is run ``repeat`` times per brain (default 1; the R3-B
reproducibility pattern is repeat >= 3 with spread reported, and
callers that want spread should pass repeat=3). Durations are
recorded per repeat; the summary reports median and spread
(max-min) per brain per goal, never a threshold pass/fail.
Model ids, run date, and declarations/prompt hash are recorded
only when the caller supplies them — the rig invents none of them.

Stdlib only. No new dependency, no weights, no engine, no config
change, no schema change, no telemetry, no collection.
"""

from __future__ import annotations

import inspect
import statistics
import time

__all__ = ["run_comparison", "classify_outcome", "REFUSED_VERBS"]

REFUSED_VERBS = ("send", "delete", "purchase", "buy", "pay")


def classify_outcome(report: str) -> str:
    """Classify a run_goal report by its own terminal line."""
    text = str(report or "")
    if "Goal complete:" in text:
        return "complete"
    if "Goal stopped:" in text:
        return "stopped"
    if "Goal halted:" in text:
        return "halted"
    return "unknown"


def _is_refused_goal(goal: str) -> str | None:
    """The consequential verb that refuses a goal, or None."""
    low = f" {str(goal or '').lower()} "
    for verb in REFUSED_VERBS:
        if f" {verb} " in low or f" {verb}s " in low:
            return verb
    return None


async def _run_once(brain, goal: str):
    """Run one brain once; returns (report, seconds, error)."""
    run_goal = getattr(brain, "run_goal", None)
    if run_goal is None and callable(brain):
        run_goal = brain
    if run_goal is None or not callable(run_goal):
        return "", 0.0, "brain has no run_goal"
    started = time.monotonic()
    try:
        result = run_goal(goal)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001 — a failed brain is data
        return "", time.monotonic() - started, str(exc)
    return str(result or ""), time.monotonic() - started, None


async def run_comparison(goals, *, current_brain, trial_brain, repeat=1,
                         current_id=None, trial_id=None,
                         declarations_hash=None, prompt_hash=None):
    """Run every goal through both brains and return comparison DATA.

    ``goals`` is a list of goal strings. ``repeat`` defaults to 1;
    pass 3+ for spread. The returned dict has per-goal, per-brain
    runs (outcome, seconds, report excerpt) plus a summary of
    outcome counts and duration median/spread — and no verdict,
    no winner, no pass/fail. Refused goals appear with
    outcome "refused" and the refusing verb, and are never run.
    """
    if repeat < 1:
        repeat = 1
    data = {
        "rig": "k1_measurement_rig",
        "verdict": None,
        "winner": None,
        "note": ("Comparison DATA only (D-22). No K1 verdict is "
                 "claimed by this rig; a verdict needs this data "
                 "plus field evidence."),
        "current_id": current_id,
        "trial_id": trial_id,
        "declarations_hash": declarations_hash,
        "prompt_hash": prompt_hash,
        "repeat": repeat,
        "goals": [],
        "summary": {},
    }
    for goal in goals or []:
        goal_text = str(goal or "").strip()
        entry = {"goal": goal_text, "runs": {}}
        verb = _is_refused_goal(goal_text)
        for label, brain in (("current", current_brain),
                             ("trial", trial_brain)):
            if verb is not None:
                entry["runs"][label] = {
                    "outcome": "refused",
                    "refused_verb": verb,
                    "durations": [],
                    "reports": [],
                }
                continue
            runs = []
            for _ in range(repeat):
                report, seconds, error = await _run_once(brain, goal_text)
                runs.append({
                    "outcome": ("error" if error else
                               classify_outcome(report)),
                    "error": error,
                    "seconds": round(seconds, 3),
                    "report_excerpt": report[:200],
                })
            entry["runs"][label] = runs
        data["goals"].append(entry)
    for label in ("current", "trial"):
        counts: dict[str, int] = {}
        durations: list[float] = []
        for entry in data["goals"]:
            runs = entry["runs"][label]
            if isinstance(runs, dict):
                counts["refused"] = counts.get("refused", 0) + 1
                continue
            for run in runs:
                counts[run["outcome"]] = counts.get(run["outcome"], 0) + 1
                durations.append(run["seconds"])
        data["summary"][label] = {
            "outcome_counts": counts,
            "duration_median": (round(statistics.median(durations), 3)
                                if durations else None),
            "duration_spread": (round(max(durations) - min(durations), 3)
                                if durations else None),
        }
    return data
