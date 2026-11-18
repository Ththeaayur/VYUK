# Live-loop verifier coverage — DESIGN ONLY (2026-11-05)

**Status (updated 2026-11-05, live staleness batch): P1–P4 are
IMPLEMENTED — delivery ledger (core/desktop/live_staleness.py),
live-origin marker (core/funnel/live_origin.py), PIL-resize
helper + funnel stage (`_exec_live_staleness`, between toolbox
and confirm). The stage is LAB-ONLY until P5 field legs land
(threshold tuning on real captures, grab-cost confirmation,
end-to-end live leg). The design below is unchanged; it is the
specification the implementation follows. Live BINDING
verification remains IMPOSSIBLE with today's records (§1.4) —
the stage checks staleness, never binding.**
**Original status: NOTHING IS WIRED YET. No live action is verified today.**
This document designs the insertion; it does not implement it.
Delivered progress since v6.0.0 shipped 2026-11-01 remains 0% —
this is lab-tree only, unshipped.

## 0. The gap, stated plainly

The C7 verifier guards coordinator paths: C3 re-issue targets
(`task_coordinator_lifecycle.py`, Family-C), pending records
(`task_coordinator_execution.py`, both pending sites), and point
targets carrying stored crops. But the LIVE loop — where the model
actually sees screenshots and clicks during live sessions —
bypasses the verifier entirely:

- `core/voice/live_client_receive.py::_execute_tool_sync`
  (executor thread) converts a Gemini Live function call
  (`fc.name` + `fc.args`, nothing else) through
  `tool_envelope.from_live_call` and dispatches it through
  `tools.execute`. No StepDef is built, no observation is
  attached, no verifier is consulted.
- The funnel NEVER imports `control_existence`
  (parent grep-confirmed; pinned by
  `tests/test_live_verification_gap.py`).
- So the highest-volume coordinate path — live model clicks —
  runs on the cursor_control geometry floor alone (Section 4),
  with no content verification of any kind.

## 1. Live-path map (from code reads, before any design)

### 1.1 How a live tool call flows

1. **Receive** (`live_client_receive.py::_receive_loop`):
   `response.tool_call.function_calls` are queued to the tool
   worker; the receive loop moves on immediately. A batch runs
   in `_run_tool_batch`: read-only calls may overlap; state
   -changing calls run one at a time in issue order.
2. **Execute** (`_execute_tool_sync`, static, executor thread):
   `fname = fc.name`, `fargs = dict(fc.args)`. `route_*` names
   dispatch through `domain_routers.dispatch_route`, which
   executes the inner tool through the funnel. Everything else
   goes through `tool_envelope.from_live_call` →
   `execute_envelope(_inv, tools.execute)`.
3. **Funnel** (`ToolRegistry.execute`, `core/funnel/tools_registry.py`):
   stages in order — setup, receipt, mark_cb, **permission
   (PF-18 choke point)**, **folder_scope**, **route**, **toolbox**,
   validate, failsafe, killchain, protected_path,
   protected_dest, install, echo, **confirm**, epoch_recheck,
   rule_of_two, limits, receipt_check, mark_dispatched,
   deadline_legs, **dispatch** (handler runs).
4. **Delivery** (`_run_tool_batch` tail): each outcome becomes
   `types.FunctionResponse(name, id, response={"result":
   outcome})`, delivered via `_deliver_tool_responses` →
   `send_tool_response`. **A funnel refusal dict IS delivered
   to the model as the tool result** — the model sees the
   refusal text. This is the natural refusal channel for live
   (Section 5); no dialog is needed for a verification refusal.

### 1.2 The three entry shapes all re-enter execute()

| Shape | Inner dispatch | Evidence |
|---|---|---|
| Direct live call | `execute_envelope` → `tools.execute(name, args)` | `tool_envelope.py:197` |
| `route_*` | `dispatch_route` → funnel execute for the inner tool | `domain_routers.py:405` + docstring |
| `use_toolbox` action=call | `_call` → `registry.execute(name, args)` | `toolbox_findcall.py:224` |

A verification stage inside `ToolRegistry.execute()` therefore
sees every live-origin inner call under its TRUE tool name —
provided it can tell the call IS live-origin (it cannot today;
see prerequisite P2).

