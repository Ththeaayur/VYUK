"""Student model — the tutor's memory of what the user knows (U1, tutor programme).

Loophole family: "a tutor that forgets" — every session started from zero.
Nothing persisted what he knows, his slips, or his pace, so the tutor
re-taught what he had mastered and never noticed what he kept getting
wrong. The evidence (audit T2/T13): ``_mastered()`` in
``core/skills_study_helpers.py`` is a flat list of card fronts;
``_grade()`` bumps global right/wrong counters only; ``mastered``
appends on the FIRST correct review answer
(``core/skills_study_flashcards.py:147-149``). No per-skill mastery,
no misconception record, no pace anywhere in the tree.

ONE design: one per-skill mastery record, persisted in the SAME
MemoryStore facts table the tree already uses (never a second store),
updated ONLY by graded check evidence, with BKT-lite per-skill
probabilities (prior/learn/guess/slip per skill) and a mastery bar of
2 unprompted novel transfers (the B17 PROVEN pattern: a claim stands
only with its proof). This record is the single source of truth for
what he knows — every later batch (lesson planner, check loop,
diagnosis, spaced repetition, progress report) reads this record and
nothing else.

BKT-lite update (standard BKT equations, implemented in-tree from the
math — no vendored code, no new dependency; pyBKT is MIT per the
research notes, but this batch implements the pattern, it does not
vendor the library). For skill mastery probability ``p``:

    P(correct) = p * (1 - slip) + (1 - p) * guess
    correct:   p_post = p * (1 - slip) / P(correct)
    incorrect: p_post = p * slip / (p * slip + (1 - p) * (1 - guess))
    learning:  p_new  = p_post + (1 - p_post) * learn

``p`` is clamped to [0.01, 0.99] so no single answer ever drives a
skill to certainty or impossibility.

Cold start: population-default priors (prior 0.30, learn 0.15,
guess 0.20, slip 0.10) until a skill has ~10 graded interactions.
Per-student guess/slip are fitted ONLY from that skill's own history
once it has >= 10 interactions AND >= 3 observations in each band
(errors when p was high for slip; correct answers when p was low for
guess), bounded to [0.02, 0.30] (slip) and [0.05, 0.40] (guess).
Never fitted from 3-8 attempts — BKT needs ~10 interactions per
skill before it is stable.

Mastery bar (T13): a skill counts as mastered ONLY after 2
unprompted novel transfers — two correct answers on novel variants
without a hint. One correct answer is not mastery. A hinted correct
answer still moves ``p`` (it is evidence) but never counts as a
transfer. A non-novel correct answer moves ``p`` but never counts
as a transfer.

Confidence gating (owner ruling D-T3, 2026-11-08): a low-confidence
spoken answer (confidence < 0.6 — a likely mishearing) updates
NOTHING. The caller re-asks / confirms instead; a mishearing is
never graded wrong.

Persistence: one MemoryStore fact per skill, category
``"student_model"``, key = the skill id, value = the record as JSON.
The store is INJECTED (``StudentModel(store)``) — this module imports
nothing from the product, so there is no import cycle and no
singleton coupling; callers pass the MemoryStore singleton. With no
store, an in-memory dict-backed store is used (tests, dry runs).
``save_fact`` already proves the write by read-back; a save that
reports False is reported honestly as ``persist_failed``.

Visibility (owner ruling D-T1, 2026-11-08): the record is local-only,
never leaves the device, readable through ``mastery_summary()`` /
``all_skills()``, and clearable by ONE explicit action
(``clear_all()``), which deletes only ``student_model`` facts and
touches nothing else.

Rejected alternatives (recorded here, not just in the audit):
- A flat mastered list (today's ``_mastered``) — "answered once"
  is not mastery.
- A second store for the student model — the MemoryStore facts
  table is the one home (B11's rule: same store, never a second).
- Fitting per-student parameters from 3-8 attempts — unstable;
  cold-start priors until >= 10 interactions.
- Updating mastery on ungraded chat turns — a chat "yeah I get
  it" is not evidence; only graded check evidence moves mastery.
- A global guess/slip — a spoken slip on one skill must not tank
  mastery; guess/slip are fitted per skill.
- Vendoring pyBKT — the update equations are five lines; the
  library would add a dependency for nothing.
"""

