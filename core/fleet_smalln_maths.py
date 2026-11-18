"""Fleet Learning track (v5.74) — FL-1 honest small-N maths, pure functions.

These functions take CALLER-SUPPLIED numbers only. They collect
nothing, retain nothing, and emit nothing: there is no cohort data
here, no counting, no aggregation, no network. They exist to keep
the D-13 decision honest — the wave proves its own title mostly
false, and the plan keeps the proof.

Pinned numbers (plan §5.17, wave 26 §1):
- At epsilon=1, per-count noise is ±1-3 (Laplace scale 1/epsilon,
  band [scale, 3*scale]).
- Split across 20 categories at epsilon=1 the per-cell SD is ~28:
  the budget is split per cell, SD = sqrt(2) * categories / epsilon.
- Epsilon 4-8 is theatre (e**8 is ~3,000x): flagged, never a
  guarantee. This library asserts NO differential-privacy guarantee
  for any input: it reports noise arithmetic only.
- Floor: N ~ 100-1,000 supports a few coarse counts at epsilon
  ~1-2; useful learning needs N ~ 5,000-10,000+. Local DP needs
  10**5-10**6 users.
- FL-1-8 threshold suppression: sources say k=5-10, real
  minThreshold 50. The "25 users" figure is illustrative, NOT a
  default. Suppression is labelled suppression, never anonymity.
"""

import math

EPSILON_THEATRE_FROM = 4
FLOOR_LOW = 100
FLOOR_HIGH = 1000
USEFUL_LOW = 5000
USEFUL_HIGH = 10000
LOCAL_DP_LOW = 100000
LOCAL_DP_HIGH = 1000000
DEFAULT_MIN_THRESHOLD = 50


def noise_band(epsilon):
    """Laplace noise band [scale, 3*scale] for epsilon > 0."""
    if not isinstance(epsilon, (int, float)) or epsilon <= 0:
        raise ValueError("epsilon must be a positive number")
    scale = 1.0 / float(epsilon)
    return {"epsilon": float(epsilon),
            "noise_low": round(scale, 3),
            "noise_high": round(3.0 * scale, 3)}


def per_cell_sd(epsilon, categories):
    """Per-cell SD when one budget is split across categories.

    SD = sqrt(2) * categories / epsilon. At epsilon=1 and 20
    categories this is ~28.28, matching the wave's ~28.
    """
    if not isinstance(epsilon, (int, float)) or epsilon <= 0:
        raise ValueError("epsilon must be a positive number")
    if not isinstance(categories, int) or categories < 1:
        raise ValueError("categories must be a positive integer")
    return round(math.sqrt(2.0) * categories / float(epsilon), 2)


def noise_verdict(n, epsilon, categories):
    """Verdict for a caller-supplied count scale.

    Returns {n, epsilon, categories, noise_band, per_cell_sd,
    theatre, verdict, reason, dp_claim}. ``dp_claim`` is ALWAYS
    False: this library reports noise arithmetic, it never asserts
    a differential-privacy guarantee. ``verdict`` is one of
    "usable" / "marginal" / "cannot-learn".
    """
    if not isinstance(n, (int, float)) or n < 0:
        raise ValueError("n must be a non-negative number")
    band = noise_band(epsilon)
    sd = per_cell_sd(epsilon, categories)
    theatre = float(epsilon) >= EPSILON_THEATRE_FROM
    if theatre:
        return {"n": n, "epsilon": float(epsilon),
                "categories": categories, "noise_band": band,
                "per_cell_sd": sd, "theatre": True,
                "verdict": "cannot-learn",
                "reason": "epsilon >= 4 is theatre (e**8 ~ 3,000x); "
                          "no honest guarantee can be stated",
                "dp_claim": False}
    if n >= USEFUL_LOW:
        verdict, reason = "usable", "at or above the useful floor"
    elif n >= FLOOR_LOW:
        verdict, reason = ("marginal",
                           "a few coarse counts only, at epsilon ~1-2")
    else:
        verdict, reason = ("cannot-learn",
                           "below the 100-user floor; noise swamps counts")
    # Downgrade one step when per-cell noise swamps the per-cell mean.
    if verdict in ("usable", "marginal"):
        per_cell_mean = float(n) / categories
        if sd > max(1.0, per_cell_mean * 0.5):
            if verdict == "usable":
                verdict, reason = ("marginal",
                                   "per-cell noise swamps the per-cell mean")
            else:
                verdict, reason = ("cannot-learn",
                                   "per-cell noise swamps the per-cell mean")
    return {"n": n, "epsilon": float(epsilon), "categories": categories,
            "noise_band": band, "per_cell_sd": sd, "theatre": False,
            "verdict": verdict, "reason": reason, "dp_claim": False}


def threshold_verdict(cohort_size, min_threshold=DEFAULT_MIN_THRESHOLD):
    """FL-1-8 suppression policy as a pure function.

    Returns {cohort_size, min_threshold, decision, label} where
    decision is "release" or "suppress" and label is ALWAYS
    "suppression" — never anonymity. The default threshold is 50
    (the wave's real minThreshold), not the illustrative 25.
    """
    if not isinstance(cohort_size, (int, float)) or cohort_size < 0:
        raise ValueError("cohort_size must be a non-negative number")
    if not isinstance(min_threshold, (int, float)) or min_threshold < 1:
        raise ValueError("min_threshold must be a positive number")
    decision = "release" if cohort_size >= min_threshold else "suppress"
    return {"cohort_size": cohort_size, "min_threshold": min_threshold,
            "decision": decision, "label": "suppression"}


def composition_note(cohorts_disjoint):
    """Composition warning: forever unless cohorts are disjoint."""
    if cohorts_disjoint:
        return {"composition": "bounded",
                "note": "cohorts are disjoint; budgets do not compose "
                        "across them"}
    return {"composition": "forever",
            "warning": "composition is forever unless cohorts are "
                       "disjoint; lifetime loss is unbounded"}


def floors():
    """The wave's floor numbers, as data (no computation, no claim)."""
    return {"floor_low": FLOOR_LOW, "floor_high": FLOOR_HIGH,
            "useful_low": USEFUL_LOW, "useful_high": USEFUL_HIGH,
            "local_dp_low": LOCAL_DP_LOW, "local_dp_high": LOCAL_DP_HIGH}
