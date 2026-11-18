"""B22 — sub-goal decomposition + re-planning: prompt builders and the
strict plan parser for the coordinator's decompose-and-re-plan capability.

Loophole family: "a goal the loop cannot break down" — the
coordinator runs only steps a plan already declares, and when
the B10 goal check returns not-met it fails honestly but nothing
re-plans. A complex goal arrives as free text and the
coordinator has nothing to do with it unless a human already
turned it into a step list.

ONE design: decompose-and-re-plan as a coordinator capability,
through the SAME text brain the picker already uses — the
companion-session pair (the coordinator's
``_second_opinion_brains``, never a new brain). The brain is
asked for a plan as JSON — ``{"subgoals": [{"name": str,
"steps": [step mappings]}]}`` — and the plan is validated
twice: structurally here (this module's strict parser — the
shape is the contract, never guessed), and then through the
existing ``StepDef.from_mapping`` + plan-level validators in
the coordinator (a step the tree cannot build never runs).
A re-plan is a bounded attempt: at most ``_REPLAN_LIMIT`` (the
strike limit, 3) per task — one shared budget with the retry
ladder's limit value, not a second number. Each re-plan is
recorded (what was tried, what failed, what the new plan is).

Rejected alternatives (do not re-open):

* re-picking one action at a time forever (today's run_goal —
  the gap itself, unbounded and unplanned);
* failing honestly on not-met without re-planning (today's
  B10 — honest but helpless);
* a second planner (one coordinator, one brain, one step
  list — the decomposition rides the existing step
  machinery);
* asking the user to write the steps (the user asked for
  autonomy — the loop must do the breaking down, not him);
* unlimited re-plans (an infinite loop wearing a plan);
* a second brain for planning (the companion pair IS the
  text brain; a new brain is new capability, not wiring);
* asking both brains and cross-checking the plan
  (cross_check is for YES/NO questions — a plan is not a
  yes/no answer, so the plan is validated structurally
  instead of voted on).

This module is stdlib-only (no product imports, like
picker_history.py): the coordinator supplies the candidate
tools and the brain call; this module only builds prompts
and parses replies. A brain reply is data, never code: the
parser extracts one JSON object and validates its shape —
anything else is a failed decomposition, never a guessed
plan.
"""

from __future__ import annotations

import json

# B22: the most steps one plan (initial or re-planned) may
# carry. Twenty-five is the run_goal step-budget shape
# (planner_rungoal goal_max_steps, default 25) — one plan is
# one goal's worth of steps, not an unbounded list; a bigger
# goal re-plans (up to _REPLAN_LIMIT further plans) instead
# of listing two hundred micro-steps up front. A plan over
# this cap is malformed, never silently truncated (dropping
# steps would lose work the brain thought was needed).
MAX_DECOMPOSED_STEPS = 25

# The step-mapping keys the brain may use — the coordinator's
# own step shape (StepDef.from_mapping), nothing else. A key
# outside this set is rejected by from_mapping's own rules
# (an unknown key is not a step field — the mapping is
# passed through whole, and from_mapping reads only what it
# knows; extra keys are ignored there, so the prompt names
# the shape to keep the brain from inventing fields).
_PLAN_SHAPE = (
    '{"subgoals": [{"name": "<plain sub-goal name>", '
    '"steps": [{"tool": "<registered tool name>", '
    '"args": {...}, "label": "<short label>", '
    '"expect": "<what should be visible afterwards>"}]}]}'
)

