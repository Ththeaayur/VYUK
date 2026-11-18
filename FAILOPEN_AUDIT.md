# Fail-open fallback audit (2026-11-05)

**Status: audit complete. ZERO clear fail-open fixes — the tree's gate
posture is consistently fail-closed, and the enumeration below is the
deliverable. Do not manufacture a fix from this document.**

Scope: `core/` + `gui/` product code (tests used only as evidence of
intended behaviour). Method: systematic search, not hunch — every
`except` block returning an allow/proceed value, every bare `except`,
every `unknown → allow` path, every default-allow, every write path
reporting success, every consult-failure path in a gate. Each finding
below carries file/line evidence and exactly one classification.
Zero unclassified findings.

Reference posture (PF-18 choke point): when a check cannot be
performed, the safe answer is refusal — or an honestly-labelled
pass-through where the design explicitly allows it (the staleness
stage's no-baseline / grab-failed / over-budget pass-throughs, which
are labelled and never claimed as checks).

## FAIL-CLOSED (verified, left alone)

| # | Finding | Evidence |
|---|---|---|
| F1 | Permission consult failure refuses | `core/permission_grants.py:425-426` — `check_tool` catches and returns `(False, pid)` ("fail closed, never pass") |
| F2 | Corrupt/missing grants store: mic/camera read denied, others unknown | `core/permission_grants.py:326,372-389` (D2 carve-out) |
| F3 | Capture-guard durable consult failure refuses | `core/capture_guard.py:447-453` (camera; mic-at-guard symmetry + screenshot_save same shape) — "a broken store must never silently open the camera" |
| F4 | Folder scope: unjudgeable path is NOT allowed | `core/permission_grants.py:697,849` |
| F5 | Kill-chain strict rung: corrupt/malformed state file raises | `core/funnel/kill_chain.py:351-365` (`_KillChainUnavailable`); `gate_tool` uses the strict reader — "a present-but-corrupt state file must never silently read as rung 0, which would clear every stop" |
| F6 | Kill-chain failsafe consult failure reads UNAVAILABLE; enforcement refuses non-read tools on UNAVAILABLE | `core/funnel/kill_chain.py:283-299` + docstring (the pre-A2 fail-open shape is documented as fixed) |
| F7 | Allowlist shortcut: any undecidable input means ask (False) | `core/confirm_gate_allowlist.py:228-266` (unknown taint → ask; undecidable → ask) |
| F8 | Allowlist store failure reads empty set → ask afresh, never skip | `core/confirm_gate_allowlist.py:122-125` |
| F9 | Skill signature/manifest verify failure refuses | `core/skill_signing.py:174-192,268-304` (verify → False; manifest → False + reasons) |
| F10 | Confirm gate: broken handler denies | `core/confirm_gate.py:594` ("a broken handler denies") |
| F11 | Confirm gate: no handler registered denies | `core/confirm_gate.py:556-560` |
| F12 | Needs-confirmation predicates fail closed (True = gated = asks) | `core/funnel/tools_gate.py:419,618,622` |
| F13 | Batch-exempt undecidable → exempt (always prompts afresh) | `core/confirm_gate_helpers.py:46,59` ("Fail closed: if either module cannot be imported, the tool is exempt") |
| F14 | Typing gate on Windows: UIA unavailable refuses | `core/input_guard.py:196-199` (`GUARD_UNAVAILABLE_TEXT`; v5.48 legacy contract change — "typing blind into a possible password field is the exact failure H20 closed") |
| F15 | Typing gate: password UNKNOWN refuses | `core/input_guard.py:204-208` |
| F16 | Password-element probe failure reads UNKNOWN → refusal path | `core/input_guard.py:142` |
| F17 | Web-search browser fallback: denied → skip; consult failure → skip | `core/funnel/tools_handlers_command_url.py` (PF-18 batch 6 ruling; unknown is NOT a refusal here by ruling — the fallback is not a funnel tool call) |
| F18 | Funnel `execute()` has no `except`: stage exceptions propagate, the tool never runs | `core/funnel/tools_registry.py` (zero `except` clauses) |
| F19 | Unguarded `injection.tainted()` in the native gate predicate fails closed by propagation | `core/funnel/tools_gate.py:295` (a raise propagates out of `execute()`; the tool never runs) |
| F20 | Money guard: missing/approximate/unparseable amount refuses (Rule 3) | `core/funnel/money_guard.py:641-648` |
| F21 | Write paths report False on failure (grants, folders, allowlist, planner store) | Atomic tmp+replace; inner tmp-cleanup `except OSError: pass` is followed by `raise`; the outer `except` reports False. The `return True` lines in these functions are the success paths, not except paths |
| F22 | Launch verification: broken probe yields no evidence → not confirmed | `core/funnel/tools_handlers_capture_launch.py` callers (`confirmed = _verify_launch(...)`; None is falsy) |
| F23 | Receipt store append failure reads not-dispatched (False) | `core/tool_receipts_store.py:263` |
| F24 | Staleness pass-throughs are labelled, never claimed as checks | `live_staleness` stage (no-baseline / grab-failed / over-budget → Activity line `staleness-unchecked: <reason>`) |
| F25 | Geometry floor is positive-evidence-only by design | `core/desktop/cursor_geometry.py` verdict docstring ("a missing or malformed record falls through, and validation that cannot run is not a refusal") |
| F26 | Extension harness: licence unknown reads not-allow-listed (False) | `core/extension_safety_harness.py:208` |

