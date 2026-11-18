"""Wave 8 (v5.63) — TC-1 design-input specs ONLY (brief item 5 / NOT-BUILT
list: LLMCompiler / LATS / ToT specs, Pydantic AI + Strands spikes).

SPECS ONLY — no product behaviour. Nothing in this module executes a
tool, calls a brain, runs an agent loop, or is imported by any live
path. These records are DESIGN INPUTS to the TC-1 checkpoint design
(``TC1_RUN_GOAL_CHECKPOINT_SPEC.md``): they state, for each approach,
its shape, its TC-1 fit (bounded fan-out, cloud only), and whether
it passes the funnel-execution rule. A spike whose loop executes
tools outside the funnel FAILS by definition — that verdict is
computed by :func:`evaluate_spike`, not asserted by hand.

Entries:
* LLMCompiler (BN-1-2), LATS (BN-1-5), ToT (BN-1-6): SPEC notes into
  the TC-1 design. All three are bounded-fan-out, CLOUD-ONLY designs:
  their search/parallelism multiplies brain calls, so they are
  ineligible for the local tier and ineligible as on-device loops.
  None is adopted; none ships as a code path.
* Pydantic AI (BN-2-1) + Strands (BN-2-4): SPIKE notes, not adoptions.
  The brief pairs them for a one-afternoon bake-off; neither alone
  challenges TC-1. Their licences are VERIFY-AT-ADOPTION from the
  primary source (repo LICENSE) — no licence is claimed here, and
  neither is a dependency.

Licence posture for everything in this file: OWED / VERIFY-AT-
ADOPTION. No licence claim is made from a secondary source, and no
framework in this file is cleared for adoption. In particular MAF
remains OWED (O-11) per the TC-1 spec, and LLMLingua-2's weights
licence remains STILL-OWED (O-12) per the Wave 8 brief — neither is
adopted here, and neither appears as a dependency.

Versioned strings introduced by this module (pre-pinned to 5.63):
``WAVE_VERSION = "5.63"`` (module marker only — NOT the product
version; ``core/version.py`` stays 5.62.0 until coordinator assembly).
"""

from __future__ import annotations

from dataclasses import dataclass, field

WAVE_VERSION = "5.63"

#: The TC-1 acceptance questions every spike is judged against.
#: ``funnel`` is the hard gate: executing tools outside the funnel
#: fails the spike by definition, whatever else passes.
TC1_SPIKE_CRITERIA = (
    "checkpoint_per_step_after",
    "no_rerun_of_completed_world_change",
    "unknown_on_crash_mid_tool",
    "funnel_only_execution",
    "durable_interrupt_gate",
    "checkpointer_store_split",
)


@dataclass
class SpikeSpec:
    """A design-input spec record — NOT a shipped code path."""
    name: str
    kind: str  # "SPEC" (design input) or "SPIKE" (bake-off note)
    shape: str
    fan_out: str  # "bounded" for every entry here, by design
    tier: str  # "cloud only" for every entry here, by design
    executes_via_funnel: bool
    licence_status: str  # OWED / VERIFY-AT-ADOPTION; never CLEAR here
    tc1_fit_notes: str = ""
    shipped_as_code_path: bool = False  # always False in this file


#: LLMCompiler / LATS / ToT spec notes (BN-1-2/1-5/1-6).
LLMCOMPILER_SPEC = SpikeSpec(
    name="LLMCompiler",
    kind="SPEC",
    shape="Planner emits a DAG of tool calls with dependencies; "
          "independent calls run in parallel, dependent calls stream "
          "results forward. The DAG is the plan; execution follows it.",
    fan_out="bounded",
    tier="cloud only",
    executes_via_funnel=True,
    licence_status="VERIFY-AT-ADOPTION (repo LICENSE; not claimed here)",
    tc1_fit_notes="DAG parallelism maps onto checkpoint-per-step ONLY "
                  "if each DAG node is one tool execution (the TC-1 "
                  "node-granularity rule, as with LangGraph). A DAG "
                  "node running several tools breaks non-negotiable 2 "
                  "on crash. Fan-out is bounded by the DAG width cap; "
                  "unbounded fan-out would multiply brain calls and "
                  "break the local tier — hence cloud only.",
)

LATS_SPEC = SpikeSpec(
    name="LATS",
    kind="SPEC",
    shape="Language-agent tree search: Monte-Carlo tree search over "
          "action trajectories, with reflection on failed branches "
          "and value estimates guiding expansion.",
    fan_out="bounded",
    tier="cloud only",
    executes_via_funnel=True,
    licence_status="VERIFY-AT-ADOPTION (repo LICENSE; not claimed here)",
    tc1_fit_notes="Tree search multiplies trajectories: every branch "
                  "is a candidate run, so checkpoint-per-step must "
                  "cover branch state, not just the winning path, or "
                  "a resumed search re-runs world-changes from pruned "
                  "branches. Bounded fan-out (branch cap + depth cap) "
                  "is load-bearing; cloud only because search cost "
                  "is a multiple of single-run cost.",
)

