"""When-to-intervene scoring + proactivity benchmark (R7-8, ProactiveAgent pattern).

MEASUREMENT MODE ONLY. This module scores whether VYUK *would*
intervene and records what it *would* have done. It NEVER acts:

- no tool is executed here (no _execute_tool call, no funnel
  dispatch, no planner) — the scorer's output is a scored
  suggestion with its reasons, shown to the user;
- no background sensing is added (no watcher, no ActivityWatch,
  no polling thread, no scheduled loop) — every score is computed
  from evidence the caller supplies, evidence the tree already
  collects (the AN-1 predictor's confidence/evidence count, the
  dismissal state, the routine-due state, a watcher notice kind);
- no intervention is ever wired to execute without the owner's
  explicit approval. Acting stays behind the owner's decision
  (and C-14). A benchmark with no scorer, or a scorer with no
  benchmark, would be half a batch — this module ships both.

Pattern source: thunlp/ProactiveAgent (ICLR 2025) — a
when-to-intervene reward model (their 0.918 F1, on THEIR bench)
plus ProactiveBench (6,790 real events) + eval harness. This is
NOT the framework: nothing is vendored, nothing is installed
(their licence is split Apache-2.0 vs BSD-3-Clause and the
fine-tuned model path needs GPU/LLM training). The pattern is
ported as VYUK's own explainable scorer (no fine-tuned model,
every weight named and every contribution reported) and VYUK's
own scripted bench (36 scenarios with known right/wrong
intervention decisions, scored for precision/recall/F1).
VYUK's scorer gets its own measured number from VYUK's own
bench — the 0.918 is theirs, never quoted as ours.

The scorer
----------
score_intervention(candidate) -> InterventionScore(score, threshold,
decision, reasons, contributions). Pure function, no I/O.

Weights (all named, all reported per score):
- confidence 0.90 x predictor confidence. Mirrors AN-1's own
  floor: SUGGESTION_CONFIDENCE_THRESHOLD is 0.70, and 0.90 x 0.70
  = 0.63 clears the 0.60 threshold on its own — a prediction
  AN-1 would speak is a prediction this scorer would act on.
  Below ~0.67 confidence a second signal is needed (0.90 x 0.67
  = 0.60); at 0.65 the score is 0.585 and the answer is silent.
- explicit request +0.45 (the user asked — the strongest signal;
  it also waives the mid-task penalty and bypasses the silence
  floor, exactly as an explicit ask should).
- routine due +0.35 (a due routine is a candidate of its own).
- watcher notice: urgent (battery, network) +0.15, soft
  (downloads, idle) +0.05, foreground +0.00 (an app switch alone
  is weak evidence — see bench case "foreground-switch-to-
  calendar", the scorer's known miss).
- dismissed recently -0.35 (the user just said no — do not
  re-intervene on the same evidence).
- user mid-task -0.25 (do not interrupt a focused session),
  waived when the request is explicit.
- no prediction available -0.20 (nothing to offer).

Silence floor (shared with AN-1): MIN_EVIDENCE_SAMPLES = 5
(core/anticipation_gates.py — the floor is defined once, there).
Below the floor, with neither an explicit request nor a due
routine, the decision is silent whatever the score — the same
progressive-validation rule AN-1 suggestion() applies. The
score is still computed and reported, so the floor itself is
visible in the reasons.

The bench
---------
PROACTIVITY_BENCH: 36 scripted scenarios (18 should-intervene,
18 should-stay-silent), each with the evidence dict, the
expected decision, and a note saying WHY. score_bench() runs
the scorer over them and reports precision / recall / F1 /
accuracy plus the per-case verdicts. Three cases are deliberate
honest misses the scorer is expected to get wrong — they pin
what the scorer CANNOT see (a low-battery notice vs a critical
one; a trivial routine vs an important one; a foreground switch
whose app IS the meeting context). A bench the scorer aces
100% would be a bench that proves nothing; the misses are the
point. The bench is deterministic: same scorer, same numbers.

Storage
-------
data/proactivity_runs.db (SQLite, WAL, mirroring core/dagflow.py):
score_measurements (one row per scored candidate) and bench_runs
(one row per bench run, with the full per-case verdicts). Every
write is re-read before the caller is told it landed
(A4-filestat / A9-measure pattern): a failed write is reported,
never silently claimed.

Funnel surface (4 tools, "proactivity" free class):
- score_intervention (effectful: records the measurement),
- run_proactivity_bench (effectful: records the bench run),
- list_proactivity_scores (read-only),
- proactivity_bench_results (read-only).
Grouped in "System & apps" (core/catalogue_groups_data.py).

Stdlib only. No new dependency. No THIRD_PARTY row is owed
(pattern-only build, nothing vendored).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .anticipation_gates import MIN_EVIDENCE_SAMPLES

# ---------------------------------------------------------------------------
# Named constants — the scorer reads these, never magic numbers inline.
# ---------------------------------------------------------------------------

#: Default intervention threshold. A score at or above this, with
#: the silence floor satisfied, is "intervene".
DEFAULT_THRESHOLD = 0.60

#: Confidence weight. 0.90 x AN-1's 0.70 threshold = 0.63, so a
#: prediction AN-1 would speak is a prediction this scorer would
#: act on; below 0.70 confidence a second signal is needed.
CONFIDENCE_WEIGHT = 0.90

#: The user asked for a suggestion — the strongest single signal.
EXPLICIT_REQUEST_WEIGHT = 0.45

#: A routine is due — a candidate of its own, not a prediction.
ROUTINE_DUE_WEIGHT = 0.35

#: Watcher notice weights. Urgent kinds (battery, network) move
#: the score; soft kinds (downloads, idle) barely do; a
#: foreground switch alone carries no weight.
URGENT_WATCHER_WEIGHT = 0.15
SOFT_WATCHER_WEIGHT = 0.05
URGENT_WATCHER_KINDS = frozenset({"battery", "network"})
SOFT_WATCHER_KINDS = frozenset({"downloads", "idle"})

#: The user dismissed the last suggestion — do not re-intervene
#: on the same evidence.
DISMISSED_PENALTY = 0.35

#: The user is mid-task — do not interrupt a focused session.
#: Waived when the request is explicit (the user asked).
MID_TASK_PENALTY = 0.25

#: There is no prediction to offer at all.
NO_PREDICTION_PENALTY = 0.20


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InterventionCandidate:
    """One candidate intervention, with the evidence behind it.

    ``evidence`` keys (all optional, defaults are the silent ones):
    confidence (0..1), evidence_samples (int), explicit_request
    (bool), routine_due (bool), dismissed_recently (bool),
    user_mid_task (bool), watcher_kind (str|None), prediction_available
    (bool). The caller supplies the evidence from state the tree
    already collects; this module collects nothing itself.
    """

    text: str
    kind: str = "suggestion"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InterventionScore:
    """The scored answer. ``decision`` is "intervene" or "silent";
    ``reasons`` is the human-readable why, in application order;
    ``contributions`` names every weight that moved the score."""

    score: float
    threshold: float
    decision: str
    reasons: tuple[str, ...]
    contributions: dict[str, float]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_intervention(
    candidate: InterventionCandidate,
    threshold: float = DEFAULT_THRESHOLD,
) -> InterventionScore:
    """Score one candidate intervention. Pure function, no I/O.

    Returns the score, the decision, and the reasons — never an
    action. The decision is "intervene" only when the score
    clears the threshold AND the AN-1 silence floor is satisfied
    (evidence_samples >= MIN_EVIDENCE_SAMPLES, or an explicit
    request, or a due routine).
    """
    ev = candidate.evidence or {}
    confidence = _clamp01(ev.get("confidence", 0.0))
    samples = int(ev.get("evidence_samples", 0) or 0)
    explicit = bool(ev.get("explicit_request", False))
    routine_due = bool(ev.get("routine_due", False))
    dismissed = bool(ev.get("dismissed_recently", False))
    mid_task = bool(ev.get("user_mid_task", False))
    watcher = ev.get("watcher_kind")
    watcher = str(watcher).strip().lower() if watcher else None
    prediction_available = bool(ev.get("prediction_available", True))

    contributions: dict[str, float] = {}
    reasons: list[str] = []

    conf_part = CONFIDENCE_WEIGHT * confidence
    contributions["confidence"] = round(conf_part, 4)
    reasons.append(
        f"confidence {confidence:.2f} x {CONFIDENCE_WEIGHT} = {conf_part:.2f}")

    if explicit:
        contributions["explicit_request"] = EXPLICIT_REQUEST_WEIGHT
        reasons.append(
            f"explicit request +{EXPLICIT_REQUEST_WEIGHT:.2f}")
    if routine_due:
        contributions["routine_due"] = ROUTINE_DUE_WEIGHT
        reasons.append(f"routine due +{ROUTINE_DUE_WEIGHT:.2f}")
    if watcher in URGENT_WATCHER_KINDS:
        contributions["watcher_urgent"] = URGENT_WATCHER_WEIGHT
        reasons.append(
            f"watcher {watcher} (urgent) +{URGENT_WATCHER_WEIGHT:.2f}")
    elif watcher in SOFT_WATCHER_KINDS:
        contributions["watcher_soft"] = SOFT_WATCHER_WEIGHT
        reasons.append(
            f"watcher {watcher} (soft) +{SOFT_WATCHER_WEIGHT:.2f}")
    elif watcher:
        contributions["watcher"] = 0.0
        reasons.append(
            f"watcher {watcher} (no weight — app switch alone is weak evidence)")
    if dismissed:
        contributions["dismissed_recently"] = -DISMISSED_PENALTY
        reasons.append(
            f"dismissed recently -{DISMISSED_PENALTY:.2f}")
    if mid_task and not explicit:
        contributions["user_mid_task"] = -MID_TASK_PENALTY
        reasons.append(f"user mid-task -{MID_TASK_PENALTY:.2f}")
    elif mid_task and explicit:
        reasons.append(
            "user mid-task (penalty waived: explicit request)")
    if not prediction_available:
        contributions["no_prediction"] = -NO_PREDICTION_PENALTY
        reasons.append(
            f"no prediction available -{NO_PREDICTION_PENALTY:.2f}")

    score = _clamp01(sum(contributions.values()))

    # Silence floor (shared with AN-1): below MIN_EVIDENCE_SAMPLES
    # with neither an explicit request nor a due routine, stay
    # silent whatever the score. The score is still reported, so
    # the floor itself is visible in the reasons.
    floor_silent = (
        samples < MIN_EVIDENCE_SAMPLES and not explicit and not routine_due)
    if floor_silent:
        reasons.append(
            f"insufficient-evidence: {samples} samples < "
            f"{MIN_EVIDENCE_SAMPLES} (silence floor)")
    decision = "intervene" if (score >= threshold and not floor_silent) \
        else "silent"
    reasons.append(
        f"score {score:.2f} vs threshold {threshold:.2f} -> {decision}")
    return InterventionScore(
        score=score,
        threshold=float(threshold),
        decision=decision,
        reasons=tuple(reasons),
        contributions=contributions,
    )


def evidence_from_prediction(
    prediction: Any,
    scored: int,
    *,
    candidate_text: str,
    kind: str = "suggestion",
    dismissed_recently: bool = False,
    user_mid_task: bool = False,
    routine_due: bool = False,
    watcher_kind: Optional[str] = None,
) -> InterventionCandidate:
    """Build a candidate from an AN-1 predictor prediction.

    Pure converter — it reads only what the caller hands it
    (a Prediction object or None, and the scored-sample count
    from predictor.stats()). It never calls predict_next() itself:
    calling predict_next() would consume the predictor's live
    scoring slot, which would perturb AN-1's progressive
    validation. The caller owns the predictor; this helper only
    translates its state into scorer evidence.
    """
    confidence = 0.0
    prediction_available = False
    if prediction is not None:
        confidence = _clamp01(getattr(prediction, "confidence", 0.0))
        prediction_available = True
    return InterventionCandidate(
        text=candidate_text,
        kind=kind,
        evidence={
            "confidence": confidence,
            "evidence_samples": int(scored or 0),
            "explicit_request": False,
            "routine_due": bool(routine_due),
            "dismissed_recently": bool(dismissed_recently),
            "user_mid_task": bool(user_mid_task),
            "watcher_kind": watcher_kind,
            "prediction_available": prediction_available,
        },
    )


# ---------------------------------------------------------------------------
# Bench — VYUK's own ProactiveBench-style harness (36 scripted cases)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProactivityCase:
    """One scripted scenario with a known right/wrong decision."""

    name: str
    context: str
    candidate_text: str
    kind: str
    evidence: dict[str, Any]
    should_intervene: bool
    note: str


def _case(name: str, context: str, candidate_text: str, kind: str,
          evidence: dict[str, Any], should_intervene: bool,
          note: str) -> ProactivityCase:
    return ProactivityCase(
        name=name,
        context=context,
        candidate_text=candidate_text,
        kind=kind,
        evidence=evidence,
        should_intervene=should_intervene,
        note=note,
    )


#: The bench. 18 should-intervene, 18 should-stay-silent. Three
#: cases are deliberate honest misses the scorer is expected to
#: get wrong — they pin what the scorer CANNOT see:
#: - "low-battery-not-critical": the 20% low notice vs the 10%
#:   critical one (watcher_kind is binary, the level is not);
#: - "trivial-routine": a trivial reminder vs an important one
#:   (routine_due is binary, the priority is not);
#: - "foreground-switch-to-calendar": the foreground app IS the
#:   meeting context (the scorer cannot see which app it is).
#: A bench the scorer aces 100% would prove nothing; the misses
#: are the point. Deterministic: same scorer, same numbers.
PROACTIVITY_BENCH: tuple[ProactivityCase, ...] = (
    # -- should intervene (18) --
    _case("confident-next-app",
          "Idle desktop, predictor confident about the next app.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.92, "evidence_samples": 20},
          True, "High confidence, enough evidence — AN-1 would speak."),
    _case("confident-plus-routine",
          "Morning routine due and the prediction agrees.",
          "Start the morning routine: calendar, then inbox.",
          "routine",
          {"confidence": 0.85, "evidence_samples": 12,
           "routine_due": True},
          True, "Two signals agree — intervene."),
    _case("explicit-low-confidence",
          "The user asked what VYUK would suggest; confidence low.",
          "Best guess: open the project folder.",
          "suggestion",
          {"confidence": 0.30, "evidence_samples": 2,
           "explicit_request": True},
          True, "An explicit ask bypasses the silence floor."),
    _case("explicit-mid-task",
          "The user asked mid-task; the ask wins over the focus.",
          "You asked: the next step is the export.",
          "suggestion",
          {"confidence": 0.60, "evidence_samples": 8,
           "explicit_request": True, "user_mid_task": True},
          True, "Explicit request waives the mid-task penalty."),
    _case("routine-due-confident",
          "A routine is due and the prediction is confident.",
          "Time for the evening wrap-up routine.",
          "routine",
          {"confidence": 0.80, "evidence_samples": 15,
           "routine_due": True},
          True, "Due routine, confident prediction — intervene."),
    _case("routine-due-borderline",
          "A routine is due; prediction middling but the routine stands.",
          "Time for the evening wrap-up routine.",
          "routine",
          {"confidence": 0.50, "evidence_samples": 10,
           "routine_due": True},
          True, "The due routine carries a middling prediction."),
    _case("confident-battery",
          "Battery notice fired; prediction confident.",
          "Battery is low — plug in before the call.",
          "watcher",
          {"confidence": 0.78, "evidence_samples": 25,
           "watcher_kind": "battery"},
          True, "Urgent watcher + confident prediction — intervene."),
    _case("confident-network",
          "Network dropped; prediction confident.",
          "Network dropped — your download will resume when it returns.",
          "watcher",
          {"confidence": 0.72, "evidence_samples": 18,
           "watcher_kind": "network"},
          True, "Urgent watcher + confident prediction — intervene."),
    _case("very-confident-downloads",
          "Download finished; prediction very confident.",
          "Your download finished — open it?",
          "watcher",
          {"confidence": 0.95, "evidence_samples": 40,
           "watcher_kind": "downloads"},
          True, "Very confident; the soft watcher is a bonus."),
    _case("confident-idle",
          "Idle for a while; prediction confident.",
          "You usually check mail after a break — open the inbox?",
          "suggestion",
          {"confidence": 0.88, "evidence_samples": 30,
           "watcher_kind": "idle"},
          True, "Confident prediction; idle is a soft second signal."),
    _case("explicit-confident",
          "The user asked; prediction confident too.",
          "You asked: open the project folder next.",
          "suggestion",
          {"confidence": 0.75, "evidence_samples": 20,
           "explicit_request": True},
          True, "Explicit ask + confident prediction — intervene."),
    _case("everything-aligned",
          "Routine due, battery notice, confident prediction.",
          "Battery is low and the wrap-up routine is due.",
          "routine",
          {"confidence": 0.90, "evidence_samples": 10,
           "routine_due": True, "watcher_kind": "battery"},
          True, "Every signal agrees — intervene."),
    _case("just-over-floor",
          "Six scored samples — one over the evidence floor.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.80, "evidence_samples": 6},
          True, "Just over the floor, confident — intervene."),
    _case("dismissed-but-battery-critical",
          "Dismissed the last suggestion, but now the battery is critical.",
          "Battery critical — plug in now or the laptop sleeps.",
          "watcher",
          {"confidence": 0.90, "evidence_samples": 20,
           "dismissed_recently": True, "watcher_kind": "battery"},
          True, "A new urgent fact outweighs the old dismissal."),
    _case("explicit-after-dismissal",
          "Dismissed earlier, but now the user explicitly asked.",
          "You asked: the next step is the export.",
          "suggestion",
          {"confidence": 0.85, "evidence_samples": 20,
           "explicit_request": True, "dismissed_recently": True},
          True, "A fresh explicit ask is not the dismissed one."),
    _case("exactly-at-floor",
          "Five scored samples — exactly the evidence floor.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.70, "evidence_samples": 5},
          True, "At the floor and at AN-1's confidence threshold."),
    _case("very-confident",
          "Fifty scored samples, very confident.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.95, "evidence_samples": 50},
          True, "The strongest plain case — intervene."),
    _case("foreground-switch-to-calendar",
          "User just switched to the calendar — the meeting-prep "
          "suggestion is exactly timely, but the scorer cannot see "
          "WHICH app the foreground switch was to.",
          "Your meeting starts in 10 minutes — open the notes?",
          "suggestion",
          {"confidence": 0.90, "evidence_samples": 20,
           "user_mid_task": True, "watcher_kind": "foreground"},
          True, "KNOWN MISS (expected FN): the scorer scores 0.56 "
          "(mid-task penalty, foreground carries no weight) and "
          "stays silent; the label is intervene because the app "
          "IS the meeting context. The scorer has no app-context "
          "signal — that is the gap, honestly recorded."),
    # -- should stay silent (18) --
    _case("middling-confidence",
          "Prediction middling, no other signal.",
          "Open the project folder — maybe?",
          "suggestion",
          {"confidence": 0.50, "evidence_samples": 20},
          False, "Middling confidence alone is not enough."),
    _case("below-evidence-floor",
          "Very confident but only 3 scored samples.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.90, "evidence_samples": 3},
          False, "The silence floor rules: no accuracy evidence yet."),
    _case("just-dismissed",
          "Very confident, but the user just dismissed the last one.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.95, "evidence_samples": 30,
           "dismissed_recently": True},
          False, "Do not re-intervene on the same evidence."),
    _case("mid-task",
          "Confident, but the user is mid-task.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.90, "evidence_samples": 20,
           "user_mid_task": True},
          False, "Do not interrupt a focused session."),
    _case("low-confidence",
          "Low confidence, no other signal.",
          "Open the project folder — maybe?",
          "suggestion",
          {"confidence": 0.30, "evidence_samples": 20},
          False, "Low confidence — silence."),
    _case("no-prediction",
          "No prediction exists at all.",
          "Nothing to suggest yet.",
          "suggestion",
          {"confidence": 0.0, "evidence_samples": 0,
           "prediction_available": False},
          False, "Nothing to offer — silence, not a guess."),
    _case("just-under-threshold",
          "Confidence 0.65 — just under AN-1's 0.70 threshold.",
          "Open the project folder — maybe?",
          "suggestion",
          {"confidence": 0.65, "evidence_samples": 20},
          False, "Just under the threshold — silence."),
    _case("low-plus-downloads",
          "Confidence 0.60 with only a soft downloads notice.",
          "Your download finished — open it?",
          "watcher",
          {"confidence": 0.60, "evidence_samples": 20,
           "watcher_kind": "downloads"},
          False, "0.59 — one point under the threshold. Silence."),
    _case("explicit-nothing-to-offer",
          "The user asked, but there is no prediction to offer.",
          "I have nothing to suggest yet.",
          "suggestion",
          {"confidence": 0.0, "evidence_samples": 0,
           "explicit_request": True, "prediction_available": False},
          False, "An ask with nothing to offer is a spoken "
          "'I don't know yet', not an intervention."),
    _case("routine-due-no-evidence",
          "A routine is due but the candidate confidence is very low.",
          "Time for the evening wrap-up routine.",
          "routine",
          {"confidence": 0.20, "evidence_samples": 10,
           "routine_due": True},
          False, "Due routine, but essentially no evidence for this candidate."),
    _case("dismissed-and-mid-task",
          "Dismissed recently AND mid-task.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.80, "evidence_samples": 20,
           "dismissed_recently": True, "user_mid_task": True},
          False, "Two suppressions — silence."),
    _case("low-idle",
          "Low confidence with only an idle notice.",
          "You usually check mail after a break — open the inbox?",
          "suggestion",
          {"confidence": 0.40, "evidence_samples": 20,
           "watcher_kind": "idle"},
          False, "Low confidence, soft signal — silence."),
    _case("weak-confidence",
          "Confidence 0.55, eight scored samples.",
          "Open the project folder — maybe?",
          "suggestion",
          {"confidence": 0.55, "evidence_samples": 8},
          False, "Weak confidence — silence."),
    _case("mid-task-network",
          "Network dropped but the user is mid-task in a document.",
          "Network dropped — your download will resume when it returns.",
          "watcher",
          {"confidence": 0.75, "evidence_samples": 20,
           "user_mid_task": True, "watcher_kind": "network"},
          False, "0.575 — the mid-task penalty keeps it silent; "
          "going offline changes nothing they can act on now."),
    _case("one-below-floor",
          "Four scored samples — one below the evidence floor.",
          "Open the project folder — you usually open it now.",
          "suggestion",
          {"confidence": 0.70, "evidence_samples": 4},
          False, "One below the floor — silence."),
    _case("low-battery-not-critical",
          "Battery notice fired at 20% (low, not critical) with "
          "middling confidence. The scorer cannot tell the 20% "
          "notice from the 10% critical one — watcher_kind is binary.",
          "Battery is low — plug in before the call.",
          "watcher",
          {"confidence": 0.62, "evidence_samples": 20,
           "watcher_kind": "battery"},
          False, "KNOWN MISS (expected FP): the scorer scores 0.71 "
          "and intervenes; the label is silent because a 20% "
          "warning with ~40 minutes left is not worth interrupting "
          "a focused session. The watcher signal needs a level, "
          "not just a kind — that is the gap, honestly recorded."),
    _case("trivial-routine",
          "A routine is due — but it is the low-priority water "
          "reminder, already done twice today. routine_due is binary.",
          "Time to drink some water.",
          "routine",
          {"confidence": 0.55, "evidence_samples": 20,
           "routine_due": True},
          False, "KNOWN MISS (expected FP): the scorer scores 0.85 "
          "and intervenes; the label is silent because the routine "
          "is trivial and already done. routine_due carries no "
          "priority — that is the gap, honestly recorded."),
    _case("weak-foreground",
          "Weak confidence with only a foreground switch.",
          "Open the project folder — maybe?",
          "suggestion",
          {"confidence": 0.45, "evidence_samples": 12,
           "watcher_kind": "foreground"},
          False, "Weak confidence, no-weight watcher — silence."),
)


def score_bench(
    cases: Optional[tuple[ProactivityCase, ...]] = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Run the scorer over the bench and report precision/recall/F1.

    Deterministic: no randomness, no I/O, no clock. Returns the
    confusion counts, the metrics, and the per-case verdicts.
    """
    cases = PROACTIVITY_BENCH if cases is None else cases
    per_case: list[dict[str, Any]] = []
    tp = fp = tn = fn = 0
    for case in cases:
        scored = score_intervention(
            InterventionCandidate(
                text=case.candidate_text,
                kind=case.kind,
                evidence=dict(case.evidence),
            ),
            threshold=threshold,
        )
        predicted = scored.decision == "intervene"
        expected = case.should_intervene
        correct = predicted == expected
        if predicted and expected:
            tp += 1
        elif predicted and not expected:
            fp += 1
        elif not predicted and not expected:
            tn += 1
        else:
            fn += 1
        per_case.append({
            "name": case.name,
            "expected": expected,
            "predicted": predicted,
            "correct": correct,
            "score": round(scored.score, 4),
            "decision": scored.decision,
            "note": case.note,
        })
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)
    total = len(cases)
    accuracy = (tp + tn) / total if total else 0.0
    return {
        "threshold": float(threshold),
        "cases": total,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
        "per_case": per_case,
    }


