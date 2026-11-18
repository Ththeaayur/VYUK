# Licence read — Wave 7 Build 5: P18 multiverse planner + compiler

**Scope:** `core/multiverse_planner.py` + `core/multiverse_tools.py`.
Own code, written for this build. Stdlib only (`hashlib`, `json`).
No third-party code, library, model, or asset is introduced,
copied, or depended on by this build, so no third-party
licence is engaged and no licence text needs to be filed.

**Reuse, not new code:** the planner re-validates branches
through Build 4 (`core/improvement_proposals.py`, own code)
and the compiler's file-effect prediction reuses Build 3
(`core/sim_predict.py`, own code, which itself reuses
Build 1's sandbox copy, own code). No new dependency is
introduced by that reuse.

**Gates this read does NOT clear:** per-branch licences
(if a future branch ever names third-party code, that
branch's licence read is owed at that branch, and the
Build 6 extension harness audit governs it first);
full-VM universes (item 12, QUEUED); M4 itself (PROPOSED,
not built); any payment, voiceprint, continuous-capture,
vehicle, medical, or marketplace surface (all out of
scope / gated, none opened by this build).

**Verdict:** no licence blocker in this build itself.