### 1.3 What the live path holds at action time

| Layer | Observation held | Verifier-usable? |
|---|---|---|
| Receive loop (`fc`) | Tool name + args ONLY. No pixels, no capture id, no marks, no stored crop. | No — nothing to verify against. |
| Funnel (`ctx`) | Tool name + args + gates state (permission, folder scope, taint, confirm). No observation supplier of any kind. | No — this is exactly the C4 finding, restated for live: the funnel has no observation supplier. |
| Vision slot (`vision_manager.last_sent_frame()`) | The LAST CAPTURED frame: grayscale thumbnail + capture id + record. Filled in `capture_screen`'s tail by every capture path (Share button, watch tick, take_screenshot/screen_look via the frame sink — all flow through `capture_screen`). | PARTLY — it is the latest capture, NOT the frame the model read the point off (see 1.4). |
| Vision record (`last_capture_info()`) | Last capture's geometry (screen size, image size, crop origin/size) + capture id. Powers the norm1000 conversion grid and the geometry floor. | Yes, for geometry — not for content. |
| Marks table (`last_marks()`) | The last grounding table (from the last capture with marks). | Yes, for mark-based clicks — already guarded (Section 4). |
| Model context (server side) | Every frame ever sent this session. | NOT visible to VYUK code — the live client cannot know which frame the model read a point off. |

### 1.4 Why the slot is NOT "the frame the model saw"

The slot fills at CAPTURE time, not DELIVERY time, and it is a
single slot overwritten by every capture:

- `send_image_frame` can return `"no-session"` or `"error"`
  (dropped session, failed send) — the slot then holds a frame
  the model never saw. The slot record carries NO delivery
  field (pinned by the gap tests).
- A tool-path capture (take_screenshot) fills the slot before
  its sink delivery completes or fails.
- The model holds MANY frames in context; the slot holds ONE
  (the latest capture). A point read off any older frame cannot
  be bound to the slot honestly.

Consequence: **binding verification for live points is
impossible with today's records** — the same tautology the
brain-stamp batch refused (2026-11-05): stamping a live point
with the slot's id would make the check pass because the
stamper just wrote the id, laundering unauthenticated
coordinates through a rigorous-looking check. Any design that
claims live binding from the slot is dishonest. Section 3
designs what IS honest instead.

### 1.5 What the model sends for a live click

`cursor_control` absolute-target actions (`move`, `click`,
`double_click`, `right_click`, `middle_click`, `drag`,
`beacon`) take x/y in `coords` space — `norm1000` is the
DEFAULT since v5.49 (an omitted `coords` resolves to norm1000
for absolute-target actions, because model coordinates
overwhelmingly come from screenshots). Conversion reads the
LAST capture's crop at ACTION time (`_norm1000_reference`):
so a point read off one screenshot is ALREADY mapped through
whatever capture is latest when the action runs — the exact
failure mode `_capture_freshness_verdict` exists to catch on
positive evidence (Section 4). `mouse_control` takes true
screen pixels (no conversion, no crop). `window_control`
takes (action, title_substring) — no coordinates, N/A.
`click_by_mark` takes a mark id from the last grounding table.

## 2. What is already protected (do not rebuild)

- **Geometry floor, in-handler, all funnel callers including
  live** (`cursor_geometry._capture_freshness_verdict`,
  reached via `_capture_freshness_refusal` inside
  `cursor_control`): refuses on POSITIVE evidence the world
  moved since the last capture — virtual bounds changed
  (resolution/monitor change), foreground-crop window moved /
  resized / closed / replaced (C8 identity: hwnd first, title
  fallback). Deliberately NOT refused: missing/malformed
  record, unreadable world (pass-through, never a fake
  refusal). Explicitly NOT covered by the floor: in-window
  content change, same-rectangle window swap, intervening
  capture with a different crop. The floor catches grid
  moves; it cannot catch content moves.
- **Mark staleness, in-handler** (`uia.click_by_mark`): the
  mark resolves against the LAST grounding table; a different
  foreground window than the marks were drawn under → STALE
  refusal (handle-first, F-07); the centre must fall inside
  current virtual bounds; §7 batch 10 adds a FRESH monitor
  table check. Mark-based live clicks already refuse on stale
  marks. No change needed.