import json
from datetime import datetime, timezone

CATEGORY = "student_model"

# Population-default priors (cold start). BKT literature defaults;
# per-skill fitting replaces guess/slip only after enough evidence.
DEFAULT_PRIOR = 0.30
DEFAULT_LEARN = 0.15
DEFAULT_GUESS = 0.20
DEFAULT_SLIP = 0.10

# A skill needs ~10 graded interactions before its own history can
# fit anything. Below that, the defaults stand.
MIN_EVIDENCE_FOR_FIT = 10
# Each fitted parameter needs >= 3 observations in its own band.
MIN_BAND_OBSERVATIONS = 3

# Mastery bar (T13): 2 unprompted novel transfers.
MASTERY_TRANSFERS = 2

# D-T3: below this confidence a spoken answer is a likely mishearing
# and updates nothing.
CONFIDENCE_FLOOR = 0.6

# Probability clamp: no single answer drives a skill to 0 or 1.
P_FLOOR = 0.01
P_CEIL = 0.99

# Bounded fit ranges for per-skill guess/slip.
GUESS_MIN, GUESS_MAX = 0.05, 0.40
SLIP_MIN, SLIP_MAX = 0.02, 0.30

# Bands used for fitting: errors when p was high estimate slip;
# correct answers when p was low estimate guess.
HIGH_P_BAND = 0.7
LOW_P_BAND = 0.3

MAX_SKILL_ID_LEN = 200
RECORD_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def norm_skill_id(skill_id) -> str | None:
    """Normalise a skill id; None when it is not a usable id."""
    if skill_id is None:
        return None
    text = " ".join(str(skill_id).split()).strip().lower()
    if not text or len(text) > MAX_SKILL_ID_LEN:
        return None
    return text


def _cold_start(skill_id: str) -> dict:
    return {
        "v": RECORD_VERSION,
        "skill_id": skill_id,
        "p": DEFAULT_PRIOR,
        "prior": DEFAULT_PRIOR,
        "learn": DEFAULT_LEARN,
        "guess": DEFAULT_GUESS,
        "slip": DEFAULT_SLIP,
        "evidence": 0,
        "correct": 0,
        "wrong": 0,
        "transfers": 0,
        "high_p_total": 0,
        "high_p_wrong": 0,
        "low_p_total": 0,
        "low_p_right": 0,
        "fitted": False,
        "updated_at": _now_iso(),
    }


def _bkt_update(p: float, correct: bool, learn: float,
                guess: float, slip: float) -> float:
    """One standard BKT observation + learning step, clamped."""
    p_correct = p * (1.0 - slip) + (1.0 - p) * guess
    if p_correct <= 0.0:
        p_correct = 1e-9
    if correct:
        p_post = p * (1.0 - slip) / p_correct
    else:
        denom = p * slip + (1.0 - p) * (1.0 - guess)
        p_post = p * slip / denom if denom > 0.0 else p
    p_new = p_post + (1.0 - p_post) * learn
    return _clamp(p_new, P_FLOOR, P_CEIL)


