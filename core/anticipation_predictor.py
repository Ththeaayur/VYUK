"""Split from core/anticipation.py (skills split B19).
Move only: code below is verbatim from the old module apart from
module-level import lines.
"""

from __future__ import annotations


from collections import Counter, defaultdict
from dataclasses import dataclass

# INTENTIONAL (skills split B19): moved code resolves these by bare
# name in the gates module's globals.
from .anticipation_gates import MIN_EVIDENCE_SAMPLES, SUGGESTION_CONFIDENCE_THRESHOLD



# ---------------------------------------------------------------------------
# AN-1 River: online next-action prediction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Prediction:
    """A raw next-action prediction (evaluation surface, NOT the
    speakable surface). ``confidence`` is the winning transition's
    share of all transitions out of the previous action;
    ``support`` / ``total`` expose the raw counts so a caller can
    see exactly how thin the evidence is."""
    action: str
    confidence: float
    support: int
    total: int


class NextActionPredictor:
    """Online bigram predictor over the CA-1 action stream.

    Learning: ``observe(action)`` records the transition
    previous -> action. Scoring (progressive validation): the
    prediction made at the previous step is scored against the
    action actually observed, so ``accuracy`` is always measured
    against real outcomes, never against the model's own
    confidence. ``suggestion()`` is the ONLY speakable surface
    and enforces both gates (evidence floor + confidence
    threshold); it returns the bare action string or None.
    """

    def __init__(self):
        self._transitions = defaultdict(Counter)
        self._history: list = []
        self._pending_prediction = None  # Prediction awaiting scoring
        self._scored = 0
        self._correct = 0

    # -- learning / scoring ------------------------------------------------

    def observe(self, action) -> None:
        """Observe one action (app token or plain action string).

        Scores any pending prediction first, then learns the
        transition from the previous action. ``None`` / empty
        actions are ignored entirely (they are not evidence).
        """
        action = str(action).strip() if action is not None else ""
        if not action:
            return
        if self._pending_prediction is not None:
            self._scored += 1
            if self._pending_prediction.action == action:
                self._correct += 1
            self._pending_prediction = None
        if self._history:
            self._transitions[self._history[-1]][action] += 1
        self._history.append(action)

    def observe_event(self, event) -> None:
        """Observe an ActivityEvent (its app token is the action).
        A None event — refused or suppressed — is ignored, so
        denylisted activity never trains the predictor."""
        if event is None:
            return
        app = getattr(event, "app", None)
        if app:
            self.observe(app)

    # -- prediction ----------------------------------------------------------

    def predict_next(self, history=None):
        """Raw prediction from the last action, or None.

        ``history`` overrides the predictor's own history for
        evaluation; the default uses observed history. The
        returned prediction is stored as pending ONLY when it
        comes from live history (an evaluation call with an
        explicit history must not consume the live scoring slot).
        Ties break deterministically (highest count, then
        alphabetical) so results are reproducible.
        """
        live = history is None
        seq = self._history if live else [str(a) for a in history]
        if not seq:
            return None
        last = seq[-1]
        counts = self._transitions.get(last)
        if not counts:
            return None
        total = sum(counts.values())
        if total <= 0:
            return None
        best_action = sorted(counts.items(),
                             key=lambda kv: (-kv[1], kv[0]))[0][0]
        prediction = Prediction(
            action=best_action,
            confidence=counts[best_action] / total,
            support=int(counts[best_action]),
            total=int(total),
        )
        if live:
            self._pending_prediction = prediction
        return prediction

    def suggestion(self):
        """The speakable surface: an action string, or None.

        Returns None (silence) when there is no prediction, when
        fewer than MIN_EVIDENCE_SAMPLES predictions have been
        scored (no accuracy evidence yet), or when confidence is
        below SUGGESTION_CONFIDENCE_THRESHOLD. A weak prediction
        NEVER speaks, and never speaks hedged — silence, not
        "maybe X".
        """
        _action, reason = self.suggestion_with_reason()
        return _action

    def suggestion_with_reason(self):
        """(action | None, reason) for the suggestion gate.

        Reasons: "ok", "no-history", "no-transition",
        "insufficient-evidence", "below-threshold". Callers and
        tests should prefer this over suggestion() when they need
        to know WHY silence was chosen.
        """
        if not self._history:
            return None, "no-history"
        prediction = self.predict_next()
        if prediction is None:
            return None, "no-transition"
        if self._scored < MIN_EVIDENCE_SAMPLES:
            return None, "insufficient-evidence"
        if prediction.confidence < SUGGESTION_CONFIDENCE_THRESHOLD:
            return None, "below-threshold"
        return prediction.action, "ok"

    # -- honest accuracy -------------------------------------------------------

    @property
    def scored(self) -> int:
        return int(self._scored)

    @property
    def correct(self) -> int:
        return int(self._correct)

    @property
    def accuracy(self):
        """Measured accuracy over scored predictions, or None
        when nothing has been scored yet (no evidence is reported
        as no number, never as 0 or as a prior)."""
        if self._scored <= 0:
            return None
        return self._correct / self._scored

    def stats(self) -> dict:
        return {
            "scored": self._scored,
            "correct": self._correct,
            "accuracy": self.accuracy,
            "history_len": len(self._history),
            "transitions": sum(sum(c.values())
                               for c in self._transitions.values()),
        }
