"""Conversion pass — Wave 8 (v5.63) wiring coordinator.

Wires the staged Wave 8 modules into live paths WITHOUT new tool
declarations, WITHOUT a VERSION bump, WITHOUT a requirements.txt
change, and WITHOUT touching the Phase 0 files. Every seam here
keeps the staged module's own safety shape:

* Checkpoint loop: tools run ONLY through the injected funnel
  executor (the live registry's execute(), via planner's
  _execute_tool seam). Crash-mid-tool = UNKNOWN, human re-run /
  skip / abort — never an auto-retry, never an auto-skip.
* Context ledger / budgets / pre-flight + prompt-cache-first
  assembly: called from the three live prompt-context sites
  (live_client session prompt, local_client per-turn memory
  block, cloud_text_client session prompt). Budgets are
  ENFORCED: an over-budget prompt drops its optional
  supplement streams first, then refuses fail-closed — it is
  never sent over budget, and never silently trimmed.
* Plan verification: every checkpointed step is validated
  against the LIVE registry (fresh declaration read) before
  any execution. Fail behaviour is FAIL-CLOSED refuse by
  default (an owner decision can overturn it to repair; this
  module never auto-repairs a plan). Irreversibility is a
  gate flag, not a failure — the registry's own confirm gate
  still applies at execution.
* Reflexion: exactly ONE retry around a live step executor,
  retries capped by the module's counter, the retry audited
  (retries_used / tainted / structure in the returned report).
  Retry policy comes from this layer, never from the
  checkpoint loop (which never auto-retries).
* Teach mode: replay() through the funnel executor, verified
  before each next step, with a file-backed stored-sequence
  store under data/ (runtime-created; data/ ships empty).
  Voice trigger is the Live router path (route_mind domain
  router + use_toolbox ranking; see TEACH_LIVE_CAPTURE_GATE).
  Live demonstration capture is GATED, not built — the gate
  names the missing observer, mapper, and field validation;
  everything up to live capture is wired.
* Second opinion: cross_check() to two REAL brain callables
  (a live cloud session's _chat_completion and a live local
  session's _llama_chat/_ollama_chat, wrapped by
  second_opinion_from_sessions). Disagreements escalate and
  resolved_answer is forced None by the module itself.
  Trigger RULED 2026-11-05 (owner): a step triggers second opinion
  ONLY when BOTH hold — a destructive word AND a side-effecting
  tool — auto-invoked in the coordinator when brains are configured
  (see SECOND_OPINION_TRIGGER_OWED). The explicit opt-in
  request_second_opinion() path is preserved; no live answer path
  calls it directly, so calling it is always a deliberate decision.
* PF-28 learning feed: fact_prompt_lines() (expiry honoured,
  opinions never included) is folded into prompt assembly by
  prompt_supplements(); append() is wired to the live learning
  event source core/learning.py::learn_from_text (see
  core/learning.py). Per-row expiry dates are OWED from the
  feed API — this module invents none (feed records carry
  only timestamp + kind-derived TTL).
* Sleeptime trio: build_memory_block() into the prompt path
  via the FILE-STORE seam only (memory_files.list_facts;
  the frozen DB is never opened by this path — sleeptime has
  no sqlite call path at all, verified by grep in the
  conversion suite). idle_pass() is wired up to, but NOT
  triggered by, any voice path: run_sleeptime_pass() refuses
  when called from a voice path, and no live tick calls it —
  the idle trigger is OWED (owner decision on signal +
  threshold). Never call sleeptime from a voice-path thread.
* Trace-mining: the reader feeds cluster_failures() ONLY
  scrubbed records from the hash-chained records store
  (core/audit_chain.read), never raw logs. Case commit needs
  a HUMAN-supplied expected_behaviour (good_output) + prompt;
  the wiring itself commits zero cases.
* PA-3 speculative flags: spec_enabled flags are consulted
  in the live local generation path (local_client's builtin
  turn) ONLY with the measurement harness attached
  (measure() + measured_gain()). measured_gain() returns
  real data or None — the plan's 1.2-1.6x figure is NEVER
  quoted as measured (claimed_gain() labels it
  PLAN-CLAIM-NOT-MEASURED).

GATED — deliberately NOT wired here, and no function below
touches them: compaction (needs a passing session-replay
eval first), the s1 auto-router (REMOVED Phase 3 C06 — never
wired; licence was owed, ONNX unavailable, heuristic unproven), the deep-think harness
(NO-DATA; needs an engine adapter + live eval incl. Hindi,
and must never run from a voice path), TC-1 spec semantics
(spec/eval code, feeds the TC-1 loop's eval once that loop
is live — no direct wiring), and no framework is adopted
(D-6 still the owner's).

This module imports no brain, no tool registry, and no GUI
at module level (all of those are lazy, inside functions),
so importing it can never create a cycle or a live side
effect. It declares no tool and adds zero fitted bytes.
"""

