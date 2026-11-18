"""w8_wiring part module (skills split B14): w8_wiring_speculative."""
from __future__ import annotations

# Module-level imports travel verbatim with the units that use them.
from . import speculative



# ---------------------------------------------------------------------
# PA-3 speculative flags + measurement harness (item 11)
# ---------------------------------------------------------------------


def speculative_generation_gate(tier: str, runs=None,
                                flags: Optional[dict] = None) -> dict:
    """Consult the PA-3 flags for one tier in a live generation
    path, WITH the measurement harness attached. Returns the
    enable record, the PA-4 measurement, and measured_gain()
    — which is real data or None. The plan's 1.2-1.6x figure
    is reported ONLY via claimed_gain() with its
    PLAN-CLAIM-NOT-MEASURED label, never as measured_gain.
    A flag that is ON does not by itself turn on speculative
    decoding (no llama.cpp runtime exists here); generation
    proceeds non-speculatively and the harness says NO-DATA
    until live runs exist."""
    enable = speculative.enable_spec(tier)
    measurement = speculative.measure(tier, enable["variant"],
                                           runs=runs)
    return {
        "tier": tier,
        "enabled": speculative.spec_enabled(tier, flags=flags),
        "enable_record": enable,
        "runtime_available": speculative.runtime_available(),
        "measurement": {"verdict": measurement.verdict,
                        "heads": dict(measurement.heads),
                        "reason": measurement.reason},
        "measured_gain": speculative.measured_gain(runs=runs),
        "claimed_gain": speculative.claimed_gain(),
    }


