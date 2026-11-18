# TC-1: run_goal checkpointing — STUDY → SPEC (Wave 1, consumed by Wave 8)

**Status:** SPEC FILE ONLY — zero product code. No framework is
adopted by this document, and no framework decision is made
here. The slot's OWNER is the owner's pick (plan §6, conflict
C-2, decision D-6) — see "The pick" at the end.
**Sources:** LangGraph docs (checkpointers, durability modes),
Microsoft Agent Framework docs (workflows as agents,
checkpoints), Google ADK docs and field guides (sessions,
resumable execution) — studied October 2026 for this spec.
Licences of all three frameworks are VERIFY-AT-ADOPTION
(see "Fit questions" — MAF's licence is explicitly owed,
O-11, and gates challenger eligibility, not this study).

## The problem this spec solves

Today's `run_goal` (core/planner + the local brain's turn
loop) is a live-process loop: one chat call per step,
tool results fed back as messages, a repair budget, an
expect-verifier, and the synchronous confirmation gate
(a popup that blocks the turn). If the process dies
mid-run — crash, reboot, user closes the window — the run
is gone: completed steps are not recorded anywhere
durable, a pending approval evaporates, and a restart
re-runs from step one, including any tool that already
changed the world. A goal that spans a human approval
(an hour, a day) cannot survive the wait at all: the
process must stay alive holding the question open.

Wave 8 builds a run_goal that survives all of that. This
spec fixes WHAT it must do, so Wave 8 can choose the
framework against a checklist instead of a vibe.

## The non-negotiables (framework-independent)

These hold whatever framework Wave 8 picks. A framework
that cannot meet one is out, no matter how elegant its
API is.

1. **Checkpoint-per-step, after — never before.** A
   checkpoint is written AFTER each tool execution
   completes (and after each human decision lands), never
   before it starts. "After" is what makes resume safe:
   the checkpoint claims only what actually happened.
2. **Never silently re-run a completed world-change.**
   On resume, completed steps are replayed from the
   checkpoint, not re-executed. This is absolute for
   send/delete/purchase-class steps: a resumed run must
   never send a second email because the first send's
   checkpoint was lost.
3. **Crash mid-tool = UNKNOWN, never assumed.** If the
   process died while a tool was running, the step's
   outcome is unknown (the tool may or may not have
   acted). On resume that step comes back as UNKNOWN and
   the HUMAN decides: re-run, skip, or abort the run.
   No framework auto-retry may decide this.
4. **The funnel is still the only door (INV-2).** A
   framework's "tool node", if it has one, must call
   `ToolRegistry.execute()` — it must never execute a
   tool directly, or the validation, failsafe, gate,
   budget, deadline, audit, taint, and Rule-of-Two
   treatment all silently drop out of the run.
5. **The gate becomes a durable interrupt, not a
   blocking popup.** Today's gate asks and waits inside
   the turn. The upgraded gate pauses the run, persists
   the checkpoint, and lets the process exit; resume
   delivers the human's decision into the exact paused
   step. Epoch-void semantics survive the upgrade: an
   approval recorded under a dead session epoch is void
   on resume, and the checkpoint carries the epoch so
   resume can check it.
6. **Checkpointer vs store, split, never merged.** The
   CHECKPOINTER holds run progress: steps, results,
   pending approvals, budgets, verdicts — short-lived,
   per-run, deleted when the run completes. The STORE
   holds long-term memory: facts, journal entries — the
   frozen memory schema (INV-8, 6,000-char cap). A run's
   scratchpad is not a memory, and a memory is not a
   checkpoint. Merging them is how a half-finished run
   would pollute recall for every later turn.
7. **Rule of Two travels with the run (SE-3.1).** The
   session legs exercised so far are part of the
   checkpoint. A resumed run keeps its budget: legs do
   not reset because the process restarted.
8. **Redaction travels with the checkpoint.** Checkpoint
   arguments pass through the audit redaction before
   persisting (same fields, same rules). Checkpoints live
   in `data/` (runtime state, never shipped) under the
   same protection posture as `audit.jsonl`. Secrets are
   never checkpointed: an argument that would be refused
   by the protected-path check is refused by the
   checkpoint writer too.