_COMMON_RULES = (
    "Rules:\n"
    "- Reply with ONLY JSON in exactly this shape, no prose, "
    "no markdown:\n"
    f"{_PLAN_SHAPE}\n"
    "- Every step's tool must be a registered tool name "
    "exactly — use a candidate tool below or another "
    "registered tool name; do not invent tools.\n"
    "- args must be plain JSON values (strings, numbers, "
    "booleans, lists, objects) — never a guess at a value "
    "you were not given.\n"
    "- A step that needs information only the user has must "
    "carry an \"ask\" {\"question\": ..., \"options\": [...], "
    "\"answer_key\": ...} instead of guessing the answer.\n"
    "- Consequential steps (send, delete, purchase, money, "
    "irreversible) run one at a time and are never parallel.\n"
    "- Steps that can run together may share a \"parallel\" "
    "label; at most 4 steps per label.\n"
    "- Keep each sub-goal small — a few steps, not a dozen.\n"
)


def _candidates_line(candidate_tools) -> str:
    """The ranked candidate tools as one prompt line, or "(none ranked)"."""
    names = [str(t or "").strip() for t in (candidate_tools or [])
             if str(t or "").strip()]
    if not names:
        return "Candidate tools: (none ranked)"
    return "Candidate tools: " + ", ".join(names)


def build_decompose_prompt(goal_text: str, candidate_tools=None) -> str:
    """The initial-decomposition prompt for a free-text goal.

    Asks the brain to break the goal into sub-goals, each
    into a small step list, in the plan shape above. The
    goal text is quoted verbatim — never paraphrased by
    the caller (a paraphrase could change the goal).
    """
    goal = str(goal_text or "").strip()
    return (
        "Break this goal into sub-goals, and each sub-goal "
        "into a small list of tool steps.\n\n"
        f"{_COMMON_RULES}\n"
        f"Goal: {goal}\n\n"
        f"{_candidates_line(candidate_tools)}\n"
    )


def build_replan_prompt(goal_text: str, tried, failed_reason: str,
                        candidate_tools=None) -> str:
    """The re-plan prompt after a B10 not-met verdict.

    ``tried`` is a list of {"label", "tool", "status"}
    dicts for the steps already run — the brain sees what
    was already tried (the B18 rule, applied to planning:
    the planner must not re-plan the same failed approach),
    and which steps already succeeded (those are done and
    must NOT be re-planned). ``failed_reason`` is the goal
    check's reason — why the goal is not met. The prompt
    plans ONLY the remaining work.
    """
    goal = str(goal_text or "").strip()
    reason = str(failed_reason or "").strip()
    tried_lines = []
    done_lines = []
    for entry in (tried or []):
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "") or entry.get("tool", "") or "")
        tool = str(entry.get("tool", "") or "")
        status = str(entry.get("status", "") or "")
        if not label and not tool:
            continue
        tried_lines.append(f"- {label or tool} [{tool}] — {status or '?'}")
        if status == "success":
            done_lines.append(label or tool)
    tried_text = "\n".join(tried_lines) if tried_lines \
        else "(no steps ran yet)"
    done_text = ", ".join(done_lines) if done_lines else "(none yet)"
    return (
        "The goal below was NOT met after the first plan ran. "
        "Plan ONLY the remaining work — do not re-plan steps "
        "that already succeeded, and do not repeat an approach "
        "that already failed.\n\n"
        f"{_COMMON_RULES}\n"
        f"Goal: {goal}\n\n"
        f"What was tried:\n{tried_text}\n\n"
        f"What already succeeded (do not repeat): {done_text}\n\n"
        f"Why the goal is not met: {reason}\n\n"
        f"{_candidates_line(candidate_tools)}\n"
    )


