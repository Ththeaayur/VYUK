"""U3 — check-question loop (tutor programme, 2026-11-08).

Lab leg only. The real-Windows field leg — a real MA 105 check
question on his machine, graded by rubric, retried with a hint,
never revealing the answer, with mastery moving exactly once —
is OWED, not this batch.

Gap proved first (audit T3, T8, T13): ``flashcard_answer``
grades by EXACT STRING match (``given.lower() ==
card["back"].lower()``, ``core/skills_study_flashcards.py:141``)
and on a wrong answer REVEALS the gold immediately ("Not
quite — 'X' has the back 'Y'"), which ends the learning — no
retry, no hint, no re-check, and a lucky right answer counts
as mastery. ``quiz_me`` (``core/skills_study_drills.py:23``)
is a 4-option MCQ from the user's own cards only. No rubric
grading, no numeric tolerance, no retry loop anywhere in the
tree; no graded check writes mastery through U1's
``record_graded``. The B15 pause shape (``_pending_question``,
``task_coordinator_execution.py:2574``; ``resolve_question``,
``task_coordinator_lifecycle.py:555``) exists but nothing runs
a bite + check on it.

LOPHOLE FAMILY — "a check that teaches nothing": the current
grading is exact-string match that reveals the answer the
moment you are wrong, so a wrong answer ends the learning and
a lucky right answer counts as knowing. ONE design closes the
class: every check runs ONE loop — ask the bite's check on the
B15 pause shape; grade against the bite's gold + rubric
(deterministic first, judge only as fallback, deterministic
fail-safe — an invalid grade fails safe to "not proven", never
a guessed pass); a wrong answer goes hint → re-teach →
re-check variant (bounded — the loop escalates, never spins
forever); the answer is NEVER revealed (a wrong answer gets a
hint, never the gold — enforced in code by a gold-containment
guard on every reply, not by convention); the graded outcome
is the ONLY thing that writes mastery, and it writes it ONLY
through U1's ``record_graded()`` (never by touching the
record directly).

REJECTED alternatives (recorded, not carried forward):
- Exact-string grading: punishes a right answer in different
  words, rewards a lucky exact match — the audit's T13 failure.
- Revealing the answer on a wrong answer: kills the retry loop
  — the current leak, fixed here in the check path, not carried
  forward. (The old drill layer keeps its existing behaviour;
  the check loop is the new path and the drill layer is not
  rewritten here — a later batch owns it.)
- An unbounded retry loop: a stuck student loops forever —
  bounded at MAX_ATTEMPTS graded answers, then the loop stops
  honestly and hands the skill to the misconception path (U4).
- Grading by LLM judge alone with no deterministic layer: an
  invalid judge output must fail safe, never guess a grade
  (research §(d): gold + rubric, fine granularity, deduction
  explanation, deterministic fail-safe to 0).
- A numeric grader that accepts one exact form only:
  deterministic parse + tolerance first — equivalent forms,
  units, significant figures pass; the judge is only a fallback
  (research §(d)).

GRADING ORDER (``grade_answer``): normalised exact match →
numeric parse + tolerance (equivalent forms, units) → LLM
judge (injected callable, only as fallback) → deterministic
fail-safe "not proven". A deterministic numeric verdict
stands: a wrong value is wrong and no judge overrules
arithmetic.

THE VARIANT SEAM: the loop never repeats a question — every
re-check is a fresh variant, so a repeated correct answer is
never a transfer. Variants come from an injected
``variant_fn(bite, attempt)`` (the tutor driver supplies it;
in production the brain generates the variant). With no
variant source the loop ends honestly after hint + re-teach
("no_fresh_variant") rather than repeating the question.

D-T3 (owner ruling, 2026-11-08): a low-confidence spoken
answer (confidence < U1's CONFIDENCE_FLOOR 0.6) is a likely
mishearing — the loop re-asks ("did you say X?") and grades
nothing, records nothing, consumes no attempt.

The session is a small deterministic state machine: pending
until an answer resolves it, done exactly once. It never
raises for a bad answer (empty, non-string, bad confidence —
all return an honest reply with state unchanged). Only a
malformed bite raises, at construction (a programming error,
not a runtime input).
"""

import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from . import student_model as sm
from .lesson_planner import Bite

