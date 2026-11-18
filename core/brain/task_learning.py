"""B17 — learning across tasks: record completed goals into the playbook with proof.

Loophole family: "every task starts from zero" — a completed
task's outcome was never recorded as a lesson, so the next task
repeated the same mistake, the same retry, the same wrong first
pick. The playbook existed and the read side existed (run_goal
looks a goal up before picking; the typed path reads proven
steps through default_plan_steps) — but NOTHING in production
ever called .add() or set_proof() (only tests did), so reuse
could only ever hit hand-added entries.

ONE design: on task completion (and on task failure), the
completion path writes the outcome into the playbook — what was
tried, what worked, what failed — with the B10 goal verdict +
the receipt as its proof. The write is ONE place
(:func:`record_task_lesson`), called from the two completion
paths (TurnCoordinator.run_task after the receipt is built,
run_goal's _end on every exit); the read is one place (the
pick's first pick — run_goal's existing playbook lookup, which
runs the proven steps first, then the picker is free).

Decision table (record_task_lesson — never raises; a lesson
failure never breaks a task):

* outcome "completed" + goal verdict "met" → a PROVEN lesson:
  add(name, steps, proof_job_id=<receipt/task id>). The proof
  is the ledger record with that id (receipt + goal verdict).
* outcome "completed" + verdict "unverifiable" / missing →
  write NOTHING. An unverified "success" must never become a
  lesson — the B10 goal verdict is the proof, and without it
  there is no lesson.
* outcome "failed" → a FAILURE lesson: an unproven entry
  naming what failed (add(..., failure=reason)). Recorded,
  never silently dropped, never re-run (reuse refuses
  unproven entries). EXCEPTION: when a PROVEN lesson already
  stands for this goal, the failure was not in its steps (a
  lesson-step failure strikes it first — see strike_lesson),
  so the proof survives and the failure is recorded as a note
  on the entry (note_failure), never by overwriting it.
* outcome "paused" / "cancelled" / anything else → write
  nothing. Only terminal outcomes teach.
* no name, or no steps tried → write nothing (a lesson with
  no steps is not a lesson; the failure is still in the
  receipt/ledger).

A lesson that fails on reuse is STRUCK (:func:`strike_lesson`,
store.strike): demoted to unproven, proof cleared, the struck
(name, proof) pair persisted — never trusted again on the SAME
evidence. A strike fires only on a DEFINITIVE step failure
(run_goal's shared strike-out path, for a step that came from
the playbook queue): a gate refusal never strikes (the safety
stack doing its job is not the lesson being wrong), and a
timeout/unknown outcome never strikes (the step may have
landed — B12's rule). A lesson reused through a typed plan
(default_plan_steps → coordinator) carries no playbook
provenance into run_task, so a failure there does not strike
— known limitation, recorded here, not hidden: the strike
covers the run_goal reuse path, which is the goal loop the
audit is about.

Rejected alternatives (do not re-open):

* writing a lesson per step (noise — a step's outcome without
  the task's outcome is not a lesson);
* letting the picker read the playbook on every pick (a stale
  lesson would steer a task that has already moved on —
  first-pick steering only, through the existing lookup);
* trusting a lesson without its proof (an unverified success
  becoming a lesson is the exact failure — the B10 goal
  verdict is the proof);
* a second learning store (the playbook is the one home —
  revived, not rebuilt).

Reflexion (core/brain/reflection.py) is RETIRED by this batch,
not wired: B12's retry ladder already provides exactly one
bounded retry per step (the same guarantee reflect_and_retry
provides), so wiring a second retry would give two retries per
step and contradict B12; and no brain callable exists at
either completion path to produce a reflection (inventing a
cloud call on every failure would be new capability, not
wiring). See reflection.RETIRED_REASON. Self-Discover
structures stay pinned and unchanged (re-pin deliberately).
"""

from __future__ import annotations


def _lesson_steps(steps) -> list:
    """Tried steps as playbook step dicts (tool/args/description).

    A step without a tool, or a non-dict step, is not a step —
    it is dropped, never guessed into one. Args are copied
    (the caller's dicts are never mutated); description is the
    step's label, or the tool name when there is none.

    M-24 (click-anywhere Batch 1): a click_anywhere step is
    never lessoned, and lesson-unsafe coordinate/selector args are
    stripped from every other step's args — a replayed click from
    a stale lesson is the failure the opt-out exists to prevent.
    The strip is scoped (neighbour r3, 2026-11-14): pointer tools'
    coordinate args always, foreign (undeclared) coordinate args
    on registered tools, verbatim for unregistered tools — so a
    lesson still records what actually ran.
    """
    try:
        from ..click_anywhere_tool import filter_lesson_steps as _filter
        steps = _filter(steps)
    except Exception:  # noqa: BLE001 — filter unreadable: fall
        # through to the plain copy below (never breaks a task).
        pass
    out = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool", "") or "").strip()
        if not tool:
            continue
        args = step.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        description = str(step.get("description")
                          or step.get("label") or tool)
        out.append({"tool": tool, "args": dict(args),
                    "description": description})
    return out


