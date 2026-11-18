"""Study edition — FSRS-6 scheduler core (ST-2).

Native, stdlib-only implementation of the FSRS-6 scheduling algorithm,
the same algorithm Anki itself uses since 23.10. Behaviour spec and
dev-side oracle: py-fsrs 6.3.2 (MIT, Open Spaced Repetition; LICENSE
re-read at the pin during the Study build — see the build report).
The library itself never ships in this tree; this module is VYUK's
own code, written to match the oracle's outputs on shared fixture
logs within the tolerance the suite states.

Authority: wave-4 scout §KW-4 (py-fsrs ADOPT, optimiser extra
excluded) + v6 LEARN (items 1437-1446) + the Study scope §2 (ST-2).
The optimiser (personal-parameter fitting, PyTorch) is EXCLUDED
from this module on weight and on the scout's drift warning, not on
licence. Two-way Anki sync is NOT promised by this module: interval
drift vs Anki's Rust FSRS is unmeasured, and the drift bench at the
surface wave decides whether sync ever exists.

Due-date cap rule (stated, not hidden): FSRS-6 defines no separate
"volatility" parameter. This module caps every card due-date by
clamping the computed interval to [1, maximum_interval] days and
flooring stability at STABILITY_MIN, exactly as the oracle does.
A single review therefore cannot explode a due date past the
maximum interval, and stability cannot collapse below the floor.
That clamp pair IS the volatility cap the scope names.

No cross-module imports (edition precedent). No pip-time dependency.
No memory-DB columns: cards and logs live wherever the caller
stores them; this module only computes.
"""

from __future__ import annotations

import json
import math
import random
from copy import copy
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Ratings, states, parameters (mirroring the oracle's public constants)
# ---------------------------------------------------------------------------

AGAIN = 1
HARD = 2
GOOD = 3
EASY = 4
RATINGS = (AGAIN, HARD, GOOD, EASY)
RATING_NAMES = {AGAIN: "Again", HARD: "Hard", GOOD: "Good", EASY: "Easy"}

STATE_LEARNING = 1
STATE_REVIEW = 2
STATE_RELEARNING = 3
STATE_NAMES = {
    STATE_LEARNING: "Learning",
    STATE_REVIEW: "Review",
    STATE_RELEARNING: "Relearning",
}

FSRS_DEFAULT_DECAY = 0.1542
DEFAULT_PARAMETERS = (
    0.212,
    1.2931,
    2.3065,
    8.2956,
    6.4133,
    0.8334,
    3.0194,
    0.001,
    1.8722,
    0.1666,
    0.796,
    1.4835,
    0.0614,
    0.2629,
    1.6483,
    0.6014,
    1.8729,
    0.5425,
    0.0912,
    0.0658,
    FSRS_DEFAULT_DECAY,
)

STABILITY_MIN = 0.001
LOWER_BOUNDS_PARAMETERS = (
    STABILITY_MIN, STABILITY_MIN, STABILITY_MIN, STABILITY_MIN,
    1.0, 0.001, 0.001, 0.001, 0.0, 0.0, 0.001, 0.001, 0.001, 0.001,
    0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.1,
)
UPPER_BOUNDS_PARAMETERS = (
    100.0, 100.0, 100.0, 100.0,
    10.0, 4.0, 4.0, 0.75, 4.5, 0.8, 3.5, 5.0, 0.25, 0.9, 4.0, 1.0,
    6.0, 2.0, 2.0, 0.8, 0.8,
)
INITIAL_STABILITY_MAX = 100.0
MIN_DIFFICULTY = 1.0
MAX_DIFFICULTY = 10.0

FUZZ_RANGES = (
    {"start": 2.5, "end": 7.0, "factor": 0.15},
    {"start": 7.0, "end": 20.0, "factor": 0.1},
    {"start": 20.0, "end": float("inf"), "factor": 0.05},
)

DEFAULT_LEARNING_STEPS = (timedelta(minutes=1), timedelta(minutes=10))
DEFAULT_RELEARNING_STEPS = (timedelta(minutes=10),)
DEFAULT_DESIRED_RETENTION = 0.9
DEFAULT_MAXIMUM_INTERVAL = 36500


def _require_utc(dt, name):
    if dt is not None and (dt.tzinfo is None or dt.tzinfo != timezone.utc):
        raise ValueError("%s must be timezone-aware and set to UTC" % name)


class Card:
    """A flashcard under scheduling. Mirrors the oracle's Card shape."""

    def __init__(self, card_id=None, state=STATE_LEARNING, step=None,
                 stability=None, difficulty=None, due=None, last_review=None):
        if card_id is None:
            # Epoch milliseconds, as the oracle does. Callers creating
            # many cards in a tight loop should pass explicit card_ids
            # (the oracle sleeps 1 ms to avoid collisions; this module
            # does not, to keep bulk creation fast).
            card_id = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.card_id = card_id
        self.state = state
        if self.state == STATE_LEARNING and step is None:
            step = 0
        self.step = step
        self.stability = stability
        self.difficulty = difficulty
        self.due = due if due is not None else datetime.now(timezone.utc)
        self.last_review = last_review

    def to_dict(self):
        return {
            "card_id": self.card_id,
            "state": self.state,
            "step": self.step,
            "stability": self.stability,
            "difficulty": self.difficulty,
            "due": self.due.isoformat(),
            "last_review": self.last_review.isoformat() if self.last_review else None,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            card_id=int(d["card_id"]),
            state=int(d["state"]),
            step=d["step"],
            stability=float(d["stability"]) if d["stability"] else None,
            difficulty=float(d["difficulty"]) if d["difficulty"] else None,
            due=datetime.fromisoformat(d["due"]),
            last_review=datetime.fromisoformat(d["last_review"]) if d["last_review"] else None,
        )

    def to_json(self, indent=None):
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, s):
        return cls.from_dict(json.loads(s))

    def __repr__(self):
        return ("Card(id=%r, state=%s, step=%r, stability=%r, difficulty=%r, "
                "due=%r)" % (self.card_id, STATE_NAMES.get(self.state, self.state),
                             self.step, self.stability, self.difficulty, self.due))