# The retry bound: graded answers per check. After the Nth
# wrong graded answer the loop stops honestly and hands the
# skill to the misconception path (U4) — never loops forever,
# never invents a pass.
MAX_ATTEMPTS = 3

# Numeric tolerance: a value within this relative band of the
# gold counts as the same answer (equivalent forms, units and
# significant figures all pass through it).
REL_TOL = 1e-6
ABS_TOL = 1e-9

# Spoken-maths normalisation: superscripts and symbols the
# student may type or say in a different form than the gold.
_SUPERSCRIPTS = {
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4",
    "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
    "⁻": "^-",
}
_SYMBOLS = {"π": "pi", "×": "*", "÷": "/", "−": "-", "√": "sqrt"}

_NUM_RE = re.compile(
    r"[+-]?\d+/\d+"
    r"|[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?")

_UNIT_ALIASES = {
    "metre": "m", "meter": "m", "metres": "m", "meters": "m",
    "centimetre": "cm", "centimeter": "cm",
    "centimetres": "cm", "centimeters": "cm",
    "millimetre": "mm", "millimeter": "mm",
    "millimetres": "mm", "millimeters": "mm",
    "kilometre": "km", "kilometer": "km",
    "kilometres": "km", "kilometers": "km",
    "second": "s", "seconds": "s",
    "minute": "min", "minutes": "min",
    "radian": "rad", "radians": "rad",
    "degree": "deg", "degrees": "deg",
    "unit": "unit", "units": "unit",
}

_GENERIC_HINT = ("Look at what the answer has to show — "
                 "what must it include, and what would make it wrong?")
_GENERIC_RETEACH = ("Let's go over this bite once more — "
                    "what must the answer show?")


def _normalise(text):
    """Lowercase, collapse whitespace, unify maths spellings."""
    out = str(text or "").lower()
    for src, dst in _SUPERSCRIPTS.items():
        out = out.replace(src, dst)
    for src, dst in _SYMBOLS.items():
        out = out.replace(src, dst)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _parse_number(token):
    """One token -> Fraction, or None. Exact, never float-guessed."""
    token = token.replace(",", "")
    if "/" in token:
        num, _, den = token.partition("/")
        try:
            return Fraction(int(num), int(den))
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return Fraction(Decimal(token))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return None


def _numbers(text):
    """The numeric values in a text, in order — [] when none."""
    values = []
    for token in _NUM_RE.findall(str(text or "")):
        value = _parse_number(token)
        if value is not None:
            values.append(value)
    return values


def _unit(text):
    """The unit token after the last number, alias-normalised.

    "" when the text carries no unit. Only the gold's unit is a
    contract (see grade_answer): a gold with no unit accepts
    any unit in the answer.
    """
    text = str(text or "")
    matches = list(_NUM_RE.finditer(text))
    if not matches:
        return ""
    tail = text[matches[-1].end():].strip().lower()
    tail = re.sub(r"[^a-z ]", " ", tail).strip()
    if not tail:
        return ""
    word = tail.split()[0]
    return _UNIT_ALIASES.get(word, word)


def _close(a, b):
    """Within tolerance: |a-b| <= REL_TOL * max(|b|, 1) + ABS_TOL."""
    diff = abs(a - b)
    allowed = Fraction(Decimal(str(REL_TOL))) * max(abs(b), 1)
    allowed += Fraction(Decimal(str(ABS_TOL)))
    return diff <= allowed


def _contains_gold(text, gold):
    """Would ``text`` reveal the gold? Normalised substring, or
    every gold number present in the text's numbers."""
    norm_text = _normalise(text)
    norm_gold = _normalise(gold)
    if norm_gold and norm_gold in norm_text:
        return True
    gold_nums = _numbers(gold)
    if not gold_nums:
        return False
    text_nums = _numbers(text)
    return all(any(_close(t, g) for t in text_nums) for g in gold_nums)


def _first_sentence(text):
    """First sentence of a text, or a safe head when unpunctuated."""
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.?!])\s+", text)
    head = parts[0].strip()
    if len(head) > 200:
        head = head[:200].rsplit(" ", 1)[0].strip()
    return head


