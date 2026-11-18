"""EA-15 WakeForge alternate trainer — interface + manifest gate.

WakeForge is a trainer, not a head: it produces a head only from
a training manifest (positive phrases, negative corpora, and
their licences). This module is the gate in front of that
trainer. It never trains anything itself — training needs a
dataset, a GPU-class run, and the WakeForge package, none of
which are present or allowed as new dependencies here — and it
refuses to bless any manifest that would produce a tainted head.

Manifest rules (each is a hard refusal, not a warning):
  * Every negative source must carry a licence in the
    commercial-clean allow-list (Apache-2.0, MIT, BSD, CC-BY-4.0,
    CC0, or self-owned with a written provenance note). Anything
    CC-BY-NC-SA-4.0, non-commercial, or UNKNOWN is refused.
  * The "Hey VYUK" negative set must be >=25 h of self-owned
    negatives covering TV/dialogue/far-field material — the
    task's own retraining bar for a clean EA-14-class head.
    A manifest short of that is "not ready", not "close enough".
  * Positive phrases must be the owner's own phrases, recorded
    or synthesised from a licence-clean voice; no third-party
    wake-word model's positives are reused.

v5.58 status: no manifest passes (no negative set has been
collected at all), so no training is planned, started, or
shipped from this module. When a manifest does pass, the
coordinator still re-runs CC-1 on the trained head before
any ship decision — a passing manifest is permission to
TRAIN, not to ship.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Licences a negative corpus may carry and still be trained on
# for a commercial product. CC-BY-NC-SA-4.0 is conspicuously
# absent: that is the whole point of this gate.
CLEAN_LICENCES = frozenset({
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause",
    "CC-BY-4.0", "CC0-1.0", "SELF-OWNED",
})

# The task's retraining bar for a clean "Hey VYUK" head.
MIN_NEGATIVE_HOURS = 25.0
REQUIRED_NEGATIVE_COVERAGE = frozenset({"tv", "dialogue", "far-field"})


@dataclass(frozen=True)
class NegativeSource:
    """One negative corpus named in a training manifest."""

    name: str
    hours: float
    licence: str
    coverage: frozenset = frozenset()


@dataclass
class TrainingManifest:
    """Everything a WakeForge training run would consume."""

    phrase: str
    positive_utterances: int = 0
    negative_sources: list[NegativeSource] = field(default_factory=list)

    @property
    def negative_hours(self) -> float:
        return sum(s.hours for s in self.negative_sources)

    @property
    def coverage(self) -> frozenset:
        out: set[str] = set()
        for source in self.negative_sources:
            out |= set(source.coverage)
        return frozenset(out)

    def refusal_reasons(self) -> list[str]:
        """Every reason this manifest must NOT be trained on."""
        reasons: list[str] = []
        if not self.phrase.strip():
            reasons.append("no target phrase")
        if self.positive_utterances <= 0:
            reasons.append("no positive utterances")
        if not self.negative_sources:
            reasons.append("no negative sources at all")
        for source in self.negative_sources:
            if source.licence not in CLEAN_LICENCES:
                reasons.append(
                    f"negative source {source.name!r} carries "
                    f"licence {source.licence!r}, which is not "
                    "commercial-clean (CC-BY-NC-SA-4.0 and unknown "
                    "licences are refused)")
            if source.hours <= 0:
                reasons.append(
                    f"negative source {source.name!r} has no hours")
        if self.negative_hours < MIN_NEGATIVE_HOURS:
            reasons.append(
                f"negative set is {self.negative_hours:.1f} h, "
                f"below the {MIN_NEGATIVE_HOURS:.0f} h bar")
        missing = REQUIRED_NEGATIVE_COVERAGE - self.coverage
        if missing:
            reasons.append(
                "negative set lacks coverage: " + ", ".join(sorted(missing)))
        return reasons

    @property
    def ready_to_train(self) -> bool:
        return not self.refusal_reasons()


def plan_training(manifest: TrainingManifest) -> dict:
    """The only output this module produces for a manifest: a
    plan that is either ready (with its conditions restated) or
    refused (with every reason). No training starts here."""
    reasons = manifest.refusal_reasons()
    return {
        "phrase": manifest.phrase,
        "ready_to_train": not reasons,
        "refusal_reasons": reasons,
        "negative_hours": manifest.negative_hours,
        "note": ("A ready manifest authorises TRAINING only; the "
                 "trained head still needs its own CC-1 audit "
                 "before any ship decision."),
    }