def parse_plan_text(text):
    """Parse a brain reply into a plan, or (None, error).

    Strict about the SHAPE, tolerant of surrounding prose
    (the picker's own parsing discipline — the JSON object
    is extracted from the first "{" to the last "}"; a reply
    with no JSON object at all is a failed decomposition).
    Returns ({"subgoals": [{"name": str, "steps": [mappings]}]},
    "") on success. Every failure names itself — a malformed
    plan is a failed decomposition, never a guessed one.
    """
    raw = str(text or "")
    if not raw.strip():
        return None, "the brain returned no plan text"
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
    except ValueError:
        return None, "the brain's reply contains no JSON object"
    try:
        data = json.loads(raw[start:end])
    except ValueError as exc:
        return None, f"the brain's plan is not valid JSON ({exc})"
    if not isinstance(data, dict):
        return None, "the brain's plan is not a JSON object"
    subgoals = data.get("subgoals")
    if not isinstance(subgoals, list) or not subgoals:
        return None, ("the brain's plan has no 'subgoals' list "
                      "(expected {\"subgoals\": [...]})")
    parsed = []
    total_steps = 0
    # Jump targets found while reading (validated after the
    # loop — the range is against the WHOLE plan, and the
    # total is not known until every sub-goal is read).
    _jump_targets = []
    for pos, subgoal in enumerate(subgoals):
        if not isinstance(subgoal, dict):
            return None, (f"sub-goal {pos + 1} is not an object")
        name = subgoal.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, (f"sub-goal {pos + 1} has no usable 'name'")
        steps = subgoal.get("steps")
        if not isinstance(steps, list) or not steps:
            return None, (f"sub-goal {name.strip()!r} has no 'steps' list")
        clean_steps = []
        for spos, step in enumerate(steps):
            if not isinstance(step, dict):
                return None, (f"sub-goal {name.strip()!r} step "
                              f"{spos + 1} is not an object")
            tool = step.get("tool")
            if not isinstance(tool, str) or not tool.strip():
                return None, (f"sub-goal {name.strip()!r} step "
                              f"{spos + 1} has no usable 'tool'")
            for _key in ("goto",):
                _target = step.get(_key)
                if _target is not None:
                    _jump_targets.append(
                        (name.strip(), spos + 1, _target))
            _on_fail = step.get("on_fail")
            if isinstance(_on_fail, dict):
                _target = _on_fail.get("goto")
                if _target is not None:
                    _jump_targets.append(
                        (name.strip(), spos + 1, _target))
            clean_steps.append(dict(step))
        total_steps += len(clean_steps)
        parsed.append({"name": name.strip(), "steps": clean_steps})
    # A jump outside the plan is unusable — the range is
    # checked against the WHOLE plan (steps are flattened
    # across sub-goals before validation, so a goto addresses
    # a plan step, not a sub-goal step). Both spellings were
    # collected above: the step machinery reads goto from
    # on_fail {"goto": N} (StepDef.from_mapping); a top-level
    # "goto" is accepted by the same mapping and checked the
    # same way. A plan that jumps outside itself is a failed
    # decomposition, never a step that runs and fails at
    # dispatch.
    for _sg, _sp, _target in _jump_targets:
        if isinstance(_target, bool) or not isinstance(_target, int) \
                or not 1 <= _target <= total_steps:
            return None, (
                f"sub-goal {_sg!r} step {_sp} jumps to step "
                f"{_target!r} — the plan has {total_steps} step(s)")
    if total_steps > MAX_DECOMPOSED_STEPS:
        return None, (f"the brain's plan has {total_steps} steps — "
                      f"at most {MAX_DECOMPOSED_STEPS} per plan "
                      f"(a bigger goal re-plans, it does not list "
                      f"them all up front)")
    return {"subgoals": parsed}, ""


def plan_subgoal_names(plan) -> list:
    """The sub-goal names of a parsed plan, in order."""
    if not isinstance(plan, dict):
        return []
    names = []
    for subgoal in plan.get("subgoals") or []:
        if isinstance(subgoal, dict):
            name = str(subgoal.get("name", "") or "").strip()
            if name:
                names.append(name)
    return names


def plan_step_mappings(plan) -> list:
    """The plan's step mappings, flattened in sub-goal order."""
    if not isinstance(plan, dict):
        return []
    mappings = []
    for subgoal in plan.get("subgoals") or []:
        if not isinstance(subgoal, dict):
            continue
        for step in subgoal.get("steps") or []:
            if isinstance(step, dict):
                mappings.append(dict(step))
    return mappings
