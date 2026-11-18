# VYUK Threat Model — the trust pack's core document

**Version:** 1.1 (v5.94.0, audit gaps batch) · **Owner:** the ship gate (see "Refresh duty" below)
**Status:** Refreshed through the audit programme (A1–A5, Gates B–C,
scheduler batch, gaps batch). Claims below name the suite that
covers them; a claim without a suite is marked as such. This
file is still not a legal conclusion, not a certification, and
not a promise that no attack succeeds.

## How to read this document (and what it is not)

This document says, in VYUK's own words, where trust lives in
her, where it stops, and what notices when a control fails.
It is organised by a layered method (see "Method" below) and
it maps outside threat lists to her controls BY ID ONLY.

Two drafting rules bind this file, both from the plan's
commercial layer:

1. **Paraphrase, never adapt.** The outside frameworks this
   document leans on carry share-alike or otherwise
   restricted reuse terms. Their wording is NOT reproduced
   here — no definitions, no copied sentences, no adapted
   paragraphs. Short titles and IDs may be cited; the
   explanations are VYUK's own. Where a reuse-terms check is
   still owed, the file says so instead of claiming the
   reuse is cleared.
2. **A control without a detector is a wish.** Every row
   below names the control AND the mechanism that would
   notice its failure (an eval, a log line, a doctor check,
   a red-team rig). A control whose detector does not exist
   yet is marked with the wave that builds it.

This document is not a legal conclusion, not a
certification, and not a promise that no attack succeeds.
It is the map the ship gate reviews, wave by wave.

## Method