def record_task_lesson(playbook, *, name, steps, outcome, goal_verdict,
                       proof_job_id, failure_reason="") -> dict:
    """Write ONE lesson for a finished task. Never raises.

    ``playbook`` is a PlaybookStore (or None — no playbook, no
    write); ``name`` is the goal text / task title (the lookup
    key); ``steps`` are the tried steps (tool/args/description
    dicts); ``outcome`` is the terminal state ("completed" /
    "failed" — anything else writes nothing); ``goal_verdict``
    is the B10 verdict dict (or None); ``proof_job_id`` names
    the ledger record that proves the lesson (f"task:{id}" /
    f"goal:{id}"); ``failure_reason`` names what failed.

    Returns {"written": bool, "proven": bool, "reason": str,
    ...} — the reason always says why a lesson was or was not
    written, so a skipped write is visible, never silent.
    """
    try:
        if playbook is None:
            return {"written": False, "reason": "no playbook supplied"}
        name = str(name or "").strip()
        if not name:
            return {"written": False,
                    "reason": "no task name to record under"}
        tried = _lesson_steps(steps)
        if not tried:
            return {"written": False,
                    "reason": "no steps were tried; nothing to record"}
        verdict = None
        if isinstance(goal_verdict, dict):
            verdict = str(goal_verdict.get("verdict", "") or "")
        if outcome == "completed":
            if verdict != "met":
                return {"written": False,
                        "reason": (f"goal verdict is "
                                   f"{verdict or 'unrecorded'} — an "
                                   f"unverified success never becomes "
                                   f"a lesson")}
            result = playbook.add(name, tried,
                                  proof_job_id=str(proof_job_id or ""))
            if not result.get("added"):
                return {"written": False,
                        "reason": result.get("reason", "")}
            return {"written": True, "proven": bool(result.get("proven")),
                    "reason": result.get("reason", "")}
        if outcome == "failed":
            reason = str(failure_reason or "task failed").strip()
            # A proven lesson already stands for this goal: the
            # failure was not in its steps (a lesson-step
            # failure strikes it first — see strike_lesson), so
            # the proof survives and the failure is recorded as
            # a note on the entry, never by overwriting it.
            try:
                lookup = playbook.reuse(name)
            except Exception:  # noqa: BLE001 — a broken lookup is "no lesson"
                lookup = None
            if isinstance(lookup, dict) and lookup.get("ok"):
                noted = False
                try:
                    noted = bool(playbook.note_failure(
                        name,
                        f"goal failed after these steps ran: {reason}"))
                except Exception:  # noqa: BLE001 — a note failure never breaks the lesson
                    noted = False
                return {"written": noted, "proven": True,
                        "failure": reason,
                        "reason": ("failure noted on the proven lesson; "
                                   "the proof stands" if noted else
                                   "proven lesson stands; failure note "
                                   "could not be recorded")}
            result = playbook.add(name, tried, failure=reason)
            if not result.get("added"):
                return {"written": False,
                        "reason": result.get("reason", "")}
            return {"written": True, "proven": False, "failure": reason,
                    "reason": result.get("reason", "")}
        return {"written": False,
                "reason": (f"outcome {outcome!r} is not terminal; only "
                           f"completed/failed tasks write lessons")}
    except Exception as exc:  # noqa: BLE001 — a lesson failure never breaks a task
        return {"written": False, "reason": f"lesson write failed: {exc}"}


def strike_lesson(playbook, *, name, reason="") -> dict:
    """Demote a lesson that failed on reuse. Never raises.

    Delegates to the store's strike(): the entry is demoted to
    unproven, its proof cleared, the struck (name, proof) pair
    persisted — never trusted again on the same evidence.
    Returns the store's strike result, or {"struck": False,
    "reason": ...} when there is no playbook to strike in.
    """
    try:
        if playbook is None:
            return {"struck": False, "reason": "no playbook supplied"}
        if not hasattr(playbook, "strike"):
            return {"struck": False,
                    "reason": "playbook has no strike"}
        return dict(playbook.strike(str(name or ""), str(reason or "")))
    except Exception as exc:  # noqa: BLE001 — a strike failure never breaks a task
        return {"struck": False, "reason": f"strike failed: {exc}"}