## FAIL-OPEN BY DESIGN / DOCUMENTED (left alone, reported with reason)

| # | Finding | Why it stays |
|---|---|---|
| D1 | Power policy: unknown metered state → downloads/updates allowed | Cost gate, not a safety gate. Documented in the module docstring: pausing updates for every host that merely lacks the WinRT binding "would be worse than the metered cost it tries to save". `core/power_policy.py:35,219,269-281` |
| D2 | Corrupt folder file reads defaults (Downloads + Documents) | Missing/corrupt are indistinguishable without extra machinery; B7 documented decision; a stored `[]` (explicitly no folders) is preserved — only None collapses to defaults. Pinned by `test_corrupt_folder_file_reads_defaults`. `core/permission_grants.py:726-736` |
| D3 | Visibility probe failure reads VISIBLE (browser locator + UIA element) | Quality filter, not a safety gate — destructive actions still face the confirm gate. Documented in `core/desktop/browser_gates.py` (`_loc_visible`: "an element whose visibility cannot be proven must degrade to that, never to a silent skip"). Changing one without the other would be inconsistent; refusing on probe failure would break legitimate actions on UIA hiccups |
| D4 | Sound sidecar: indicator-note failure does not sink start; message still claims "indicator live" | Documented ("the indicator must not sink start", `core/sound_event_sidecar.py:452`). The message wrinkle (claiming "indicator live" when the note failed) is a follow-up honesty note, not a gate fix — refusing the mic stream over a note failure would contradict the documented design |
| D5 | Input guard off-Windows: name-hint fallback when UIA does not exist | Documented platform limitation (no UIA exists on a Linux dev desktop); the name regex still refuses password-like names. `core/input_guard.py:196-203` |
| D6 | Hotkey release failure still reports "Pressed hotkey" | The press happened; release failure is a stuck-keys hazard, not a gate bypass. Out of scope for this audit (not a gate); noted, not fixed |

## AMBIGUOUS — REPORTED, NOT FIXED (a single-site fix would be worse)

| # | Finding | Why not fixed here |
|---|---|---|
| A1 | Taint-consult convention: unconsultable reads untainted/absent | `core/funnel/tools_gate.py:666`, `core/funnel/rule_of_two.py:145,169`, `core/funnel/money_guard.py:517`, `core/confirm_gate.py:369-371` all read unconsultable taint as untainted/absent — against `core/injection.py`'s own contract: "The taint check must fail CLOSED for state-changing tools — if the gate machinery raises, the tool does not run (the parent wave owns that policy change)." The failure mode is essentially unreachable (pure-stdlib in-memory read), the fix is a cross-cutting policy decision the contract itself reserves to a parent wave, and the consumers differ in kind (confirm taint is advisory — warning text + batch exemption, not a gate; money-guard Rule 3 is the most load-bearing consumer). Fixing one site would be inconsistent; fixing all sites in an audit batch would be a policy change, not a fix. REPORTED for a parent-wave decision |

## BENIGN (verified, left alone)

| # | Finding | Why benign |
|---|---|---|
| B1 | Confirm `_taint_context` failure reads untainted | Taint in confirm is advisory (warning text + batch exemption); even tainted asks proceed only on human approval. No grant |
| B2 | Receipt-guard release failure still resolves `verified_absent` | The resolution is the human's verified-absent verdict; the message honestly says "release unavailable". `core/funnel/tool_operations.py:299` |
| B3 | Clipboard exclusion-flag failure after the paste landed | Bookkeeping only; the paste already happened |
| B4 | DPI-awareness, listener, narration, appearance, watcher, transcript except-pass blocks | No gate involvement; the except blocks guard logging/display/polling, never a decision |
| B5 | Write-path tmp-cleanup `except OSError: pass` | Cleanup idiom followed by `raise`; the outer handler reports False (see F21) |

## Follow-ups (with reasons)

1. **A1 taint-consult policy (parent wave):** decide the fail-closed taint posture the `core/injection.py` contract already demands, across all consult sites at once — not one site at a time.
2. **D4 message honesty (small):** the sound sidecar's success message could name the indicator-note failure instead of claiming "indicator live" — a wording fix, not a gate fix.
3. **D6 stuck keys (out of scope):** hotkey release failure leaves keys possibly down while reporting success — a reliability note, not a gate finding.