- **Coordinator paths**: C3 re-issue, pending records, C6
  region gate for destructive points with stored crops.
  Untouched by this design.
- **Confirm gate**: destructive live actions still confirm
  (GUI dialog / voice-resolvable layer 1) AFTER any future
  verification stage passes. Family-C tools
  (`cursor_control`, `mouse_control`, `window_control`) are
  NOT in `_CONFIRM_GATE_TOOLS` — live point actions never pop
  a dialog today, so a verification stage before confirm adds
  no dialog either (Section 5).

## 3. The insertion design (implementation batch scope)

### 3.1 Design decision: staleness check, NOT binding check

Because binding is impossible (1.4), the live insertion is a
**staleness check**: is the screen still the screen the model
is looking at? It refuses when the world changed since the
model last saw it — regardless of target — with a re-observe
instruction. It NEVER claims the point is right; it claims
only that the view is current. A false refusal costs one
re-observe round-trip, never a wrong click. That is the
correct fail-closed posture for live voice.

### 3.2 The check

For a live-origin absolute-target point action, BEFORE the
handler runs:

1. Take a FRESH grab (same capture path as `capture_screen`,
   marks off — marks are not needed for a frame compare).
2. Downsample the fresh frame AND the last DELIVERED frame
   (prerequisite P1's ledger, NOT the capture slot) to a
   small common grid with PIL resize (C speed — see 6.2 for
   why not `normalise_grid`).
3. Compare with the C7 compare family (mean absolute
   difference + fraction of pixels changed a lot). Thresholds
   are TUNED ON REAL WINDOWS CAPTURES in the implementation
   batch (prerequisite P5) — the C7 region constants
   (mean ≤ 12, ≤10% pixels > 48, normalised ≤ 48 px/side)
   were tuned for 96 px REGIONS and must NOT be reused
   blindly for full frames: a full screen always has some
   changing pixels (clock, cursor blink, notifications), and
   the resize path (bilinear) differs from `normalise_grid`
   (block averaging). No numbers are invented here.
4. Verdicts:
   - **Match** → proceed (to confirm if gated, else dispatch).
   - **Changed** → REFUSE with kind `stale-view` and a
     re-observe instruction (Section 5). The model re-shares
     / re-screenshots (refreshing the ledger baseline) and
     retries.
   - **No ledger baseline** (no frame delivered yet) →
     PASS THROUGH, labelled (not a refusal). Rationale: the
     verifier's established posture is positive-evidence-only
     — an unverifiable world passes through, never a fake
     refusal. The geometry floor still applies. This preserves
     today's behaviour exactly when no baseline exists.
   - **Grab failed / baseline unusable** → PASS THROUGH,
     labelled, same rationale. A broken grab must not brick
     live voice.

### 3.3 Scope of the check (tool filter)

- **IN**: live-origin `cursor_control` absolute-target
  actions (`move`, `click`, `double_click`, `right_click`,
  `middle_click`, `drag`, `beacon`) and live-origin
  `mouse_control` absolute actions (true pixels — the check
  is frame-level, not point-level, so it applies equally).
  Relative actions (`move_by`, `nudge`, `move_fraction`,
  `scroll` deltas) are NOT in scope: they carry no absolute
  target to stale-check.
- **OUT (already covered)**: `click_by_mark` (in-handler
  staleness, Section 2); `window_control` (no coordinates).
- **OUT (deliberately)**: text-brain loops (no pixels —
  `desktop_snapshot` text + marks table; their mark-based
  actions go through `click_by_mark`'s guard); §7 engines
  (handle/menu re-proof at dispatch); browser tools
  (locator re-resolution at dispatch; raw-point browser
  verdicts have no element); coordinator/routine/ledger
  paths (no live frame — C3 on re-issue, not live staleness).

### 3.4 Placement: a funnel stage, after toolbox, before confirm

New stage `_exec_live_staleness` in `ToolRegistry.execute()`,
ordered AFTER `_exec_toolbox` and BEFORE `_exec_confirm`:

- After toolbox: inner-tool resolution is complete for direct
  calls, and route_/use_toolbox inner calls re-enter
  `execute()` fresh (1.2), so the stage always sees the TRUE
  tool name.
- Before confirm: a stale destructive action refuses BEFORE
  any dialog — no wasted user approval. This is the C4
  principle (verify before the approval dialog wherever the
  path both verifies and asks), applied to the funnel.
- The stage runs ONLY for live-origin calls (prerequisite
  P2's marker). All other funnel callers (coordinator,
  text brains, typed turns, MCP) are untouched — zero
  behaviour change outside live.
- Refusal shape: the funnel's normal audited refusal
  (`status: "error"`, kind `stale-view`, message carrying the
  re-observe instruction) + an Activity line. No new
  refusal plumbing; the stage returns exactly like the
  permission stage returns today.

### 3.5 Why not the alternatives

- **Binding check against the slot** — REJECTED as
  dishonest (1.4). The slot is the latest capture, not the
  authored frame; a match would prove nothing about the
  point.
- **Verify the point with `verify()` and an empty fresh
  observation** — REJECTED (C4 lesson, pinned by the gap
  tests): `_verify_point` with no `capture_info` passes
  through `geometry-unverified` — it cannot refuse for
  content reasons without an observation supplier. Calling
  it in the funnel would be theatre, not protection.
- **Full-frame exact compare** — REJECTED: animation churn
  (video, clocks, progress) would refuse constantly, and a
  pure-Python full-frame compare is too slow (6.2).
  Downsampled compare is insensitive to small-area animation
  by construction.
- **A modal dialog on staleness** — REJECTED (Section 5):
  a dialog blocks voice mid-conversation; the refused tool
  result already reaches the model, which voices it.

## 4. Refusal UX, per verdict (mid-live-conversation)

The funnel result IS the UX: it is delivered to the model as
the tool result (`FunctionResponse`), and the model speaks
from it. No dialog, no user click, no silent skip.

| Verdict | Funnel result | What the user hears / sees |
|---|---|---|
| Stale view (content changed) | Refused, kind `stale-view`, message: the screen changed since the last shared frame; re-observe (screenshot/share) and retry; nothing was clicked. | A short spoken line from the model ("The screen changed since I last saw it — let me look again"), then the model re-observes and retries. No user action needed. |
| Geometry floor refusal (already exists) | Refused, existing geometry code + re-observe instruction. | Unchanged from today. |
| Pass-through, no baseline / grab failed | Success path continues; result labelled `staleness-unchecked` + Activity line naming the reason. | Nothing spoken (no evidence of a problem); the Activity line records that the check did not run. |
| Fresh view | Proceeds (to confirm if gated, else dispatch). | Nothing — the action just works. |

Deliberately NOT done: silent skip (the model must know the
click did not happen, or it will narrate a click that never
landed); modal dialog (blocks voice); auto-retry inside the
funnel (the model, not the funnel, decides whether the goal
still needs the click after re-observing).

## 5. Latency budget

Measured on this VM (pure Python, `bench_live_verify.py` in
`~/workspace/code-lab/`, n=20, synthetic grids):

- `compare_region` 96×96 (C7 region): ~7.3 ms/call.
- `compare_region` 160×90 (downsampled full-frame): ~6.5 ms/call.
- `normalise_grid` 1024×576 → 160-side: ~108 ms (pure-Python
  block averaging over ~590K pixels dominates).
- `normalise_grid` 1024×576 → 48-side: ~80 ms.

### 5.1 Why the implementation must NOT use normalise_grid

A staleness check built on `normalise_grid` would pay
~80–110 ms per downsample (both frames) PLUS the grab, in
pure Python, on every live point action. The recommended path
is PIL resize (C speed, already a dependency) for the
downsample, then the C7 compare family on the small grids:
check cost ~10–20 ms + one screen grab. Thresholds must be
re-tuned for the resize path (3.2, P5) — resize (bilinear)
and block averaging do not produce identical grids.

### 5.2 Grab cost: estimated, not measured

No display exists in this VM (lab captures use synthetic
backends), so a real Windows grab cost is NOT measurable
here. Bounded from code: one PIL grab + thumbnail + JPEG
encode + grayscale — no model call, no network — typically
tens of ms on desktop hardware. Stated as an ESTIMATE; the
implementation batch confirms it on real Windows (P5).

### 5.3 Budget and over-budget behaviour

- **Budget (PROPOSAL, to be pinned by measurement on real
  Windows): 500 ms wall for grab + downsample + compare.**
- Over budget → PASS THROUGH, labelled
  `staleness-unchecked (slow grab)` + Activity line. The
  geometry floor still applies. Rationale: verification must
  never stall live voice — a slow check degrades to the
  floor, it does not block the conversation and does not
  refuse an action it could not evaluate. (A refusal on
  timeout would be a fake refusal: slowness is not evidence
  of staleness.)
- The budget applies per live point action, not per batch:
  batch calls are already serialised for state-changing
  actions, so the cost adds per action, never multiplied
  across a batch.

## 6. Scope summary

| Area | Status |
|---|---|
| Live-origin absolute-target cursor/mouse actions | DESIGNED HERE (staleness stage, 3.x) — implementation owed |
| Live mark clicks | Already guarded in-handler (Section 2) |
| Live geometry moves | Already guarded by the floor (Section 2) |
| Coordinator / pending / C3 / C6 paths | Already guarded; untouched |
| Confirm-gated destructive live actions | Unchanged; verification precedes confirm |
| Text-brain loops (no pixels) | Deliberately out of scope |
| §7 engines, browser tools | Deliberately out of scope (own re-proof) |
| Live binding verification (point ↔ authored frame) | IMPOSSIBLE with today's records (1.4) — not designed, not claimed |

## 7. Implementation prerequisites (in order)

- **P1 — Delivery ledger** (in `send_image_frame`): record
  ONLY on `"delivered"`: frame id (monotonic, same counter
  family as capture ids), geometry (screen/image/crop, same
  fields as the capture record), and the grayscale grid
  thumbnailed IDENTICALLY to the fresh path (same max_dim,
  same resize). Single slot, no history (mirroring the
  vision slot). The capture slot keeps its current role
  (latest capture); the ledger is the new "model's view"
  record. Without P1 the staleness baseline is the slot —
  i.e. the dishonest baseline of 1.4 — so P1 is a hard
  prerequisite, not a nice-to-have.
- **P2 — Live-origin marker across the funnel boundary**:
  today `from_live_call` sets `source="live"` on the
  `ToolInvocation`, but `execute_envelope` passes only
  `(tool, args)` to `execute_fn` — the origin is DROPPED
  (pinned by the gap tests). RECOMMENDED mechanism: a
  contextvar set and cleared in `_execute_tool_sync`
  (executor thread) for the duration of the call — it
  survives route_/use_toolbox re-entry without changing
  their call signatures, and `execute()`'s signature stays
  `(name, args, cancel_token)`. Alternative (more churn):
  an `execute()` parameter forwarded through
  `dispatch_route` and toolbox `_call` re-entry. Either way,
  the stage keys on the marker, never on tool-name guessing.
- **P3 — Fresh-grab + PIL-resize downsample helper +
  staleness compare**: fresh grab (marks off), PIL resize to
  the ledger's grid size, C7 compare family. New helper
  module or companion to `point_crop.py` — NOT a change to
  `normalise_grid` / C7 constants (those stay region-tuned).
- **P4 — Funnel stage + refusal shape**: `_exec_live_staleness`
  between `_exec_toolbox` and `_exec_confirm`, audited
  refusal (`stale-view` + re-observe instruction) + Activity
  line, pass-through labelling for no-baseline / grab-failed
  / over-budget. Registry stays 654; no new tools.
- **P5 — Field legs (real Windows, NOT lab-invented)**:
  threshold tuning on real captures (animated vs static
  screens, false-refusal rate); grab-cost confirmation
  against the 500 ms budget proposal; end-to-end live leg
  (share → act → stale → re-observe → retry) on a real
  session. Until P5 lands, the stage is lab-only even after
  implementation.

## 8. What this design does NOT do

- No verifier wiring into the live path (no funnel change,
  no live_client change, no new verification calls in
  product code) — that is the implementation batch, after
  P1–P4 are ruled/built.
- No C7 constant change, no confirm-gate change, no new
  tools, no PF-18 change, no folder-scope change.
- No claim that live actions are verified: they are NOT,
  today, and this document is the map to changing that —
  not the change itself.
