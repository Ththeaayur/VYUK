"""U5 — Socratic mode (tutor programme, 2026-11-08).

Lab leg only. The real-Windows field leg — a real MA 105 Socratic
session on his machine, hinted in order, never leaking the answer,
with "I don't know" dropping to the prerequisite — is OWED, not this
batch.

Gap proved first (audit T5): U3's hint is a SINGLE nudge — the
rubric's first clause (``hint_for``, ``core/brain/check_loop.py``),
no ladder state, no levels. No tutor policy file exists anywhere in
the tree. No solver/speaker split exists: ``CheckSession`` holds the
bite INCLUDING the gold (``self.bite.gold``) and its ``snapshot()``
serialises the gold — the same object that grades also produces the
replies, so the speaking path can see the gold. Zero
"socratic"/"hint_ladder" hits in ``core/``.

LOPHOLE FAMILY — "a tutor that tells instead of asking": every wrong
answer gets told (or leaked) instead of questioned, so the student
never has to think, and a spoken slip can't be unsaid. ONE design
closes the class: Socratic mode as CODE STATE, not prompting — a
3-level hint ladder (L1 point -> L2 principle -> L3 bottom-out +
"now explain why") held as explicit ladder state per check; a
human-editable tutor policy file (the pedagogy lives in
``TUTOR_POLICY.md``, not buried in prompts); and a solver/speaker
SPLIT (the Aristotle pattern — licence unverified, pattern only):
the solver verifies against the gold and returns ONLY verifier
metadata ``{is_correct, error_location, hint_suggestion}``; the
speaker holds metadata only, never the gold and never the worked
solution, so it CANNOT leak the answer even under pressure.

The split, precisely: the Speaker's ONLY input is a
``SpeakerPacket`` — a frozen dataclass with no field that can carry
the gold. The Solver builds every string in the packet and
gold-guards each one (U3's ``_contains_gold``); the Speaker renders
packets and holds no bite, no gold, no solution. The Speaker cannot
self-guard its output — it must not know the gold — so the guard
lives in the Solver, which is the only component that touches the
gold. The one exception is the persisted snapshot (B21 seam): like
U3's snapshot it carries the gold, because the inner check session
needs it to resume after a restart. A snapshot is solver-side state,
never a speaking context, and the Speaker never reads it.

The ladder and the loop: the Socratic session WRAPS U3's
``CheckSession`` (it does not change it) and drives it. A wrong
answer advances the ladder one level; the spoken words "hint" /
"show me" / "I don't know" (from the policy file) advance it, jump
to the bottom, or drop to the prerequisite between attempts — none
of them consumes a graded attempt or grades anything. The loop's
grading, retry bound, and mastery writes stay U3's.

The hinted-flag seam: ``CheckSession`` records a correct answer as
an unhinted transfer unless ITS OWN wrong-answer path produced a
variant. A ladder hint given between attempts ("hint", then a
correct answer) would therefore count as a CLEAN transfer — a
loophole in the mastery bar. The session closes it with a tiny
model proxy (``_HintedModel``): ``CheckSession`` only ever calls
``record_graded`` on its model, so the proxy ORs in the session's
ladder-used flag. The write still goes through U1's
``record_graded`` — the proxy never touches the record itself.

Teach-back: after the bottom of the ladder, a correct answer does
NOT end the check — the session asks "now explain why". The
explanation is graded by the judge against the principle, or (no
judge) by rubric keyword overlap; it writes mastery once, hinted
(never a clean transfer). Two failed explanations hand the skill to
diagnosis. When the policy sets ``teach_back: false``, the
bottom-out answer ends the check without the explanation step.

"I don't know": the session drops to the prerequisite — the bite's
``reinforce`` skill (or its first ``requires`` entry when the
reinforce is the bite itself) — and returns the U4 back-track
record ``{"skill", "plan_topic", "question"}`` with the library's
diagnostic question for that skill when one exists. The check is
parked, not graded; the driver runs the prerequisite check.

REJECTED alternatives (recorded, not carried forward):
- A prompt that says "be Socratic" (prompt-only tutors leak 80-90%
  under pressure — the Aristotle finding; a spoken leak can't be
  unsaid). The ladder and the split are code, not a prompt.
- A hint button / click-gated ladder (the research ladders are all
  click-gated — voice needs the spoken escalation protocol plus
  per-check ladder state across turns).
- Letting the speaker see the gold (any path where the speaking
  context contains the gold is a leak path — the split is
  structural, not a rule in a prompt).
- Burying the pedagogy in prompts (uneditable, unverifiable — the
  policy file is the human's handle on the tutor's behaviour).
- Re-implementing the check loop for Socratic mode (the loop's
  grading, bound, and mastery writes are U3's — the session wraps
  them; duplicating them would fork the grading contract).
- Forcing bites through ``plan_approval.approve_plan`` (U2's
  boundary, unchanged: that store is defined over confirm-gated
  tool calls; bites never pop).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import student_model as sm
from .check_loop import (
    CheckSession,
    _contains_gold,
    _GENERIC_HINT,
    _GENERIC_RETEACH,
    grade_answer,
    hint_for,
)
from .error_diagnosis import Diagnoser
from .lesson_planner import Bite

POLICY_FILENAME = "TUTOR_POLICY.md"

# The ladder has exactly three levels. The policy file can rename
# the escalation words and switch teach-back off; it cannot change
# the number of levels (the bottom-out + teach-back contract is
# defined over L3).
DEFAULT_POLICY = {
    "hint_word": "hint",
    "show_word": "show me",
    "dont_know": "i don't know",
    "teach_back": True,
}

MAX_TEACH_BACK_ATTEMPTS = 2

_GENERIC_PRINCIPLE = (
    "The principle is the idea this bite turns on — what must "
    "the answer show, and what would make it wrong?")
_GENERIC_STEP = (
    "Let's go over the step itself, one piece at a time.")
_GENERIC_QUESTION = (
    "Let's step back — what is this skill, in your own words?")

_STOPWORDS = frozenset(
    "that with this from must show what when which their there "
    "have been will would could should about into over under "
    "your you the and for are but not all any can had her his "
    "one two its our out use used using answer".split())


def _norm(text) -> str:
    """Lowercase, collapse whitespace — the escalation match."""
    return " ".join(str(text or "").split()).strip().lower()


def _content_words(text):
    """Significant words (4+ letters, no stopwords) for the
    teach-back keyword check."""
    words = re.findall(r"[a-z]{4,}", _norm(text))
    return {w for w in words if w not in _STOPWORDS}


# ------------------------------------------------------------------
# Tutor policy file
# ------------------------------------------------------------------

def _default_policy_path() -> Path:
    # core/brain/socratic.py -> parents[2] is the tree root.
    return Path(__file__).resolve().parents[2] / POLICY_FILENAME


def _policy_block(text: str):
    match = re.search(r"```policy\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1) if match else None


def _normalise_policy(raw: dict) -> dict:
    policy = dict(DEFAULT_POLICY)
    for key in ("hint_word", "show_word", "dont_know"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            policy[key] = _norm(value)
    teach_back = raw.get("teach_back")
    if isinstance(teach_back, bool):
        policy["teach_back"] = teach_back
    elif isinstance(teach_back, str):
        if teach_back.strip().lower() in ("true", "yes", "1"):
            policy["teach_back"] = True
        elif teach_back.strip().lower() in ("false", "no", "0"):
            policy["teach_back"] = False
    return policy


def load_policy(path=None):
    """Read the tutor policy file. Returns (policy dict, source).

    ``source`` is "file" when the file was read and its ``policy``
    block parsed, else "default" (missing file, unreadable file,
    or no ``policy`` block — the session runs on defaults and
    says so). Unknown keys are ignored; a bad value keeps its
    default. A policy change changes behaviour without a code
    change — that is the point of the file.
    """
    target = Path(path) if path is not None else _default_policy_path()
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return dict(DEFAULT_POLICY), "default"
    block = _policy_block(text)
    if block is None:
        return dict(DEFAULT_POLICY), "default"
    raw = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        raw[key.strip()] = value.strip()
    return _normalise_policy(raw), "file"


# ------------------------------------------------------------------
# Hint ladder (code state, per check)
# ------------------------------------------------------------------

class HintLadder:
    """The 3-level hint ladder as explicit state.

    ``hints`` is [L1 point, L2 principle, L3 bottom-out step] —
    gold-guarded texts built by the Solver. ``level`` is 0 (no
    hint given yet) through 3. Levels advance in order, never
    skip; ``jump_to_bottom`` is the only way to reach L3
    without climbing. State persists across turns within a
    check and resets on ``start()``.
    """

    def __init__(self, hints):
        self._hints = [str(h) for h in hints]
        self._level = 0

    @property
    def level(self) -> int:
        return self._level

    def current(self):
        """The hint at the current level, or None before L1."""
        if self._level <= 0:
            return None
        return self._hints[self._level - 1]

    def advance(self):
        """Climb one level; returns (level, hint text). At the
        bottom already, returns L3 again."""
        if self._level < 3:
            self._level += 1
        return self._level, self._hints[self._level - 1]

    def jump_to_bottom(self):
        """The "show me" step: straight to L3."""
        self._level = 3
        return self._level, self._hints[2]

    def at_bottom(self) -> bool:
        return self._level >= 3

    def reset(self):
        self._level = 0


# ------------------------------------------------------------------
# Solver (verification side) and Speaker (talking side)
# ------------------------------------------------------------------

def _principle(bite) -> str:
    """L2: the principle the bite turns on — the rubric stated
    in full. Gold-guarded; falls back to a generic principle
    when the rubric would reveal the gold."""
    rubric = " ".join(str(getattr(bite, "rubric", "") or "").split())
    if not rubric:
        return _GENERIC_PRINCIPLE
    text = f"The principle this bite turns on is: {rubric}"
    if _contains_gold(text, getattr(bite, "gold", "")):
        return _GENERIC_PRINCIPLE
    return text


@dataclass(frozen=True)
class SpeakerPacket:
    """The ONLY thing the Speaker accepts. No field can carry
    the gold: the Solver builds every string in it and
    gold-guards each one before packing. ``correct`` is the
    verdict; ``error_location`` is a short tag ("value" /
    "unit" / "reasoning") or None; the rest are display strings
    the Solver has already cleared."""

    correct: bool
    error_location: str | None = None
    hint: str | None = None
    reteach: str | None = None
    question: str | None = None
    note: str | None = None


class Solver:
    """Verification side: holds the bite (including the gold)
    and returns ONLY verifier metadata — never the gold, never
    the worked solution. Every string it hands to the Speaker
    passes through ``_guard`` first."""

    def __init__(self, bite, judge=None):
        self._bite = bite
        self._judge = judge

    def hint_texts(self):
        """[L1 point, L2 principle, L3 bottom-out step]."""
        l1 = hint_for(self._bite)
        l2 = _principle(self._bite)
        step = getattr(self._bite, "bite", "") or ""
        step = " ".join(str(step).split()).strip()
        if not step or _contains_gold(step, self._bite.gold):
            step = _GENERIC_STEP
        return [l1, l2, step]

    def verify(self, answer):
        """Standalone verify (teach-back grading uses it too):
        metadata {is_correct, error_location, hint_suggestion}."""
        grade = grade_answer(answer, self._bite.gold,
                             self._bite.rubric, judge=self._judge)
        return self.metadata_from_grade(grade, hint=None)

    def metadata_from_grade(self, grade, hint):
        """Verifier metadata from a U3 grade dict. The judge's
        deduction is NOT passed on — it is unverified prose
        that could leak the gold; the speaker gets the location
        tag, never the prose."""
        reason = str((grade or {}).get("reason", ""))
        if "unit" in reason:
            error_location = "unit"
        elif "value" in reason:
            error_location = "value"
        elif (grade or {}).get("method") == "judge":
            error_location = "reasoning"
        else:
            error_location = None
        return {
            "is_correct": bool((grade or {}).get("correct")),
            "error_location": error_location,
            "hint_suggestion": hint,
        }

    def verify_teach_back(self, text):
        """Grade the "explain why": the judge against the
        principle first; with no judge, rubric keyword overlap.
        Returns (correct, reason). Never raises."""
        if not isinstance(text, str) or not text.strip():
            return False, "empty_explanation"
        given = text.strip()
        if self._judge is not None:
            try:
                verdict = self._judge(given, _principle(self._bite),
                                      self._bite.rubric)
            except Exception:  # noqa: BLE001 — a judge that
                # raises proves nothing; fall through to the
                # deterministic check.
                verdict = None
            if isinstance(verdict, dict) \
                    and isinstance(verdict.get("correct"), bool):
                return bool(verdict["correct"]), (
                    "judge" if verdict["correct"] else "judge_no")
        rubric_words = _content_words(self._bite.rubric)
        if rubric_words and _content_words(given) & rubric_words:
            return True, "keyword_overlap"
        return False, "no_overlap"

    def reask_note(self, given):
        """The D-T3 re-ask. Echoes the student's own hearing
        when it does not contain the gold; a hearing that
        contains the gold gets a generic re-ask (echoing it
        would confirm the answer)."""
        if isinstance(given, str) and given.strip() \
                and not _contains_gold(given, self._bite.gold):
            return (f"I didn't quite catch that — did you say "
                    f"'{given.strip()}'? Say it again and I'll "
                    "check it.")
        return ("I didn't quite catch that — say it again and "
                "I'll check it.")

    def build_packet(self, grade, *, hint=None, reteach=None,
                     question=None, note=None):
        """Pack a speaker packet, gold-guarding every string.
        A hint/reteach that would reveal the gold falls back to
        the generic line; a question that would reveal the gold
        (the U3 variant residual — a caller-supplied variant is
        unguarded in the raw loop) falls back to the bite's own
        check text. A note that would reveal the gold is
        dropped."""
        metadata = self.metadata_from_grade(grade, hint)
        gold = self._bite.gold

        def _guarded(text, fallback):
            if not text:
                return fallback
            if _contains_gold(text, gold):
                return fallback
            return text

        return SpeakerPacket(
            correct=metadata["is_correct"],
            error_location=metadata["error_location"],
            hint=_guarded(metadata["hint_suggestion"], _GENERIC_HINT)
            if metadata["hint_suggestion"] else None,
            reteach=_guarded(reteach, _GENERIC_RETEACH)
            if reteach else None,
            question=_guarded(question, self._bite.check)
            if question else None,
            note=_guarded(note, None) if note else None,
        )


class Speaker:
    """The talking side: renders packets. Holds no bite, no
    gold, no worked solution — it cannot leak what it never
    had. It renders ONLY SpeakerPackets; anything else is
    refused outright (a dict carrying a "gold" key never
    reaches the student through this class)."""

    def render(self, packet):
        if not isinstance(packet, SpeakerPacket):
            raise TypeError(
                "the speaker only renders SpeakerPackets — "
                "it never accepts a bite, a gold answer, or a "
                "worked solution")
        if packet.correct:
            parts = ["Right — that checks out."]
            if packet.note:
                parts.append(packet.note)
            return " ".join(parts)
        parts = []
        if packet.error_location == "unit":
            parts.append("The value is right but the unit is wrong.")
        elif packet.error_location == "value":
            parts.append("That value doesn't match — check the "
                         "working, not the answer.")
        elif packet.error_location == "reasoning":
            parts.append("The reasoning doesn't check out yet.")
        else:
            parts.append("Not quite.")
        if packet.hint:
            parts.append(f"Hint: {packet.hint}")
        if packet.reteach:
            parts.append("Let's go over the bite once more: "
                         f"{packet.reteach}")
        if packet.question:
            parts.append(f"Try this one: {packet.question}")
        if packet.note:
            parts.append(packet.note)
        return "\n".join(parts)


# ------------------------------------------------------------------
# The hinted-flag seam (keeps the mastery bar honest)
# ------------------------------------------------------------------

class _HintedModel:
    """CheckSession only ever calls ``record_graded`` on its
    model. This proxy ORs in the session's ladder-used flag, so
    a correct answer after ANY ladder hint is recorded hinted —
    the transfer bar stays honest even when the hint came from
    the ladder between attempts, not from the loop's own
    wrong-answer path. The write still goes through U1's
    ``record_graded``; the proxy never touches the record."""

    def __init__(self, model, ladder_used):
        self._model = model
        self._ladder_used = ladder_used

    def record_graded(self, skill_id, correct, *, novel=False,
                      hinted=False, confidence=1.0,
                      source="check"):
        return self._model.record_graded(
            skill_id, correct, novel=novel,
            hinted=hinted or bool(self._ladder_used()),
            confidence=confidence, source=source)


# ------------------------------------------------------------------
# Socratic session (the orchestrator)
# ------------------------------------------------------------------

class SocraticSession:
    """One bite, taught Socratically.

    Wraps U3's CheckSession (grading, retry bound, mastery
    writes stay U3's) and drives the ladder + the spoken
    escalation protocol around it. ``respond()`` is the single
    entry for student utterances: escalation words from the
    policy file are intercepted (no grade, no attempt); anything
    else is a check answer, graded by the loop and rendered by
    the Speaker from solver metadata only. After the bottom of
    the ladder, a correct answer opens the teach-back phase
    ("now explain why") before the check can end.
    """

    def __init__(self, bite, model, *, judge=None, variant_fn=None,
                 policy=None, policy_path=None, diagnoser=None):
        for field in ("skill", "check", "gold", "rubric"):
            value = getattr(bite, field, None)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"socratic mode needs a bite with a usable "
                    f"{field!r}")
        self.bite = bite
        self._model = model
        if policy is not None:
            self._policy = _normalise_policy(dict(policy))
            self._policy_source = "given"
        else:
            self._policy, self._policy_source = load_policy(
                policy_path)
        self._solver = Solver(bite, judge=judge)
        self._ladder = HintLadder(self._solver.hint_texts())
        self._speaker = Speaker()
        self._diagnoser = diagnoser if diagnoser is not None \
            else Diagnoser()
        self._ladder_used = False
        self._bottomed_out = False
        self._teach_back_attempts = 0
        self._status = "not_started"
        self._pending = None
        self._outcome = None
        self._proxy = _HintedModel(model, lambda: self._ladder_used)
        self._check = CheckSession(bite, self._proxy, judge=judge,
                                   variant_fn=variant_fn)

    # -- accessors ---------------------------------------------------
    @property
    def status(self):
        """not_started | checking | teach_back | done."""
        return self._status

    @property
    def ladder_level(self) -> int:
        return self._ladder.level

    @property
    def policy(self) -> dict:
        return dict(self._policy)

    @property
    def policy_source(self) -> str:
        return self._policy_source

    @property
    def outcome(self):
        return dict(self._outcome) if self._outcome else None

    def pending(self):
        return dict(self._pending) if self._pending else None

    # -- start ---------------------------------------------------------
    def start(self):
        """Ask the bite's check; the lesson pauses (B15 shape)."""
        if self._status != "not_started":
            return dict(self._pending) if self._pending else None
        self._status = "checking"
        self._ladder.reset()
        self._pending = self._check.start()
        return dict(self._pending) if self._pending else None

    # -- the single entry ----------------------------------------------
    def respond(self, text, *, confidence=1.0):
        """One student utterance. Returns {"reply", "status",
        "outcome", "pending", "backtrack", "ladder_level"} —
        always all keys. Never raises for a bad utterance."""
        if self._status == "done":
            return self._result(
                "This check is finished — start a new check for "
                "the next bite.")
        if self._status == "teach_back":
            return self._teach_back_answer(text,
                                           confidence=confidence)
        if self._status != "checking" or self._pending is None:
            return self._result(
                "Start the check first — ask the bite's check "
                "question, then answer it.")
        if not isinstance(text, str) or not text.strip():
            return self._result(
                "Give me your answer and I'll check it against "
                "the rubric.")
        given = text.strip()
        norm = _norm(given)
        if norm == self._policy["dont_know"]:
            return self._backtrack()
        if norm == self._policy["hint_word"]:
            return self._escalate_hint()
        if norm == self._policy["show_word"]:
            return self._escalate_show()
        result = self._check.answer(given, confidence=confidence)
        return self._render_check_result(result, given)

    # -- escalation ------------------------------------------------------
    def _escalate_hint(self):
        """Next ladder level, between attempts. No grade, no
        attempt consumed — but the ladder is now used, so the
        next correct answer is recorded hinted (the proxy)."""
        level, hint = self._ladder.advance()
        self._ladder_used = True
        note = None
        if level >= 3:
            self._bottomed_out = True
            note = ("That's the bottom of the ladder — the step "
                    "itself. Answer the check, and then I'll ask "
                    "you to explain why it works.")
        packet = self._solver.build_packet(
            {"correct": False, "reason": "", "deduction": ""},
            hint=hint,
            question=(self._pending or {}).get("question"),
            note=note)
        return self._result_packet(packet)

    def _escalate_show(self):
        """Straight to the bottom: the step, shown. The
        teach-back will follow a correct answer."""
        _, hint = self._ladder.jump_to_bottom()
        self._ladder_used = True
        self._bottomed_out = True
        packet = self._solver.build_packet(
            {"correct": False, "reason": "", "deduction": ""},
            hint=hint,
            question=(self._pending or {}).get("question"),
            note=("That's the step, shown. Answer the check — "
                  "then I'll ask you to explain why it works."))
        return self._result_packet(packet)

    def _backtrack(self):
        """'I don't know' drops to the prerequisite (the U4
        back-track rule). The check is parked, not graded; the
        driver runs the prerequisite's diagnostic question.
        Returns the back-track record naming the skill."""
        target = getattr(self.bite, "reinforce", None) or None
        if not target or target == self.bite.skill:
            requires = list(getattr(self.bite, "requires", ()) or ())
            target = requires[0] if requires else self.bite.skill
        question = None
        try:
            entries = self._diagnoser.library.entries_for(target)
        except Exception:  # noqa: BLE001 — no library entry is
            # a generic step-back, never a crash.
            entries = []
        if entries:
            question = entries[0].get("diagnostic_question")
        if not question:
            question = _GENERIC_QUESTION.replace(
                "this skill", target)
        backtrack = {"skill": target, "plan_topic": target,
                     "question": question}
        packet = self._solver.build_packet(
            {"correct": False, "reason": "", "deduction": ""},
            question=question,
            note=(f"Let's step back to {target} first — the "
                  f"prerequisite, before this bite."))
        result = self._result_packet(packet)
        result["backtrack"] = backtrack
        return result

    # -- rendering a graded check result ---------------------------------
    def _render_check_result(self, result, given):
        grade = result.get("grade")
        if grade is None:
            # Low-confidence re-ask (D-T3): no grade, no mastery,
            # no attempt consumed. The note echoes the student's
            # own hearing unless it contains the gold.
            packet = self._solver.build_packet(
                {"correct": False, "reason": "", "deduction": ""},
                question=(self._pending or {}).get("question"),
                note=self._solver.reask_note(given))
            return self._result_packet(packet)
        if grade.get("correct"):
            if self._bottomed_out and self._policy["teach_back"]:
                # Bottom-out answer: the loop is done, but the
                # check is not — "now explain why" first.
                self._status = "teach_back"
                self._teach_back_attempts = 0
                packet = self._solver.build_packet(
                    {"correct": True, "reason": "", "deduction": ""},
                    question=("Now explain why — in your own "
                              "words, why does that step work?"),
                    note=("You got the step after the bottom of "
                          "the ladder. Before this counts, explain "
                          "why it works."))
                return self._result_packet(packet)
            self._status = "done"
            self._pending = None
            self._outcome = result.get("outcome")
            mastery = result.get("mastery") or {}
            transfers = mastery.get("transfers", 0)
            transfer_word = ("transfer" if transfers == 1
                             else "transfers")
            if mastery.get("mastered"):
                note = (f"That's {transfers} clean transfers, so "
                        f"{self.bite.skill} counts as mastered.")
            elif self._ladder_used:
                # Hinted correct answer: the record is honest
                # (the proxy forces hinted, so no transfer was
                # earned) — the spoken note must be too. Never
                # claim a clean transfer that was not earned.
                note = (f"With a hint behind it, so it moves the "
                        f"estimate but doesn't count as a clean "
                        f"transfer. You have {transfers} clean "
                        f"{transfer_word} on record for "
                        f"{self.bite.skill}.")
            else:
                # Clean correct answer: phrase from the actual
                # count, never a hardcoded "one".
                remaining = max(0, 2 - transfers)
                note = (f"That's {transfers} clean {transfer_word} "
                        f"on record — {remaining} more on a fresh "
                        f"variant and {self.bite.skill} counts as "
                        f"mastered.")
            packet = self._solver.build_packet(
                grade, note=note)
            return self._result_packet(packet, outcome=self._outcome)
        # Wrong graded answer: the ladder climbs one level (the
        # loop's own hint IS L1, so the first wrong answer lands
        # on L1, the second on L2, the third on the bottom).
        level, hint = self._ladder.advance()
        self._ladder_used = True
        if level >= 3:
            self._bottomed_out = True
        outcome = result.get("outcome")
        if outcome is not None and outcome.get("outcome") == \
                "handed_to_diagnosis":
            self._status = "done"
            self._pending = None
            self._outcome = outcome
            packet = self._solver.build_packet(
                grade, hint=hint,
                note=("That's still not landing — I'm handing "
                      f"{self.bite.skill} to the diagnosis step "
                      "so we can find out why, instead of "
                      "guessing. Nothing is marked as known."))
            return self._result_packet(packet, outcome=outcome)
        pending = result.get("pending") or {}
        self._pending = dict(pending) if pending else self._pending
        reteach = None if level >= 3 else getattr(
            self.bite, "bite", None)
        note = None
        if level >= 3:
            note = ("That's the bottom of the ladder — the step "
                    "itself. Answer the check, and then I'll ask "
                    "you to explain why it works.")
        packet = self._solver.build_packet(
            grade, hint=hint, reteach=reteach,
            question=pending.get("question"), note=note)
        return self._result_packet(packet)

    # -- teach-back --------------------------------------------------------
    def _teach_back_answer(self, text, *, confidence=1.0):
        if not isinstance(text, str) or not text.strip():
            return self._result(
                "Explain why, in your own words — why does that "
                "step work?")
        given = text.strip()
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < sm.CONFIDENCE_FLOOR:
            return self._result(
                self._solver.reask_note(given))
        correct, reason = self._solver.verify_teach_back(given)
        # One mastery write per graded teach-back, hinted —
        # never a clean transfer.
        mastery = self._model.record_graded(
            self.bite.skill, correct, novel=True, hinted=True,
            confidence=conf, source="teach_back")
        self._teach_back_attempts += 1
        if correct:
            self._status = "done"
            self._pending = None
            mastered = bool(mastery.get("mastered"))
            self._outcome = {
                "outcome": "mastered" if mastered
                           else "still_learning",
                "skill": self.bite.skill,
                "teach_back": "passed",
                "attempts": self._teach_back_attempts,
                "mastery": dict(mastery),
            }
            packet = self._solver.build_packet(
                {"correct": True, "reason": "", "deduction": ""},
                note=("That's the why — you can explain the step, "
                      "not just do it."))
            return self._result_packet(packet, outcome=self._outcome)
        if self._teach_back_attempts >= MAX_TEACH_BACK_ATTEMPTS:
            self._status = "done"
            self._pending = None
            self._outcome = {
                "outcome": "handed_to_diagnosis",
                "skill": self.bite.skill,
                "teach_back": "failed",
                "reason": "teach_back_failed",
                "reinforce": getattr(self.bite, "reinforce", None),
            }
            packet = self._solver.build_packet(
                {"correct": False, "reason": "", "deduction": ""},
                note=("You can do the step but the why isn't "
                      "landing yet — I'm handing "
                      f"{self.bite.skill} to the diagnosis step "
                      "instead of guessing."))
            return self._result_packet(packet, outcome=self._outcome)
        packet = self._solver.build_packet(
            {"correct": False, "reason": "", "deduction": ""},
            question=("Now explain why — in your own words, why "
                      "does that step work?"),
            note=("Not quite the why yet — what is the step doing, "
                  "and why does it work here?"))
        return self._result_packet(packet)

    # -- reply assembly ------------------------------------------------------
    def _result_packet(self, packet, outcome=None):
        reply = self._speaker.render(packet)
        return {
            "reply": reply,
            "status": self._status,
            "outcome": dict(outcome)
                        if outcome is not None
                        else (dict(self._outcome)
                              if self._outcome else None),
            "pending": dict(self._pending)
                       if self._pending else None,
            "backtrack": None,
            "ladder_level": self._ladder.level,
        }

    def _result(self, reply):
        return {
            "reply": reply,
            "status": self._status,
            "outcome": dict(self._outcome)
                        if self._outcome else None,
            "pending": dict(self._pending)
                       if self._pending else None,
            "backtrack": None,
            "ladder_level": self._ladder.level,
        }

    # -- snapshot (B21 seam: the tutor driver persists this) ------------------
    def snapshot(self):
        """Plain-data snapshot of the session's state. Carries the
        gold (like U3's snapshot — the inner check session needs
        it to resume after a restart). Solver-side state only;
        the Speaker never reads a snapshot."""
        return {
            "skill": self.bite.skill,
            "bite": self.bite.bite,
            "check": self.bite.check,
            "gold": self.bite.gold,
            "rubric": self.bite.rubric,
            "reinforce": getattr(self.bite, "reinforce", None),
            "requires": list(getattr(self.bite, "requires", ()) or ()),
            "diagnostic": bool(getattr(self.bite, "diagnostic",
                                       False)),
            "policy": dict(self._policy),
            "status": self._status,
            "ladder_level": self._ladder.level,
            "ladder_used": self._ladder_used,
            "bottomed_out": self._bottomed_out,
            "teach_back_attempts": self._teach_back_attempts,
            "pending": dict(self._pending)
                       if self._pending else None,
            "outcome": dict(self._outcome)
                       if self._outcome else None,
            "check_session": self._check.snapshot(),
        }

    @classmethod
    def from_snapshot(cls, data, model, *, judge=None,
                      variant_fn=None, diagnoser=None):
        """Rebuild a session from snapshot() (judge and
        variant_fn are re-injected by the caller)."""
        bite = Bite(
            skill=data["skill"], bite=data["bite"],
            check=data["check"], gold=data["gold"],
            rubric=data["rubric"],
            reinforce=data.get("reinforce"),
            requires=tuple(data.get("requires", ())),
            diagnostic=bool(data.get("diagnostic", False)))
        session = cls(bite, model, judge=judge,
                      variant_fn=variant_fn,
                      policy=data.get("policy"),
                      diagnoser=diagnoser)
        session._status = data.get("status", "not_started")
        session._ladder_used = bool(data.get("ladder_used", False))
        session._bottomed_out = bool(data.get("bottomed_out", False))
        session._teach_back_attempts = int(
            data.get("teach_back_attempts", 0))
        session._pending = (dict(data["pending"])
                            if data.get("pending") else None)
        session._outcome = (dict(data["outcome"])
                            if data.get("outcome") else None)
        # Rebuild the ladder level (hint texts are rebuilt from
        # the bite, so the level is the only state to restore).
        for _ in range(int(data.get("ladder_level", 0))):
            session._ladder.advance()
        check_data = data.get("check_session")
        if isinstance(check_data, dict):
            session._check = CheckSession.from_snapshot(
                check_data, session._proxy, judge=judge,
                variant_fn=variant_fn)
        return session