9. **Atomic writes, last-known-good fallback.** A
   checkpoint write is atomic (tmp file + os.replace, or
   the store's transaction). A torn write must never
   corrupt the previous good checkpoint: on a read
   failure the run falls back to the last good checkpoint
   and reports the gap, never to a half-written state.
10. **Budgets and verdicts are checkpointed, not
    recomputed.** The step budget, the repair budget, and
    every expect-verdict are run evidence: they persist
    with the step, so resume continues the same budgets
    instead of granting a fresh allowance per restart
    (a restart must never be a budget reset).

## The checkpoint record (framework-agnostic shape)

One record per step, appended in order, per run:

* `run_id`, `step_index`, `created_at`, `epoch`
  (the session epoch the run started under).
* `step`: the tool name + redacted arguments + the
  step's `expect` text (if any).
* `status`: `completed` | `pending_approval` | `unknown`
  (crashed mid-tool) | `skipped` (human decision) |
  `failed` (tool error, with the error kind).
* `result`: the redacted tool result (or the failure),
  plus the expect-verdict when one exists.
* `legs`: the Rule-of-Two legs exercised by this step.
* `approval`: for gated steps, the gate request ID
  (joining the audit record) + the human's decision +
  when it landed.
* `budgets`: the step/repair budget counters after this
  step.

A run is resumable while any record exists; a run with
no `pending_approval` and no `unknown` steps resumes by
continuing at `step_index + 1`. A run WITH an `unknown`
step resumes by asking the human about that step first
— never by proceeding past it.

## The three challengers, on equal footing

### BN-2-1 · LangGraph (the study's base)

* **Checkpointing:** a checkpointer object passed to
  `compile()`; state saved after every node, keyed by
  `thread_id`; resume restores the thread exactly.
* **Interrupt:** `interrupt()` pauses a node and saves
  state via the checkpointer; resume delivers a
  `Command(resume=...)` hours or days later. This is the
  closest published shape to the durable-gate upgrade
  in non-negotiable 5.
* **Checkpointer vs store:** the split exists natively —
  the checkpointer holds short-term thread state, the
  Store API holds long-term cross-session memory. The
  spec's split (non-negotiable 6) maps onto it directly.
* **Durability modes (a real trade-off, not a detail):**
  `exit` (persist only on exit/interrupt — fastest, no
  crash recovery mid-run), `async` (background writes —
  the DEFAULT, with a small documented risk that a crash
  loses the latest checkpoint), `sync` (written before
  the next step — highest durability, performance cost).
  For run_goal, `sync` is the only mode that meets
  non-negotiable 2: an `async` checkpoint lost to a crash
  is exactly a completed world-change that resume would
  re-run.
* **Granularity warning (load-bearing for Wave 8):**
  LangGraph writes ONE checkpoint per NODE, not per tool
  call. A node that runs four tool calls and then
  crashes re-runs all four on resume. So a LangGraph
  run_goal must put ONE tool execution per node — the
  node boundary IS the checkpoint boundary, and the
  graph must be drawn that way, or non-negotiable 2
  fails by construction.
* **Replay:** invoking with a prior checkpoint ID
  re-executes later nodes, and interrupts re-trigger on
  replay — replay is a debugging tool, not a resume
  path, and Wave 8 must not confuse the two.
* **Fit notes:** Python + TypeScript, 1.0 stable
  (October 2025). The graph model fits VYUK's
  think→act→observe loop well, but every VYUK control
  (funnel, gate, taint, budget) must be re-expressed as
  graph nodes/edges — the framework does not supply
  them, and a node that bypasses the funnel breaks
  non-negotiable 4.

### BN-2-2 · Microsoft Agent Framework (MAF)

* **Checkpointing:** a `CheckpointManager` + a
  `CheckpointStorage` (in-memory, file, and other
  backends) provided when creating a workflow run; a
  checkpoint captures the whole workflow state — all
  executors, pending messages for the next superstep,
  pending requests/responses, shared state — and is
  surfaced per superstep (`SuperStepCompletedEvent`).
* **Interrupt/human-in-the-loop:** checkpoint + pause
  for approval, resume from the checkpoint on the
  human's decision; a workflow can also be wrapped as
  an agent whose session serialises (`SerializeSession`
  / `DeserializeSession`) for cross-restart continuity.
* **State storage:** a `StateStore` interface with
  pluggable backends (in-memory, Redis, Cosmos DB,
  PostgreSQL per secondary reports — verify against the
  primary docs at build time). Snapshots at agent
  boundaries; recovery loads the last checkpoint.
* **Stated principle (adopt it whatever wins):**
  checkpoint AFTER each step completes, not before —
  identical to non-negotiable 1. The framework also
  demands idempotent agents (re-running an agent with
  the same input must not corrupt state) — which is the
  framework's way of stating non-negotiable 3's problem
  without solving it: VYUK still needs the UNKNOWN rule
  for non-idempotent tools, because "be idempotent" is
  not an available property of sending an email.
* **Fit notes:** .NET-first with a Python SDK
  (`agent-framework`); graph-based workflows, built-in
  OpenTelemetry observability (telemetry-OFF discipline
  applies — an exporter must never be configured by
  default), MCP server support (complements, never
  replaces, the HE-1 bus). The superstep granularity
  question mirrors LangGraph's node question: Wave 8
  must verify that one checkpoint covers exactly one
  tool execution, not a whole superstep of several.
* **Licence: OWED (O-11).** MAF's licence gates
  challenger eligibility, not this study — but Wave 8
  may not adopt MAF until O-11 clears from the primary
  source. Do not assume it.

### BN-2-3 · Google ADK (Agent Development Kit)

* **Sessions as the persistence unit:** a `Session`
  holds conversation history, intermediate results, and
  user-scoped state; a `SessionService` persists it
  (in-memory for dev, database-backed — Redis, MongoDB,
  Postgres — or cloud storage / Firestore in production).
  State scopes are explicit: session (this session),
  `user:` (across sessions), `app:` (all users), `temp:`
  (not persisted past the run). The scope discipline is
  a good model for non-negotiable 6's split, though the
  mapping is session→checkpointer, not session→store.
* **Resumable execution (ADK 2.0):** a run pauses for
  human input or long tool completion and resumes later;
  the same checkpoint-and-resume architecture runs
  locally against SQLite and in production against
  managed storage with no code change. Long-running
  agents (days/weeks, idle time, webhooks) are the
  documented use case — the closest published cousin to
  a run_goal that waits on a human.
* **Interrupt-shaped gap (verify at build):** this study
  surfaced ADK's pause/resume as a session/event
  mechanism, but NO single `interrupt()`-equivalent
  primitive with LangGraph's pause-inside-a-node
  semantics. Before ADK can be picked, Wave 8 must verify
  the exact approval primitive: what pauses, what
  persists at the pause, and what replays on resume.
  An unverified approval primitive is a disqualifier for
  the durable-gate upgrade, not a detail.
* **Fit notes:** Python-first (the production-ready
  implementation), Gemini by default with other models
  via LiteLLM; tools as decorated functions with
  Pydantic models (a natural fit for VYUK's schema-first
  tools, but a rewrite of the tool surface, not a reuse
  of it); multi-agent orchestration (sequential,
  parallel, sub-agents) built in; evaluation via
  `AgentEvaluator` (feeds Wave 2's rig thinking, not
  this spec). Managed-agent flavours (remote state as
  source of truth) conflict with VYUK's local-first,
  zero-setup shape and are out of scope for run_goal.
  OpenTelemetry integration exists — same telemetry-OFF
  discipline as MAF.
* **Licence: VERIFY-AT-ADOPTION** from the repository
  LICENSE, like LangGraph's. Neither is claimed here.

## Comparison (the table Wave 8 decides from)

| Question | LangGraph | MAF | ADK |
|---|---|---|---|
| Checkpoint unit | Per node (must be 1 tool/node) | Per superstep (verify = 1 tool) | Per session/event (verify granularity) |
| Durable pause for approval | `interrupt()` + `Command(resume=)` — native | Checkpoint + pause + resume — native | Pause/resume — verify the primitive |
| Durability default | `async` (lossy on crash — must pick `sync`) | After-step checkpoints (verify sync-ness) | Session persistence (verify atomicity) |
| Checkpointer vs store | Native split | StateStore backends (verify split) | Session scopes (map, don't merge) |
| Language fit for VYUK | Python-native | Python SDK, .NET-first | Python-first |
| Telemetry posture | Verify at build | OTel built in — OFF by config | OTel built in — OFF by config |
| Licence status | Verify at adoption | OWED (O-11) — gates eligibility | Verify at adoption |
| Biggest Wave 8 risk | Node granularity (4 tools, 1 checkpoint) | Superstep granularity + licence owed | Approval primitive unverified |

## What Wave 8 must prove (acceptance, framework-agnostic)

1. Kill the process mid-run after a completed
   send-class step: resume sends NOTHING twice.
2. Kill the process while a tool runs: resume reports
   that step UNKNOWN and asks the human — never
   auto-re-runs, never silently skips.
3. Start a run, reach a gated step, EXIT the process,
   relaunch tomorrow, approve: the run continues from
   the exact paused step with the same budgets and legs.
4. Corrupt the newest checkpoint file: the run falls
   back to the last good checkpoint and reports the gap.
5. Complete a run: its checkpoints are deleted; its
   memories (if any were recorded) remain in the store
   — the split survives completion.
6. Every resumed tool call still passes through
   `execute()` — the audit log shows the funnel's
   record for resumed calls, indistinguishable from
   first-run calls except for a resume marker.

## The pick (OPEN — the owner's decision, D-6)

**Analyst's note (not a decision):** on the evidence
above, LangGraph's shape maps most directly onto this
spec — a native `interrupt()`, a native
checkpointer/store split, and a durability mode
(`sync`) that meets non-negotiable 2 — with its main
risk (node granularity) being a design rule Wave 8 can
enforce (one tool per node). MAF's checkpoint story is
comparable but carries the owed licence (O-11) and a
.NET-first centre of gravity; ADK's resumable sessions
are the closest long-running story but its approval
primitive is unverified in this study. None of that
picks a winner: the pick needs the owner's judgement
about which risk he wants to carry, and Wave 8's build
spikes (one per finalist, each proving acceptance tests
1–3 above) may still overturn the lean.

**Pick box (for the owner):** LangGraph / MAF / ADK /
"build the checkpoint layer in-house on SQLite + the
existing funnel" (the no-framework option — always on
the table, and the fallback if every finalist fails
acceptance). The pick is recorded here when made, and
Wave 8 builds only the picked shape.
