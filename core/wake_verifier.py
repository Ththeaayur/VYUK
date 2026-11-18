"""EA-19 verifier: a second model confirms before she wakes.

Protocol (additive; it wraps a head, it never rewrites one):
  1. The primary head fires (score >= its threshold).
  2. Within VERIFIER_WINDOW_S, the verifier scores the SAME
     utterance window independently.
  3. She wakes ONLY if the verifier score >= its threshold.
     Anything else is logged as a primary fire suppressed by the
     verifier — a rejected false accept, not a wake.

Why this helps, stated without hand-waving: if the verifier's
false-accept behaviour on negative audio is independent of the
primary's (the design assumption, which a real rig run must
CHECK by measuring the joint false-accept rate, not assume),
the combined false-accept rate is approximately the product of
the two rates. The verifier therefore trades a little true
accept (an utterance must now clear two bars) for a large
false-accept reduction — exactly the trade the ROC table in
core/wake_bakeoff.py exists to price. `combined_estimate`
implements that product form ONLY as an estimate, labelled as
such; the measured joint rate from a rig run always wins.

The verifier model itself is injected (a callable), never
imported here: this module has no model dependency, no weights,
and no network access, so the "second model" can be any
licence-clean on-device model the coordinator clears — and,
until one is cleared, the gate simply has no verifier to run
(see `VerifierGate.unavailable()`), which leaves the primary
head's behaviour unchanged rather than inventing confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

# Seconds the verifier has to confirm a primary fire before the
# fire expires unconfirmed. Kept short: a wake that waits is a
# wake the user has already repeated.
VERIFIER_WINDOW_S = 1.5


@dataclass
class VerifierGate:
    """Second-model confirmation gate around a primary head."""

    verifier_threshold: float = 0.5
    window_s: float = VERIFIER_WINDOW_S
    _verifier: object = None  # callable(score_window) -> float | None

    def __init__(self, verifier=None, verifier_threshold: float = 0.5,
                 window_s: float = VERIFIER_WINDOW_S):
        if not 0.0 <= verifier_threshold <= 1.0:
            raise ValueError("verifier threshold must be in [0, 1]")
        if window_s <= 0:
            raise ValueError("verifier window must be positive")
        self.verifier_threshold = verifier_threshold
        self.window_s = window_s
        self._verifier = verifier

    @property
    def available(self) -> bool:
        """False until a licence-clean verifier model is injected."""
        return self._verifier is not None

    @classmethod
    def unavailable(cls) -> "VerifierGate":
        """The v5.58 state: no cleared second model exists, so the
        gate cannot confirm anything and must not pretend to."""
        return cls(verifier=None)

    def confirm(self, verifier_score: float | None) -> bool:
        """True only when a verifier MODEL is installed, a score
        exists, and it clears the bar. A score handed to a gate
        with no verifier installed NEVER confirms — there is no
        second model behind it, so the number proves nothing.
        None (no verifier, failed inference) NEVER confirms."""
        if not self.available or verifier_score is None:
            return False
        return verifier_score >= self.verifier_threshold

    def decide(self, primary_fired: bool,
               verifier_score: float | None) -> str:
        """'wake' / 'suppressed' / 'no-fire', for logging."""
        if not primary_fired:
            return "no-fire"
        return "wake" if self.confirm(verifier_score) else "suppressed"

    def combined_false_accept_estimate(
            self, primary_fa_per_hour: float,
            verifier_fa_rate: float) -> float:
        """Product-form ESTIMATE of the combined false-accept
        rate, valid only under independence of the two models'
        false accepts. Labelled estimate everywhere it is used;
        a rig run's measured joint rate supersedes it."""
        if primary_fa_per_hour < 0 or not 0.0 <= verifier_fa_rate <= 1.0:
            raise ValueError("invalid rates for combined estimate")
        return primary_fa_per_hour * verifier_fa_rate
