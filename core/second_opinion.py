"""Wave 8 (v5.63) — Second-opinion mode (A8 #3).

Two brains cross-check a high-stakes answer.

SHAPE AND HONESTY RULES, enforced by construction and tested
in tests/test_second_opinion_autoinvoke.py:

* The module takes two injected brain-answer callables and
  nothing else. It imports no brain, declares no tool, and
  performs no side effect. Which answers count as
  "high-stakes" is the caller's decision; this module only
  cross-checks what it is handed.
* The result is ``"agree"``, ``"disagree"``, or
  ``"unavailable"`` (a brain errored or returned nothing).
  Comparison is on normalised text (case and whitespace
  collapsed), so trivial formatting differences are not
  reported as disagreement — but nothing is fuzzed into
  agreement: different wording that normalises differently
  disagrees.
* Cross-checking REDUCES error; it never eliminates it.
  Every UI-facing string this module returns says so in
  plain words, and none of them claims verification,
  certainty, or a guarantee. The word-check is tested:
  ``render_for_ui`` output contains none of "verified",
  "certain", or "guaranteed" in any case.
* A disagreement is NEVER auto-resolved. ``resolved_answer``
  is always ``None``; there is no code path that picks one
  brain silently. A disagreement (or an unavailable brain)
  sets ``escalate`` and yields an escalation payload for a
  human decision.
* No new dependencies (stdlib only), no weights, no
  telemetry, no lifelog, no third-party material — so no
  third-party licence is involved in this module at all.

Versioned string introduced by this module (pre-pinned for
the 5.63.0 assembly; core/version.py is NOT touched here and
still reads 5.62.0 until the coordinator assembles the wave):
``VERSION = "5.63.0"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VERSION = "5.63.0"

__all__ = [
    "VERSION",
    "HONESTY_NOTICE",
    "SecondOpinionResult",
    "normalize_answer",
    "cross_check",
    "render_for_ui",
    "escalation_payload",
]

# UI-facing honesty notice. Rendered verbatim by callers; it must
# never claim that cross-checking verifies an answer or makes it
# certain. (Tested: no "verified" / "certain" / "guaranteed".)
HONESTY_NOTICE = (
    "A second brain checking an answer reduces mistakes, but it "
    "does not eliminate them. Two brains agreeing does not make "
    "an answer right, and a disagreement is not settled here — "
    "it is passed on for a human decision."
)

_WHITESPACE_RE = re.compile(r"\s+")


def _as_text(answer):
    """Normalise a brain answer to text.

    Accepts a plain string, or a dict carrying a ``"text"`` entry
    (the shape brain answers commonly take). Anything else is
    stringified; ``None`` becomes "" (missing, not an answer).
    """
    if answer is None:
        return ""
    if isinstance(answer, dict):
        return _as_text(answer.get("text"))
    if isinstance(answer, str):
        return answer
    return str(answer)


def normalize_answer(answer):
    """Comparison form of an answer: text with case folded and
    whitespace collapsed. Used for agree/disagree only; the
    original wording is always preserved in the result."""
    return _WHITESPACE_RE.sub(" ", _as_text(answer).strip()).casefold()


@dataclass
class SecondOpinionResult:
    """One cross-check. ``resolved_answer`` is ALWAYS ``None``:
    this module never picks a winner, silently or otherwise."""

    question: str
    answer_a: str
    answer_b: str
    verdict: str  # "agree" | "disagree" | "unavailable"
    disagreement: str = ""
    escalate: bool = False
    resolved_answer: object = None
    unavailable_side: str = ""  # "" | "a" | "b" | "both"

    def __post_init__(self):
        if self.verdict not in ("agree", "disagree", "unavailable"):
            raise ValueError(f"unknown second-opinion verdict: {self.verdict!r}")
        if self.verdict == "agree":
            if self.escalate:
                raise ValueError("an agreement must not escalate")
            if self.disagreement:
                raise ValueError("an agreement carries no disagreement")
        else:
            if not self.escalate:
                raise ValueError("a disagreement or unavailable brain must escalate")
        # The no-auto-resolution rule, stated as an invariant rather
        # than a convention: even a caller-constructed result cannot
        # smuggle a picked answer through this type.
        object.__setattr__(self, "resolved_answer", None)


def cross_check(question, brain_a, brain_b):
    """Cross-check two injected brains on ``question``.

    Each brain is a callable taking the question and returning an
    answer (a string, or a dict with a ``"text"`` entry). A brain
    that raises, or returns nothing usable, makes the verdict
    ``"unavailable"`` — never a silent fallback to the other brain.
    """
    if not callable(brain_a) or not callable(brain_b):
        raise ValueError("second opinion needs two injected brain callables")
    answers = {}
    unavailable = []
    for side, brain in (("a", brain_a), ("b", brain_b)):
        try:
            raw = brain(question)
        except Exception as exc:  # noqa: BLE001 - any brain failure is "unavailable"
            answers[side] = ""
            unavailable.append((side, f"brain {side.upper()} errored ({exc})"))
            continue
        text = _as_text(raw)
        answers[side] = text
        if not text.strip():
            unavailable.append((side, f"brain {side.upper()} returned no answer"))
    if unavailable:
        sides = sorted(side for side, _ in unavailable)
        detail = "; ".join(reason for _, reason in unavailable)
        return SecondOpinionResult(
            question=str(question),
            answer_a=answers["a"],
            answer_b=answers["b"],
            verdict="unavailable",
            disagreement=(
                "no second opinion could be formed: " + detail
                + " — the single available answer was NOT used on its own"
            ),
            escalate=True,
            unavailable_side="both" if len(sides) == 2 else sides[0],
        )
    if normalize_answer(answers["a"]) == normalize_answer(answers["b"]):
        return SecondOpinionResult(
            question=str(question),
            answer_a=answers["a"],
            answer_b=answers["b"],
            verdict="agree",
            disagreement="",
            escalate=False,
        )
    return SecondOpinionResult(
        question=str(question),
        answer_a=answers["a"],
        answer_b=answers["b"],
        verdict="disagree",
        disagreement=(
            "the two brains gave different answers — brain A said: "
            f"{answers['a']!r}; brain B said: {answers['b']!r}. "
            "Neither answer was picked over the other."
        ),
        escalate=True,
    )


def render_for_ui(result):
    """UI-facing strings for a cross-check result, as data the
    caller renders. Every rendering carries the honesty notice;
    none claims verification, certainty, or a guarantee."""
    if result.verdict == "agree":
        verdict_line = (
            "Both brains gave the same answer. That lowers the "
            "chance of a mistake — it does not remove it."
        )
        escalation_line = ""
    elif result.verdict == "disagree":
        verdict_line = (
            "The two brains disagreed. This needs a human "
            "decision — neither answer was picked automatically."
        )
        escalation_line = (
            "Escalated for a human decision: " + result.disagreement
        )
    else:
        verdict_line = (
            "A second opinion could not be obtained, so this "
            "answer stands unchecked by a second brain."
        )
        escalation_line = (
            "Escalated for a human decision: " + result.disagreement
        )
    return {
        "verdict": result.verdict,
        "verdict_line": verdict_line,
        "notice": HONESTY_NOTICE,
        "disagreement": result.disagreement,
        "escalation_line": escalation_line,
        "escalate": result.escalate,
    }


def escalation_payload(result):
    """The escalation signal for a human decision, or ``None``
    when the brains agreed. A disagreement payload carries BOTH
    answers and picks neither."""
    if not result.escalate:
        return None
    return {
        "question": result.question,
        "verdict": result.verdict,
        "answer_a": result.answer_a,
        "answer_b": result.answer_b,
        "disagreement": result.disagreement,
        "resolved_answer": None,
    }