class ReviewLog:
    """One review event for one card."""

    def __init__(self, card_id, rating, review_datetime, review_duration=None):
        self.card_id = card_id
        self.rating = rating
        self.review_datetime = review_datetime
        self.review_duration = review_duration

    def to_dict(self):
        return {
            "card_id": self.card_id,
            "rating": int(self.rating),
            "review_datetime": self.review_datetime.isoformat(),
            "review_duration": self.review_duration,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            card_id=d["card_id"],
            rating=int(d["rating"]),
            review_datetime=datetime.fromisoformat(d["review_datetime"]),
            review_duration=d["review_duration"],
        )

    def to_json(self, indent=None):
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, s):
        return cls.from_dict(json.loads(s))


class Scheduler:
    """FSRS-6 scheduler. Formula-for-formula behaviour match with the
    py-fsrs 6.3.2 oracle; see the module docstring for the cap rule."""

    def __init__(self, parameters=DEFAULT_PARAMETERS,
                 desired_retention=DEFAULT_DESIRED_RETENTION,
                 learning_steps=DEFAULT_LEARNING_STEPS,
                 relearning_steps=DEFAULT_RELEARNING_STEPS,
                 maximum_interval=DEFAULT_MAXIMUM_INTERVAL,
                 enable_fuzzing=True):
        self._validate_parameters(parameters)
        self.parameters = tuple(parameters)
        self.desired_retention = desired_retention
        self.learning_steps = tuple(learning_steps)
        self.relearning_steps = tuple(relearning_steps)
        self.maximum_interval = maximum_interval
        self.enable_fuzzing = enable_fuzzing
        self._DECAY = -self.parameters[20]
        self._FACTOR = 0.9 ** (1 / self._DECAY) - 1

    @staticmethod
    def _validate_parameters(parameters):
        if len(parameters) != len(LOWER_BOUNDS_PARAMETERS):
            raise ValueError(
                "Expected %d parameters, got %d."
                % (len(LOWER_BOUNDS_PARAMETERS), len(parameters)))
        errors = []
        for i, (p, lo, hi) in enumerate(
                zip(parameters, LOWER_BOUNDS_PARAMETERS, UPPER_BOUNDS_PARAMETERS)):
            if not lo <= p <= hi:
                errors.append("parameters[%d] = %r is out of bounds: (%r, %r)"
                              % (i, p, lo, hi))
        if errors:
            raise ValueError("One or more parameters are out of bounds:\n"
                             + "\n".join(errors))

    def get_card_retrievability(self, card, current_datetime=None):
        """Predicted probability of correct recall at current_datetime."""
        if card.last_review is None or card.stability is None:
            return 0
        if current_datetime is None:
            current_datetime = datetime.now(timezone.utc)
        elapsed_days = max(0, (current_datetime - card.last_review).days)
        return (1 + self._FACTOR * elapsed_days / card.stability) ** self._DECAY

    def review_card(self, card, rating, review_datetime=None, review_duration=None):
        """Review a card; returns (updated_card, review_log). The input
        card is not mutated (a copy is reviewed, as the oracle does)."""
        _require_utc(review_datetime, "review_datetime")
        card = copy(card)
        if review_datetime is None:
            review_datetime = datetime.now(timezone.utc)
        days_since = ((review_datetime - card.last_review).days
                      if card.last_review else None)

        if card.state == STATE_LEARNING:
            assert card.step is not None
            if card.stability is None or card.difficulty is None:
                card.stability = self._initial_stability(rating)
                card.difficulty = self._initial_difficulty(rating, clamp=True)
            elif days_since is not None and days_since < 1:
                card.stability = self._short_term_stability(card.stability, rating)
                card.difficulty = self._next_difficulty(card.difficulty, rating)
            else:
                card.stability = self._next_stability(
                    card.difficulty, card.stability,
                    self.get_card_retrievability(card, review_datetime), rating)
                card.difficulty = self._next_difficulty(card.difficulty, rating)
            if len(self.learning_steps) == 0 or (
                    card.step >= len(self.learning_steps)
                    and rating in (HARD, GOOD, EASY)):
                card.state = STATE_REVIEW
                card.step = None
                next_interval = timedelta(days=self._next_interval(card.stability))
            else:
                if rating == AGAIN:
                    card.step = 0
                    next_interval = self.learning_steps[card.step]
                elif rating == HARD:
                    if card.step == 0 and len(self.learning_steps) == 1:
                        next_interval = self.learning_steps[0] * 1.5
                    elif card.step == 0 and len(self.learning_steps) >= 2:
                        next_interval = (self.learning_steps[0]
                                         + self.learning_steps[1]) / 2.0
                    else:
                        next_interval = self.learning_steps[card.step]
                elif rating == GOOD:
                    if card.step + 1 == len(self.learning_steps):
                        card.state = STATE_REVIEW
                        card.step = None
                        next_interval = timedelta(days=self._next_interval(card.stability))
                    else:
                        card.step += 1
                        next_interval = self.learning_steps[card.step]
                elif rating == EASY:
                    card.state = STATE_REVIEW
                    card.step = None
                    next_interval = timedelta(days=self._next_interval(card.stability))
                else:
                    raise ValueError("Unknown rating: %r" % (rating,))

        elif card.state == STATE_REVIEW:
            assert card.stability is not None
            assert card.difficulty is not None
            if days_since is not None and days_since < 1:
                card.stability = self._short_term_stability(card.stability, rating)
            else:
                card.stability = self._next_stability(
                    card.difficulty, card.stability,
                    self.get_card_retrievability(card, review_datetime), rating)
            card.difficulty = self._next_difficulty(card.difficulty, rating)
            if rating == AGAIN:
                if len(self.relearning_steps) == 0:
                    next_interval = timedelta(days=self._next_interval(card.stability))
                else:
                    card.state = STATE_RELEARNING
                    card.step = 0
                    next_interval = self.relearning_steps[card.step]
            elif rating in (HARD, GOOD, EASY):
                next_interval = timedelta(days=self._next_interval(card.stability))
            else:
                raise ValueError("Unknown rating: %r" % (rating,))

        elif card.state == STATE_RELEARNING:
            assert card.stability is not None
            assert card.difficulty is not None
            assert card.step is not None
            if days_since is not None and days_since < 1:
                card.stability = self._short_term_stability(card.stability, rating)
                card.difficulty = self._next_difficulty(card.difficulty, rating)
            else:
                card.stability = self._next_stability(
                    card.difficulty, card.stability,
                    self.get_card_retrievability(card, review_datetime), rating)
                card.difficulty = self._next_difficulty(card.difficulty, rating)
            if len(self.relearning_steps) == 0 or (
                    card.step >= len(self.relearning_steps)
                    and rating in (HARD, GOOD, EASY)):
                card.state = STATE_REVIEW
                card.step = None
                next_interval = timedelta(days=self._next_interval(card.stability))
            else:
                if rating == AGAIN:
                    card.step = 0
                    next_interval = self.relearning_steps[card.step]
                elif rating == HARD:
                    if card.step == 0 and len(self.relearning_steps) == 1:
                        next_interval = self.relearning_steps[0] * 1.5
                    elif card.step == 0 and len(self.relearning_steps) >= 2:
                        next_interval = (self.relearning_steps[0]
                                         + self.relearning_steps[1]) / 2.0
                    else:
                        next_interval = self.relearning_steps[card.step]
                elif rating == GOOD:
                    if card.step + 1 == len(self.relearning_steps):
                        card.state = STATE_REVIEW
                        card.step = None
                        next_interval = timedelta(days=self._next_interval(card.stability))
                    else:
                        card.step += 1
                        next_interval = self.relearning_steps[card.step]
                elif rating == EASY:
                    card.state = STATE_REVIEW
                    card.step = None
                    next_interval = timedelta(days=self._next_interval(card.stability))
                else:
                    raise ValueError("Unknown rating: %r" % (rating,))
        else:
            raise ValueError("Unknown card state: %r" % (card.state,))

        if self.enable_fuzzing and card.state == STATE_REVIEW:
            next_interval = self._get_fuzzed_interval(next_interval)

        card.due = review_datetime + next_interval
        card.last_review = review_datetime
        log = ReviewLog(card.card_id, rating, review_datetime, review_duration)
        return card, log

    def reschedule_card(self, card, review_logs):
        """Replay a card's logs through this scheduler from scratch."""
        for log in review_logs:
            if log.card_id != card.card_id:
                raise ValueError(
                    "ReviewLog card_id %r does not match Card card_id %r"
                    % (log.card_id, card.card_id))
        logs = sorted(review_logs, key=lambda l: l.review_datetime)
        rescheduled = Card(card_id=card.card_id, due=card.due)
        for log in logs:
            rescheduled, _ = self.review_card(
                rescheduled, log.rating, log.review_datetime)
        return rescheduled

    def to_dict(self):
        return {
            "parameters": list(self.parameters),
            "desired_retention": self.desired_retention,
            "learning_steps": [int(s.total_seconds()) for s in self.learning_steps],
            "relearning_steps": [int(s.total_seconds()) for s in self.relearning_steps],
            "maximum_interval": self.maximum_interval,
            "enable_fuzzing": self.enable_fuzzing,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            parameters=d["parameters"],
            desired_retention=d["desired_retention"],
            learning_steps=[timedelta(seconds=s) for s in d["learning_steps"]],
            relearning_steps=[timedelta(seconds=s) for s in d["relearning_steps"]],
            maximum_interval=d["maximum_interval"],
            enable_fuzzing=d["enable_fuzzing"],
        )

    def to_json(self, indent=None):
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, s):
        return cls.from_dict(json.loads(s))

    # -- internals (formula-for-formula with the oracle) ---------------------

    @staticmethod
    def _clamp_difficulty(difficulty):
        return min(max(difficulty, MIN_DIFFICULTY), MAX_DIFFICULTY)

    @staticmethod
    def _clamp_stability(stability):
        return max(stability, STABILITY_MIN)

    def _initial_stability(self, rating):
        return self._clamp_stability(self.parameters[rating - 1])

    def _initial_difficulty(self, rating, clamp):
        d = self.parameters[4] - (math.e ** (self.parameters[5] * (rating - 1))) + 1
        return self._clamp_difficulty(d) if clamp else d

    def _next_interval(self, stability):
        ivl = (stability / self._FACTOR) * (
            (self.desired_retention ** (1 / self._DECAY)) - 1)
        ivl = round(ivl)
        ivl = max(ivl, 1)
        ivl = min(ivl, self.maximum_interval)
        return ivl

    def _short_term_stability(self, stability, rating):
        inc = (math.e ** (self.parameters[17] * (rating - 3 + self.parameters[18]))
               ) * (stability ** -self.parameters[19])
        if rating in (HARD, GOOD, EASY):
            inc = max(inc, 1.0)
        return self._clamp_stability(stability * inc)

    def _next_difficulty(self, difficulty, rating):
        def _linear_damping(delta, diff):
            return (10.0 - diff) * delta / 9.0

        def _mean_reversion(a1, a2):
            return self.parameters[7] * a1 + (1 - self.parameters[7]) * a2

        arg_1 = self._initial_difficulty(EASY, clamp=False)
        delta = -(self.parameters[6] * (rating - 3))
        arg_2 = difficulty + _linear_damping(delta, difficulty)
        return self._clamp_difficulty(_mean_reversion(arg_1, arg_2))

    def _next_stability(self, difficulty, stability, retrievability, rating):
        if rating == AGAIN:
            nxt = self._next_forget_stability(difficulty, stability, retrievability)
        elif rating in (HARD, GOOD, EASY):
            nxt = self._next_recall_stability(difficulty, stability, retrievability, rating)
        else:
            raise ValueError("Unknown rating: %r" % (rating,))
        return self._clamp_stability(nxt)

    def _next_forget_stability(self, difficulty, stability, retrievability):
        long_term = (self.parameters[11]
                     * (difficulty ** -self.parameters[12])
                     * (((stability + 1) ** self.parameters[13]) - 1)
                     * (math.e ** ((1 - retrievability) * self.parameters[14])))
        short_term = stability / (math.e ** (self.parameters[17] * self.parameters[18]))
        return min(long_term, short_term)

    def _next_recall_stability(self, difficulty, stability, retrievability, rating):
        hard_penalty = self.parameters[15] if rating == HARD else 1
        easy_bonus = self.parameters[16] if rating == EASY else 1
        return stability * (
            1
            + (math.e ** self.parameters[8])
            * (11 - difficulty)
            * (stability ** -self.parameters[9])
            * ((math.e ** ((1 - retrievability) * self.parameters[10])) - 1)
            * hard_penalty
            * easy_bonus
        )

    def _get_fuzzed_interval(self, interval):
        interval_days = interval.days
        if interval_days < 2.5:
            return interval

        def _fuzz_range(days):
            delta = 1.0
            for r in FUZZ_RANGES:
                delta += r["factor"] * max(min(float(days), r["end"]) - r["start"], 0.0)
            lo = max(2, round(days - delta))
            hi = min(round(days + delta), self.maximum_interval)
            lo = min(lo, hi)
            return lo, hi

        lo, hi = _fuzz_range(interval_days)
        fuzzed = random.random() * (hi - lo + 1) + lo
        fuzzed = min(round(fuzzed), self.maximum_interval)
        return timedelta(days=fuzzed)


__all__ = [
    "AGAIN", "HARD", "GOOD", "EASY", "RATINGS", "RATING_NAMES",
    "STATE_LEARNING", "STATE_REVIEW", "STATE_RELEARNING", "STATE_NAMES",
    "DEFAULT_PARAMETERS", "STABILITY_MIN", "DEFAULT_DESIRED_RETENTION",
    "DEFAULT_MAXIMUM_INTERVAL", "Card", "ReviewLog", "Scheduler",
]