def _refit(record: dict) -> bool:
    """Refit guess/slip from this skill's own history, bounded.

    Runs only when evidence >= MIN_EVIDENCE_FOR_FIT and each band
    has >= MIN_BAND_OBSERVATIONS. Returns True when a parameter
    actually moved. Before that, the cold-start defaults stand.
    """
    if record["evidence"] < MIN_EVIDENCE_FOR_FIT:
        return False
    moved = False
    if record["high_p_total"] >= MIN_BAND_OBSERVATIONS:
        slip = _clamp(
            record["high_p_wrong"] / record["high_p_total"],
            SLIP_MIN, SLIP_MAX)
        if slip != record["slip"]:
            record["slip"] = slip
            moved = True
    if record["low_p_total"] >= MIN_BAND_OBSERVATIONS:
        guess = _clamp(
            record["low_p_right"] / record["low_p_total"],
            GUESS_MIN, GUESS_MAX)
        if guess != record["guess"]:
            record["guess"] = guess
            moved = True
    if moved:
        record["fitted"] = True
    return moved


def _record_from_json(skill_id: str, value) -> dict:
    """Parse a stored record; a corrupt row is a cold start, never a
    crash and never invented evidence. The next graded save
    overwrites the corrupt row."""
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return _cold_start(skill_id)
    if not isinstance(parsed, dict) or parsed.get("v") != RECORD_VERSION:
        return _cold_start(skill_id)
    record = _cold_start(skill_id)
    for key in ("p", "prior", "learn", "guess", "slip"):
        try:
            record[key] = float(parsed.get(key, record[key]))
        except (TypeError, ValueError):
            pass
    record["p"] = _clamp(record["p"], P_FLOOR, P_CEIL)
    for key in ("evidence", "correct", "wrong", "transfers",
                "high_p_total", "high_p_wrong", "low_p_total",
                "low_p_right"):
        try:
            record[key] = max(0, int(parsed.get(key, 0)))
        except (TypeError, ValueError):
            record[key] = 0
    record["fitted"] = bool(parsed.get("fitted", False))
    if isinstance(parsed.get("updated_at"), str):
        record["updated_at"] = parsed["updated_at"]
    return record


class _MemoryFacts:
    """Dict-backed store with the MemoryStore fact API (store=None
    use and tests). No persistence — the caller owns the lifetime."""

    def __init__(self):
        self._rows = {}
        self._next_id = 1

    def save_fact(self, category: str, key: str, value: str) -> bool:
        ident = (category.strip().lower(), key.strip())
        row = self._rows.get(ident)
        if row is None:
            row = {"id": self._next_id, "category": ident[0],
                   "key": ident[1], "value": value}
            self._next_id += 1
            self._rows[ident] = row
        else:
            row["value"] = value
        return self._rows[ident]["value"] == value

    def get_fact(self, category: str, key: str):
        return self._rows.get((category.strip().lower(), key.strip()))

    def get_all_facts(self):
        return sorted(self._rows.values(),
                      key=lambda r: (r["category"], r["key"]))

    def delete_fact(self, fact_id: int) -> bool:
        for ident, row in list(self._rows.items()):
            if row["id"] == fact_id:
                del self._rows[ident]
                return True
        return False