def hint_for(bite):
    """The single U3 nudge: the rubric's first clause, never gold.

    The rubric states what a correct answer must show; its
    first clause is a nudge, not the answer. When the clause
    would reveal the gold (or there is no usable rubric), the
    generic nudge is used instead — the gold is never hinted.
    """
    clause = _first_sentence(getattr(bite, "rubric", "") or "")
    if not clause or _contains_gold(clause, getattr(bite, "gold", "")):
        return _GENERIC_HINT
    return clause


def grade_answer(answer, gold, rubric="", *, judge=None):
    """Grade one answer against gold + rubric. Never raises.

    Returns {"correct": bool, "method": ..., "reason": ...,
    "deduction": ...}. Methods, in order: "exact" (normalised
    match), "numeric" (parse + tolerance, units), "judge"
    (injected fallback), "fail_safe" (nothing proved it — an
    invalid judge output, no judge, or an empty answer; never
    a guessed pass). ``judge`` is a callable
    (answer, gold, rubric) -> dict with a bool "correct";
    anything else fails safe. ``deduction`` carries the judge's
    explanation when it gives one.
    """
    if not isinstance(answer, str) or not answer.strip():
        return {"correct": False, "method": "fail_safe",
                "reason": "empty_answer", "deduction": ""}
    if not isinstance(gold, str) or not gold.strip():
        return {"correct": False, "method": "fail_safe",
                "reason": "empty_gold", "deduction": ""}
    given = answer.strip()
    if _normalise(given) == _normalise(gold):
        return {"correct": True, "method": "exact",
                "reason": "matches the gold answer",
                "deduction": ""}
    gold_nums = _numbers(gold)
    given_nums = _numbers(given)
    if gold_nums and given_nums and len(gold_nums) == len(given_nums):
        if all(_close(g, v) for g, v in zip(given_nums, gold_nums)):
            gold_unit = _unit(gold)
            if gold_unit and _unit(given) != gold_unit:
                return {"correct": False, "method": "numeric",
                        "reason": "right value, wrong unit",
                        "deduction": "the value matches but the "
                                     "unit does not"}
            return {"correct": True, "method": "numeric",
                    "reason": "value matches within tolerance",
                    "deduction": ""}
        return {"correct": False, "method": "numeric",
                "reason": "value does not match the gold",
                "deduction": ""}
    if judge is not None:
        try:
            verdict = judge(given, gold, rubric or "")
        except Exception as exc:  # noqa: BLE001 — a judge that
            # raises proves nothing; fail safe, never guess.
            return {"correct": False, "method": "fail_safe",
                    "reason": "judge_error", "deduction": str(exc)}
        if not isinstance(verdict, dict) \
                or not isinstance(verdict.get("correct"), bool):
            return {"correct": False, "method": "fail_safe",
                    "reason": "judge_unparseable", "deduction": ""}
        deduction = verdict.get("deduction", "")
        deduction = deduction if isinstance(deduction, str) else ""
        return {"correct": bool(verdict["correct"]),
                "method": "judge",
                "reason": ("judge graded it correct"
                           if verdict["correct"]
                           else "judge graded it incorrect"),
                "deduction": deduction}
    return {"correct": False, "method": "fail_safe",
            "reason": "no_judge", "deduction": ""}