The organising method is the layered agent-threat method
from the cloud security community (MAESTRO as method,
ADOPT per the plan's Amendment A2): look at the system
layer by layer — the models underneath, the data flowing
through them, the framework that plans and calls tools,
the machine it is deployed on, the evaluation and
observation around it, the security rules across all of
it, and the ecosystem it talks to — and ask, per layer,
what an attacker standing at that layer could reach.

Reuse-terms check O-24e (the method's own reuse terms)
stays STILL-OWED: paraphrase is unblocked meanwhile, and
this file paraphrases. If that check ever returns a
restriction this file cannot meet, the method section is
rewritten, not quietly kept.

## Her planes and their trust boundaries

VYUK is one process with six planes. A plane is a surface
with its own inputs, its own outputs, and its own boundary
— a compromise of one plane must not silently become a
compromise of the next. The boundary is the control that
stops it.

### 1. Ears and voice (microphone, wake word, speech)

* **Inputs:** room audio, the wake-word spotter, push-to-talk.
* **Boundary:** audio is data, never instruction, until a
  human spoke it in this session. Wake words gate
  listening, not acting: waking her never authorises a
  tool. Voice input from a speaker the session did not
  start with is untrusted content (see the provenance
  lock), and a voice factor may ADD a restriction to any
  decision below — it may never REMOVE one.
* **Failure detector:** the mic doctor (deep check +
  dropped-chunk count); the audit log's provenance lines.

### 2. Brains (cloud Live, cloud text, local tiers)

* **Inputs:** the user's words, tool results, retrieved
  memories, screen text, page text, email text.
* **Boundary:** the brain proposes; it never authorises.
  Every tool call the brain emits passes through the
  funnel (plane 3) before anything runs, and the brain's
  own words about what ran are never evidence — the audit
  log is. Model output that arrives from outside (a tool
  result, a page) is wrapped in the untrusted envelope and
  screened for instruction shapes before it re-enters a
  prompt.
* **Failure detector:** the failure script's "tried/next"
  shape; the deliberation guard; the eval rig (Wave 2).

### 3. Hands (the executor, the funnel, the gate)

* **Inputs:** tool calls from any brain, via one funnel
  (`ToolRegistry.execute`) — there is no second door.
* **Boundary:** this is the load-bearing plane. Validation
  (schema, strict — full enforcement, audit fix A4) →
  failsafe (trip outranks everything except reads; a
  consult that cannot be answered refuses non-reads —
  fail-closed tri-state guards, audit fix A2) → protected
  paths and install-tree writes (always refused, no
  confirmation can override) → confirmation gate
  (fail-closed: no handler, no run; batching keyed on the
  full arguments digest, never the display summary — audit
  fix A1) → Rule of Two (below) → cool-down, safety budget,
  deadline → dispatch on a worker thread. Every result
  carries an explicit status (success / failure / refused /
  timeout / unknown — a failure string can never wrap as
  success, audit fix A5); a timed-out call gets a durable
  operation ID and an unknown outcome with exactly-once
  reconciliation, never a silent drop or an auto-retry
  before reconciliation (A5). Batches preserve issue order
  whenever they contain a write, and one raising call never
  takes down its siblings' responses (A5). The audit log
  records exactly one redacted record per call.
* **Failure detector:** the audit log itself; the doctor's
  registry/payload checks; the red-team rig (Wave 2).

### 4. Bus (the toolbox, MCP servers, external tools)

* **Inputs:** MCP server listings, tool descriptions,
  tool results — all server-controlled strings.
* **Boundary:** the bus section below GATES HE-1: no
  server, tool, or description is trusted on arrival.
  Tools are classified ONLY from the local operator-reviewed
  capability policy (a digest-pinned record per tool — audit
  fix A3); remote annotations are advisory and never
  decisive, and an unknown or changed tool defaults to
  confirmation-gated. Descriptions are hash-pinned; ALL
  MCP-sourced text (descriptions, schemas, discovery,
  invocation results, errors) passes through ONE taint-wrap
  seam before reaching the model (A3). Credentialed MCP
  over plain http to a non-loopback host is refused at
  config validation; loopback http with credentials is
  allowed only with a recorded warning (Gate C). A dead
  server fails as "toolbox unavailable" in plain words —
  never a hang, never a silent retry.
* **Failure detector:** the doctor's per-server lines;
  the description-change warnings in the log; the
  egress-allowlist rig (Wave 2).

### 5. Memory and data (facts, journal, secrets, logs)

* **Inputs:** recalled facts, the day journal, the
  secrets store, the milestone log, the audit log.
* **Boundary:** memory stores FACTS about the user's
  life, never the user's opinions as settled fact, and
  never the words of a first command (event-only
  milestone recording until the owner rules otherwise).
  Secrets are never readable by the model: the secrets
  store has no model-facing read tool, and the protected
  path list refuses every read of credential material.
  Logs are append-only and redacted.
* **Failure detector:** the integrity manifest (signed,
  verified per install); the milestone log's own shape;
  Wave 6's re-verification retention.

### 6. Face (the window, the confirm popup, the tray)

* **Inputs:** human clicks, approvals, settings picks.
* **Boundary:** the popup is the human's instrument, not
  the model's: decision-critical fields render in full,
  approvals join the audit record by request ID, and an
  approval given under a dead session is void (epoch
  check). The face never shows "connected" for a session
  the watchdog has declared dead, and never shows a tool
  result as a system fact.
* **Failure detector:** the approval-join in the audit
  log; the honest-state watchdog (Live); the UI self-tests
  (Wave 2).

## The Rule of Two (SE-3.1 — ADOPT pattern)

A session operating without human oversight may possess
at most TWO of these three legs at once:

* **A — processing untrusted input** (the session is
  tainted: outside content tried to instruct her);
* **B — access to sensitive systems or data** (reads of
  the user's own stored private data: fact memory, day
  journal, calendar, mailbox);
* **C — the ability to change state or communicate
  externally** (a write, or a read that sends bytes to a
  host the model chose).

Holding all three unsupervised is the forbidden
configuration: it is the shape of an exfiltration and of
an injection-driven state change. Enforcement rides the
existing taint tiers and the existing confirmation gate
(no new dependency): the funnel computes session legs
plus this call's legs, and a call completing all three
that the human has not already approved in this call is
forced through the gate with the rule named. One approval
satisfies one call, never the session. Legs are recorded
only for calls that actually run; refused calls confer
nothing. Untainted sessions cannot trip (A absent).
Wave 8 inherits the budget as run_goal run state — that
half is not built here.

Two riders: (a) a voice factor may ADD a Rule-of-Two
restriction and may never remove one (no speaker
verifier exists in the product, so this is doctrine only
in v1); (b) the serial-arm rider — a raw serial channel
is legs B+C by itself, so the arm is driven only through
wrapped, clamped tools with a dead-man first, never
through a raw channel (Wave 7).

## The bus section (GATES HE-1)

This section is the gate for the MCP bus. If it is
stale, the bus does not ship.

* **Server identity.** A server is its config entry, not
  its self-description. Entries live in `mcp_servers.json`
  (user state, never shipped); the file merges over
  built-in defaults by name. A server that is not in the
  config does not exist to the toolbox.
* **Transports.** Stdio (a local command) or remote
  (http/https). A server naming both, or neither, is
  dead by config. Stdio environment values starting with
  `$` resolve from the process environment; anything else
  passes through as-is.
* **Secrets.** Never in the config file. OAuth client
  secrets arrive only as an environment-variable NAME
  (`client_secret_env`); a literal `client_secret` key
  kills the server. Tokens live in process memory only
  (in-memory token storage) and are never written to disk
  by the bus.
* **The CVE pin (CVE-2026-115563).** The fixed behaviour
  is `mcp` ≥1.30.0 or ≥2.2.0 AND `issuer=` passed for the
  two machine-to-machine OAuth providers — the version
  floor alone is NOT the fix. The bus enforces it in
  code: a missing issuer is a dead server, not a warning.
  FastMCP's own client-credentials auth cannot express
  `issuer=` (verified by source read), so OAuth servers
  drop to the SDK's own providers — one protocol
  standard, with an SDK-level escape hatch the pin
  forces, not two competing stacks. Interactive
  (authorization-code) auth is not runnable in v1: the
  interactive flow would hang a headless call, so it is
  refused in plain words instead of silently downgraded.
  Private-key-JWT auth is config-accepted but
  connect-gated: its signing callback cannot be expressed
  in a JSON file, so no server runs on it until a later
  wave adds an in-process provider registry.
* **Classification.** From the server's own annotations,
  fail-closed: destructive → confirm (every call);
  open-world → egress (free untainted, asks under
  taint); read-only → read (free untainted, asks under
  taint — an MCP read is never "provably local"); missing
  or contradictory → confirm. MCP tools are never
  confirm-allowlistable and never failsafe-exempt.
* **Description-hash pinning (SE-5.12 runtime half).**
  On every listing, each tool's (name + description +
  inputSchema) is hashed and compared with the last-seen
  hash in runtime state. A changed hash marks the tool
  confirm-class for the rest of the session and logs a
  warning: a tool whose description changed since
  approval IS a new tool. SE-5.12's gate half (scanning
  real server configs) stays owed behind O-24a's posture
  verification and is NOT built here.
