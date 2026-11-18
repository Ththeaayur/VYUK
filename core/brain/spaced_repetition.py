"""U8 — Spaced repetition, FSRS (tutor programme, 2026-11-08).

Lab leg only. The real-Windows field leg — a real MA 105 review on
his machine, a skill due for review surfacing at the right time, a
correct recall pushing its next review later, a forgotten skill
surfacing sooner — is OWED, not this batch.

Gap proved first (audit T8): the tutor teaches a bite once and never
brings it back. The flashcard path asks cards in random deck order —
``flashcard_review`` (``core/skills_study_flashcards.py:73``) picks a
random unmastered card; ``_answer_review`` grades by exact string
(``given.lower() == card["back"].lower()``) and a card counts as
mastered on the FIRST correct answer (``:147-149``); ``_mastered()``
(``core/skills_study_helpers.py:72``) is a flat list of card fronts.
No due date, no stability, no interval, no retrievability anywhere
in that path. The U1 student-model record (``core/brain/
student_model.py``) holds p/evidence/transfers — no stability, no
difficulty, no due. And the tutor never touches the FSRS core that
already exists: zero ``st_schedule`` references in ``core/brain/``.

FSRS DECISION — reuse the in-tree FSRS-6 core (``core/st_schedule.py``,
527 lines, native stdlib-only, formula-for-formula matched against
the py-fsrs 6.3.2 oracle per its docstring, tested by
``tests/test_triage1_study.py`` + ``tests/test_scheduler_fix.py``,
38 tests OK). This module is the TUTOR-facing layer on top of it:
per-skill scheduler records in the MemoryStore facts table, an
``observe()`` mapping graded U3 outcomes to FSRS ratings, a due
queue ordered by retrievability, and a planner-facing merge helper.
It adds no FSRS math of its own — the core is the single source of
truth for stability/difficulty/retrievability/intervals.
Rejected alternatives (recorded, not carried forward):
- Adapting py-fsrs in-tree (MIT + attribution) — vendors
  third-party code into the shipped tree and creates a SECOND FSRS
  implementation that can drift from ``st_schedule.py``; the work is
  already done and oracle-verified in-tree, so vendoring buys risk,
  not correctness.
- Re-implementing FSRS from the algorithm in ``core/brain/`` — the
  same duplication, plus real formula risk (the recalled FSRS-5
  parameters/formulas differ from FSRS-6's: in FSRS-6 the decay is a
  fitted parameter w20 = 0.1542, retrievability uses whole-day
  elapsed time, and ``_next_interval`` rounds to whole days).
- Adding the ``fsrs`` package as a runtime dependency — needs a
  parent ruling, adds a pip dependency to the shipped product, and
  duplicates the in-tree core for no gain.

LOPHOLE FAMILY — "a tutor that forgets what you forget": every
review system that asks cards in deck order, or on a fixed interval,
or whenever the student remembers to ask, wastes reviews on what he
knows and misses what he is about to forget; and a scheduler that
counts a hinted or repeated answer as a clean recall inflates
stability and schedules the next review too late. ONE design closes
the class: FSRS as the review memory — per-skill stability +
difficulty + retrievability from the in-tree FSRS-6 core, a
desired-retention target, and a due queue ordered by lowest
retrievability; every review is a GRADED check through U3 (never a
self-reported "I knew it"), and the scheduler updates
stability/difficulty ONLY from graded evidence (correct/incorrect,
hinted or not — a hinted correct answer is a Hard, never a Good, so
it grows stability less than a clean one, never the same).

The split, precisely: this module NEVER grades and NEVER writes
mastery. It holds no ``record_graded`` handle — there is no
``record_graded`` call anywhere in this module, and the prover
checks that by AST. The only write it makes is its own scheduler
record (category ``"spaced_repetition"``) in the same MemoryStore
facts table U1 uses. A review reaches it as an OBSERVED graded
outcome: the future tutor session driver calls ``observe()`` with
the U3 check's result (correct / hinted / attempts / confidence);
spoken reviews go through U7's ``grade_spoken`` first, so the
outcome is already graded when it arrives here. The scheduler reads
U1's record only as plain data handed to it (``record_for`` /
``observe`` accept an optional ``mastery`` dict, the
``StudentModel.get_skill`` output) so the visible schedule can show
mastery alongside stability — it never holds the model, never
creates a U1 record, and invents no p-to-stability mapping (FSRS
initial stability comes from the first rating, as the algorithm
defines; a skill with no history is a cold start with no due date).

Rating mapping (``rating_for`` — pure, tested in isolation):
- not correct -> Again (the recall failed; FSRS sends it to
  relearning, stability collapses toward the floor).
- correct + hinted -> Hard (it needed help — the recall was not
  clean; the FSRS hard penalty, w15 = 0.6014, grows stability
  less than a Good at the same state).
- correct + clean + more than one attempt -> Hard (the first
  recall failed; the eventual correct answer came with effort).
- correct + clean + one attempt + retrievability below 0.8 ->
  Easy (a clean recall after more than four times the scheduled
  interval is a strong recall; the FSRS easy bonus, w16 = 1.8729,
  grows stability more than a Good). Retrievability unknown (no
  prior review) never triggers Easy — a first review has no decay
  to survive, so it is a Good.
- correct + clean + one attempt otherwise -> Good.

Fuzz: the FSRS core fuzzes Review-state intervals by default; this
scheduler disables it (``enable_fuzzing=False``). Fuzz is an
interval-presentation jitter, not FSRS math — disabling it keeps
dues exact and tests deterministic, and the tutor's queue is small
and ordered by retrievability anyway. The flag is exposed so the
driver can enable it later.

Desired retention: a single tunable on the scheduler (default 0.9,
the FSRS standard). A higher target pulls every future due earlier;
dues already stored do not move until the next review (the due was
computed under the old target — that is honest, not a bug).

What this batch does NOT build: the progress report (U9 renders
this schedule — U8 exposes it as plain data); proactive nudges or
due-review notifications (post-tutor roadmap, not this batch); the
tutor session driver that calls ``observe()`` after a U3 check
(still owed, as is the production ``variant_fn`` gold-guard from
the U3 residual); two-way Anki sync (the core's docstring already
says it is not promised).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .. import st_schedule as _fsrs
from . import student_model as sm

CATEGORY = "spaced_repetition"
RECORD_VERSION = 1

# A clean first-attempt recall counts as Easy only when the memory
# had decayed this far: retrievability below 0.8 means the skill was
# recalled after more than four times its scheduled interval. The
# ordinary due-point recall (retrievability ~0.9) stays Good.
EASY_RETRIEVABILITY_THRESHOLD = 0.8

DEFAULT_DESIRED_RETENTION = _fsrs.DEFAULT_DESIRED_RETENTION


def rating_for(correct, hinted=False, attempts=1,
               retrievability=None) -> int:
    """Map a graded check outcome to an FSRS rating. Pure function.

    ``retrievability`` is the skill's retrievability at review time,
    or None when there is no prior review (a first review has no
    decay to survive, so it can never be Easy).
    """
    if not correct:
        return _fsrs.AGAIN
    if hinted:
        return _fsrs.HARD
    try:
        attempts = int(attempts)
    except (TypeError, ValueError):
        attempts = 1
    if attempts > 1:
        return _fsrs.HARD
    if retrievability is not None:
        try:
            retrievability = float(retrievability)
        except (TypeError, ValueError):
            retrievability = None
        if (retrievability is not None
                and retrievability < EASY_RETRIEVABILITY_THRESHOLD):
            return _fsrs.EASY
    return _fsrs.GOOD


def order_next(due, ready):
    """Merge a due queue with ready bites: due reviews first (in
    queue order), then ready bites in the order given. Pure function,
    driver-facing — it does not touch the planner's ordering or
    approval; it is what the driver consults when it has a free slot.

    ``due`` is the ``due_queue()`` output (dicts with ``"skill_id"``);
    ``ready`` is any sequence of bites (opaque objects, passed
    through unchanged). Returns a list of ("review", skill_id) /
    ("bite", bite) pairs.
    """
    merged = [("review", entry["skill_id"]) for entry in (due or ())]
    merged.extend(("bite", bite) for bite in (ready or ()))
    return merged


class ReviewScheduler:
    """Per-skill FSRS scheduling on top of the in-tree FSRS-6 core.

    One scheduler record per skill, persisted in the same MemoryStore
    facts table U1 uses (category ``"spaced_repetition"``, key = the
    skill id, value = the record as JSON). The store is INJECTED —
    this module imports nothing from the product except the FSRS core
    and the student-model's skill-id normalisation, so there is no
    import cycle. The clock is injectable (a callable returning an
    aware UTC datetime) so tests never wait in real time.
    """

    def __init__(self, store, *, desired_retention=DEFAULT_DESIRED_RETENTION,
                 clock=None, enable_fuzzing=False):
        self._store = store
        self._fsrs = _fsrs.Scheduler(
            desired_retention=desired_retention,
            enable_fuzzing=enable_fuzzing)
        self._desired_retention = desired_retention
        self._clock = clock

    # -- time ---------------------------------------------------------
    def _now(self) -> datetime:
        if self._clock is not None:
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            return now
        return datetime.now(timezone.utc)

    # -- persistence (mirrors U1's pattern exactly) -------------------
    @staticmethod
    def _card_id(skill_id: str) -> int:
        """Deterministic card id for a skill (first 48 bits of its
        SHA-256) so a re-created card keeps its identity."""
        digest = hashlib.sha256(skill_id.encode("utf-8")).hexdigest()
        return int(digest[:12], 16)

    def _load(self, skill_id: str) -> dict | None:
        row = self._store.get_fact(CATEGORY, skill_id)
        if row is None:
            return None
        return _record_from_json(skill_id, row.get("value"))

    def _save(self, record: dict) -> bool:
        value = json.dumps(record, sort_keys=True)
        try:
            return bool(self._store.save_fact(
                CATEGORY, record["skill_id"], value))
        except Exception:  # noqa: BLE001 - a failed write is a
            # failed write, never a crash in the tutor path.
            return False

    # -- reads (never move mastery, never schedule) -------------------
    def record_for(self, skill_id, mastery=None, now=None) -> dict | None:
        """This skill's scheduler record as plain data, or None for a
        cold-start skill (no reviews yet — no due date). Reading never
        writes. ``mastery`` is an optional U1 record dict
        (``StudentModel.get_skill`` output), attached for display
        only — it is never stored here and never moves anything.
        """
        skill_id = sm.norm_skill_id(skill_id)
        if skill_id is None:
            return None
        record = self._load(skill_id)
        if record is None:
            return None
        return _record_view(record, self._fsrs, now or self._now(),
                            mastery=mastery)

    def schedule(self, now=None) -> list:
        """Every scheduled skill as plain data, sorted by due (most
        urgent first). The U9 reader surface — read-only."""
        now = now or self._now()
        views = []
        for row in self._store.get_all_facts():
            if row.get("category") != CATEGORY:
                continue
            skill_id = sm.norm_skill_id(row.get("key"))
            if skill_id is None:
                continue
            record = self._load(skill_id)
            if record is None:
                continue
            views.append(_record_view(record, self._fsrs, now))
        views.sort(key=lambda v: (v["due"], v["skill_id"]))
        return views

    def next_due(self, skill_id, now=None) -> datetime | None:
        """When this skill is next due, or None for a cold-start
        skill (no reviews yet — the planner's diagnostic first bite,
        U2's rule, is untouched)."""
        view = self.record_for(skill_id, now=now)
        if view is None:
            return None
        return datetime.fromisoformat(view["due"])

    def due_queue(self, now=None, limit=None) -> list:
        """Skills due for review right now, most at risk first:
        lowest retrievability first, earliest due breaking ties.
        A skill with no reviews is never in the queue (cold start).
        """
        now = now or self._now()
        due = []
        for view in self.schedule(now=now):
            if datetime.fromisoformat(view["due"]) <= now:
                due.append(view)
        due.sort(key=lambda v: (v["retrievability"], v["due"],
                                v["skill_id"]))
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = None
            if limit is not None and limit >= 0:
                due = due[:limit]
        return due

    def clear_all(self) -> int:
        """Delete ONLY the spaced-repetition facts. Returns how many
        were deleted. One explicit action; touches nothing else."""
        deleted = 0
        for row in list(self._store.get_all_facts()):
            if row.get("category") != CATEGORY:
                continue
            try:
                if self._store.delete_fact(row["id"]):
                    deleted += 1
            except Exception:  # noqa: BLE001 - keep clearing the
                # rest even if one row fails.
                continue
        return deleted

    # -- the one write: observe a graded review -----------------------
    def observe(self, skill_id, *, correct, hinted=False, attempts=1,
                confidence=1.0, now=None) -> dict:
        """Record one GRADED review outcome and advance the skill's
        FSRS state. This is the scheduler's only write, and it writes
        only its own record — it never grades, never calls
        ``record_graded``, never touches mastery. The outcome must
        already be graded (by U3, or by U7's ``grade_spoken`` for a
        spoken review) when it arrives here.

        ``correct`` must be a bool. ``hinted`` marks an answer given
        after a hint. ``attempts`` counts the graded attempts the
        check took. ``confidence`` is the hearing confidence for a
        spoken answer (1.0 for typed/written); below the D-T3 floor
        (0.6) the outcome is a likely mishearing: nothing is
        recorded and the caller re-asks instead.
        """
        skill_id = sm.norm_skill_id(skill_id)
        if skill_id is None:
            return {"ok": False, "recorded": False,
                    "reason": "invalid_skill_id", "skill_id": None}
        if not isinstance(correct, bool):
            return {"ok": False, "recorded": False,
                    "reason": "invalid_correct", "skill_id": skill_id}
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < sm.CONFIDENCE_FLOOR:
            view = self.record_for(skill_id)
            return {"ok": True, "recorded": False,
                    "reason": "low_confidence", "skill_id": skill_id,
                    "stability": view["stability"] if view else None,
                    "difficulty": view["difficulty"] if view else None,
                    "due": view["due"] if view else None}
        now = now or self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        record = self._load(skill_id)
        if record is None:
            card = _fsrs.Card(
                card_id=self._card_id(skill_id),
                state=_fsrs.STATE_LEARNING, step=0,
                stability=None, difficulty=None, due=now,
                last_review=None)
            reviews = 0
            lapses = 0
            retrievability = None
        else:
            card = _fsrs.Card.from_dict(record["card"])
            reviews = record["reviews"]
            lapses = record["lapses"]
            retrievability = self._fsrs.get_card_retrievability(card, now)

        rating = rating_for(correct, hinted=hinted, attempts=attempts,
                            retrievability=retrievability)
        updated, _log = self._fsrs.review_card(card, rating,
                                               review_datetime=now)
        reviews += 1
        if rating == _fsrs.AGAIN:
            lapses += 1
        record = {
            "skill_id": skill_id,
            "version": RECORD_VERSION,
            "card": updated.to_dict(),
            "reviews": reviews,
            "lapses": lapses,
            "updated_at": now.isoformat(),
        }
        if not self._save(record):
            return {"ok": False, "recorded": False,
                    "reason": "persist_failed", "skill_id": skill_id}
        return {
            "ok": True, "recorded": True, "skill_id": skill_id,
            "rating": rating,
            "rating_name": _fsrs.RATING_NAMES[rating],
            "stability": updated.stability,
            "difficulty": updated.difficulty,
            "retrievability": retrievability,
            "due": updated.due.isoformat(),
            "state": updated.state,
            "reviews": reviews,
            "lapses": lapses,
        }


def _record_from_json(skill_id: str, raw) -> dict | None:
    """Parse a stored scheduler record; None when the row is corrupt
    (a corrupt row is a cold start, never a crash)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("skill_id") != skill_id:
        return None
    if data.get("version") != RECORD_VERSION:
        return None
    card = data.get("card")
    if not isinstance(card, dict):
        return None
    try:
        parsed = _fsrs.Card.from_dict(card)
    except Exception:  # noqa: BLE001 - corrupt card, cold start.
        return None
    # A stored record from observe() always has stability and a last
    # review (review_card sets both). A row missing either is not a
    # real scheduler record — it is a cold start, never a crash in
    # the due queue (whose sort key compares retrievabilities).
    if parsed.stability is None or parsed.last_review is None:
        return None
    if not isinstance(data.get("reviews"), int):
        return None
    if not isinstance(data.get("lapses"), int):
        return None
    return data


def _record_view(record: dict, fsrs, now: datetime,
                 mastery=None) -> dict:
    """A scheduler record as plain data for display (U9) and for the
    due queue. Retrievability is computed at ``now``; a card never
    reviewed has retrievability None (not 0 — 0 would mean forgotten,
    and a new skill is not forgotten, it is unseen)."""
    card = _fsrs.Card.from_dict(record["card"])
    if card.last_review is None or card.stability is None:
        retrievability = None
    else:
        retrievability = fsrs.get_card_retrievability(card, now)
    view = {
        "skill_id": record["skill_id"],
        "stability": card.stability,
        "difficulty": card.difficulty,
        "retrievability": retrievability,
        "due": card.due.isoformat(),
        "state": card.state,
        "state_name": _fsrs.STATE_NAMES.get(card.state, card.state),
        "reviews": record["reviews"],
        "lapses": record["lapses"],
        "last_review": (card.last_review.isoformat()
                        if card.last_review else None),
    }
    if isinstance(mastery, dict):
        try:
            view["mastery_p"] = float(mastery.get("p"))
        except (TypeError, ValueError):
            view["mastery_p"] = None
        try:
            view["mastered"] = bool(
                int(mastery.get("transfers", 0))
                >= sm.MASTERY_TRANSFERS)
        except (TypeError, ValueError):
            view["mastered"] = False
    return view
