"""Wave 8 (v5.63) — PA-3 llama.cpp speculative suite (PA-4 discipline).

Flag-gated speculative decoding per tier. This module is FLAGS +
MEASUREMENT HARNESS, not an inference engine: no llama.cpp runtime
exists on this VM, no binary is spawned, no draft model is loaded,
and no speedup is measured here. Every gain figure in this module
is either the plan's claimed figure (labelled as such, never as
measured) or NO-DATA.

NGRAM SELF-SPECULATION (~16 MB, no draft model):
  * The ngram variant needs no draft model: it speculates from
    the output's own recent ngrams, so its INV-21 cost is just the
    ~16 MB table (``NGRAM_MB``), not a second model. It is the only
    variant that is cheap enough to consider on every tier.
  * Honest CPU gains of ~1.2-1.6x on repetitive output are the
    PLAN'S figure (``PLAN_CLAIMED_GAIN``), not a measurement made
    here: repetitive output speculates well, novel output does
    not, and CPU acceptance rates are lower than GPU ones. Any
    claim beyond that range, or any claim presented as measured,
    would be fabrication — ``measured_gain`` returns None until
    live runs exist.

DRAFT VARIANTS PER TIER — DRAFT MODELS ARE NOT FREE:
  * A draft model costs its full bytes on top of the target model
    (``draft_gb`` in ``DRAFT_VARIANTS``). ``enabled_for`` refuses
    any tier whose target + draft + ngram total breaks the 8 GB
    INV-21 budget, and any variant whose draft size is still
    unknown (``draft_gb=None``) is refused until it is sized —
    an unsized draft is an unbounded cost, not a free option.
  * Per-tier defaults: ngram-only everywhere. A draft variant
    earns a tier only by being sized, fitting, and winning a
    live measurement — not by being named here.

PA-4 MEASUREMENT DISCIPLINE (same heads, not double-built):
  * ``MEASUREMENT_HEADS`` is the single list of measured heads:
    latency, throughput, acceptance rate, and quality delta.
    ``measure`` runs each head ONCE per config and reports it
    once; there is no second measurement pass that could double-
    count or quietly disagree with the first. With no runtime and
    no injected runner, ``measure`` returns NO-DATA, not zeros —
    zeros would read as "measured, no effect".
  * A measurement that reuses another module's heads must import
    this list, not copy it: a copied list drifts, and drift is
    how double-building starts.

RUNTIME DETECTION (honest, no spawning):
  * ``runtime_available`` only checks whether a llama.cpp CLI is
    on PATH (``shutil.which``); it never runs one. On this VM it
    is False, which is why the verdict is NO-DATA.

LICENCE (no bundling in this module):
  * llama.cpp is widely reported as MIT (secondary sources only
    in this session: e.g. the project's Wikipedia infobox and
    distro spec files listing "License: MIT"). The repo LICENSE
    file text was NOT read from a primary source here, so the
    primary-source check is OWED at any future bundling gate.
    This module bundles no llama.cpp code and needs no clearance
    to exist as flags + harness; the OWED item blocks bundling,
    not the harness.

Versioned strings introduced by this module (for the coordinator's
pre-pin): ``MODULE_VERSION = "5.63.0"``; ``NGRAM_MB = 16``;
``PLAN_CLAIMED_GAIN = (1.2, 1.6)`` (plan figure, not measured);
the tier names in ``TIER_GB`` (shared with local_assets tiers).
This module does NOT touch core/version.py.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

MODULE_VERSION = "5.63.0"

RESIDENT_BUDGET_GB = 8.0
NGRAM_MB = 16
NGRAM_GB = NGRAM_MB / 1024.0

# Resident target-model sizes (GB), same tier table as the plan's
# INV-21 accounting (core/voice/local_assets.py MODEL_TIERS).
TIER_GB = {"lite": 1.1, "balanced": 2.5, "quality": 5.0,
           "deep-think": 2.5}

# The plan's claimed figure — labelled as a claim, never a result.
# "Honest CPU gains ~1.2-1.6x on repetitive output" is what the
# plan asserts; nothing on this VM measured it.
PLAN_CLAIMED_GAIN = (1.2, 1.6)
PLAN_CLAIMED_GAIN_NOTE = (
    "PLAN FIGURE, NOT MEASURED HERE: ~1.2-1.6x on repetitive "
    "output only. Novel output and CPU acceptance rates will be "
    "lower. Do not quote this as a measured speedup.")

# PA-4: the single list of measured heads. Import this list; do
# not copy it into another module.
MEASUREMENT_HEADS = ("latency_ms", "tokens_per_s", "acceptance_rate",
                     "quality_delta")

LICENCE_VERDICTS = {
    "llama.cpp": {
        "licence": "MIT (secondary sources only; primary OWED)",
        "verdict": "OWED-primary",
        "primary_source": (
            "NONE READ this session: no repo LICENSE file text "
            "was retrieved from a primary source. Secondary "
            "reports (project infobox, distro spec files) list "
            "MIT. Bundling llama.cpp code requires reading the "
            "repo LICENSE first. This module bundles no llama.cpp "
            "code, so the OWED item blocks bundling, not the "
            "harness."
        ),
        "ships_code": False,
    },
}


def licence_verdict(item: str) -> dict | None:
    """The licence verdict record for an item, or None if unknown."""
    return LICENCE_VERDICTS.get(item)


# --- Draft variants ------------------------------------------------------------
@dataclass(frozen=True)
class DraftVariant:
    """One speculative variant. ``draft_gb=None`` means unsized:
    the variant exists as a name only and is refused everywhere
    until a real size is recorded."""
    name: str
    draft_gb: float | None
    needs_draft_model: bool
    note: str


DRAFT_VARIANTS = {
    # No draft model: the only variant cheap enough by default.
    "ngram": DraftVariant(
        name="ngram",
        draft_gb=0.0,
        needs_draft_model=False,
        note="Self-speculation from the output's own ngrams; "
             "cost is the ~16 MB table only."),
    # Named but unsized: refused until sized (drafts are not free).
    "small-draft": DraftVariant(
        name="small-draft",
        draft_gb=None,
        needs_draft_model=True,
        note="UNSIZED: no draft model is chosen and no size is "
             "recorded. Refused until sized, fitted, and measured."),
    "tier-draft": DraftVariant(
        name="tier-draft",
        draft_gb=None,
        needs_draft_model=True,
        note="UNSIZED: a same-family draft for the tier, if one is "
             "ever sized. Refused until sized, fitted, and measured."),
}


def variant_for(variant_name: str) -> DraftVariant:
    """The variant record, or a refusal for an unknown name."""
    if variant_name not in DRAFT_VARIANTS:
        raise ValueError(f"unknown speculative variant: {variant_name!r}")
    return DRAFT_VARIANTS[variant_name]


def tier_total_gb(tier: str, variant_name: str = "ngram") -> float:
    """Target + draft + ngram table, in GB, for one tier.

    Raises on an unsized draft: an unknown cost must not silently
    pass as zero. (``ngram`` is the only variant with a known,
    zero draft cost.)"""
    if tier not in TIER_GB:
        raise ValueError(f"unknown tier: {tier!r}")
    variant = variant_for(variant_name)
    if variant.draft_gb is None:
        raise ValueError(
            f"draft variant {variant_name!r} is unsized: draft "
            "models are not free, and an unknown size cannot be "
            "accounted. Size it before any fit check.")
    return TIER_GB[tier] + variant.draft_gb + NGRAM_GB


def fits_budget(tier: str, variant_name: str = "ngram") -> bool:
    """True only if the tier's speculative total fits 8 GB.

    An unsized draft never 'fits': it refuses (False) rather than
    passing on an assumed size."""
    try:
        return tier_total_gb(tier, variant_name) <= RESIDENT_BUDGET_GB
    except ValueError:
        return False


# --- Per-tier flags ---------------------------------------------------------------
# Flag-gated per tier: OFF unless explicitly enabled AND fitting.
# Defaults are ngram-only and OFF — speculation is opt-in per tier,
# never a silent global switch.
SPEC_FLAGS = {
    "lite": {"enabled": False, "variant": "ngram"},
    "balanced": {"enabled": False, "variant": "ngram"},
    "quality": {"enabled": False, "variant": "ngram"},
    "deep-think": {"enabled": False, "variant": "ngram"},
}


def spec_enabled(tier: str, flags: dict | None = None,
                 explicit: bool | None = None) -> bool:
    """Is speculation enabled for this tier?

    ``explicit`` wins over the flag table (same shape as the other
    Wave 8/9 flag gates); a tier whose total breaks the budget is
    never enabled, whatever the flag says — the budget refusal
    outranks the flag."""
    table = flags if flags is not None else SPEC_FLAGS
    entry = table.get(tier)
    if entry is None:
        raise ValueError(f"unknown tier: {tier!r}")
    wanted = entry.get("enabled", False) if explicit is None else explicit
    if not wanted:
        return False
    return fits_budget(tier, entry.get("variant", "ngram"))


def enable_spec(tier: str, variant_name: str = "ngram") -> dict:
    """The flag record that WOULD enable speculation for a tier,
    or the refusal that blocks it. This function changes no global
    state: the coordinator owns the flag table and applies this
    record deliberately."""
    variant = variant_for(variant_name)
    if variant.draft_gb is None:
        return {
            "tier": tier, "enabled": False, "variant": variant_name,
            "reason": ("Refused: draft variant is unsized. Size "
                       "the draft, fit it under INV-21, and measure "
                       "it before enabling."),
        }
    if not fits_budget(tier, variant_name):
        return {
            "tier": tier, "enabled": False, "variant": variant_name,
            "reason": (f"Refused: {tier} + {variant_name} totals "
                       f"{tier_total_gb(tier, variant_name):.3f} GB, "
                       "over the 8 GB INV-21 budget."),
        }
    return {
        "tier": tier, "enabled": True, "variant": variant_name,
        "total_gb": round(tier_total_gb(tier, variant_name), 4),
        "reason": "Fits under INV-21; still needs a live measurement.",
    }


# --- Runtime detection (no spawning) --------------------------------------------
def runtime_available() -> bool:
    """True only if a llama.cpp CLI is on PATH. Checked with
    ``shutil.which`` only — nothing is executed, no model loads."""
    return (shutil.which("llama-cli") is not None
            or shutil.which("llama-server") is not None)


# --- PA-4 measurement harness ------------------------------------------------------
@dataclass(frozen=True)
class SpecRun:
    """One measured speculative run, supplied by a live runner or
    a labelled fixture. ``live=False`` runs never yield a measured
    gain."""
    tier: str
    variant: str
    baseline_tokens_per_s: float
    spec_tokens_per_s: float
    acceptance_rate: float
    quality_delta: float = 0.0
    live: bool = False


@dataclass
class Measurement:
    """One PA-4 measurement: each head measured once, reported
    once. ``verdict`` is NO-DATA unless live runs exist."""
    tier: str
    variant: str
    heads: dict = field(default_factory=dict)
    verdict: str = "NO-DATA"
    reason: str = ""


def measure(tier: str, variant_name: str,
            runs: list[SpecRun] | None = None) -> Measurement:
    """Measure one tier/variant config, once per head.

    With no runner and no live runs this returns NO-DATA with
    empty heads — not zeros. Zeros would masquerade as "measured,
    no effect"; empty heads say "not measured". A live run set
    yields one value per head, computed once from the same runs
    (PA-4: same heads measured, not double-built)."""
    live = [r for r in (runs or []) if r.live]
    if not live:
        return Measurement(
            tier=tier, variant=variant_name, heads={},
            verdict="NO-DATA",
            reason=("No llama.cpp runtime on this VM and no live "
                    "runner injected. Nothing was measured; the "
                    "plan's 1.2-1.6x figure remains a claim, not a "
                    "result."))
    n = len(live)
    heads = {
        "latency_ms": None,  # Latency needs timed runs; token-rate
        # runs alone do not imply it, so it stays explicitly None
        # rather than being derived from throughput.
        "tokens_per_s": round(
            sum(r.spec_tokens_per_s for r in live) / n, 3),
        "acceptance_rate": round(
            sum(r.acceptance_rate for r in live) / n, 4),
        "quality_delta": round(
            sum(r.quality_delta for r in live) / n, 4),
    }
    return Measurement(tier=tier, variant=variant_name, heads=heads,
                       verdict="LIVE",
                       reason=f"Measured on {n} live run(s).")


def measured_gain(runs: list[SpecRun] | None = None) -> float | None:
    """Measured speedup, or None. Never falls back to the plan's
    claimed range: a claim is not a measurement."""
    live = [r for r in (runs or []) if r.live]
    if not live:
        return None
    base = sum(r.baseline_tokens_per_s for r in live) / len(live)
    spec = sum(r.spec_tokens_per_s for r in live) / len(live)
    if base <= 0:
        return None
    return round(spec / base, 3)


def claimed_gain() -> dict:
    """The plan's claimed gain range, labelled as a claim."""
    return {
        "range": PLAN_CLAIMED_GAIN,
        "status": "PLAN-CLAIM-NOT-MEASURED",
        "note": PLAN_CLAIMED_GAIN_NOTE,
        "applies_to": "repetitive output, CPU",
    }


# --- Coordinator seam ---------------------------------------------------------------
def wiring_seam() -> dict:
    """What the coordinator wires, and what it must NOT wire.

    The coordinator owns the flag table (apply ``enable_spec``
    records deliberately), injects a live runner into ``measure``
    once a llama.cpp runtime exists, and maps tier names onto the
    real inference path. It must NOT enable a tier that breaks
    INV-21, NOT enable an unsized draft, and NOT quote the plan's
    gain range as a measured result."""
    return {
        "flags": "SPEC_FLAGS (OFF by default, per tier)",
        "enable": "enable_spec(tier, variant) -> record or refusal",
        "measure": "measure(tier, variant, runs) (PA-4 heads, once)",
        "runtime": "runtime_available() (PATH check only, no spawn)",
        "must_not_wire": [
            "a tier whose total breaks the 8 GB INV-21 budget",
            "an unsized draft variant",
            "the plan's 1.2-1.6x claim as a measured speedup",
            "a llama.cpp binary bundle while the licence is OWED",
        ],
        "disposition": "STAGED flags + measurement harness; "
                        "verdict NO-DATA (no llama.cpp runtime here)",
    }