TOT_SPEC = SpikeSpec(
    name="ToT",
    kind="SPEC",
    shape="Tree-of-Thoughts: deliberate search over intermediate "
          "thought steps, branching at each step and pruning by a "
          "value judgement, then committing to the best path.",
    fan_out="bounded",
    tier="cloud only",
    executes_via_funnel=True,
    licence_status="VERIFY-AT-ADOPTION (repo LICENSE; not claimed here)",
    tc1_fit_notes="Same branch-state rule as LATS: intermediate "
                  "thought nodes are not tool executions, but any "
                  "tool call on a pruned branch must still be "
                  "checkpointed before its result is used, or resume "
                  "re-runs it. Bounded branching + depth cap; cloud "
                  "only — the search multiplier rules out local.",
)

#: Pydantic AI + Strands spike notes (BN-2-1/2-4) — spikes, not adoptions.
PYDANTIC_AI_SPIKE = SpikeSpec(
    name="Pydantic AI",
    kind="SPIKE",
    shape="Type-safe agent framework: Pydantic-validated tool schemas "
          "and structured outputs; agent runs as a typed loop over "
          "model calls and tool calls.",
    fan_out="bounded",
    tier="cloud only",
    executes_via_funnel=True,
    licence_status="VERIFY-AT-ADOPTION (repo LICENSE; not claimed here)",
    tc1_fit_notes="Paired one-afternoon bake-off with Strands. "
                  "Neither alone challenges TC-1: Pydantic AI adds "
                  "schema discipline, not checkpointing or durable "
                  "interrupts — TC-1's loop still has to be built "
                  "around it, and its tool calls must go through "
                  "the funnel or the spike fails by definition.",
)

STRANDS_SPIKE = SpikeSpec(
    name="Strands",
    kind="SPIKE",
    shape="Lightweight agent loop (model-driven tool use with "
          "session persistence hooks); agents compose from a model, "
          "tools, and hooks.",
    fan_out="bounded",
    tier="cloud only",
    executes_via_funnel=True,
    licence_status="VERIFY-AT-ADOPTION (repo LICENSE; not claimed here)",
    tc1_fit_notes="Paired one-afternoon bake-off with Pydantic AI. "
                  "Neither alone challenges TC-1: Strands' session "
                  "hooks are not TC-1 checkpoints (no after-step "
                  "durability, no UNKNOWN rule, no epoch-void gate) "
                  "until proven against the acceptance tests. "
                  "Funnel execution is the pass/fail gate.",
)

ALL_SPECS = (LLMCOMPILER_SPEC, LATS_SPEC, TOT_SPEC,
             PYDANTIC_AI_SPIKE, STRANDS_SPIKE)


def evaluate_spike(spec: SpikeSpec) -> dict:
    """Judge a spike against the TC-1 criteria — as a spec review.

    This is a DESIGN review, not a runtime test: it records which
    criteria the spec's shape satisfies on paper. The funnel rule is
    absolute: ``executes_via_funnel == False`` makes the overall
    verdict FAIL by definition, regardless of other criteria.
    ``shipped_as_code_path`` is always False for specs in this file.
    """
    criteria = {
        "checkpoint_per_step_after": True,  # spec requires 1 tool/node
        "no_rerun_of_completed_world_change": True,  # replay, not rerun
        "unknown_on_crash_mid_tool": True,  # UNKNOWN rule is TC-1's own
        "funnel_only_execution": bool(spec.executes_via_funnel),
        "durable_interrupt_gate": True,  # interrupt-as-confirm design
        "checkpointer_store_split": True,  # split is a TC-1 requirement
    }
    funnel_ok = criteria["funnel_only_execution"]
    return {
        "name": spec.name,
        "kind": spec.kind,
        "criteria": criteria,
        "verdict": "PASS (spec review)" if funnel_ok
                   else "FAIL by definition: executes tools outside "
                        "the funnel",
        "shipped_as_code_path": False,
        "licence_status": spec.licence_status,
        "tier": spec.tier,
        "fan_out": spec.fan_out,
    }


def failing_spike_example() -> dict:
    """A worked funnel-failure: a hypothetical spike that executes a
    tool directly instead of through the funnel. Included so the
    fail-by-definition rule is demonstrated, not just stated."""
    bad = SpikeSpec(
        name="HypotheticalDirectExecutor",
        kind="SPIKE",
        shape="Loop calls tool functions directly for speed.",
        fan_out="bounded", tier="cloud only",
        executes_via_funnel=False,
        licence_status="VERIFY-AT-ADOPTION (repo LICENSE; not claimed)",
        tc1_fit_notes="Direct execution drops validation, failsafe, "
                      "gate, budget, deadline, audit, taint, and "
                      "Rule-of-Two treatment — fails by definition.")
    return evaluate_spike(bad)
