"""Conversion pass — Wave 4 (v5.59, "Hands & Web") wiring coordinator.

Wires the staged Wave 4 modules into live paths WITHOUT new tool
declarations, WITHOUT a VERSION bump, WITHOUT a requirements.txt
change, WITHOUT touching any locked file (core/funnel/tools_handlers.py,
core/funnel/tools_gate.py), and WITHOUT touching core/browser.py,
core/music_player.py, or any other wave's wiring module.

Per-item truth (Phase 1 audit, NOT the wave's claims):

* Item 1, patched-engine seam — REMOVED (GAPS-1, 2026-11-12).
  The seam module and its A/B harness are deleted; the patched
  engine is no longer a dependency, and the worker starts stock
  Playwright through the one shared start (GAPS-18:
  core/desktop/browser_engine.start_playwright, called by
  core/desktop/browser_session.py: _start_pw). The old GATED-SKIPPED verdict and the
  licence-conflict note are moot — the engine is gone, so there
  is nothing to gate and nothing to reconcile. The stealth pilot
  (flag-stripped Playwright launch) was DELETED v5.70 with the
  own-browser launch it modified — there is no live evasion step
  left.
* Item 2, desktop worker W3 — LIVE (PARTIAL), VERIFIED, NOT
  RE-CLAIMED. easy_uiauto_find (core/desktop/uia.py:1454, inside
  registered uia_find_control) and clipboard_paste
  (core/desktop/uia.py:2201, rung 4 inside registered uia_type_text)
  are already live. Nothing here re-wires them; the suite only
  re-verifies the call sites are still present. The rest of
  the module is covered by items 3-4 below, never counted
  twice.
* Item 3, HA-5 pyvda virtual-desktop verbs — HANDLER-ONLY,
  VERIFIED END-TO-END, DECLARATION READY. core/desktop/control.py:1809
  virtual_desktop() already delegates to desktop
  desktop_action(), and core/desktop/control.py:1834 window_control()
  already accepts all five desktop_* words; the suite proves
  the delegation with stubbed pilots. The ONLY block is the
  fitted window_control declaration's action enum (list,
  focus, minimize, maximize, close): the funnel validator
  refuses desktop_* before the handler runs. The exact
  declaration addition + measured fitted cost live in
  WINDOW_CONTROL_DECLARATION_ADDITION below; the file is
  locked for this leaf, so the coordinator's combined
  registration follow-up applies it. pyvda stays lazy /
  optional, Windows-only.
* Item 4, PC-C cursor verbs — DISPATCHER WIRED, DECLARATION
  READY. core/desktop/cursor.py gains exactly one branch (see the
  branch comment there): pilot actions route to
  cursor_pilot_action() here, which calls the desktop
  pilots. core/desktop/cursor.py's register_all enum is UNCHANGED by
  this leaf, so the fitted payload does not move; the exact
  declaration addition + measured fitted cost live in
  CURSOR_CONTROL_DECLARATION_ADDITION below for the
  coordinator. PyDirectInput stays optional-only (its sdist
  ships no LICENSE.txt): nothing here imports it, bundles
  it, or requires it — the pilots run on cursor.py's own
  seeded-Bezier planner. Windows validation of the pilots
  is OWED (named in CURSOR_PILOTS_WINDOWS_VALIDATION_OWED).
* Item 5, HE-2/HE-3 trial harness — TRIAL-INVOCATION WIRED,
  NO WINNER PICKED. run_browser_agent_trial() /
  select_trial_winner() / trial_loser_removal_plan() are the
  trial runner's entry points to the staged harness. The
  harness itself refuses to pick off stub data (select_winner
  returns NO-DATA without LIVE results for BOTH pilots on the
  same task set), and this module adds no second refusal
  bypass: neither pilot is wired as a live feature, and no
  loser is wired or removed. Pilot-both live trials on a
  Windows box with a real browser are OWED.
* Item 6, Camoufox lane — DECISION-OWED, NOT WIRED. No second
  browser engine is wired. camoufox_lane_status() only
  verifies the lane module is intact (isolated profile,
  geoip permanently off, Chrome profile/logins never
  reused) and reports the owed decision: licence + trial
  decision first, like item 5. Nothing here launches
  anything.
* Item 7, curl_cffi read lane — LIVE READ PATH WIRED,
  DECLARATION READY. curl_read() calls the lane module
  (core/curl_cffi_lane.py fetch_read) through the gate
  classification already in core/funnel/tools_gate.py (class
  "egress", Playwright-context cookies only, taint refusal).
  The lane itself re-checks every gate, so this wrapper is
  a second lock, never a bypass. The tool-shaped handler
  curl_cffi_read_tool() + CURL_CFFI_READ_DECLARATION are
  REGISTRATION-READY for the coordinator (locked files).
  Two closing steps ride with the declaration: the
  coordinator's handler must supply cookies from the live
  Playwright browser context (this module never reads a
  cookie file, and refuses with no cookies supplied), and
  CC-1 must carry the curl_cffi wheel's bundled-native
  licence texts (libcurl-impersonate, BoringSSL) into
  THIRD_PARTY_LICENSES.md, read from the wheel itself,
  before any shipped use.
* Item 8, MCP trial harness — TRIAL CALLS WIRED THROUGH THE
  FUNNEL ONLY. mcp_trial_call() runs a trial call through
  ToolRegistry.execute (the module's own contract: trial
  tools ride the Wave 1 bus/toolbox, never the native
  registry) and ONLY for entries on a caller-supplied
  approved list that ALSO pass mcp_trials.trial_call_allowed
  (blocked file/YAML fragments refused, confirm-class
  refused without confirmation). No approved-trial list
  exists yet, so the default call (no list supplied)
  refuses — the approved list is the OWED decision
  (APPROVED_MCP_TRIALS_OWED). This is trial infrastructure,
  not a shipped tool: no MCP tool is registered here.
* Item 9, replay programmes — LIVE ENTRY POINT WIRED,
  DECLARATION READY. replay_programme() /
  replay_programme_json() call core/replay.py replay()
  through ToolRegistry.execute with validate_program gating:
  an invalid programme (including any password/captcha
  argument) is refused and NEVER executed — validation runs
  before the executor is touched, and replay() itself
  re-validates and halts at the first non-success step.
  This replay() is Wave 4 TC-2 (core/replay.py), DISTINCT
  from Wave 8 teach replay() (core/brain/teach.py) — the two
  are never conflated here. The tool-shaped handler
  replay_programme_tool() + REPLAY_PROGRAMME_DECLARATION
  are REGISTRATION-READY for the coordinator.
* Item 10, BR-5 honest playback — ALREADY LIVE, VERIFIED,
  NOT RE-CLAIMED. browser.py:1913 page_playback_votes ->
  music_player.py:177 triangulate_playback -> :261
  _browser_playback_confirmed. The suite re-verifies the
  chain by source + the pure 2-of-3 triangulation; nothing
  here touches browser.py or music_player.py.

Fitted budget (INV-1, 240,000 B): this module declares NO
tool and edits NO declaration, so the fitted payload is
unchanged by this leaf (485 tools / 232,229 B, 7,771 B
headroom — the Wave 6 registration leaf's baseline). Every
READY declaration below was measured FIRST against the
live registry's full declaration list
(len(json.dumps(d))): window +5 words = 82 B, cursor +3
words = 74 B, curl_cffi_read tool = 575 B, replay_programme
tool = 475 B; all four together = 1,206 B, landing at
233,435 B with 6,565 B headroom — everything fits, so NO
overflow_queue.md entry is needed for Wave 4. The suite
recomputes each delta from the embedded declarations and
asserts the fit; if a later leaf moves the baseline, the
recomputation (not the frozen numbers) is authoritative.

This module imports stdlib only at module level; every
staged module, the gate, and the registry are imported
lazily inside functions, so importing it can never create
a cycle or a live side effect.
"""