# ---------------------------------------------------------------------------
# Storage — data/proactivity_runs.db (SQLite, WAL, mirroring dagflow)
# ---------------------------------------------------------------------------

def default_db_path() -> Path:
    from .config import DATA_DIR

    return Path(DATA_DIR) / "proactivity_runs.db"


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = Path(db_path) if db_path else default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_store(db_path: Optional[Path] = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS score_measurements ("
            "id TEXT PRIMARY KEY, created_at REAL NOT NULL, "
            "candidate_text TEXT NOT NULL, kind TEXT NOT NULL, "
            "score REAL NOT NULL, threshold REAL NOT NULL, "
            "decision TEXT NOT NULL, reasons_json TEXT NOT NULL, "
            "evidence_json TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bench_runs ("
            "id TEXT PRIMARY KEY, created_at REAL NOT NULL, "
            "threshold REAL NOT NULL, cases INTEGER NOT NULL, "
            "tp INTEGER NOT NULL, fp INTEGER NOT NULL, "
            "tn INTEGER NOT NULL, fn INTEGER NOT NULL, "
            "precision REAL NOT NULL, recall REAL NOT NULL, "
            "f1 REAL NOT NULL, accuracy REAL NOT NULL, "
            "per_case_json TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()


def record_score(
    scored: InterventionScore,
    candidate: InterventionCandidate,
    db_path: Optional[Path] = None,
) -> str:
    """Store one scored candidate and re-read it before returning.

    A4-filestat / A9-measure pattern: a failed write (or a
    re-read that does not match) raises, never silently claims
    the measurement landed.
    """
    init_store(db_path)
    run_id = uuid.uuid4().hex[:16]
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO score_measurements "
            "(id, created_at, candidate_text, kind, score, threshold, "
            "decision, reasons_json, evidence_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, time.time(), candidate.text, candidate.kind,
             scored.score, scored.threshold, scored.decision,
             json.dumps(list(scored.reasons)),
             json.dumps(candidate.evidence)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT score, decision FROM score_measurements WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None or row[0] != scored.score \
                or row[1] != scored.decision:
            raise RuntimeError(
                "score record re-read mismatch after write")
    finally:
        conn.close()
    return run_id


def record_bench_run(
    metrics: dict[str, Any],
    db_path: Optional[Path] = None,
) -> str:
    """Store one bench run and re-read it before returning."""
    init_store(db_path)
    run_id = uuid.uuid4().hex[:16]
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO bench_runs "
            "(id, created_at, threshold, cases, tp, fp, tn, fn, "
            "precision, recall, f1, accuracy, per_case_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, time.time(), metrics["threshold"], metrics["cases"],
             metrics["tp"], metrics["fp"], metrics["tn"], metrics["fn"],
             metrics["precision"], metrics["recall"], metrics["f1"],
             metrics["accuracy"], json.dumps(metrics["per_case"])),
        )
        conn.commit()
        row = conn.execute(
            "SELECT f1, cases FROM bench_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None or row[0] != metrics["f1"] \
                or row[1] != metrics["cases"]:
            raise RuntimeError(
                "bench record re-read mismatch after write")
    finally:
        conn.close()
    return run_id


def list_scores(
    limit: int = 10,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Most recent scored candidates, newest first. Read-only."""
    init_store(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, created_at, candidate_text, kind, score, "
            "threshold, decision, reasons_json FROM score_measurements "
            "ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [{
        "id": r[0],
        "created_at": r[1],
        "candidate_text": r[2],
        "kind": r[3],
        "score": r[4],
        "threshold": r[5],
        "decision": r[6],
        "reasons": json.loads(r[7]),
    } for r in rows]


def list_bench_runs(
    limit: int = 5,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Most recent bench runs, newest first. Read-only."""
    init_store(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, created_at, threshold, cases, tp, fp, tn, fn, "
            "precision, recall, f1, accuracy FROM bench_runs "
            "ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [{
        "id": r[0],
        "created_at": r[1],
        "threshold": r[2],
        "cases": r[3],
        "tp": r[4],
        "fp": r[5],
        "tn": r[6],
        "fn": r[7],
        "precision": r[8],
        "recall": r[9],
        "f1": r[10],
        "accuracy": r[11],
    } for r in rows]


def get_bench_run(
    run_id: str,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """One bench run with its full per-case verdicts. Read-only."""
    init_store(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, created_at, threshold, cases, tp, fp, tn, fn, "
            "precision, recall, f1, accuracy, per_case_json "
            "FROM bench_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "created_at": row[1],
        "threshold": row[2],
        "cases": row[3],
        "tp": row[4],
        "fp": row[5],
        "tn": row[6],
        "fn": row[7],
        "precision": row[8],
        "recall": row[9],
        "f1": row[10],
        "accuracy": row[11],
        "per_case": json.loads(row[12]),
    }


# ---------------------------------------------------------------------------
# Funnel handlers — the product surface (measurement only)
# ---------------------------------------------------------------------------

def _result(message: str, ok: bool = True, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": ok, "message": message}
    out.update(extra)
    return out


def score_intervention_handler(
    candidate_text: str = "",
    kind: str = "suggestion",
    confidence: Any = 0.0,
    evidence_samples: Any = 0,
    explicit_request: Any = False,
    routine_due: Any = False,
    dismissed_recently: Any = False,
    user_mid_task: Any = False,
    watcher_kind: Any = None,
    prediction_available: Any = True,
    threshold: Any = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Score one candidate intervention and record the measurement.

    MEASUREMENT ONLY: the answer is a scored suggestion with its
    reasons — "what would you have done?" — never an action taken.
    The measurement is recorded in data/proactivity_runs.db; a
    failed record is disclosed in the message, never silently
    claimed, and never blocks the score itself (the score is the
    answer; the record is bookkeeping).
    """
    try:
        conf = _clamp01(float(confidence))
    except (TypeError, ValueError):
        return _result(
            "score refused: confidence must be a number", ok=False)
    try:
        samples = int(evidence_samples)
    except (TypeError, ValueError):
        return _result(
            "score refused: evidence_samples must be an integer", ok=False)
    try:
        thresh = float(threshold)
    except (TypeError, ValueError):
        return _result(
            "score refused: threshold must be a number", ok=False)
    if not (0.0 < thresh <= 1.0):
        return _result(
            "score refused: threshold must be in (0, 1]", ok=False)
    if not str(candidate_text or "").strip():
        return _result(
            "score refused: candidate_text must not be empty", ok=False)
    watcher = str(watcher_kind).strip().lower() \
        if watcher_kind is not None else None
    candidate = InterventionCandidate(
        text=str(candidate_text).strip(),
        kind=str(kind or "suggestion").strip() or "suggestion",
        evidence={
            "confidence": conf,
            "evidence_samples": samples,
            "explicit_request": bool(explicit_request),
            "routine_due": bool(routine_due),
            "dismissed_recently": bool(dismissed_recently),
            "user_mid_task": bool(user_mid_task),
            "watcher_kind": watcher,
            "prediction_available": bool(prediction_available),
        },
    )
    scored = score_intervention(candidate, threshold=thresh)
    try:
        record_id = record_score(scored, candidate)
        recorded = f"recorded as {record_id}"
    except Exception as exc:  # noqa: BLE001 — store failure is disclosed, not fatal
        record_id = None
        recorded = f"NOT recorded (store failed: {exc})"
    lines = [
        f"{scored.decision.upper()}: '{candidate.text}' "
        f"(score {scored.score:.2f} vs threshold {scored.threshold:.2f})",
        "Reasons:",
        *[f"  - {r}" for r in scored.reasons],
        f"Measurement {recorded}.",
        "This is measurement only — no action was taken.",
    ]
    return _result(
        "\n".join(lines),
        score=scored.score,
        threshold=scored.threshold,
        decision=scored.decision,
        reasons=list(scored.reasons),
        contributions=scored.contributions,
        record_id=record_id,
    )


def run_proactivity_bench_handler(
    threshold: Any = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Run the 36-case proactivity bench and record the run.

    MEASUREMENT ONLY: the bench scores the scorer — precision /
    recall / F1 on scripted scenarios with known right/wrong
    decisions. Nothing is executed, nothing is sensed; the run
    record is stored in data/proactivity_runs.db.
    """
    try:
        thresh = float(threshold)
    except (TypeError, ValueError):
        return _result(
            "bench refused: threshold must be a number", ok=False)
    if not (0.0 < thresh <= 1.0):
        return _result(
            "bench refused: threshold must be in (0, 1]", ok=False)
    metrics = score_bench(threshold=thresh)
    try:
        record_id = record_bench_run(metrics)
        recorded = f"recorded as {record_id}"
    except Exception as exc:  # noqa: BLE001 — store failure is disclosed, not fatal
        record_id = None
        recorded = f"NOT recorded (store failed: {exc})"
    misses = [c for c in metrics["per_case"] if not c["correct"]]
    lines = [
        f"Proactivity bench: {metrics['cases']} cases, "
        f"F1 {metrics['f1']:.3f} (precision {metrics['precision']:.3f}, "
        f"recall {metrics['recall']:.3f}, accuracy {metrics['accuracy']:.3f})",
        f"Confusion: TP {metrics['tp']}, FP {metrics['fp']}, "
        f"TN {metrics['tn']}, FN {metrics['fn']} "
        f"(threshold {metrics['threshold']:.2f})",
    ]
    if misses:
        lines.append("Misses (the scorer's known blind spots):")
        for miss in misses:
            lines.append(
                f"  - {miss['name']}: expected "
                f"{'intervene' if miss['expected'] else 'silent'}, "
                f"got {miss['decision']} (score {miss['score']:.2f})")
    else:
        lines.append("No misses — every case decided as labelled.")
    lines.append(f"Bench run {recorded}.")
    lines.append("This is measurement only — no action was taken.")
    return _result(
        "\n".join(lines),
        record_id=record_id,
        **{k: metrics[k] for k in
           ("threshold", "cases", "tp", "fp", "tn", "fn",
            "precision", "recall", "f1", "accuracy")},
        misses=[m["name"] for m in misses],
    )


def list_proactivity_scores_handler(
    limit: Any = 10,
) -> dict[str, Any]:
    """List the most recent scored interventions. Read-only."""
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        return _result(
            "list refused: limit must be an integer", ok=False)
    if lim < 1 or lim > 100:
        return _result(
            "list refused: limit must be between 1 and 100", ok=False)
    rows = list_scores(limit=lim)
    if not rows:
        return _result(
            "no scored interventions recorded yet "
            "(see score_intervention)",
            scores=[],
        )
    lines = [f"{len(rows)} most recent scored intervention(s):"]
    for row in rows:
        lines.append(
            f"  - [{row['decision']}] {row['score']:.2f} "
            f"'{row['candidate_text']}' ({row['kind']})")
    return _result("\n".join(lines), scores=rows)


def proactivity_bench_results_handler(
    limit: Any = 5,
) -> dict[str, Any]:
    """List the most recent bench runs. Read-only."""
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        return _result(
            "list refused: limit must be an integer", ok=False)
    if lim < 1 or lim > 100:
        return _result(
            "list refused: limit must be between 1 and 100", ok=False)
    rows = list_bench_runs(limit=lim)
    if not rows:
        return _result(
            "no bench runs recorded yet (see run_proactivity_bench)",
            runs=[],
        )
    lines = [f"{len(rows)} most recent bench run(s):"]
    for row in rows:
        lines.append(
            f"  - F1 {row['f1']:.3f} (P {row['precision']:.3f}, "
            f"R {row['recall']:.3f}, {row['cases']} cases, "
            f"TP {row['tp']} FP {row['fp']} TN {row['tn']} "
            f"FN {row['fn']})")
    return _result("\n".join(lines), runs=rows)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register the four proactivity tools (R7-8, ProactiveAgent pattern)."""
    registry.register(
        name="score_intervention",
        description="Score one candidate intervention and record the measurement: 'what would you have done?' Returns the score, the threshold, the decision (intervene/silent) and the reasons — measurement only, no action is ever taken. Evidence comes from state the tree already collects (predictor confidence/evidence count, dismissal state, routine-due, watcher notice kind). The silence floor (5 scored samples, shared with AN-1) keeps the answer silent below the evidence floor unless the user explicitly asked or a routine is due.",
        parameters={
            "type": "object",
            "properties": {
                "candidate_text": {"type": "string", "description": "The candidate intervention in plain language, e.g. 'Open the project folder — you usually open it now.'"},
                "kind": {"type": "string", "description": "Candidate kind: 'suggestion', 'routine' or 'watcher'"},
                "confidence": {"type": "number", "description": "Predictor confidence for this candidate, 0..1"},
                "evidence_samples": {"type": "integer", "description": "How many times the prediction has been scored against real outcomes (the AN-1 evidence floor is 5)"},
                "explicit_request": {"type": "boolean", "description": "True when the user explicitly asked for a suggestion"},
                "routine_due": {"type": "boolean", "description": "True when a routine is due"},
                "dismissed_recently": {"type": "boolean", "description": "True when the user dismissed the last suggestion"},
                "user_mid_task": {"type": "boolean", "description": "True when the user is mid-task (do not interrupt)"},
                "watcher_kind": {"type": "string", "description": "Watcher notice kind, if any: 'battery', 'network', 'downloads', 'idle' or 'foreground'"},
                "prediction_available": {"type": "boolean", "description": "False when there is no prediction to offer at all"},
                "threshold": {"type": "number", "description": "Intervention threshold, 0..1 (default 0.60)"},
            },
            "required": ["candidate_text"],
        },
        handler=score_intervention_handler,
    )
    registry.register(
        name="run_proactivity_bench",
        description="Run the 36-case proactivity bench: scripted scenarios with known right/wrong intervention decisions, scored for precision/recall/F1. Measures the when-to-intervene scorer before anything ships — measurement only, nothing is executed, nothing is sensed. The run record (including per-case verdicts) is stored for later comparison.",
        parameters={
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "description": "Intervention threshold to evaluate, 0..1 (default 0.60)"},
            },
            "required": [],
        },
        handler=run_proactivity_bench_handler,
    )
    registry.register(
        name="list_proactivity_scores",
        description="List the most recent scored interventions (what VYUK would have done, with scores and reasons). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many recent scores to list, 1..100 (default 10)"},
            },
            "required": [],
        },
        handler=list_proactivity_scores_handler,
    )
    registry.register(
        name="proactivity_bench_results",
        description="List the most recent proactivity bench runs (F1, precision, recall, confusion counts). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many recent bench runs to list, 1..100 (default 5)"},
            },
            "required": [],
        },
        handler=proactivity_bench_results_handler,
    )
