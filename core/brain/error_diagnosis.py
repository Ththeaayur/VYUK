"""U4 — error diagnosis: name the misconception behind a wrong answer (tutor programme, 2026-11-08).

Lab leg only. The real-Windows field leg — a real MA 105 wrong
answer on his machine, classified into a named misconception
from his slips, back-tracked to its prerequisite, with the
feedback line never revealing the answer — is OWED, not this
batch.

Gap proved first (audit T4): when he gets a check wrong,
nothing says WHY. U3's check loop retries (hint -> re-teach ->
re-check variant, bounded at 3) and then hands the skill over
honestly as ``handed_to_diagnosis`` (``core/brain/check_loop.py:469``
retry_bound, :494 no_fresh_variant) — but nothing consumes that
handover. U2's reinforce edge (``core/brain/lesson_planner.py:129-142``)
names a fallback skill per bite, but nothing asks a diagnostic
question and nothing back-tracks to the prerequisite that is
actually missing. A grep for "misconception" in ``core/`` finds
only forward references in docstrings (``student_model.py:11``,
``check_loop.py:50``/``:97``); the "diagnos" hits are network /
developer diagnostics, never tutor error diagnosis.

LOPHOLE FAMILY — "a wrong answer with no why": every failure is
treated the same (retry the same kind of question), so a student
stuck on a missing prerequisite keeps failing the same bite
instead of being sent back to the thing he actually doesn't
know. ONE design closes the class: a NAMED misconception
library — each entry names the misconception (surface error ->
underlying cause), carries ONE diagnostic question that
separates this misconception from the others, names the
prerequisite skill to back-track to, and carries a spoken
feedback line that names the error without revealing the
answer. The diagnoser consumes U3's ``handed_to_diagnosis``
outcome, classifies the wrong answer against the library
(surface-error match first, then the diagnostic question when
the surface match is ambiguous), and returns the back-track
(the U2 reinforce edge names the fallback skill; the library
names the prerequisite to re-teach). Entries are DATA the
diagnoser reads, so a new misconception is added as data, not
as code.

REJECTED alternatives (recorded, not carried forward):
- Retrying the same bite harder: the U3 loop already does
  that — diagnosis is what happens when retry has failed.
- A free-form LLM "why was that wrong" with no library:
  unverifiable, unrepeatable, and it leaks answers under
  pressure (the research's Aristotle finding: prompt-only
  tutors leak 80-90%).
- Forcing every error into a category: an error that matches
  nothing is recorded honestly as unclassified, never squeezed
  into the nearest entry — a wrong diagnosis teaches the
  wrong fix.
- Vendoring CTAT or Mathmatix code: CTAT is HARD-BLOCKED
  (custom CMU licence, noncommercial research only, no
  modification/derivatives); Mathmatix has no LICENSE file.
  Patterns / data-model only, never code. The library is
  hand-authored in-tree from his slips — the only honest
  source for university calculus anyway (the research found
  no calculus diagnosis anywhere).
- Auto-growing the library from unclassified errors: a new
  entry is added when a real wrong answer matches no existing
  entry AND the recorded error is curated into an entry.
  Promotion is a curation step, never automatic — an
  auto-promoted entry would be an unverified diagnosis.

HOW THE DIAGNOSER CLASSIFIES (``diagnose``):
1. Surface match: every library entry scoped to the failing
   skill is scored on its markers (numeric_ratio — the
   student's value is off the gold by a clean factor, weight
   2; grade_reason / grade_method — the U3 grade's own
   verdict, weight 1; answer_contains — a distinctive phrase
   in the answer, weight 2). One clear leader -> classified.
2. Ambiguous: two or more entries tie at the top -> the
   diagnosis returns the tied candidates plus the diagnostic
   question of the top candidate; ``resolve_diagnostic``
   settles it from the student's answer to that question
   (each entry carries diagnostic_markers — the phrases whose
   presence confirms it). A diagnostic answer that confirms
   none of them, or several, stays ambiguous honestly.
3. Unclassified: nothing scores -> the error is recorded
   (answer, skill, attempt count, grade reason) for library
   growth and the diagnosis says so, with the U2 reinforce
   edge as the fallback back-track. Never forced into a
   category, never silently dropped.

BACK-TRACK RULE: the diagnosis always names the prerequisite
skill to re-teach. On a repeat failure (the handover's
``attempts`` >= 2 — the student has already failed this check
more than once) ``backtrack_now`` is True: the tutor runs a
U2 lesson plan for the prerequisite topic instead of asking
another question. On a first failure the tutor asks the
diagnostic question first. The back-track is a plan the tutor
runs, not an improvised detour.

GOLD GUARD (U3's rule): a feedback line is checked against
the bite's gold with ``check_loop._contains_gold`` before it
is returned; a line that would reveal the gold is replaced
by the generic feedback line. The check loop's gold rule has
ONE definition — it is imported, not copied.

The library is seeded from HIS slips (D-T4: MA 105 first):
dropped factor / dropped integrand, and limits read off habit
instead of the region. It grows only from his real mistakes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from fractions import Fraction

from .check_loop import _contains_gold, _numbers

# Where unclassified errors are recorded for library growth:
# the same MemoryStore facts table U1 uses (never a second
# store). The store is injected (duck-typed: save_fact); with
# no store the records live in memory on the diagnoser.
UNCATEGORISED_CATEGORY = "diagnosis_unclassified"

# A repeat failure: the handover already carries >= 2 graded
# answers for this check. Back-track now instead of asking
# another question.
REPEAT_FAILURE_ATTEMPTS = 2

_GENERIC_QUESTION = (
    "Talk me through the step where it went wrong, one line "
    "at a time.")
_GENERIC_FEEDBACK = (
    "That didn't land — let's find the step where it went "
    "wrong, one line at a time.")

# Marker kinds an entry may carry. Each marker is a dict
# {"kind": ..., "value": ..., "weight": int}.
MARKER_KINDS = ("numeric_ratio", "grade_reason", "grade_method",
                "answer_contains")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text) -> str:
    """Lowercase, collapse whitespace — for marker matching."""
    return " ".join(str(text or "").lower().split())


def _skill_matches(entry_skills, skill: str) -> bool:
    """An entry applies to a skill when its skills list is empty
    (a general entry) or one of its skills equals the failing
    skill or is a prefix of it (a "ma105-" entry applies to
    every MA 105 skill)."""
    if not entry_skills:
        return True
    skill = str(skill or "")
    for listed in entry_skills:
        listed = str(listed or "")
        if not listed:
            continue
        if skill == listed or skill.startswith(listed):
            return True
    return False


# ------------------------------------------------------------------
# The library, seeded from his slips (D-T4: MA 105 first).
# ------------------------------------------------------------------

SEEDED_ENTRIES = [
    {
        "id": "ma105-dropped-factor",
        "name": "Dropped factor / dropped integrand",
        "skills": ["ma105-"],
        "surface": (
            "The answer is off the gold by a clean factor — a "
            "term of the integrand never made it into the "
            "calculation."),
        "cause": (
            "A factor of the integrand was dropped while setting "
            "up or evaluating the integral."),
        "markers": [
            {"kind": "numeric_ratio", "value": "1/2", "weight": 2},
            {"kind": "numeric_ratio", "value": "2", "weight": 2},
            {"kind": "numeric_ratio", "value": "1/3", "weight": 2},
            {"kind": "numeric_ratio", "value": "3", "weight": 2},
            {"kind": "grade_reason",
             "value": "value does not match the gold", "weight": 1},
        ],
        "diagnostic_question": (
            "Read the integrand back to me, factor by factor — "
            "is there one you left out?"),
        "diagnostic_markers": [
            "dropped", "left out", "forgot", "missed", "factor",
        ],
        "prerequisite": "ma105-integrand-setup",
        "feedback": (
            "The value is off by a clean factor — something in "
            "the integrand didn't make it into the calculation. "
            "Check the integrand term by term."),
    },
    {
        "id": "ma105-limits-off-habit",
        "name": "Limits read off habit instead of the region",
        "skills": ["ma105-"],
        "surface": (
            "The answer matches the integral over the remembered "
            "bounds, not the bounds of the region this problem "
            "actually describes."),
        "cause": (
            "The limits were read from habit — the usual range — "
            "instead of the region the problem describes."),
        "markers": [
            {"kind": "grade_reason",
             "value": "value does not match the gold", "weight": 1},
        ],
        "diagnostic_question": (
            "What region are you integrating over — what are its "
            "actual bounds, from the picture, not the usual ones?"),
        "diagnostic_markers": [
            "limit", "bound", "region", "from 0", "0 to",
        ],
        "prerequisite": "ma105-regions-and-limits",
        "feedback": (
            "The value doesn't match — and the limits are the "
            "first thing to check. What region does this problem "
            "actually describe?"),
    },
]


def validate_entry(entry):
    """Check one library entry's shape. (True, "") or
    (False, reason). An entry without a diagnostic question,
    a prerequisite, or a feedback line is not an entry."""
    if not isinstance(entry, dict):
        return False, "a library entry must be an object"
    for field in ("id", "name", "surface", "cause",
                  "diagnostic_question", "prerequisite", "feedback"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            return False, (f"entry {entry.get('id')!r} has no "
                           f"usable {field!r}")
    skills = entry.get("skills")
    if not isinstance(skills, list) or any(
            not isinstance(s, str) for s in skills):
        return False, (f"entry {entry.get('id')!r} 'skills' must "
                       f"be a list of strings")
    markers = entry.get("markers")
    if not isinstance(markers, list) or not markers:
        return False, (f"entry {entry.get('id')!r} needs at "
                       f"least one surface marker")
    for pos, marker in enumerate(markers):
        if not isinstance(marker, dict):
            return False, (f"entry {entry.get('id')!r} marker "
                           f"{pos} is not an object")
        if marker.get("kind") not in MARKER_KINDS:
            return False, (f"entry {entry.get('id')!r} marker "
                           f"{pos} has an unknown kind "
                           f"{marker.get('kind')!r}")
        if not isinstance(marker.get("value"), str) \
                or not marker["value"].strip():
            return False, (f"entry {entry.get('id')!r} marker "
                           f"{pos} has no usable value")
        weight = marker.get("weight")
        if not isinstance(weight, int) or isinstance(weight, bool) \
                or weight <= 0:
            return False, (f"entry {entry.get('id')!r} marker "
                           f"{pos} has no positive weight")
    dm = entry.get("diagnostic_markers")
    if not isinstance(dm, list) or not dm or any(
            not isinstance(m, str) or not m.strip() for m in dm):
        return False, (f"entry {entry.get('id')!r} needs at "
                       f"least one diagnostic marker")
    return True, ""


class MisconceptionLibrary:
    """The named-misconception library: entries are data.

    Seeded from his slips; new entries are added as data
    (``add_entry``) after curation — never auto-promoted from
    unclassified errors.
    """

    def __init__(self, entries=None):
        self._entries = []
        for entry in (SEEDED_ENTRIES if entries is None else entries):
            ok, error = validate_entry(entry)
            if not ok:
                raise ValueError(f"bad library entry: {error}")
            self._entries.append(dict(entry))

    def __len__(self):
        return len(self._entries)

    def entries(self):
        """All entries, in library order (seeded first)."""
        return [dict(e) for e in self._entries]

    def get(self, entry_id):
        """One entry by id, or None."""
        for entry in self._entries:
            if entry["id"] == entry_id:
                return dict(entry)
        return None

    def entries_for(self, skill):
        """Entries scoped to a skill, in library order."""
        return [dict(e) for e in self._entries
                if _skill_matches(e.get("skills"), skill)]

    def add_entry(self, entry):
        """Add one entry after curation. (True, "") or
        (False, reason) — a duplicate id or a malformed entry
        is refused, never silently merged."""
        ok, error = validate_entry(entry)
        if not ok:
            return False, error
        if self.get(entry["id"]) is not None:
            return False, (f"entry {entry['id']!r} already exists "
                           f"in the library")
        self._entries.append(dict(entry))
        return True, ""


def _marker_matches(marker, *, answer, gold, grade):
    """One surface marker against the evidence."""
    kind = marker["kind"]
    value = marker["value"]
    if kind == "grade_reason":
        return (grade or {}).get("reason") == value
    if kind == "grade_method":
        return (grade or {}).get("method") == value
    if kind == "answer_contains":
        return _norm(value) in _norm(answer)
    if kind == "numeric_ratio":
        student_nums = _numbers(answer)
        gold_nums = _numbers(gold)
        if len(student_nums) != 1 or len(gold_nums) != 1:
            return False
        if gold_nums[0] == 0:
            return False
        try:
            ratio = Fraction(value)
        except (ValueError, ZeroDivisionError):
            return False
        return student_nums[0] / gold_nums[0] == ratio
    return False


class Diagnoser:
    """Consume U3's handed_to_diagnosis outcome and name the why.

    ``store`` is an optional MemoryStore-like object
    (duck-typed: ``save_fact``) for recording unclassified
    errors; with no store the records live in memory on this
    diagnoser. The diagnoser never writes mastery and never
    changes the check loop, the planner, or the library's
    seeded entries.
    """

    def __init__(self, library=None, store=None):
        self.library = library if library is not None \
            else MisconceptionLibrary()
        self._store = store
        self._unclassified = []
        self._seq = 0

    # -- the unclassified record -----------------------------------
    def unclassified(self):
        """Errors that matched nothing, in order recorded."""
        return [dict(r) for r in self._unclassified]

    def _record_unclassified(self, record):
        self._unclassified.append(dict(record))
        if self._store is None:
            return False
        self._seq += 1
        key = (f"{record.get('skill', 'unknown')}-"
               f"{self._seq}-{record.get('recorded_at', '')}")
        try:
            return bool(self._store.save_fact(
                UNCATEGORISED_CATEGORY, key,
                json.dumps(record, sort_keys=True)))
        except Exception:  # noqa: BLE001 — a failed write is
            # recorded in memory anyway, never a crash.
            return False

    # -- classification --------------------------------------------
    def diagnose(self, outcome, *, answer=None, gold=None,
                 bite=None):
        """Classify one handover. Returns a diagnosis dict —
        never raises for a bad handover.

        ``outcome`` is U3's handed_to_diagnosis outcome
        (skill, attempts, reinforce, grade, mastery, reason).
        ``answer`` is the wrong answer text and ``gold`` the
        gold answer (taken from ``bite`` when given). The
        diagnosis always names a prerequisite and a diagnostic
        question; ``status`` is "classified", "ambiguous", or
        "unclassified".
        """
        if not isinstance(outcome, dict):
            outcome = {}
        skill = outcome.get("skill")
        if bite is not None:
            skill = skill or getattr(bite, "skill", None)
            gold = gold if gold is not None \
                else getattr(bite, "gold", None)
        skill = str(skill or "").strip()
        if not skill:
            return {
                "status": "unclassified",
                "misconception": None,
                "name": None,
                "candidates": [],
                "question": _GENERIC_QUESTION,
                "prerequisite": outcome.get("reinforce"),
                "backtrack_now": False,
                "backtrack": None,
                "feedback": _GENERIC_FEEDBACK,
                "reason": "no skill given — nothing to diagnose",
                "recorded": False,
                "persisted": False,
                "gold": "",
            }
        try:
            attempts = int(outcome.get("attempts", 1))
        except (TypeError, ValueError):
            attempts = 1
        grade = outcome.get("grade") or {}
        if not isinstance(grade, dict):
            grade = {}
        answer = answer if answer is not None else ""
        gold = gold if gold is not None else ""
        reinforce = outcome.get("reinforce")
        if bite is not None and reinforce is None:
            reinforce = getattr(bite, "reinforce", None)

        scored = []
        for pos, entry in enumerate(
                self.library.entries_for(skill)):
            score = 0
            matched = []
            for marker in entry["markers"]:
                if _marker_matches(marker, answer=answer,
                                   gold=gold, grade=grade):
                    score += marker["weight"]
                    matched.append(marker["kind"] + ":"
                                   + marker["value"])
            scored.append((score, pos, entry, matched))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top_score = scored[0][0] if scored else 0
        leaders = [item for item in scored
                   if item[0] == top_score] if scored else []
        backtrack_now = attempts >= REPEAT_FAILURE_ATTEMPTS

        if top_score <= 0 or not scored:
            record = {
                "answer": str(answer or ""),
                "skill": skill,
                "attempts": attempts,
                "grade_reason": grade.get("reason", ""),
                "grade_method": grade.get("method", ""),
                "recorded_at": _now_iso(),
            }
            persisted = self._record_unclassified(record)
            backtrack = None
            if reinforce:
                backtrack = {"skill": reinforce,
                             "plan_topic": reinforce}
            return {
                "status": "unclassified",
                "misconception": None,
                "name": None,
                "candidates": [],
                "question": _GENERIC_QUESTION,
                "prerequisite": reinforce,
                "backtrack_now": backtrack_now,
                "backtrack": backtrack,
                "feedback": _GENERIC_FEEDBACK,
                "reason": ("no library entry matched this error — "
                           "recorded for library growth"),
                "recorded": True,
                "persisted": persisted,
                "gold": gold,
            }

        if len(leaders) == 1:
            _, _, entry, matched = leaders[0]
            feedback = entry["feedback"]
            if gold and _contains_gold(feedback, gold):
                feedback = _GENERIC_FEEDBACK
            return {
                "status": "classified",
                "misconception": entry["id"],
                "name": entry["name"],
                "candidates": [{
                    "id": entry["id"],
                    "name": entry["name"],
                    "score": top_score,
                }],
                "question": entry["diagnostic_question"],
                "prerequisite": entry["prerequisite"],
                "backtrack_now": backtrack_now,
                "backtrack": {"skill": entry["prerequisite"],
                              "plan_topic": entry["prerequisite"]},
                "feedback": feedback,
                "reason": ("surface match: "
                           + ", ".join(matched)),
                "recorded": False,
                "persisted": False,
                "gold": gold,
            }

        # Ambiguous: the surface match ties — the diagnostic
        # question separates the candidates.
        _, _, top_entry, _ = leaders[0]
        feedback = top_entry["feedback"]
        if gold and _contains_gold(feedback, gold):
            feedback = _GENERIC_FEEDBACK
        return {
            "status": "ambiguous",
            "misconception": None,
            "name": None,
            "candidates": [{
                "id": item[2]["id"],
                "name": item[2]["name"],
                "score": item[0],
            } for item in leaders],
            "question": top_entry["diagnostic_question"],
            "prerequisite": top_entry["prerequisite"],
            "backtrack_now": backtrack_now,
            "backtrack": {"skill": top_entry["prerequisite"],
                          "plan_topic": top_entry["prerequisite"]},
            "feedback": feedback,
            "reason": (f"surface match ties {len(leaders)} "
                       f"entries — the diagnostic question "
                       f"separates them"),
            "recorded": False,
            "persisted": False,
            "gold": gold,
        }

    def resolve_diagnostic(self, diagnosis, diagnostic_answer):
        """Settle an ambiguous diagnosis from the student's
        answer to its diagnostic question. Returns a diagnosis
        dict — never raises for a bad answer.

        Exactly one candidate whose diagnostic markers appear
        in the answer wins. An answer confirming none of them,
        or several, stays ambiguous honestly. An already
        classified diagnosis is returned unchanged.
        """
        if not isinstance(diagnosis, dict):
            return diagnosis
        if diagnosis.get("status") != "ambiguous":
            return dict(diagnosis)
        answer = _norm(diagnostic_answer)
        hits = []
        for candidate in diagnosis.get("candidates", []):
            entry = self.library.get(candidate.get("id"))
            if entry is None:
                continue
            markers = entry.get("diagnostic_markers", [])
            if any(_norm(m) in answer for m in markers):
                hits.append(entry)
        if len(hits) != 1:
            settled = dict(diagnosis)
            settled["reason"] = (
                "the diagnostic answer did not separate the "
                "candidates — still ambiguous")
            return settled
        entry = hits[0]
        settled = dict(diagnosis)
        settled["status"] = "classified"
        settled["misconception"] = entry["id"]
        settled["name"] = entry["name"]
        settled["candidates"] = [{
            "id": entry["id"],
            "name": entry["name"],
            "score": next(
                (c.get("score", 0)
                 for c in diagnosis.get("candidates", [])
                 if c.get("id") == entry["id"]), 0),
        }]
        settled["question"] = entry["diagnostic_question"]
        settled["prerequisite"] = entry["prerequisite"]
        settled["backtrack"] = {"skill": entry["prerequisite"],
                                "plan_topic": entry["prerequisite"]}
        feedback = entry["feedback"]
        gold = diagnosis.get("gold", "")
        if gold and _contains_gold(feedback, gold):
            feedback = _GENERIC_FEEDBACK
        settled["feedback"] = feedback
        settled["reason"] = (
            "the diagnostic answer confirms "
            f"{entry['id']}")
        return settled
