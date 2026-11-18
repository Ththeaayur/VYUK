"""Wave 4 P21 + P22 slices — grounding lens, calibration, interruption budget.

Stdlib only, no capture, no execution. The grounding lens shows
the evidence a claim rests on; the calibration sequence checks a
stated confidence against a stated outcome; the interruption
budget enforces the Wave 2 visible rule in control breadth.

Rules, enforced in code:
  * Grounding lens: a claim with no evidence is shown as
    ungrounded, never dressed up. Evidence is caller-supplied
    source lines; this module never invents a source.
  * Calibration: a claimed confidence is compared against the
    caller-supplied outcome (correct / incorrect). The result
    names over- or under-confidence plainly; it never adjusts a
    confidence silently.
  * Interruption budget: a caller-supplied budget (max
    interruptions per mission). Exceeding it holds further
    interruptions, except items the caller marks urgent by his
    own rules — the urgent rule is caller-supplied, never assumed.
    Held-back items are listed, never dropped.
"""

from __future__ import annotations


def ground_claim(claim: str, evidence: list) -> dict:
    """Show the evidence for a claim, or show it as ungrounded."""
    if not claim:
        return {"ok": False, "reason": "claim is empty"}
    if not evidence:
        return {"ok": True, "claim": claim, "grounded": False,
                "evidence": [],
                "note": "ungrounded: no evidence supplied"}
    return {"ok": True, "claim": claim, "grounded": True,
            "evidence": list(evidence)}


def calibration_check(claimed_confidence: float, outcome: str) -> dict:
    """Check a claimed confidence against a caller-supplied outcome.

    outcome must be "correct" or "incorrect". A high confidence
    with an incorrect outcome is over-confidence; a low confidence
    with a correct outcome is under-confidence; anything else is
    consistent. No confidence is adjusted here.
    """
    if outcome not in ("correct", "incorrect"):
        return {"ok": False, "reason": "outcome must be 'correct' or "
                "'incorrect'"}
    if not isinstance(claimed_confidence, (int, float)) \
            or isinstance(claimed_confidence, bool) \
            or claimed_confidence < 0 or claimed_confidence > 1:
        return {"ok": False, "reason": "confidence outside [0, 1]"}
    if outcome == "incorrect" and claimed_confidence >= 0.7:
        verdict = "over-confident"
    elif outcome == "correct" and claimed_confidence <= 0.3:
        verdict = "under-confident"
    else:
        verdict = "consistent"
    return {"ok": True, "claimed": claimed_confidence, "outcome": outcome,
            "verdict": verdict}


class InterruptionBudget:
    """Enforce the interruption budget (Wave 2 rule, Wave 4 breadth)."""

    def __init__(self, max_interruptions: int):
        if not isinstance(max_interruptions, int) or max_interruptions < 0:
            raise ValueError("max_interruptions must be a non-negative int")
        self.max = max_interruptions
        self.used = 0
        self.held = []

    def request(self, item: str, *, urgent_by_owner_rules: bool = False) -> dict:
        """Request an interruption. Over budget: held, unless urgent
        by the caller's own rules — and even then it is recorded
        as an urgent exception, never as a normal spend."""
        if self.used < self.max:
            self.used += 1
            return {"ok": True, "item": item, "used": self.used,
                    "note": "within budget"}
        if urgent_by_owner_rules:
            return {"ok": True, "item": item, "used": self.used,
                    "note": "urgent exception by owner rules; budget not spent"}
        self.held.append(item)
        return {"ok": False, "item": item, "used": self.used,
                "reason": "interruption budget exceeded; item held, not dropped"}

    def held_items(self) -> list:
        return list(self.held)

    def remaining(self) -> int:
        return self.max - self.used
