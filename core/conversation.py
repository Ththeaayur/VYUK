"""Wave 9 (v5.64) — conversation craft.

Three small, testable rules for how she talks:

1. ONE QUESTION PER TURN. A turn that asks two things gets
   two half-answers. ``count_questions`` / ``check_turn`` find
   the questions in a turn; a turn with more than one fails
   the check and the caller must split it, not send it.
2. ERROR LADDER, max 2 re-asks, never 3 errors in a row.
   After a failed turn she may re-ask twice, each time with
   more guidance; the third failure ENDS the turn gracefully
   with a summary of what was understood — it never produces
   a third error prompt. The ladder is a state machine, so the
   count cannot drift across calls.
3. ERROR-STRING LINT. Every spoken error must carry problem +
   cause + solution, in that order, and must not use banned
   phrases that say nothing ("something went wrong"). An error
   that fails lint is not spoken as-is.

Plus a YapBench-style brevity scorer: per-brain mean brevity
scores in CI, so a brain that rambles is visible as a number,
not a vibe. The scorer measures length against a target only;
it never judges content.

NOT ADOPTED, explicitly: Google's anti-formality defaults.
Those defaults trade clarity for casualness by rule; this
assistant's house rule is plain direct language instead —
no corporate formality templates, and no forced-casual
templates either. ``FORMALITY_DEFAULTS_ADOPTED`` is False and
stays False unless a future wave explicitly overturns it.

Stdlib only. No telemetry. No GUI wiring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MODULE_VERSION = "5.64.0"

# Explicit non-adoption, recorded so a later reader does not
# re-derive it from a style guide and silently flip the default.
FORMALITY_DEFAULTS_ADOPTED = False

__all__ = [
    "MODULE_VERSION",
    "FORMALITY_DEFAULTS_ADOPTED",
    "MAX_REASKS",
    "BANNED_ERROR_PHRASES",
    "count_questions",
    "check_turn",
    "TurnCheck",
    "ErrorLadder",
    "LadderOutcome",
    "lint_error_string",
    "LintResult",
    "brevity_score",
    "score_brain_responses",
    "BrainBrevity",
]

MAX_REASKS = 2

# Phrases that fill an error message without saying anything.
# Matching is case-insensitive and substring-based, so casing
# tricks ("Something Went Wrong") do not slip through.
BANNED_ERROR_PHRASES = (
    "something went wrong",
    "an error occurred",
    "error occurred",
    "oops",
    "try again later",
    "unknown error",
    "failed successfully",
)


# ------------------------------------------------------------------ #
# 1. One question per turn
# ------------------------------------------------------------------ #

# A question mark ends a question; so does a sentence starting
# with a question word and ending in a period in terse assistant
# copy ("What time works"). Both are counted, conservatively:
# over-counting only forces a split, under-counting ships a
# double question.
_QUESTION_WORDS = re.compile(
    r"(?im)^\s*(what|when|where|which|who|whom|whose|why|how|"
    r"is|are|do|does|did|can|could|would|should|shall|will)\b")


def count_questions(text: str) -> int:
    """Questions in a turn: '?' marks, plus question-word
    sentences without a '?'. Empty text has zero questions."""
    if not text or not text.strip():
        return 0
    marks = text.count("?")
    # Worded questions are sentences ending in '.' (or with no
    # terminator) that start with a question word. Sentences
    # ending in '?' are already counted by the mark above and
    # must not be counted twice.
    worded = 0
    for span in re.findall(r"[^.?!]+[.?!]?", text.strip()):
        span = span.strip()
        if not span or span.endswith("?"):
            continue
        if _QUESTION_WORDS.match(span):
            worded += 1
    return marks + worded


@dataclass(frozen=True)
class TurnCheck:
    """Result of ``check_turn``: ok, the count, and the fix."""

    ok: bool
    question_count: int
    guidance: str


def check_turn(text: str) -> TurnCheck:
    """A turn is sendable when it asks at most one question.
    Zero questions is fine (a statement turn); two or more is
    not — the guidance says to split, never to merge."""
    count = count_questions(text)
    if count <= 1:
        return TurnCheck(True, count, "")
    return TurnCheck(
        False, count,
        f"Turn asks {count} questions; split it into {count} turns, "
        "one question each.")


# ------------------------------------------------------------------ #
# 2. Error ladder state machine
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class LadderOutcome:
    """What the ladder says to do after a failure."""

    action: str  # "reask" | "close"
    reask_number: int  # 1 or 2 for reasks, 0 when closing
    guidance: str
    consecutive_errors: int


@dataclass
class ErrorLadder:
    """Per-conversation-turn error state. ``record_failure``
    advances the machine; ``record_success`` resets it. The
    invariant — never 3 errors in a row — is structural: after
    MAX_REASKS re-asks the only outcome left is "close"."""

    consecutive_errors: int = 0
    reasks_used: int = 0

    def record_success(self) -> None:
        self.consecutive_errors = 0
        self.reasks_used = 0

    def record_failure(self, understood_so_far: str = "") -> LadderOutcome:
        self.consecutive_errors += 1
        if self.reasks_used < MAX_REASKS:
            self.reasks_used += 1
            more = ("Try rephrasing, or give one detail at a time."
                    if self.reasks_used == 1
                    else "One more try — a single short phrase is enough, "
                         "or say 'stop' to leave it here.")
            return LadderOutcome("reask", self.reasks_used, more,
                                 self.consecutive_errors)
        # Third failure: close, with what was understood. This is
        # the only path once reasks are spent — no third error
        # prompt can ever be produced from this state.
        summary = (f"Pausing here. What I understood so far: "
                   f"{understood_so_far}." if understood_so_far
                   else "Pausing here — nothing to retry against.")
        return LadderOutcome("close", 0, summary, self.consecutive_errors)


# ------------------------------------------------------------------ #
# 3. Error-string lint
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class LintResult:
    """Lint verdict: ok, and every problem found."""

    ok: bool
    problems: tuple[str, ...] = ()


def lint_error_string(problem: str, cause: str, solution: str) -> LintResult:
    """An error is speakable only with all three parts present
    and no banned phrase in any part. Missing parts are listed
    individually so the fix is obvious."""
    problems: list[str] = []
    for name, value in (("problem", problem), ("cause", cause),
                        ("solution", solution)):
        if not value or not value.strip():
            problems.append(f"missing {name}")
    combined = " ".join(p for p in (problem, cause, solution)).lower()
    for banned in BANNED_ERROR_PHRASES:
        if banned in combined:
            problems.append(f"banned phrase: '{banned}'")
    return LintResult(not problems, tuple(problems))


def format_error(problem: str, cause: str, solution: str) -> str:
    """Assemble a linted error string, or raise with the lint
    problems. Callers never speak an unlinted error."""
    result = lint_error_string(problem, cause, solution)
    if not result.ok:
        raise ValueError("error string failed lint: "
                         + "; ".join(result.problems))
    return f"{problem.strip()} {cause.strip()} {solution.strip()}"


# ------------------------------------------------------------------ #
# 4. YapBench-style brevity scoring (CI harness)
# ------------------------------------------------------------------ #

# Words, not characters: a long word is one word. The score is
# 100 at or under target, then decays linearly to 0 at twice
# the target — a brain twice as long as the target scores 0 on
# brevity, whatever it said.
DEFAULT_TARGET_WORDS = 40


def brevity_score(text: str,
                 target_words: int = DEFAULT_TARGET_WORDS) -> float:
    """0..100 brevity score for one response."""
    if target_words <= 0:
        raise ValueError("target_words must be positive")
    words = len(text.split())
    if words <= target_words:
        return 100.0
    if words >= 2 * target_words:
        return 0.0
    return round(100.0 * (2 * target_words - words) / target_words, 2)


@dataclass(frozen=True)
class BrainBrevity:
    """Per-brain CI summary: mean score, responses counted."""

    brain: str
    mean_score: float
    responses: int


def score_brain_responses(by_brain: dict[str, list[str]],
                          target_words: int = DEFAULT_TARGET_WORDS
                          ) -> list[BrainBrevity]:
    """Mean brevity per brain, best first. A brain with no
    responses scores nothing and is omitted, not zero-filled —
    absence of data is not a bad score."""
    out: list[BrainBrevity] = []
    for brain, responses in by_brain.items():
        if not responses:
            continue
        scores = [brevity_score(text, target_words) for text in responses]
        out.append(BrainBrevity(brain, round(sum(scores) / len(scores), 2),
                                len(responses)))
    out.sort(key=lambda b: (-b.mean_score, b.brain))
    return out
