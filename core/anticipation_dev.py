"""Split from core/anticipation.py (skills split B19).
Move only: code below is verbatim from the old module apart from
module-level import lines.
"""

from __future__ import annotations


from dataclasses import dataclass

# INTENTIONAL (skills split B19): moved code resolves these by bare
# name in the predictor module's globals.
from .anticipation_predictor import NextActionPredictor



# ---------------------------------------------------------------------------
# AN-3 / AN-4 — DEV-ONLY proactivity benches.
# These helpers evaluate a gate offline. They are NEVER called by
# suggestion(), predict_next(), or any runtime path in this module.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WhenToSpeakCase:
    """One synthetic bench case: ``context`` describes the moment
    (free text, caller-supplied); ``should_speak`` is the labelled
    ground truth. The bundled dataset is synthetic — no user data."""
    context: str
    should_speak: bool


def dev_when_to_speak_dataset():
    """A small synthetic when-to-speak dataset (AN-3).

    Deliberately balanced and obviously synthetic: it exists so a
    gate can be scored without touching real user activity. Never
    use it as training data for a shipped gate.
    """
    return [
        WhenToSpeakCase("user finished a repeated app switch, next app is near-certain", True),
        WhenToSpeakCase("user explicitly asked for a suggestion", True),
        WhenToSpeakCase("user is typing in a document, no prediction available", False),
        WhenToSpeakCase("prediction confidence is low and user is mid-task", False),
        WhenToSpeakCase("routine trigger fired and routine is due", True),
        WhenToSpeakCase("user just dismissed the last suggestion", False),
    ]


def dev_score_when_to_speak_gate(gate_fn, cases=None):
    """Score a when-to-speak gate (AN-3) over bench cases.

    ``gate_fn(case)`` returns truthy when the gate would speak
    (or a suggestion string — any non-None, non-False value
    counts as speaking; None/False counts as silence). Returns
    counts plus accuracy, false-positive rate (spoke when it
    should not have) and false-negative rate (silent when it
    should have spoken). DEV-ONLY: the result informs bench
    evaluation, never a live decision.
    """
    cases = list(cases) if cases is not None else dev_when_to_speak_dataset()
    total = len(cases)
    spoke = silent = 0
    false_positive = false_negative = 0
    correct = 0
    for case in cases:
        try:
            spoke_now = bool(gate_fn(case))
        except Exception:
            spoke_now = False
        if spoke_now:
            spoke += 1
        else:
            silent += 1
        if spoke_now == bool(case.should_speak):
            correct += 1
        elif spoke_now and not case.should_speak:
            false_positive += 1
        else:
            false_negative += 1
    return {
        "dev_only": True,
        "cases": total,
        "spoke": spoke,
        "silent": silent,
        "correct": correct,
        "accuracy": (correct / total) if total else None,
        "false_positives": false_positive,
        "false_negatives": false_negative,
    }


def dev_score_proactivity(histories_and_expected, predictor=None):
    """Score next-action proactivity (AN-4) over caller-supplied
    (history, expected_next) pairs.

    With ``predictor`` None, a fresh NextActionPredictor is trained
    on each history's own transitions (in-history learning only,
    so the score measures the algorithm, not leaked state). With a
    predictor supplied, its predict_next(history) is used in
    evaluation mode (explicit history — never consuming the live
    scoring slot). Returns top-1 accuracy over the pairs.
    DEV-ONLY: never feeds a live suggestion.
    """
    pairs = list(histories_and_expected)
    total = len(pairs)
    correct = 0
    for history, expected in pairs:
        if predictor is None:
            local = NextActionPredictor()
            for action in history:
                local.observe(action)
            prediction = local.predict_next()
        else:
            prediction = predictor.predict_next(list(history))
        if prediction is not None and prediction.action == str(expected):
            correct += 1
    return {
        "dev_only": True,
        "cases": total,
        "correct": correct,
        "accuracy": (correct / total) if total else None,
    }
