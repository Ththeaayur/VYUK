"""Split of task_coordinator: second-opinion helpers."""

from __future__ import annotations


def _menu_leaf(step) -> str:
    """Leaf name of a menu_action step's path, or "".

    Mirrors core.desktop.menu_paths.parse_path's split
    precedence (→ before >): the leaf is the last segment.
    """
    path = (step.args or {}).get("path")
    if isinstance(path, (list, tuple)):
        segs = [str(s or "").strip() for s in path
                if str(s or "").strip()]
        return segs[-1] if segs else ""
    text = str(path or "").strip()
    if "→" in text:
        return text.split("→")[-1].strip()
    if ">" in text:
        return text.split(">")[-1].strip()
    return text


def _second_opinion_question(step) -> str:
    """One-word YES/NO confirmation question for a step.

    Built from the step's label (or, for a menu invoke, its
    leaf name) ONLY — never from args, which may carry message
    text, paths, or other user data the brains do not need to
    judge whether the step should run.
    """
    if step.tool == "menu_action":
        leaf = _menu_leaf(step)
        what = (f"invoke the menu item {leaf!r}" if leaf
                else "invoke a menu item")
    else:
        what = f"carry out this step: {(step.label or step.tool)!r}"
    return (f"Answer with one word, YES or NO: is it safe and correct "
            f"for the assistant to {what}?")


def _second_opinion_pause_sentence(step, result) -> str:
    """Dialog sentence for a second-opinion pause (both views).

    The S-1 dialog renders the pending sentence verbatim, so
    this sentence IS the human-facing surface: it names the
    step, carries both brains' answers (or the unavailable
    detail), states that neither answer was picked, and frames
    the choice as Allow once / Don't allow. The refused
    outcome on denial carries this same sentence as its
    message, so the record keeps both views.
    """
    label = step.label or step.tool
    if result.verdict == "disagree":
        return (
            f"Second opinion needed before {label!r}: the two brains "
            f"disagreed — brain A said: {result.answer_a!r}; brain B "
            f"said: {result.answer_b!r}. Neither answer was picked. "
            "Allow once to run this step anyway, or Don't allow to "
            "refuse it.")
    if result.verdict == "unavailable":
        return (
            f"Second opinion needed before {label!r}: "
            f"{result.disagreement} Allow once to run this step "
            "anyway, or Don't allow to refuse it.")
    # Agree without YES/YES confirmation (both NO, or identical
    # non-YES answers): agreement on a non-confirmation is not
    # a confirmation — the step pauses for a human decision
    # instead of proceeding on advice that did not confirm it.
    return (
        f"Second opinion needed before {label!r}: both brains "
        f"answered {result.answer_a!r} — that is not a confirmation "
        "to run this step. Allow once to run it anyway, or Don't "
        "allow to refuse it.")
