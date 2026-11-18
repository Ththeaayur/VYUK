# w4_wiring_declarations.py — split from core/w4_wiring.py (Phase B B12): gate constants + READY/OWED declarations.
# Move only; the facade in core/w4_wiring.py re-exports every name.

from __future__ import annotations  # keep def-time annotation semantics identical to the old module

# ---------------------------------------------------------------------------
# Item 3 / 4 — action words and READY declaration additions
# ---------------------------------------------------------------------------

DESKTOP_ACTIONS: tuple[str, ...] = (
    "desktop_list",
    "desktop_switch",
    "desktop_move",
    "desktop_pin",
    "desktop_unpin",
)



# Exact edit for the coordinator's registration follow-up
# (core/funnel/tools_handlers.py window_control registration):
# replace the action enum
#   ["list", "focus", "minimize", "maximize", "close"]
# with the new enum below. Nothing else in that registration
# changes; the handler already accepts the words.
WINDOW_CONTROL_DECLARATION_ADDITION: dict[str, Any] = {
    "tool": "window_control",
    "file": "core/funnel/tools_handlers.py (window_control registration)",
    "old_enum": ["list", "focus", "minimize", "maximize", "close"],
    "new_enum": [
        "list", "focus", "minimize", "maximize", "close",
        "desktop_list", "desktop_switch", "desktop_move",
        "desktop_pin", "desktop_unpin",
    ],
    "exact_replacement": (
        '"action": {"type": "string", "enum": ["list", "focus", '
        '"minimize", "maximize", "close", "desktop_list", '
        '"desktop_switch", "desktop_move", "desktop_pin", '
        '"desktop_unpin"], "description": '
        '"Window action to perform"}'
    ),
    "fitted_cost_bytes": 82,
    "closing_step": (
        "Coordinator registration follow-up applies this enum "
        "addition (file-locked for this leaf), then re-measures "
        "the fitted payload before shipping."
    ),
}



# Exact edit for the coordinator's registration follow-up
# (core/desktop/cursor.py register_all): replace
#   "enum": list(_ACTIONS),
# with
#   "enum": list(_ACTIONS) + list(_PILOT_ACTIONS),
# where _PILOT_ACTIONS is this module's PILOT_ACTIONS (the
# cursor.py branch reads it lazily, so the enum order is
# legacy actions first, pilots appended — exactly the order
# the 74 B measurement assumed). _ACTIONS itself is NOT
# extended: the branch, not the legacy tuple, owns pilots.
CURSOR_CONTROL_DECLARATION_ADDITION: dict[str, Any] = {
    "tool": "cursor_control",
    "file": "core/desktop/cursor.py (register_all)",
    "old_enum_note": "list(_ACTIONS) — the 13 legacy actions",
    "new_enum": [
        "position", "move", "move_fraction", "move_by", "nudge",
        "click", "double_click", "right_click", "middle_click",
        "scroll", "drag", "beacon", "park",
        "ole_drag_drop", "cursor_waypoint_glide",
        "cursor_drag_with_hover_pause",
    ],
    "exact_replacement": (
        '"enum": list(_ACTIONS) + list(_PILOT_ACTIONS),'
    ),
    "fitted_cost_bytes": 74,
    "closing_step": (
        "Coordinator registration follow-up applies this enum "
        "addition (declaration growth; cursor.py's register_all "
        "is the declaration site), then re-measures the fitted "
        "payload before shipping. Windows validation of the "
        "pilots is still owed after registration."
    ),
}



CURSOR_PILOTS_WINDOWS_VALIDATION_OWED = (
    "PC-C pilots (ole_drag_drop, cursor_waypoint_glide, "
    "cursor_drag_with_hover_pause) have never run on Windows: "
    "a live Windows bench (drag gesture, waypoint glide, "
    "hover-pause drag against a real target) is owed before "
    "the pilot words are presented as working. True COM OLE "
    "data objects stay STUDY — ole_drag_drop is a cursor drag "
    "on the seeded-Bezier path, and its result says so."
)



# ---------------------------------------------------------------------------
# Item 7 / 9 — READY tool declarations (exact dicts the coordinator
# registers; the suite recomputes their fitted deltas from these)
# ---------------------------------------------------------------------------