* **Failure posture.** Lazy connect (nothing at start),
  bounded waits, cached dead state (one timeout per
  process, explicit refresh to retry), stale tools
  dropped the moment a server dies. The doctor reports
  config validity + binary resolvability per server and
  never connects (a fresh doctor process can never
  observe a live session, so a connect test there would
  be permanent noise, not diagnosis).
* **Telemetry.** OFF, verified by source read: the SDK
  client path carries no analytics, and the client
  telemetry is an observability span stream with no
  exporter configured — no exporter is ever created by
  the bus. The reference servers (Fetch, Git, Time) ship
  no telemetry configuration either; a server that phones
  home on its own is a server to stop using, and the
  egress-allowlist rig (Wave 2) is the mechanism that
  would prove it.

## Outside-list mapping (by ID only)

The rows below map an outside agent-threat list's IDs to
the VYUK control that answers each, and to the mechanism
that would notice the control failing. Titles and
definitions of the outside list are NOT reproduced here
(share-alike reuse terms — see the drafting rules at the
top). The mapping is by ID; the descriptions are VYUK's.

| ID | Control that answers it | Detector (what would notice a failure) |
|---|---|---|
| ASI01 | The confirmation gate + the taint tiers: untrusted content cannot drive a state change without a human. | Audit gate dispositions; the red-team rig (Wave 2). |
| ASI02 | Scoped tools + the Rule-of-Two budgets: a session's reach is bounded per session, per leg. | The Rule-of-Two ask log; the audit log's leg record. |
| ASI03 | MCP server identity + description-hash pinning: a server is its config, and a changed tool re-confirms. | Description-change warnings; the doctor's server lines. |
| ASI04 | The SE-5 shelf (server vetting, scanning posture, signature discipline) — the gate half stays owed behind O-24a. | The Wave 2 rig's server-config scan output. |
| ASI05 | The executor sandbox posture: one funnel, deadlines, budget, cool-down, no second door. | The audit log; the budget-trip lines. |
| ASI06 | Memory staging + Wave 6's re-verification retention: recalled facts are re-checked, not trusted on age. | Wave 6's re-verification report. |
| ASI07 | Bus message signing posture (Wave A): messages between her parts carry provenance the receiver checks. | The bus log's provenance lines (Wave A). |
| ASI08 | Graduated response (SE-6.9): trip, degrade, stop — in that order, with a human re-arm. | The failsafe trip record; the tray state. |
| ASI09 | Visible approvals + honest-claim discipline: the human sees the full decision, and she never claims a state she has not proven. | The approval-join in the audit log; the watchdog's honest-state lines. |
| ASI10 | The detection set + the kill-chain: what she watches for, and the ordered shutdown when it fires. | The kill-chain drill (Wave 2 rig). |