from __future__ import annotations  # INTENTIONAL (skills split B14): old module carried it (B1 lesson).

# INTENTIONAL (skills split B14): stdlib imports travel as facade
# re-exports so the dir diff is empty both ways (files3/files2 lesson).
# json/os/re/Path/Any/Callable/Optional: imported by the old module;
# json is used by taught, re by steps; os/Callable are imported-but-unused
# in the old module and travel here for the dir-diff bar only.
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

# INTENTIONAL (skills split B14): original module imports travel as
# facade re-exports (same objects) so importers and dir() see them.
from .brain import checkpoint, context, plan_verify, reflection, teach
from . import learning_feed, second_opinion, sleeptime, speculative, trace_mining
from .memory.memory_files import list_facts

# INTENTIONAL (skills split B14): re-exports from w8_wiring_gates.
from .w8_wiring_gates import (
    WIRING_VERSION,
    TEACH_LIVE_CAPTURE_GATE,
    SECOND_OPINION_TRIGGER_OWED,
    SLEEPTIME_IDLE_TRIGGER_OWED,
    SLEEPTIME_CANDIDATE_GATE,
    LEARNING_EXPIRY_OWED,
    _REFUSAL_MARKERS,
    _MAX_SUPPLEMENT_CHARS,
    _MAX_LEARNING_LINES,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_registry.
from .w8_wiring_registry import (
    _registry,
    live_tool_names,
    _JSON_TYPE_MAP,
    live_arg_schemas,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_funnel.
from .w8_wiring_funnel import (
    funnel_step_executor,
    funnel_tool_executor,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_prompt.
from .w8_wiring_prompt import (
    _LAST_STABLE_PREFIX,
    note_stable_prefix,
    stable_prefix_key,
    stable_prefix_is_stable,
    preflight_prompt,
    enforce_prompt,
    learning_lines,
    sleeptime_block,
    prompt_supplements,
    record_learning_event,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_steps.
from .w8_wiring_steps import (
    _cove_answer_factory,
    verify_steps_against_live_tools,
    execute_step_with_reflection,
    checkpoint_store_dir,
    run_steps_checkpointed,
    resume_checkpointed_run,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_taught.
from .w8_wiring_taught import (
    TAUGHT_DIRNAME,
    taught_dir,
    _taught_path,
    record_taught_sequence,
    load_taught_sequence,
    list_taught_sequences,
    replay_taught_sequence,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_opinion.
from .w8_wiring_opinion import (
    second_opinion_from_sessions,
    request_second_opinion,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_sleeptime.
from .w8_wiring_sleeptime import (
    run_sleeptime_pass,
    maybe_sleeptime_on_idle,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_mining.
from .w8_wiring_mining import (
    live_failure_records,
    commit_mined_case,
)

# INTENTIONAL (skills split B14): re-exports from w8_wiring_speculative.
from .w8_wiring_speculative import (
    speculative_generation_gate,
)


# INTENTIONAL (skills split B14, B2 pattern): mine_live_trace_clusters
# is DEFINED in the facade, not re-exported from w8_wiring_mining.
# tests/test_wiring_triage_v600.py:272 patches w8.live_failure_records
# (the facade-module attribute) and calls w8.mine_live_trace_clusters().
# The miner resolves live_failure_records by bare name, so it must live
# in the module whose attribute the test patches: a re-exported miner
# resolves the mining module's own binding and the patch silently
# misses (proven live: triage failed 0 != 1 clusters; backup PASS).
# The function resolves live_failure_records (re-exported above from
# mining) and trace_mining (re-exported above) in facade globals.
def mine_live_trace_clusters(limit: int = 200) -> dict:
    """Cluster scrubbed live-log failures (no cases committed).
    The returned clusters are mined-cluster records WITHOUT a
    good_output — they become regression cases ONLY when a
    human supplies expected_behaviour via commit_mined_case()."""
    records = live_failure_records(limit=limit)
    return trace_mining.cluster_failures(records)

