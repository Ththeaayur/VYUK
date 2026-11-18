"""Custom wake-word enrolment + evaluation (Wave 3, v5.58).

What ships: the enrolment record, its validation, and the
evaluation gate. What does NOT ship: a custom phrase as a live
wake word on the strength of enrolment alone.

Enrolment (user-recorded phrase):
  * The user supplies a phrase (words they recorded, or typed for
    a phrase they intend to record). The record stores the phrase,
    the number of enrolment utterances, and the conditions they
    were recorded under (distance, accent tag) — nothing else.
    No audio is stored here: enrolment audio stays with the
    caller (on-device capture), and this module never uploads,
    streams, or persists it anywhere. Telemetry is OFF by shape:
    nothing in this module emits anything.
  * Validation is strict and boring on purpose: 2-4 words, only
    letters/spaces/apostrophes, no existing wake phrase, no
    single word (single words false-fire far too easily — the
    "supernova"/"casanova" lesson in core/voice/wake_word.py applies
    with interest to a one-word custom phrase).

Evaluation (the bar stands):
  * `evaluate_custom_phrase` runs the phrase's rig scores
    through the SAME section-10 gate as the bake-off
    (core/wake_bakeoff.py): <=0.5 false-accepts/hour AND >=95%
    true-accept at 1.5 m. A phrase that has not been measured at
    1.5 m, or has no negative-playback hours behind it, FAILS
    with reason "not-measured" — it is reported as NOT SHIPPED
    with its (absent) numbers, never as a pass.
  * A phrase that passes the gate still needs a trained
    classifier behind it (a grammar entry alone is not a trained
    head — see the enrolment-vs-training note in
    core/wake_trainer.py). The gate is necessary, not sufficient.

v5.58 status: enrolment + evaluation are implemented and tested
against stub scores. NO custom phrase has been measured against
the section-10 targets (no positive set at 1.5 m, no negative
corpus), so NO custom wake phrase ships in v5.58. The existing
WAKE_PHRASE_ALT path in core/voice/wake_word.py is untouched: a phrase
the owner sets there still joins the default list exactly as
before, and this module neither enables nor disables it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .wake_bakeoff import (
    FALSE_ACCEPTS_PER_HOUR_MAX,
    TRUE_ACCEPT_DISTANCE_M,
    TRUE_ACCEPT_MIN,
    HeadResult,
    ScoredClip,
    evaluate_head,
)

_PHRASE_RE = re.compile(r"^[a-z][a-z' ]*[a-z]$|^[a-z]$")
_EXISTING_PHRASES = ("via oak", "hey via oak", "hey vyuk")

# Minimum enrolment utterances before evaluation is even
# attempted: fewer than this, and the phrase has no evidence
# base at all (it is still recordable — just not evaluable).
MIN_ENROLMENT_UTTERANCES = 5


@dataclass
class CustomWakeEnrolment:
    """One user-recorded custom wake phrase, on-device only."""

    phrase: str
    utterances: int = 0
    distance_m: float | None = None
    accent: str | None = None
    audio_stored_here: bool = False  # invariant: always False

    def __post_init__(self) -> None:
        self.phrase = self.phrase.strip().lower()

    @property
    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        words = self.phrase.split()
        if not 2 <= len(words) <= 4:
            errors.append("phrase must be 2-4 words")
        if self.phrase and not _PHRASE_RE.match(self.phrase):
            errors.append("phrase may contain only letters, spaces, "
                          "apostrophes")
        if self.phrase in _EXISTING_PHRASES:
            errors.append("phrase duplicates an existing wake phrase")
        if self.utterances < 0:
            errors.append("utterance count cannot be negative")
        return errors

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors

    @property
    def evaluable(self) -> bool:
        """Valid AND enough enrolment utterances to mean anything."""
        return self.is_valid and self.utterances >= MIN_ENROLMENT_UTTERANCES


def validate_custom_wake_phrase(phrase: str) -> list[str]:
    """Validation errors for a phrase string, [] when valid."""
    return CustomWakeEnrolment(phrase=phrase).validation_errors


@dataclass
class CustomPhraseEvaluation:
    """Evaluation outcome: the numbers, or their honest absence."""

    phrase: str
    shipped: bool
    reason: str
    result: HeadResult | None = None


def evaluate_custom_phrase(
        enrolment: CustomWakeEnrolment, clips: list[ScoredClip],
        threshold: float, corpus_path: str,
        negative_hours: float) -> CustomPhraseEvaluation:
    """Gate a custom phrase against the section-10 targets.

    Returns shipped=False with an explicit reason whenever the
    phrase is invalid, under-enrolled, unmeasured at 1.5 m, or
    fails either target. The caller reports the returned numbers;
    a not-measured phrase reports NO numbers, not zeroes dressed
    up as a measurement (zero false accepts over zero hours is
    not a measurement).
    """
    if not enrolment.is_valid:
        return CustomPhraseEvaluation(
            phrase=enrolment.phrase, shipped=False,
            reason="invalid phrase: "
            + "; ".join(enrolment.validation_errors))
    if not enrolment.evaluable:
        return CustomPhraseEvaluation(
            phrase=enrolment.phrase, shipped=False,
            reason=(f"under-enrolled: {enrolment.utterances} utterances, "
                    f"need >= {MIN_ENROLMENT_UTTERANCES}"))
    if not corpus_path or negative_hours <= 0 or not clips:
        return CustomPhraseEvaluation(
            phrase=enrolment.phrase, shipped=False,
            reason="not-measured: no rig corpus / scores supplied")
    result = evaluate_head(
        "CUSTOM", clips, threshold, corpus_path, negative_hours,
        distance_m=TRUE_ACCEPT_DISTANCE_M, accent=enrolment.accent)
    if result.true_total <= 0:
        return CustomPhraseEvaluation(
            phrase=enrolment.phrase, shipped=False,
            reason="not-measured at 1.5 m: no positive clips at "
            f"{TRUE_ACCEPT_DISTANCE_M} m", result=result)
    if not result.passes_section10:
        return CustomPhraseEvaluation(
            phrase=enrolment.phrase, shipped=False,
            reason=(f"targets unmet: {result.false_accepts_per_hour:.3f} "
                    f"false-accepts/hour (max "
                    f"{FALSE_ACCEPTS_PER_HOUR_MAX}), "
                    f"{(result.true_accept_rate or 0.0):.1%} true-accept "
                    f"at 1.5 m (min {TRUE_ACCEPT_MIN:.0%})"),
            result=result)
    return CustomPhraseEvaluation(
        phrase=enrolment.phrase, shipped=True,
        reason="meets section-10 targets (trained classifier still "
        "required before live use)", result=result)
