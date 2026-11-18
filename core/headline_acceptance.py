"""Wave 1 (v6 build flow) — R4-H headline acceptance script.

Scope (v6 §12 R4-H): a written five-task script for "Hands-free
computing" with a pass/fail check per task, run on the bench
first and filmed ONLY after it passes. Exit (v6): the script
passes on HIS machine with hands visibly off the keyboard, with
zero wrong-window actions and zero false-done claims. Both the
filming and the hands-off proof are field work, owed — this
module never claims them.

Pass rule (fail-closed, the false-done guard): a task passes
ONLY when ALL three hold —
  1. the loop report contains "Goal complete:",
  2. its bench check passes (a deterministic probe, not the
     model's claim — e.g. the arithmetic result computed here
     appears in the report), and
  3. no gated/refused terminal ("Goal halted:", "Goal stopped:")
     and no wrong-window marker appears in the report.
A task whose bench check is absent is recorded as
"not scored — check owed", NEVER as a pass. A task whose report
says complete but whose check fails is a FALSE-DONE and is
counted as such in the summary — that is the R4-C headline
number at this level, not a hidden failure.

Tasks (bench-safe, read/compute only; no send, delete, purchase,
or file change; no window driving is required to score them in
the lab — the bench check is on the report, and window-driving
tasks are field tasks, owed):
  T1 current time — goal asks for the time; check: report
     matches a time pattern (HH:MM with am/pm or 24-hour).
  T2 quick math — goal asks 17 + 25; check: "42" in the report.
  T3 days between — goal asks days from 2026-01-01 to
     2026-10-26; check: the value computed here (298) in report.
  T4 unit conversion — goal asks 5 kilometres in miles;
     check: "3.11" in the report (5 * 0.621371 = 3.106855).
  T5 plan list (read-only) — goal asks to list saved plans;
     check: report contains "plan" (case-insensitive) or a
     "No plan" / empty-list wording. This check is deliberately
     weak and is labelled as such: it proves the loop ran and
     reported, not that any plan exists.

The runner takes an injected brain (async ``run_goal``) — the
bridge's text loop in the field, a stub in the lab. It makes no
model call itself and executes no tool. Running this module
directly with no brain prints the tasks and "not run" — it
never fakes a pass.

Stdlib only. No new dependency, no weights, no engine.
"""

from __future__ import annotations

import datetime
import inspect
import re

__all__ = ["TASKS", "score_task", "run_acceptance"]

_DAYS = (datetime.date(2026, 10, 26) - datetime.date(2026, 1, 1)).days


def _has_time(report: str) -> bool:
    """Time pattern in a report (HH:MM, with or without am/pm)."""
    return re.search(r"\b\d{1,2}:\d{2}\s*(?:[AP]M|am|pm)?\b",
                     report or "") is not None


def _contains(expected: str):
    """A bench check: the expected string appears in the report."""
    def _check(report: str) -> bool:
        return expected in (report or "")
    return _check


def _plan_list_check(report: str) -> bool:
    """Weak, labelled check: the loop reported about plans at all."""
    low = (report or "").lower()
    return "plan" in low


TASKS = [
    {"id": "T1", "goal": "What time is it right now?",
     "check": _has_time,
     "check_note": "Time pattern in report (weak: proves a time was "
                   "reported, not that it was the right time)."},
    {"id": "T2", "goal": "What is 17 plus 25?",
     "check": _contains("42"),
     "check_note": "Deterministic: 17 + 25 = 42 must appear."},
    {"id": "T3",
     "goal": "How many days are there from 1 January 2026 to 26 October 2026?",
     "check": _contains(str(_DAYS)),
     "check_note": f"Deterministic: computed here as {_DAYS} days."},
    {"id": "T4", "goal": "Convert 5 kilometres to miles.",
     "check": _contains("3.11"),
     "check_note": "Deterministic: 5 km = 3.106855 miles, rounded 3.11."},
    {"id": "T5", "goal": "List my saved plans.",
     "check": _plan_list_check,
     "check_note": "Weak, labelled: proves the loop reported about "
                   "plans, not that any plan exists."},
]


def score_task(task, report: str) -> dict:
    """Score one task's report under the fail-closed pass rule."""
    text = str(report or "")
    complete = "Goal complete:" in text
    halted = ("Goal halted:" in text or "Goal stopped:" in text)
    wrong_window = "wrong-window" in text.lower()
    check = task.get("check")
    if check is None:
        return {"id": task.get("id"), "result": "not scored",
                "reason": "check owed — no bench check supplied",
                "false_done": False}
    try:
        check_passed = bool(check(text))
    except Exception:  # noqa: BLE001 — a broken check is a failed check
        check_passed = False
    if complete and check_passed and not halted and not wrong_window:
        return {"id": task.get("id"), "result": "pass",
                "reason": "complete + bench check passed",
                "false_done": False}
    false_done = bool(complete and not check_passed)
    return {"id": task.get("id"), "result": "fail",
            "reason": ("false-done (report claims complete, bench "
                       "check failed)" if false_done else
                       "complete/check/halt/window rule not met"),
            "false_done": false_done,
            "complete": complete, "check_passed": check_passed,
            "halted": halted, "wrong_window": wrong_window}


async def run_acceptance(brain, *, tasks=None):
    """Run the five tasks through an injected brain and score them.

    Returns a summary with per-task results, false-done count,
    and wrong-window count (the R4-C headline numbers at this
    level). No filming claim, no hands-off claim, no field claim
    is made here — those are owed on his machine.
    """
    run_goal = getattr(brain, "run_goal", None)
    if run_goal is None and callable(brain):
        run_goal = brain
    if run_goal is None or not callable(run_goal):
        return {"script": "headline_acceptance", "result": "not run",
                "reason": "no brain with run_goal supplied",
                "tasks": [], "passes": 0, "fails": 0,
                "false_done_count": 0, "wrong_window_count": 0,
                "field_note": ("Filming and hands-off proof are "
                               "field work, owed on his machine.")}
    results = []
    for task in (tasks if tasks is not None else TASKS):
        try:
            report = run_goal(task["goal"])
            if inspect.isawaitable(report):
                report = await report
        except Exception as exc:  # noqa: BLE001 — a failed task is data
            report = f"Goal halted: brain failed ({exc})"
        scored = score_task(task, str(report or ""))
        scored["report_excerpt"] = str(report or "")[:200]
        results.append(scored)
    passes = sum(1 for r in results if r["result"] == "pass")
    return {
        "script": "headline_acceptance",
        "result": "pass" if passes == len(results) and results else "fail",
        "tasks": results,
        "passes": passes,
        "fails": sum(1 for r in results if r["result"] == "fail"),
        "not_scored": sum(1 for r in results
                          if r["result"] == "not scored"),
        "false_done_count": sum(1 for r in results
                                if r.get("false_done")),
        "wrong_window_count": sum(1 for r in results
                                  if r.get("wrong_window")),
        "field_note": ("A lab pass is NOT a field pass. Filming "
                       "and hands-off proof wait for his machine, "
                       "zero wrong-window actions, zero false-done."),
    }


if __name__ == "__main__":  # pragma: no cover - manual entry point
    print("R4-H headline acceptance script (five tasks):")
    for task in TASKS:
        print(f"  {task['id']}: {task['goal']} — {task['check_note']}")
    print("Not run: no brain supplied. Supply a brain to "
          "run_acceptance() — a pass is never faked.")