class CheckSession:
    """One bite's check, run as a bounded loop on the B15 pause
    shape.

    ``start()`` asks the bite's check and the lesson pauses —
    the pending record names the question (B15's shape:
    question + bite skill + attempt + label), and only
    ``answer()`` resumes it. A correct answer ends the loop
    (outcome "mastered" or "still_learning" from U1); a wrong
    answer gives a hint + a re-teach + a fresh variant, up to
    MAX_ATTEMPTS graded answers, then ends honestly as
    "handed_to_diagnosis" (U4's job). The gold is never in any
    reply — hint and re-teach are gold-guarded in code.

    ``model`` is a U1 StudentModel; the session writes mastery
    ONLY through ``record_graded`` (source "check"), exactly
    once per graded answer. ``judge`` is the rubric-judge
    fallback (see grade_answer). ``variant_fn`` is
    (bite, attempt) -> fresh check text or None; without one
    the loop ends honestly after hint + re-teach rather than
    repeating the question.
    """

    def __init__(self, bite, model, *, judge=None, variant_fn=None):
        for field in ("skill", "check", "gold", "rubric"):
            value = getattr(bite, field, None)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"check loop needs a bite with a usable "
                    f"{field!r}")
        self.bite = bite
        self.model = model
        self._judge = judge
        self._variant_fn = variant_fn
        self._status = "not_started"
        self._pending = None
        self._attempts = 0
        self._hinted = False
        self._outcome = None
        self._grade = None
        self._mastery = None

    # -- pause shape (B15) -----------------------------------------
    def start(self):
        """Ask the bite's check; the lesson pauses for the answer.

        A diagnostic bite's check runs before any teaching (U2
        contract) — the session only asks; the caller teaches
        the bite first except for diagnostic bites.
        """
        if self._status != "not_started":
            return dict(self._pending) if self._pending else None
        self._status = "pending"
        self._pending = {
            "question": self.bite.check,
            "bite_skill": self.bite.skill,
            "attempt": 1,
            "variant": 1,
            "label": "tutor check",
            "diagnostic": bool(getattr(self.bite, "diagnostic",
                                       False)),
        }
        return dict(self._pending)

    def pending(self):
        """The current pending question record, or None."""
        return dict(self._pending) if self._pending else None

    @property
    def status(self):
        """not_started | pending | done."""
        return self._status

    @property
    def outcome(self):
        """The terminal outcome dict, or None while running."""
        return dict(self._outcome) if self._outcome else None

    # -- the loop ----------------------------------------------------
    def answer(self, text, *, confidence=1.0):
        """Resolve the pending check with an answer. Never raises
        for a bad answer. Returns {"reply", "status", "outcome",
        "pending", "grade", "mastery"} — always all keys."""
        if self._status == "done":
            return self._reply(
                "This check is finished — start a new check for "
                "the next bite.")
        if self._status != "pending" or self._pending is None:
            return self._reply("Start the check first — ask the "
                               "bite's check question, then answer "
                               "it.")
        if not isinstance(text, str) or not text.strip():
            return self._reply("Give me your answer and I'll check "
                               "it against the rubric.")
        given = text.strip()
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < sm.CONFIDENCE_FLOOR:
            # D-T3: a likely mishearing re-asks; it grades
            # nothing, records nothing, consumes no attempt.
            return self._reply(
                f"I didn't quite catch that — did you say "
                f"'{given}'? Say it again and I'll check it.")
        grade = grade_answer(given, self.bite.gold,
                             self.bite.rubric, judge=self._judge)
        self._grade = grade
        # The ONLY mastery write in this module: exactly one
        # record_graded per graded answer. Every question the
        # loop asks is a fresh variant by construction, so novel
        # is always True; the transfer bar is carried by hinted.
        mastery = self.model.record_graded(
            self.bite.skill, grade["correct"], novel=True,
            hinted=self._hinted, confidence=confidence,
            source="check")
        self._mastery = mastery
        self._attempts += 1
        if grade["correct"]:
            return self._finish_correct()
        return self._handle_wrong(grade)

    def _finish_correct(self):
        """A correct graded answer ends the loop."""
        self._status = "done"
        self._pending = None
        mastered = bool(self._mastery.get("mastered"))
        outcome = ("mastered" if mastered else "still_learning")
        transfers = self._mastery.get("transfers", 0)
        self._outcome = {
            "outcome": outcome,
            "skill": self.bite.skill,
            "attempts": self._attempts,
            "grade": dict(self._grade),
            "mastery": dict(self._mastery),
        }
        if mastered:
            reply = (f"Right — that checks out. That's {transfers} "
                     f"clean transfers, so {self.bite.skill} counts "
                     f"as mastered.")
        elif not self._hinted:
            reply = (f"Right — that checks out. That's one clean "
                     f"transfer — one more on a fresh variant and "
                     f"{self.bite.skill} counts as mastered.")
        else:
            reply = (f"Right — that checks out. It came after a "
                     f"hint, so it moves your mastery but doesn't "
                     f"count as a clean transfer — the next one "
                     f"needs to be unhinted on a fresh variant.")
        return self._reply(reply)

    def _handle_wrong(self, grade):
        """A wrong graded answer: hint → re-teach → re-check
        variant, or an honest terminal handover."""
        hint = hint_for(self.bite)
        reteach = self.bite.bite
        if _contains_gold(reteach, self.bite.gold):
            reteach = _GENERIC_RETEACH
        if self._attempts >= MAX_ATTEMPTS:
            self._status = "done"
            self._pending = None
            self._outcome = {
                "outcome": "handed_to_diagnosis",
                "skill": self.bite.skill,
                "attempts": self._attempts,
                "reinforce": getattr(self.bite, "reinforce", None),
                "grade": dict(grade),
                "mastery": dict(self._mastery),
                "reason": "retry_bound",
            }
            return self._reply(
                f"That's still not landing after {self._attempts} "
                f"tries — I'm handing {self.bite.skill} to the "
                f"diagnosis step so we can find out why, instead "
                f"of guessing. Nothing is marked as known.")
        variant = None
        if self._variant_fn is not None:
            try:
                variant = self._variant_fn(self.bite,
                                           self._attempts + 1)
            except Exception:  # noqa: BLE001 — a variant source
                # that raises proves nothing; end honestly.
                variant = None
        if not isinstance(variant, str) or not variant.strip():
            self._status = "done"
            self._pending = None
            self._outcome = {
                "outcome": "handed_to_diagnosis",
                "skill": self.bite.skill,
                "attempts": self._attempts,
                "reinforce": getattr(self.bite, "reinforce", None),
                "grade": dict(grade),
                "mastery": dict(self._mastery),
                "reason": "no_fresh_variant",
            }
            return self._reply(
                f"Not quite. Hint: {hint}\n"
                f"Let's go over the bite once more: {reteach}\n"
                f"I don't have a fresh variant of this check, so "
                f"I'll hand {self.bite.skill} to the diagnosis "
                f"step rather than ask the same question twice.")
        self._hinted = True
        self._pending = {
            "question": variant.strip(),
            "bite_skill": self.bite.skill,
            "attempt": self._attempts + 1,
            "variant": self._attempts + 1,
            "label": "tutor check",
            "diagnostic": bool(getattr(self.bite, "diagnostic",
                                       False)),
        }
        return self._reply(
            f"Not quite. Hint: {hint}\n"
            f"Let's go over the bite once more: {reteach}\n"
            f"Try this one: {variant.strip()}")

    def _reply(self, reply):
        return {
            "reply": reply,
            "status": self._status,
            "outcome": dict(self._outcome)
                        if self._outcome else None,
            "pending": dict(self._pending)
                       if self._pending else None,
            "grade": dict(self._grade) if self._grade else None,
            "mastery": dict(self._mastery)
                       if self._mastery else None,
        }

    # -- snapshot (B21 seam: the tutor driver persists this) --------
    def snapshot(self):
        """Plain-data snapshot of the loop's state."""
        return {
            "skill": self.bite.skill,
            "bite": self.bite.bite,
            "check": self.bite.check,
            "gold": self.bite.gold,
            "rubric": self.bite.rubric,
            "reinforce": getattr(self.bite, "reinforce", None),
            "diagnostic": bool(getattr(self.bite, "diagnostic",
                                       False)),
            "status": self._status,
            "attempts": self._attempts,
            "hinted": self._hinted,
            "pending": dict(self._pending)
                       if self._pending else None,
            "outcome": dict(self._outcome)
                       if self._outcome else None,
        }

    @classmethod
    def from_snapshot(cls, data, model, *, judge=None,
                      variant_fn=None):
        """Rebuild a session from snapshot() (judge and
        variant_fn are re-injected by the caller)."""
        bite = Bite(
            skill=data["skill"], bite=data["bite"],
            check=data["check"], gold=data["gold"],
            rubric=data["rubric"],
            reinforce=data.get("reinforce"),
            diagnostic=bool(data.get("diagnostic", False)))
        session = cls(bite, model, judge=judge,
                      variant_fn=variant_fn)
        session._status = data.get("status", "not_started")
        session._attempts = int(data.get("attempts", 0))
        session._hinted = bool(data.get("hinted", False))
        session._pending = (dict(data["pending"])
                            if data.get("pending") else None)
        session._outcome = (dict(data["outcome"])
                            if data.get("outcome") else None)
        return session
