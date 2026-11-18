"""Split of task_coordinator: constants + classification family."""

from __future__ import annotations

import re
from dataclasses import dataclass


TURN_KINDS = (
    "conversation",
    "single_action",
    "multi_step",
    "saved_plan",
    "routine",
)


TASK_STATES = (
    "pending",
    "running",
    "paused",
    "verifying",
    "completed",
    "failed",
    "cancelled",
)


# Retry-on-not-met strike limit (regression-batch follow-up):
# mirrors planner's _EXPECT_STRIKE_LIMIT (core/brain/planner.py):
# a step's expectation may come back "not-met" at most this
# many consecutive times before the task fails — i.e. the
# initial action plus up to (limit - 1) retries. "unverifiable"
# never strikes and never retries (unknown effect state).
_RETRY_STRIKE_LIMIT = 3


# Mission-Receipt status mapping for to_mission_receipt().
_MISSION_STATUS = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "stopped",
    "pending": "not-run",
    "running": "not-run",
    "paused": "not-run",
}


# Heuristic imperative lexicon for the single_action class. This is
# a CLASSIFIER heuristic only: single_action turns still flow down
# the existing path unchanged (no task, no behavior change), so a
# misclassification here costs nothing but a label. The list is
# deliberately small and verb-first; anything else is conversation.
_SINGLE_VERBS = frozenset({
    "open", "play", "set", "turn", "send", "create", "list", "show",
    "find", "search", "read", "write", "run", "start", "stop", "check",
    "get", "remind", "schedule", "add", "remove", "delete", "close",
    "pause", "resume", "cancel", "enable", "disable", "install",
})


_PLAN_PATTERNS = (
    re.compile(r"^(?:run|execute|start)\s+playbook\s+(.+)$"),
    re.compile(r"^(?:run|execute|start)\s+plan\s+(.+)$"),
    re.compile(r"^playbook\s+(.+)$"),
)


_ROUTINE_PATTERNS = (
    re.compile(r"^(?:run|execute|start)\s+routine\s+(.+)$"),
    re.compile(r"^routine\s+(.+)$"),
)


_NUMBERED_STEP = re.compile(r"^\s*\d+[.)]\s+\S")


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _clean_name(raw: str) -> str:
    name = _norm(raw).strip("\"'").strip()
    if name.lower().startswith("the "):
        name = name[4:].strip()
    return name


def _match_names(text: str, patterns, names) -> str | None:
    """Exact (case-insensitive) name match against a pattern set.

    Returns the matched name in the caller's canonical casing, or
    None. No fuzzy matching, no similar-name guessing.
    """
    lowered = {str(n).strip().lower(): str(n) for n in names}
    for pattern in patterns:
        m = pattern.match(text)
        if not m:
            continue
        candidate = _clean_name(m.group(1))
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def _is_multi_step_structure(text: str) -> bool:
    """True when the text carries explicit multi-step structure."""
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    numbered = sum(1 for ln in lines if _NUMBERED_STEP.match(ln))
    if numbered >= 2:
        return True
    low = _norm(text).lower()
    if "step 1" in low and "step 2" in low:
        return True
    if "first " in low and " then " in low:
        return True
    if " and then " in low:
        return True
    parts = [p for p in str(text).split(";") if p.strip()]
    if len(parts) >= 2 and all(len(p.split()) >= 2 for p in parts):
        return True
    return False


@dataclass
class TurnClassification:
    """One turn, exactly one kind, with the evidence for it."""

    kind: str
    name: str = ""
    note: str = ""


def classify_turn(text: str, *, plan_names=(), routine_names=()) -> TurnClassification:
    """Classify one chat turn. Pure function; no I/O, no model.

    Order matters and is deliberate: an explicit, resolvable
    plan/routine invocation wins over structure (a plan name
    that happens to contain "then" is still a plan); explicit
    multi-step structure wins over the single-action heuristic;
    anything else is conversation. Unresolvable names fall
    through — they never claim a class they cannot satisfy.
    """
    raw = _norm(text)
    low = raw.lower()
    if not low:
        return TurnClassification("conversation", note="empty turn")
    plan = _match_names(low, _PLAN_PATTERNS, plan_names)
    if plan:
        return TurnClassification("saved_plan", name=plan,
                                  note="explicit playbook invocation")
    routine = _match_names(low, _ROUTINE_PATTERNS, routine_names)
    if routine:
        return TurnClassification("routine", name=routine,
                                  note="explicit routine invocation")
    # Structure is checked on the ORIGINAL text: _norm collapses
    # newlines, which would destroy numbered lists before they
    # could be counted (coordinator batch: caught by its own
    # suite — numbered steps classified as conversation).
    if _is_multi_step_structure(text):
        return TurnClassification(
            "multi_step",
            note="explicit multi-step structure; model-side "
                 "decomposition (coordinator does not invent steps)")
    sentences = [s for s in re.split(r"[.?!]\s*", raw) if s.strip()]
    first_word = low.split(" ", 1)[0].strip(".,!?") if low else ""
    if len(sentences) == 1 and first_word in _SINGLE_VERBS:
        return TurnClassification("single_action",
                                  note="single imperative, heuristic verb")
    return TurnClassification("conversation", note="no action structure")