from __future__ import annotations  # INTENTIONAL (w4_wiring split B12): the old module carried it, and it binds `annotations` in the namespace

import json  # INTENTIONAL (w4_wiring split B12): leaked stdlib import from the old module
from typing import Any, Callable  # INTENTIONAL (w4_wiring split B12): leaked stdlib imports from the old module

# INTENTIONAL (w4_wiring split B12): re-exported from .w4_wiring_declarations
from .w4_wiring_declarations import (
    DESKTOP_ACTIONS,
    WINDOW_CONTROL_DECLARATION_ADDITION,
    CURSOR_CONTROL_DECLARATION_ADDITION,
    CURSOR_PILOTS_WINDOWS_VALIDATION_OWED,
    CURL_CFFI_READ_DECLARATION,
    REPLAY_PROGRAMME_DECLARATION,
    W4_REGISTRATION_READY,
    W4_REGISTRATION_OWED,
    BROWSER_AGENT_LIVE_TRIALS_OWED,
    CAMOUFOX_DECISION_OWED,
    APPROVED_MCP_TRIALS_OWED,
)

# INTENTIONAL (w4_wiring split B12): re-exported from .w4_wiring_pilots
from .w4_wiring_pilots import (
    PILOT_ACTIONS,
    _default_pilots,
    _parse_points,
    validate_cursor_pilot_args,
    cursor_pilot_action,
)

# INTENTIONAL (w4_wiring split B12): re-exported from .w4_wiring_trials
from .w4_wiring_trials import (
    run_browser_agent_trial,
    select_trial_winner,
    trial_loser_removal_plan,
    browser_agent_trial_status,
    camoufox_lane_status,
)

# INTENTIONAL (w4_wiring split B12): re-exported from .w4_wiring_curl_mcp
from .w4_wiring_curl_mcp import (
    curl_read,
    curl_cffi_read_tool,
    mcp_trial_configs,
    mcp_trial_call,
)

# INTENTIONAL (w4_wiring split B12): re-exported from .w4_wiring_replay
from .w4_wiring_replay import (
    _live_executor,
    replay_programme,
    replay_programme_json,
    compile_replay_trace,
    replay_programme_text,
    replay_programme_tool,
    br5_status,
)