class StudentModel:
    """One per-skill mastery record, persisted in the MemoryStore
    facts table. The ONLY writer is :meth:`record_graded` — graded
    check evidence only. Reads never move mastery."""

    def __init__(self, store=None):
        self._store = store if store is not None else _MemoryFacts()

    # -- persistence -------------------------------------------------
    def _load(self, skill_id: str) -> dict:
        row = self._store.get_fact(CATEGORY, skill_id)
        if row is None:
            return _cold_start(skill_id)
        return _record_from_json(skill_id, row.get("value"))

    def _save(self, record: dict) -> bool:
        value = json.dumps(record, sort_keys=True)
        try:
            return bool(self._store.save_fact(
                CATEGORY, record["skill_id"], value))
        except Exception:  # noqa: BLE001 - a failed write is a
            # failed write, never a crash in the tutor path.
            return False

    # -- reads (never move mastery) ---------------------------------
    def get_skill(self, skill_id) -> dict | None:
        """This skill's record, or a cold-start record when unknown.
        None for an unusable skill id. Reading never writes."""
        skill_id = norm_skill_id(skill_id)
        if skill_id is None:
            return None
        return self._load(skill_id)

    def all_skills(self) -> list:
        """Every recorded skill, sorted by skill id."""
        records = []
        for row in self._store.get_all_facts():
            if row.get("category") != CATEGORY:
                continue
            key = row.get("key")
            skill_id = norm_skill_id(key)
            if skill_id is None:
                continue
            records.append(self._load(skill_id))
        records.sort(key=lambda r: r["skill_id"])
        return records

    def is_mastered(self, skill_id) -> bool:
        """Mastery bar (T13): 2 unprompted novel transfers."""
        record = self.get_skill(skill_id)
        if record is None:
            return False
        return record["transfers"] >= MASTERY_TRANSFERS

    def mastery_summary(self) -> dict:
        """The U9 reader surface: one line per skill, plus counts.
        Read-only — it never moves mastery."""
        skills = [{
            "skill_id": r["skill_id"],
            "p": round(r["p"], 4),
            "evidence": r["evidence"],
            "transfers": r["transfers"],
            "mastered": r["transfers"] >= MASTERY_TRANSFERS,
        } for r in self.all_skills()]
        return {
            "skills": skills,
            "total": len(skills),
            "mastered_count": sum(1 for s in skills if s["mastered"]),
        }

    # -- the ONLY writer ---------------------------------------------
    def record_graded(self, skill_id, correct, *, novel=False,
                      hinted=False, confidence=1.0,
                      source="check") -> dict:
        """Record ONE graded check answer. Returns an outcome dict;
        never raises for bad input.

        ``correct`` must be a bool. ``novel`` marks a fresh variant
        (not a repeat of a seen question); ``hinted`` marks an answer
        given after a hint. ``confidence`` is the hearing confidence
        for a spoken answer (1.0 for typed/written). Below
        CONFIDENCE_FLOOR the answer is a likely mishearing: nothing
        is recorded and the caller re-asks / confirms instead (D-T3).
        """
        skill_id = norm_skill_id(skill_id)
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
        if confidence < CONFIDENCE_FLOOR:
            record = self._load(skill_id)
            return {"ok": True, "recorded": False,
                    "reason": "low_confidence", "skill_id": skill_id,
                    "p": record["p"], "evidence": record["evidence"],
                    "transfers": record["transfers"],
                    "mastered": self.is_mastered(skill_id)}

        record = self._load(skill_id)
        p_before = record["p"]
        record["p"] = _bkt_update(
            p_before, correct, record["learn"], record["guess"],
            record["slip"])
        record["evidence"] += 1
        if correct:
            record["correct"] += 1
        else:
            record["wrong"] += 1
        # Fit bands: errors when p was high estimate slip; correct
        # answers when p was low estimate guess.
        if p_before >= HIGH_P_BAND:
            record["high_p_total"] += 1
            if not correct:
                record["high_p_wrong"] += 1
        if p_before <= LOW_P_BAND:
            record["low_p_total"] += 1
            if correct:
                record["low_p_right"] += 1
        # Transfer: correct, novel variant, no hint. A hinted or
        # repeated correct answer moves p but never counts.
        if correct and novel and not hinted:
            record["transfers"] += 1
        _refit(record)
        record["updated_at"] = _now_iso()
        if not self._save(record):
            return {"ok": False, "recorded": False,
                    "reason": "persist_failed", "skill_id": skill_id,
                    "p": record["p"], "evidence": record["evidence"],
                    "transfers": record["transfers"],
                    "mastered": record["transfers"] >= MASTERY_TRANSFERS}
        return {"ok": True, "recorded": True, "reason": "recorded",
                "skill_id": skill_id, "p": round(record["p"], 4),
                "evidence": record["evidence"],
                "transfers": record["transfers"],
                "mastered": record["transfers"] >= MASTERY_TRANSFERS,
                "fitted": record["fitted"]}

    # -- clearing ------------------------------------------------------
    def clear_all(self) -> int:
        """Delete ONLY the student-model facts. Returns how many
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