## Audit remediation ledger (through the gaps batch, v5.94.0)

Each row names the remediation AND the suite that covers it.
A row without a suite would be a wish, not a control — there
are none below.

* **Confirmation integrity (A1):** sensitive operations never
  batch; remaining batching is keyed on the SHA-256 of the
  full arguments, never the display summary; approval is
  strict-boolean only; a handler is invoked exactly once.
  Suite: tests/test_auditfixA1.py.
* **Fail-closed guards (A2):** money guard, failsafe, and
  kill-chain consults that cannot be answered refuse
  (tri-state: allowed / denied / unavailable — unavailable
  is never "allowed"). Suite: tests/test_auditfixA2.py.
* **MCP trust (A3):** local policy classification + the
  single taint-wrap seam for all MCP-sourced content.
  Suite: tests/test_auditfixA3.py.
* **Validator (A4):** full schema enforcement (ranges,
  nested required, extras where declared, lengths, null,
  bool-vs-integer, array item types) or an explicit
  unsupported-keyword refusal; malformed dispatch arguments
  are rejected, never silently turned into {}.
  Suite: tests/test_auditfixA4.py.
* **Execution (A5):** timeout → durable operation ID +
  unknown outcome + exactly-once finalizer, no auto-retry
  before reconciliation; structured result contract;
  write-then-read batch ordering; per-call batch isolation.
  Suite: tests/test_auditfixA5.py.
* **Updater (Gate B):** protected paths (.env and kin)
  rejected at extraction and before apply; owned-file
  reconciliation for files the update did not ship;
  crash-atomic ordering (version marker last, journal
  recovery at next startup). Full journaled atomicity is
  NOT built — the swap is still file-by-file (see the
  open items below). Suite: tests/test_gateB.py.
* **MCP transport / policy / plugin entry (Gate C):**
  credentialed-http refusal, strict enabled parsing,
  manifest/name validation BEFORE plugin import, policy
  enforced at updater/download entry points, private_key_jwt
  rejected at validation. Suite: tests/test_gateC.py.
* **Scheduler:** callbacks fire OUTSIDE the lock (no
  deadlock); idempotent start/stop; aware-UTC normalization
  with a stated DST rule; briefing content computed at fire
  time; catch-up means the next future occurrence, never a
  per-second storm; fired one-shots purged with a retention
  cap; persistence with a missed-event policy.
  Suite: tests/test_scheduler_fix.py.
* **Crash reports (gaps, C-01/C-02):** collision-proof
  filenames (O_EXCL + random suffix); allowlisted payload
  with recursive sanitization at every nesting level
  (including the error string); preview == sent by
  construction; send still requires explicit_send + a
  transport. Suite: tests/test_gaps.py.
* **Uninstall (gaps, P1-12):** registry strings are parsed
  into argv, the executable is validated (no shells or
  interpreters, must exist or be msiexec), and the exact
  target is displayed; no shell ever interprets a registry
  string. Suite: tests/test_gaps.py.
* **Plugin isolation (gaps):** every plugin-registered tool
  routes through execute() (confirm gate by capability
  class, validation, taint, budget, deadline, audit log);
  pre-import validation (Gate C) means an untrusted file is
  never imported. What this does NOT provide is stated in
  the honest limits below. Suite: tests/test_gaps.py.

## Honest limits stated in this document (not buried)

* **DPAPI does not protect secrets from code running as
  the same user — which includes a successfully
  prompt-injected her.** Any secret readable by her
  process is readable by an injection that achieves her
  process's privileges. The mitigation is not DPAPI; it
  is never giving the model a read tool for secrets, and
  the protected-path refusals around credential material.
  (SE-4.6, stated plainly because it is the kind of limit
  that gets quietly assumed away.)
* **No vendor kill requirement.** No vendor, model
  provider, or server author gets a switch that stops her
  or rewrites her rules from outside the owner's machine.
  Updates are owner-approved, signatures verified, and
  the local brains keep running with no vendor at all.
  (SE-6.9.)
* **The grammar is not a guarantee.** See SC-1's note in
  the module: a grammar narrows what the model emits;
  the gate decides what runs.
* **Telemetry-off is a configuration claim, proven per
  release** by the egress-allowlist rig (Wave 2), not a
  one-time assertion. Until that rig exists, the claim
  rests on source reads, and this document says so.
