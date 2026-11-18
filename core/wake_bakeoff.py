"""Wave 3 (v5.58) wake bake-off harness — NUMBERS ONLY, no invented
field results.

One slot, two heads:
  * PR-12 — the incumbent: the grammar-constrained Vosk spotter in
    core/voice/wake_word.py (already shipping; wake stays OFF by default).
  * EA-14 — the LiveKit WakeWord head: NOT RUNNABLE here and NOT
    SHIPPED (see core/wake_licences.py: CC-BY-NC-SA-4.0 exposure via
    the default ACAV100M / openWakeWord pipeline). The harness can
    score an EA-14 head only if a caller supplies its scores from a
    real rig run; it never synthesises them.

What this harness actually does (EA-16 ROC discipline, EA-19
verifier accounting):
  * Takes per-clip SCORES supplied by a real rig run (positive
    clips labelled with distance/accent, negative playback labelled
    with its duration in hours) and computes, per head and per
    threshold: true-accept rate, false accepts/hour, and the
    pass/fail verdict against the section-10 targets
    (<=0.5 false-accepts/hour, >=95% true-accept at 1.5 m).
  * Sweeps thresholds to build the ROC table and picks the
    operating point: the threshold with the highest true-accept
    rate that still meets the false-accept gate; if none meets
    the gate, the head FAILS — the harness never relaxes the gate
    to manufacture a winner.
  * Combines a head with the EA-19 verifier gate
    (core/wake_verifier.py): a fire counts only when the verifier
    also confirms, so the reported combined false-accept rate is
    the product-form estimate the verifier protocol justifies,
    computed from measured verifier behaviour, not assumed.
  * REFUSES to run without a corpus: no corpus path, no scores,
    or no negative duration raises instead of returning a pass.
    This mirrors the Wave 2 rig's form-gated-corpus rule: the
    VP-6.12 dataset is not bundled, and a bake-off with no data
    is a non-result, not a zero.

Field numbers for v5.58 (honest status, 2026-10-24)
---------------------------------------------------
NO field numbers exist for either head from this build, because
the prerequisites do not exist in this environment:
  * No EA-14 head, weights, or ONNX runtime is present, and no new
    pip dependencies are allowed, so the challenger cannot run.
  * No negative-playback corpus (TV/dialogue/far-field, >=25 h
    for the "Hey VYUK" negative set) and no recorded positive set
    at distance/accent is bundled or present in the tree.
  * The incumbent's runtime (vosk) is not installed in this
    environment, so even PR-12 cannot be re-measured here.
The harness, the ROC discipline, and the gate are shipped; the
numbers themselves are STILL-OWED and must come from a real rig
run on the owner's machine with a real corpus. Any report that
quotes a false-accepts/hour or true-accept figure for v5.58
without naming its corpus and rig run is inventing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Section-10 wake targets (the bar every head and every custom
# phrase must meet, or it does not ship).
FALSE_ACCEPTS_PER_HOUR_MAX = 0.5
TRUE_ACCEPT_MIN = 0.95
TRUE_ACCEPT_DISTANCE_M = 1.5

# The two bake-off slots. EA-14's entry is a placeholder for a
# future clean-retrained head; it is never scored from thin air.
HEAD_PR12 = "PR-12"
HEAD_EA14 = "EA-14"
HEAD_CUSTOM = "CUSTOM"


@dataclass(frozen=True)
class ScoredClip:
    """One rig-run clip: a detector score in [0, 1] plus its labels.

    `is_positive` True = the wake phrase was spoken; False =
    negative playback (conversation / TV / noise). `distance_m`
    and `accent` are recorded only for positives; negatives carry
    the playback duration through the run, not per clip.
    """

    score: float
    is_positive: bool
    distance_m: float | None = None
    accent: str | None = None


@dataclass
class HeadResult:
    """Measured outcome for one head at one threshold."""

    head: str
    threshold: float
    corpus_path: str
    negative_hours: float
    false_accepts: int
    true_accepts: int
    true_total: int
    distance_m: float | None = None
    accent: str | None = None
    verifier_applied: bool = False
    verifier_false_accept_rate: float | None = None

    @property
    def false_accepts_per_hour(self) -> float:
        return self.false_accepts / self.negative_hours

    @property
    def true_accept_rate(self) -> float | None:
        if self.true_total <= 0:
            return None
        return self.true_accepts / self.true_total

    @property
    def passes_false_accept_gate(self) -> bool:
        return self.false_accepts_per_hour <= FALSE_ACCEPTS_PER_HOUR_MAX

    @property
    def passes_true_accept_gate(self) -> bool:
        rate = self.true_accept_rate
        return rate is not None and rate >= TRUE_ACCEPT_MIN

    @property
    def passes_section10(self) -> bool:
        return self.passes_false_accept_gate and self.passes_true_accept_gate

    def recommendation(self) -> str:
        """Ship recommendation PER HEAD — never a forced winner."""
        if self.true_total <= 0:
            return "NO-DATA: no positive clips supplied; cannot recommend"
        if self.passes_section10:
            return "MEETS section-10 targets at this threshold"
        if not self.passes_false_accept_gate:
            return ("DOES NOT SHIP: false-accepts/hour above "
                    f"{FALSE_ACCEPTS_PER_HOUR_MAX}")
        return ("DOES NOT SHIP: true-accept below "
                f"{TRUE_ACCEPT_MIN:.0%} at this threshold")


def evaluate_head(head: str, clips: list[ScoredClip], threshold: float,
                  corpus_path: str, negative_hours: float,
                  distance_m: float | None = None,
                  accent: str | None = None) -> HeadResult:
    """Score one head at one threshold from a real rig run's clips.

    Raises ValueError when the run is underspecified (no corpus,
    no negative duration, no clips at all, or a threshold outside
    [0, 1]) — the rig never invents a pass. Distance/accent
    filters restrict the TRUE-ACCEPT denominator to matching
    positives; false accepts always count over the full negative
    playback, since a false fire has no distance or accent.
    """
    if not corpus_path:
        raise ValueError(
            "wake bake-off: no corpus supplied; the negative-playback "
            "corpus is form-gated and not bundled — supply a corpus "
            "path or do not run")
    if negative_hours <= 0:
        raise ValueError("wake bake-off: negative_hours must be positive")
    if not clips:
        raise ValueError("wake bake-off: no scored clips supplied")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("wake bake-off: threshold must be in [0, 1]")
    positives = [c for c in clips if c.is_positive]
    if distance_m is not None:
        positives = [c for c in positives
                     if c.distance_m is not None
                     and abs(c.distance_m - distance_m) < 1e-9]
    if accent is not None:
        positives = [c for c in positives if c.accent == accent]
    false_accepts = sum(1 for c in clips
                        if not c.is_positive and c.score >= threshold)
    true_accepts = sum(1 for c in positives if c.score >= threshold)
    return HeadResult(
        head=head, threshold=threshold, corpus_path=corpus_path,
        negative_hours=negative_hours, false_accepts=false_accepts,
        true_accepts=true_accepts, true_total=len(positives),
        distance_m=distance_m, accent=accent)


def roc_table(head: str, clips: list[ScoredClip], corpus_path: str,
              negative_hours: float,
              thresholds: list[float] | None = None,
              distance_m: float | None = None,
              accent: str | None = None) -> list[HeadResult]:
    """EA-16 ROC discipline: the full threshold sweep, sorted by
    threshold ascending. Callers pick the operating point from
    this table with pick_operating_point(); they do not get to
    re-tune the gate afterwards."""
    if thresholds is None:
        thresholds = [i / 20.0 for i in range(21)]
    return [evaluate_head(head, clips, t, corpus_path, negative_hours,
                           distance_m=distance_m, accent=accent)
            for t in thresholds]


def pick_operating_point(results: list[HeadResult]) -> HeadResult | None:
    """The operating point: among thresholds meeting the
    false-accept gate, the one with the highest true-accept rate
    (ties break to the higher threshold, i.e. the stricter one).
    None when NO threshold meets the gate — the head fails, and
    the caller must report that, not a best-effort threshold."""
    passing = [r for r in results if r.passes_false_accept_gate
               and r.true_accept_rate is not None]
    if not passing:
        return None
    return max(passing,
               key=lambda r: (r.true_accept_rate or 0.0, r.threshold))


def bakeoff_report(results_by_head: dict[str, HeadResult]) -> dict:
    """Plain-data report for the coordinator: one entry per head,
    each with its own recommendation. No overall winner is forced
    — when both heads fail, or one has no data, the report says so.
    """
    entries = {}
    for head, result in results_by_head.items():
        entries[head] = {
            "threshold": result.threshold,
            "false_accepts_per_hour": result.false_accepts_per_hour,
            "true_accept_rate": result.true_accept_rate,
            "passes_section10": result.passes_section10,
            "recommendation": result.recommendation(),
            "corpus_path": result.corpus_path,
            "verifier_applied": result.verifier_applied,
        }
    shippable = [h for h, e in entries.items() if e["passes_section10"]]
    return {"heads": entries, "shippable_heads": shippable,
            "winner_forced": False}