CURL_CFFI_READ_DECLARATION: dict[str, Any] = {
    "name": "curl_cffi_read",
    "description": (
        "Read an HTTPS page through the curl_cffi "
        "browser-fingerprint lane, using only cookies from her "
        "own Playwright browser context. Refused while "
        "tainted; never reads a cookie file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTPS URL to read",
            },
            "impersonate": {
                "type": "string",
                "enum": ["chrome", "safari", "edge", "firefox"],
                "description": "Browser fingerprint to impersonate",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (default 30)",
            },
        },
        "required": ["url"],
    },
    # Not part of the fitted declaration; kept alongside it so
    # the coordinator cannot register the tool without seeing
    # its closing steps. Strip these keys at registration.
    "_fitted_cost_bytes": 575,
    "_closing_steps": (
        "Coordinator registration follow-up registers this "
        "declaration (file-locked for this leaf) with dispatch "
        "to curl_cffi_read_tool() in this module. Two steps "
        "ride with it: (1) the dispatching handler must supply "
        "cookies taken from the LIVE Playwright browser "
        "context — until that caller exists the tool refuses "
        "honestly with no cookies supplied, and it must never "
        "be pointed at a cookie file; (2) CC-1 must carry the "
        "curl_cffi wheel's bundled-native licence texts "
        "(libcurl-impersonate, BoringSSL), read from the wheel "
        "itself, into THIRD_PARTY_LICENSES.md before any "
        "shipped use of the lane."
    ),
}



REPLAY_PROGRAMME_DECLARATION: dict[str, Any] = {
    "name": "replay_programme",
    "description": (
        "Replay a recorded programme of her own successful "
        "tool calls through the funnel, in order. Each step "
        "is re-validated and re-gated; a failed step halts "
        "the replay. Programmes carrying password or CAPTCHA "
        "arguments are refused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "programme": {
                "type": "string",
                "description": (
                    "Programme as JSON text: {\"steps\": "
                    "[{\"tool\": name, \"args\": {...}}]}"
                ),
            },
        },
        "required": ["programme"],
    },
    "_fitted_cost_bytes": 475,
    "_closing_steps": (
        "Coordinator registration follow-up registers this "
        "declaration (file-locked for this leaf) with dispatch "
        "to replay_programme_tool() in this module, then "
        "re-measures the fitted payload before shipping. "
        "This is Wave 4 TC-2 replay (core/replay.py), not "
        "Wave 8 teach replay."
    ),
}



W4_REGISTRATION_READY: dict[str, Any] = {
    "window_control_enum": WINDOW_CONTROL_DECLARATION_ADDITION,
    "cursor_control_enum": CURSOR_CONTROL_DECLARATION_ADDITION,
    "curl_cffi_read": CURL_CFFI_READ_DECLARATION,
    "replay_programme": REPLAY_PROGRAMME_DECLARATION,
    "total_fitted_cost_bytes": 82 + 74 + 575 + 475,
    "fitted_total_after_all_bytes": 232229 + 1206,
    "fitted_headroom_after_all_bytes": 240000 - 233435,
}



W4_REGISTRATION_OWED = (
    "Coordinator combined registration follow-up: apply the "
    "four READY additions in W4_REGISTRATION_READY "
    "(window_control enum +5 words, cursor_control enum +3 "
    "words, curl_cffi_read tool, replay_programme tool), "
    "re-measure the fitted payload first (all four together "
    "measured 1,206 B at this leaf's 232,229 B baseline, "
    "landing at 233,435 B — inside the 240,000 B cap), and "
    "only then present the words/tools as live. Nothing in "
    "this leaf edits a declaration, so nothing is live "
    "through the funnel yet for these four."
)



# ---------------------------------------------------------------------------
# Item 5 / 6 / 8 — owed decisions (named, never silent)
# ---------------------------------------------------------------------------

BROWSER_AGENT_LIVE_TRIALS_OWED = (
    "HE-2/HE-3 pilot-both live trials are OWED: live SPA "
    "trials on a Windows box with a real browser, both "
    "pilots (browser-use, stagehand) on the same task set, "
    "before select_trial_winner() can return anything but "
    "NO-DATA. No winner is picked offscreen, neither pilot "
    "is a live feature, and the loser's removal plan runs "
    "only after a live winner exists."
)



CAMOUFOX_DECISION_OWED = (
    "Camoufox lane decision OWED: licence verification + "
    "trial decision first (like HE-2/HE-3). No second "
    "browser engine is wired without an owner decision, "
    "and the lane never becomes the default browser path, "
    "never reuses her Chrome profile or logins, and never "
    "runs with geoip (permanently off in the lane module)."
)



APPROVED_MCP_TRIALS_OWED = (
    "No approved MCP trial-entry list exists yet, so "
    "mcp_trial_call() refuses every call until the caller "
    "supplies an approved list. The approved-trial list "
    "(which servers/tools, under which trial terms) is the "
    "OWED decision; the gate predicate "
    "(trial_call_allowed) is necessary but not sufficient — "
    "an entry must be BOTH approved AND gate-passing."
)