* **Plugin execution is NOT OS-contained.** Plugin handlers
  run in-process with the application's full privileges;
  the funnel gates tool-call actions, it does not contain
  code. A trusted-but-malicious plugin is not contained at
  the OS level. Full process isolation (an out-of-process
  plugin host with IPC) is future work, not a built
  control. (Gaps batch; core/plugin_isolation.py states
  the same limit, pinned by tests/test_gaps.py.)
* **Crash-report sanitization over-redacts by design.**
  Long token-shaped strings in a report are replaced even
  when benign — a dropped hash is safe, a shipped secret
  is not. (Gaps batch.)
* **Update atomicity is ordering + recovery, not a
  journaled swap.** An interrupted update is recovered at
  next startup from the journal, but the swap itself is
  still file-by-file; a crash mid-swap can leave a mixed
  tree until recovery runs. (Gate B, honest remainder.)

## Diagram (pilot): the SAIF agent map

The diagram for this document is the agent map from the
secure-agent-framework community (SAIF as the map, PILOT
per the plan's Amendment A2): one loop drawn as
hear → think → gate → act → observe → hear again, with
the trust boundary drawn at every arrow — audio in
(plane 1), brain proposal (plane 2), funnel and gate
(plane 3), bus calls (plane 4), memory reads and writes
(plane 5), and the human's face (plane 6). The map is a
diagram, not a control: its value is that a reviewer can
point at any arrow and ask "what stops a lie crossing
here?" and this document answers by plane.

SAIF's own licence verification is OWED — recorded here,
not claimed. Until it clears, the map is described in
this section's words, not reproduced. The companion
risk-management data set (CoSAI-RM) stays WATCH: it is
not a source this document draws on.

## AITG evidence column (SE-1.11 — PILOT, dev-only)

The testing-guide IDs (OWASP AI Testing Guide v1, PILOT
dev-only) are tracked as a column beside the rows above,
not as a second document: Wave 2's rig records its
outputs as AITG evidence, so a control's detector and its
test-guide evidence live in the same place. Minor owed
item: confirm the guide's repository LICENSE matches its
print-edition listing (CC BY-SA) before treating
repository-only content identically — until then,
repository-only content is treated as unquoted reference,
never reproduced.

## Refresh duty (ship-gate note, beside CC-1)

This document is refreshed EVERY wave, diffed like code,
and reviewed at the ship gate beside the licence audit
(CC-1, Wave 2). A wave that changes a boundary, a
classification, a gate, or a detector updates this file
in the same build — a stale threat model is a review
finding, not a footnote. The version line at the top
moves with every refresh, and the change is summarised
in the wave's ARCHITECTURE.md row.

## Owed items this document records (not closes)

* O-24e — MAESTRO reuse terms (method section above).
* SAIF's own licence verification (diagram section).
* AITG repository LICENSE confirmation (minor owed).
* O-24a — mcp-scan posture verification (SE-5.12 gate
  half: inspect/local modes only, opt-out flag, public
  candidate servers only, sandboxed, no credentials in
  the environment, first-run capture per SE-3.11,
  post-Snyk data-terms re-read at pilot time).
* O-11 — Microsoft Agent Framework licence (TC-1
  challenger eligibility, not the study itself).
* O-24k — the redaction seat's model licence (Wave 2's
  #1 ship-blocker; untouched by Wave 1).
* **M4 — secrets fallback policy, UNCHANGED by the gaps
  batch.** The store still falls back keyring → machine
  key → plaintext, honestly labelled. Changing it to
  guaranteed-secure-or-session-only is a product decision
  awaiting the owner's ruling; no batch silently "fixes"
  it.
* **Egress classification inversion, open.** At rung 2 the
  gate-egress classification is inverted against its name
  (a clock read is refused as egress-class while a real
  network read is allowed). Fixing it changes rung-2
  behavior for every tool, so it rides its own batch with
  its own routing decision — it is not snuck into any
  other batch.
* **Rung-5-behind-unreadable-consult residual, open.** A
  full stop hidden behind an unreadable kill-chain consult
  still allows pure reads (strictly better than the old
  silent rung-0 fallback, but not a full stop).
* **Frozen-build packaging, open.** The PyInstaller
  hidden-import collection, the router catalogue as a
  packaged resource, the frozen integrity manifest, and
  the frozen-artifact updater are all gated on a real
  frozen Windows build, which has not been run; the
  source-tree updater must not be mistaken for one.
  (Wiring audit WIR-01/02/07/08.)
* The SC-1 generation-side constraint (sampling through
  the grammar in the turn loop) — unbuilt, gated on
  measured evidence.
* The SE-5.10 dev-side signing half (plan-placed in
  Wave 1's vicinity, not in Wave 1's build list).
