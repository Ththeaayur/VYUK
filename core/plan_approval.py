"""Approve-plan-once for coordinator tasks (Fix 8, 2026-11-06).

THE COMPLAINT (owner field report 2026-11-05): "the permission
thing is so annoying, it pops up all the time" — clarified:
"multiple whenever I ask it to do some complex tasks". Every
confirm-gated step of a complex task popped its own dialog.

LOPHOLE FAMILY (proved from the tree before building — three
brief premises corrected, not assumed):
  L1  No plan approval existed anywhere. The TurnCoordinator
      runs steps one by one; each confirm-gated step pops its
      own dialog through the funnel (core/funnel/
      tools_registry_exec_gates._exec_confirm ->
      confirm_gate.request_confirmation) or pauses on its own
      engine confirm stop (task_coordinator_execution._run_step
      -> pending_approval -> S-1 dialog -> resolve_pending).
      mission_control's DelegationDial ("autonomous-within-
      plan") is vocabulary only: nothing in the coordinator
      consumes set_delegation / gate_requirement_for. The
      coordinator docstring says it plainly: "The coordinator
      approves nothing, ever".
  L2  Per-kind "Always allow this kind" ALREADY EXISTS
      (v5.54.1 field fix: core/confirm_gate_allowlist.py +
      the third button in gui/window_services_confirm.py +
      the Settings "Remembered approvals" card with
      Remove / Forget-all, live disk re-read per call so
      revocation is immediate). It covers only 8 navigation
      kinds — the steps of a complex task (write a file,
      create art, apply a wallpaper) are not among them, so
      the button never appears for the popups that annoy.
      Widening the kinds would violate the safe shape, not
      fix the complaint.
  L3  Full-digest approval binding ALREADY EXISTS (A1, P0-1:
      core/confirm_gate_helpers._canonical_args_digest is a
      SHA-256 over the COMPLETE canonical args; sensitive
      tools never batch; handler identity is by object, not
      id()). The 30-char clip is display scrubbing only,
      never a key. Nothing to fix here — this module reuses
      the same digest.
  L4  The mic session grant ALREADY survives a reconnect:
      grant_session_only is module-level in-memory state and
      _maybe_mic_first_use_ask fires only on a human Connect
      (user_started=True); auto-reconnect never pops it
      (gui/window_session_connection.py). No re-ask bug.
  L5  Settings-granted permissions are ALREADY honoured
      without re-asking the permission question: the funnel's
      permission stage reads permission_grants.check_tool
      live on every call (tools_registry_exec_gates.
      _exec_permission), and a grant can never skip a
      confirmation — except for the kinds the owner has
      explicitly always-allowed, each with its own
      plain-words terms, each revocable in one tap (D4, as
      changed by the UI rethink §4.2; the excepted kinds
      are only ever safe kinds). The remaining popups are
      confirmations, and that is what this module addresses.
  L6  Voice can NEVER set an always-allow: allow_kind has
      exactly three call sites, all GUI taps — the two
      confirm-dialog button handlers
      (gui/window_services_confirm.py) and the Activity-line
      Undo re-grant (core/confirm_gate_allowlist.py
      revoke_kind's undo lambda, reached only from an
      Activity Undo tap); the voice hook only ever resolves
      confirm-gate requests. Pinned by test, not just by
      reading. (Note: the pin test scans for callers outside
      the defining module, so the revoke-undo call site
      inside confirm_gate_allowlist.py itself is covered by
      this comment, not by the test.)
  L7  A plan approval must never become a blanket: it has
      to be scoped to ONE task, ONE step, ONE exact args
      digest, in-memory only, and it must die with the
      task — or it is a standing grant wearing a plan's
      name, and D4 forbids exactly that.

THE ONE DESIGN — "Approve the plan once, exact steps only":
  * The GUI shows the plan ONCE, in plain language, before
    the task runs (gui/plan_approval_dialog.py): the safe
    steps that this approval would cover, and the steps that
    will still ask one by one, with the reason each. The
    safe default is "Ask me each time" (Enter / close /
    60 s silence = ask each time, mirroring the S-1 safe
    default). Approving is an explicit button press.
  * approve_plan() records, per task id, per step index:
    (tool, SHA-256 digest of the DECLARED args, label,
    session epoch at approve time). Eligibility is
    re-checked at approve time, not just at preview time.
  * At dispatch, the coordinator (task_coordinator_execution
    ._run_step) sets a contextvar around the ONE step's
    funnel call (the live_origin.py precedent: set/reset
    with a token, never leaking to another step or call).
    The funnel's confirm stage (tools_registry_exec_gates
    ._exec_confirm) consults covering_call(), which
    re-verifies EVERYTHING at dispatch — contextvar present,
    tool matches, digest of the funnel's args matches the
    dispatch digest, the record still exists, the session
    epoch has not moved, no taint of any kind, the money
    guard does not refuse, the provenance lock does not
    gate, and the step is STILL eligible (including the
    new-file leg — a file created by an earlier step of
    the same task voids a later step's approval). Any
    failure reads as "ask individually" — a miss only
    ever ASKS, never skips.
  * The approval covers the FUNNEL confirm popup only.
    Engine confirm stops (§7), second-opinion pauses,
    permission refusals, taint refusals, money refusals,
    folder-scope refusals, stale-view refusals, budget
    refusals and epoch voids all still fire individually.
  * The approval dies with the task: the coordinator clears
    it when the task reaches a terminal state (completed /
    failed / cancelled) and on cancel; it is in-memory
    only, never ledger-serialized, so a restart re-gates
    (mirroring the one-shot re-issue flag). A paused task
    keeps its approval for resume — same plan, same task.
  * Every plan-approved call leaves the same audit trail
    as a human approval: audit-gate value "plan-approved"
    plus an Activity line naming the task and step.

ELIGIBILITY — what a plan approval may cover (safe steps
only; destructive steps ask individually, always):
  * The step must actually gate (_needs_confirmation True)
    — otherwise no popup exists to suppress and the step
    is listed as "runs without asking".
  * NEVER (by kind, hand-maintained with a per-entry
    grounding, the _NEVER_ALLOWLIST_KINDS precedent):
    send / delete / purchase / money, irreversible and
    system actions, shell, persistence, replay, extension
    and mission state, real-world-cost actions (print),
    the paste family (consequence depends on the focused
    app at dispatch, which a plan cannot pin), and tools
    whose only gated leg is destructive (window_control
    close, press_hotkey shell-escape).
  * Consequential by content (read from the tree's own
    sets at call time, never hand-copied): mission_control
    ._GATE_ACTION_WORDS (send/delete/purchase/buy/pay) in
    the tool name or its action/operation/verb arg, and
    money_guard._MONEY_TOOL_NAMES / _MONEY_ACTIONS.
  * Otherwise, approvable ONLY in one of three shapes:
      - a UI action (menu / dialog / surface / adapter /
        context-menu / drag-drop / journal) — the funnel
        popup is suppressed, the engine confirm stop
        still pauses individually for destructive targets;
      - wallpaper_apply / wallpaper_revert (a reversible,
        visible desktop change; revert is the undo);
      - a single-destination writer whose destination is
        named by the tree's own _WRITE_DESTINATION_ARGS
        map AND does not exist yet on disk (a brand-new
        file — nothing is overwritten). A destination
        that exists, an omitted destination, or several
        destinations all read as "asks individually".
  * Anything undecidable reads as NOT approvable. The
    default is always "ask individually".

REJECTED ALTERNATIVES (with reasons):
  * Widening the always-allow kinds to writers — a standing
    grant for write_file would outlive the task and the
    plan; the complaint is about one task's popups, and a
    standing grant is the wrong scope for it.
  * A delegation-dial level ("autonomous-within-plan") as
    the mechanism — the dial is a policy vocabulary with no
    enforcement seam; the enforcement has to live where the
    popup fires (the funnel confirm stage), not in a data
    module.
  * Approving by tool name only (no args digest) — two
    write_file steps to different paths would share one
    approval; the A1 lesson says the digest is the
    approval, not the name.
  * Persisting plan approvals to disk — a stored approval
    would survive the task and the process; the one-shot
    re-issue precedent says approvals die with the run.
  * Letting the model / voice approve a plan — only the
    GUI dialog (a human press) calls approve_plan; no
    registry tool exists for it, so no voice or model path
    can reach it. Pinned by test.
  * Suppressing engine confirm stops for plan-approved
    steps — those stops are C-14 leaves for destructive
    targets (dialog overwrite, drag destructive target);
    the plan approval covers the funnel popup only.

This module is stdlib-only at import time; every tree
classification is read function-locally at call time
(the v5.32 lesson: a copied constant drifts from the
product it guards).
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import threading

__all__ = [
    "approve_plan",
    "begin_step_dispatch",
    "clear",
    "clear_all",
    "covering_call",
    "current",
    "end_step_dispatch",
    "is_plan_approvable",
    "is_jump_refused",
    "preview",
    "register_plan_handler",
    "request_plan_decision",
    "request_plan_decision_ex",
]

# ---------------------------------------------------------------------------
# In-memory record: {task_id: {step_index: record}}.
# A record is {"tool", "digest", "label", "epoch"}. Never
# persisted, never ledger-serialized: a restart re-gates.
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_APPROVALS: dict = {}

# The dispatch channel (live_origin.py precedent): set by the
# coordinator around ONE step's funnel call, read by the
# funnel's confirm stage, reset in a finally. The value is
# {"task_id", "index", "tool", "digest"} where digest is the
# SHA-256 of the FINAL dispatched args (post handle injection).
_CURRENT: contextvars.ContextVar = contextvars.ContextVar(
    "plan_approval_current", default=None)


# ---------------------------------------------------------------------------
# Eligibility — the NEVER set + the approvable shapes.
# ---------------------------------------------------------------------------
# NEVER plan-approvable, by kind. Each entry's grounding is
# the tool's consequence, not its name: these are the steps
# that must ask individually, one by one, under C-14/D4.
# The set is hand-maintained (the _NEVER_ALLOWLIST_KINDS
# precedent in core/confirm_gate_allowlist.py); the prover
# pins every member against the tree's own classifications.
_NEVER_PLAN_KINDS = frozenset({
    # C-14 consequential: send / delete / purchase.
    "send_email", "create_calendar_event",
    "delete_plan", "delete_routine", "delete_snippet",
    "delete_window_layout", "finance_playbook_delete",
    "social_entry_delete", "continuous_delete_data",
    "empty_recycle_bin", "shred_file", "clean_temp_files",
    "batch_rename",
    # file_control's kind includes delete (and copy/move of
    # live files) — excluded as a kind, exactly as the
    # allowlist excludes kinds with a destructive action.
    "file_control", "replace_in_file",
    # Irreversible / system / shell / persistence.
    "power_control", "uninstall_app", "winget_install",
    "winget_upgrade_all", "close_application",
    "process_control", "service_control",
    # D-19 (click-anywhere Batch 0): run_command is REMOVED from
    # this set — a DECLARED run_command step (exact command digest
    # match, full command shown in the dialog) is plan-coverable
    # under the plan-context branch of is_plan_approvable. All
    # other members stay never. A jump to a run_command step is
    # still refused unless the jump's target matches an approved
    # plan step's digest (the jump path re-verifies through
    # begin_step_dispatch / arm_for_call).
    "set_startup_program", "forget_wifi",
    "restore_point_before_changes", "eject_drive",
    "safe_eject_all", "wake_on_lan",
    "firewall_control", "vpn_control", "task_scheduler_control",
    "fast_startup_control", "hibernate_setting", "power_plans",
    "network_control", "env_var", "connect_wifi", "ip_renew",
    "set_process_priority", "create_restore_point",
    # Persistence / replay / extension / mission state.
    "save_routine", "teach_record_sequence", "replay_programme",
    "teach_replay_sequence", "extension_register",
    "extension_approve", "extension_enable", "extension_revoke",
    "mission_run_job", "sandbox_run", "undo_last_change",
    "creative_rights_add",
    # Real-world irreversible cost (paper / ink).
    "print_file",
    # Paste family: the consequence depends on the focused
    # app at dispatch, which a plan shown up front cannot pin.
    "paste_text", "paste_snippet", "type_clipboard",
    # window_control's only gated action is close (unsaved
    # work); press_hotkey's only gated leg is shell-escape.
    "window_control", "press_hotkey",
})

# UI actions: the funnel popup is suppressed for a
# plan-approved step, but the engine confirm stop still
# pauses individually for destructive targets (§7).
_UI_ACTION_KINDS = frozenset({
    "menu_action", "file_dialog_action", "system_surface_action",
    "adapter_action", "context_menu_action", "drag_drop_action",
    "journal_action",
})

# Reversible, visible desktop changes (revert is the undo).
_DESKTOP_STATE_KINDS = frozenset({
    "wallpaper_apply", "wallpaper_revert",
})

_NO_ASK = "runs without asking"


def _digest(args) -> str:
    """SHA-256 over a canonical JSON serialisation of the
    COMPLETE args dict — the same digest the A1 batch key
    uses (core/confirm_gate_helpers._canonical_args_digest).
    A digest of non-JSON-shaped values may differ between
    two equal-shaped calls (repr addresses) — that only
    ever MISSES a coverage, never falsely covers a
    different call."""
    try:
        from .confirm_gate_helpers import (
            _canonical_args_digest as _cad)
        return _cad(args if isinstance(args, dict) else {})
    except Exception:  # noqa: BLE001 — same fallback as the helper
        try:
            blob = json.dumps(args, sort_keys=True,
                              separators=(",", ":"), default=repr)
        except Exception:  # noqa: BLE001
            blob = repr(args)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _is_consequential(tool_name: str, args) -> bool:
    """True when the step is consequential under C-14 / money
    rules: the tree's own gate words (mission_control) in the
    tool name or its action/operation/verb arg, or the money
    guard's names/actions. Fail-closed: undecidable reads as
    consequential."""
    try:
        from . import mission_control as _mc
        words = set(_mc._GATE_ACTION_WORDS)
    except Exception:  # noqa: BLE001 — undecidable: consequential
        return True
    name_words = set(str(tool_name or "").split("_"))
    if name_words & words:
        return True
    try:
        from .funnel import money_guard as _mg
        if tool_name in _mg._MONEY_TOOL_NAMES:
            return True
        if isinstance(args, dict):
            for key in ("action", "operation", "verb"):
                value = args.get(key)
                if isinstance(value, str) and \
                        value.strip().lower() in _mg._MONEY_ACTIONS:
                    return True
    except Exception:  # noqa: BLE001 — undecidable: consequential
        return True
    return False


def _single_destination(tool_name: str, args):
    """The step's single write destination, or None.

    Read from the tree's own _WRITE_DESTINATION_ARGS map
    (core/funnel/tools_gate_paths.py): exactly one
    destination arg actually given, as a non-empty string.
    Zero destinations (omitted — the handler derives its
    own default) or several both read as None: undecidable.
    """
    try:
        from .funnel import tools_gate_paths as _paths
        keys = _paths._WRITE_DESTINATION_ARGS.get(tool_name, ())
    except Exception:  # noqa: BLE001 — undecidable: no destination
        return None
    if not isinstance(args, dict):
        return None
    given = [str(args[k]).strip() for k in keys
             if isinstance(args.get(k), str)
             and str(args.get(k)).strip()]
    if len(given) != 1:
        return None
    return given[0]


def _destination_is_new(path: str) -> bool:
    """True when the destination does not exist yet on disk
    (a brand-new file — nothing is overwritten). Mirrors
    the handler's expansion; any failure reads as "not
    new" (ask individually)."""
    try:
        expanded = os.path.expandvars(os.path.expanduser(path))
        return not os.path.lexists(expanded)
    except Exception:  # noqa: BLE001 — undecidable: not new
        return False


def _plan_step_match(tool: str, args, plan):
    """The declared plan step whose (tool, args digest) matches
    this call, or None. Plan steps may be objects with .tool /
    .args (StepDef) or dicts with "tool" / "args". A match is the
    plan-context branch's evidence that the call was DECLARED —
    the digest is over the COMPLETE args, so a call whose args
    differ from every declared step matches nothing."""
    if not plan:
        return None
    want = _digest(args if isinstance(args, dict) else {})
    try:
        steps = list(plan)
    except Exception:  # noqa: BLE001 — unlistable plan: no match
        return None
    for step in steps:
        try:
            if isinstance(step, dict):
                stool = str(step.get("tool", "") or "")
                sargs = step.get("args", {}) or {}
            else:
                stool = str(getattr(step, "tool", "") or "")
                sargs = getattr(step, "args", {}) or {}
        except Exception:  # noqa: BLE001 — malformed step: skip
            continue
        if stool != tool:
            continue
        if _digest(sargs if isinstance(sargs, dict) else {}) == want:
            return step
    return None


def _undo_store_configured() -> bool:
    """True when the file undo store is configured — the write
    is undoable (Batch 0, D-19: a write_file to an EXISTING
    destination is plan-coverable only when undoable)."""
    try:
        from .resilience import resilience_gate as _gate
    except Exception:  # noqa: BLE001
        try:
            from core.resilience import resilience_gate as _gate
        except Exception:  # noqa: BLE001 — unreadable: not configured
            return False
    try:
        return _gate.shared_undo_store() is not None
    except Exception:  # noqa: BLE001 — unreadable: not configured
        return False


def _is_browser_navigation(args) -> bool:
    """True when this browser_control call is a navigation (goto
    / new_tab with a URL)."""
    if not isinstance(args, dict):
        return False
    action = str(args.get("action", "") or "").strip().lower()
    url = str(args.get("url", "") or "").strip()
    return action in ("goto", "new_tab") and bool(url)


def _browser_click_eligible(tool: str, args) -> bool:
    """The plan-context branch's conditions for a browser_click /
    browser_type / browser_press / click_anywhere call: the
    picker's declared action class is Tier 1 (navigation /
    read-only), the site is in the allowed-sites list (full
    scope), and — for browser_click / click_anywhere — the
    gate-side backstop has not fired on the element's name/text
    (a D-18 word match refuses coverage regardless of the
    declared class). Consequential clicks are NEVER eligible
    under this branch.

    click_anywhere (Batch 1): the site comes from args["site"]
    when present, else from the target URL for a navigate
    action; a click/type/press with no site named is not
    eligible (it asks individually — fail-safe). The backstop
    for click_anywhere matches the complete list (D-18 words
    PLUS Like/Follow/Save/Star — matches_backstop_words), not
    the browser_gates regexes, which contain neither
    "download" nor "logout"."""
    if not isinstance(args, dict):
        return False
    try:
        from . import browser_scope as _scope
    except Exception:  # noqa: BLE001
        try:
            from core import browser_scope as _scope
        except Exception:  # noqa: BLE001 — unreadable: not eligible
            return False
    action_class = str(args.get("action_class", "") or "").strip().lower()
    if action_class not in _scope.TIER1_ACTION_CLASSES:
        return False
    site = str(args.get("site", "") or "").strip()
    if not site and tool == "click_anywhere":
        action = str(args.get("action", "") or "").strip().lower()
        if action == "navigate":
            site = _scope.host_of(args.get("target", ""))
    if not site or not _scope.site_allowed_for_navigation(site):
        return False
    if tool in ("browser_click", "click_anywhere"):
        # Gate-side backstop (Q-12): the name/text is what
        # distinguishes a "Send" button from a "Next" button —
        # a D-18 word match refuses coverage whatever class
        # the picker declared (a mislabeled consequential
        # control still asks).
        match = (_scope.matches_backstop_words
                 if tool == "click_anywhere"
                 else _scope.matches_d18_words)
        for key in ("name", "text"):
            if match(args.get(key)):
                return False
    return True


def _approvable_route(tool: str, args, plan=None):
    """The leg that approves this step, or None when no leg does.

    The check order is explicit (Plan 9 §2.5, D-19) and the
    prover pins it:
      1. _needs_confirmation → False means _NO_ASK (unchanged,
         first) — route "no-ask", never recorded as coverage.
      2. never-set → refuse.
      3. _is_consequential → refuse (checks tool-name words and
         action/operation/verb args only, NEVER the "command"
         arg — a declared run_command's sole backstop is the
         user's plan approval with the FULL command shown).
      4. NEW plan-context branch (only when plan is given):
         (tool, _digest(args)) matches a declared step, plus
         the kind's conditions (run_command: exact digest match
         only; write_file to an EXISTING destination: declared
         + digest matches + undo store configured; browser_control
         navigation: URL declared + site in the allowed-sites
         list; browser_click/type/press: Tier 1 action class +
         site declared + digest matches; consequential clicks
         NEVER eligible). Route "plan-context".
      5. Existing rules unchanged: _UI_ACTION_KINDS → "ui";
         _DESKTOP_STATE_KINDS → "desktop-state"; single
         new-file destination → "new-file".
    Pure except for the filesystem existence check and the
    tree reads; never raises. Undecidable always reads as
    not approvable."""
    tool = str(tool or "").strip()
    if not tool:
        return None
    try:
        from .funnel import tools_gate as _gate
        if not _gate._needs_confirmation(tool, args):
            return "no-ask"
    except Exception:  # noqa: BLE001 — gate undecidable: ask
        return None
    if tool in _NEVER_PLAN_KINDS:
        return None
    if _is_consequential(tool, args):
        return None
    if plan is not None:
        matched = _plan_step_match(tool, args, plan)
        if matched is not None:
            if tool == "run_command":
                return "plan-context"
            if tool == "write_file":
                dest = _single_destination(tool, args)
                if dest is not None and not _destination_is_new(dest) \
                        and _undo_store_configured():
                    return "plan-context"
            if tool == "browser_control":
                if _is_browser_navigation(args):
                    try:
                        from . import browser_scope as _scope
                    except Exception:  # noqa: BLE001
                        try:
                            from core import browser_scope as _scope
                        except Exception:  # noqa: BLE001
                            _scope = None
                    if _scope is not None and \
                            _scope.site_allowed_for_navigation(
                                _scope.host_of(args.get("url", ""))):
                        return "plan-context"
            if tool in ("browser_click", "browser_type",
                        "browser_press", "click_anywhere"):
                if _browser_click_eligible(tool, args):
                    return "plan-context"
            # Matched the digest but the kind's conditions are
            # unmet — fall through to step 5, never cover.
    if tool in _UI_ACTION_KINDS:
        return "ui"
    if tool in _DESKTOP_STATE_KINDS:
        return "desktop-state"
    dest = _single_destination(tool, args)
    if dest is None:
        return None
    if not _destination_is_new(dest):
        return None
    return "new-file"


def is_plan_approvable(tool_name: str, args, plan=None):
    """(True, "") when a plan approval may cover this step,
    else (False, reason). The reason is plain words for the
    plan dialog's "will still ask" list. ``plan`` is the task's
    declared steps (or None): the plan-context branch runs
    only when a plan is given. Pure except for the filesystem
    existence check and the tree reads; never raises.
    Undecidable always reads as not approvable."""
    tool = str(tool_name or "").strip()
    if not tool:
        return False, "no tool named — asks individually"
    try:
        route = _approvable_route(tool, args, plan=plan)
    except Exception:  # noqa: BLE001 — undecidable: ask
        return False, "gate undecidable — asks individually"
    if route == "no-ask":
        return False, _NO_ASK
    if route in ("plan-context", "ui", "desktop-state", "new-file"):
        return True, ""
    if route is None:
        # The reason names the failing leg, as before: the
        # never-set / consequential refusals first, then the
        # destination reasons.
        try:
            from .funnel import tools_gate as _gate
            if not _gate._needs_confirmation(tool, args):
                return False, _NO_ASK
        except Exception:  # noqa: BLE001 — gate undecidable: ask
            return False, "gate undecidable — asks individually"
        if tool in _NEVER_PLAN_KINDS:
            return False, ("destructive or system action — "
                           "always asks individually")
        if _is_consequential(tool, args):
            return False, ("send / delete / purchase — "
                           "always asks individually")
        dest = _single_destination(tool, args)
        if dest is None:
            return False, ("no single new-file destination — "
                           "asks individually")
        return False, ("destination already exists — "
                       "asks individually")
    return False, "asks individually"


def is_jump_refused(tool_name: str, args):
    """(True, reason) when a jump must NEVER land on this
    step, else (False, ""). B4: the D4/C-14 refusal set —
    a granted permission never skips a confirmation (D4, as
    changed by the UI rethink §4.2: EXCEPT for the kinds
    the owner has explicitly always-allowed, each with its
    own plain-words terms, each revocable in one tap — and
    the excepted kinds are only ever safe kinds, so a jump
    target in the never-list is still refused) and send /
    delete / purchase always ask first (C-14), so a jump to
    such a step would smuggle a destructive action past the
    human: the step was declared in the plan, not approved
    at the moment of failure, and the plan approval
    explicitly refuses to cover it. The set is the tree's
    own: _NEVER_PLAN_KINDS plus the consequential predicate
    (_is_consequential) — read here, never copied (the
    v5.32 lesson). A refused jump halts the task honestly;
    the target step never runs. Pure except for the tree
    reads; never raises. Undecidable reads as refused
    (fail-closed)."""
    tool = str(tool_name or "").strip()
    if not tool:
        return True, "no tool named — jump refused"
    try:
        if tool in _NEVER_PLAN_KINDS:
            return True, (f"{tool} is a destructive, system or "
                          f"irreversible step — jumps never land on it")
        if _is_consequential(tool, args):
            return True, (f"{tool} is a send / delete / purchase "
                          f"step — jumps never land on it")
    except Exception:  # noqa: BLE001 — undecidable: refused
        return True, "jump target undecidable — jump refused"
    return False, ""


def _summarize(args) -> str:
    """The masked args summary for the plan dialog — the
    same summariser the confirm popup uses, so secrets are
    masked identically. Never raises; degrades to a plain
    truncated repr."""
    try:
        from .confirm_gate import summarize_args as _summ
        return str(_summ(args if isinstance(args, dict) else {}))
    except Exception:  # noqa: BLE001 — degrade, never break the plan
        try:
            text = repr(args)
        except Exception:  # noqa: BLE001
            text = ""
        return text[:200]


def preview(task):
    """Split a task's steps into the plan dialog's three
    lists: "approvable" (this approval would cover),
    "still_individual" (each with its plain-words reason),
    and "no_ask" (the count of steps that never pop at
    all). Pure; never raises."""
    approvable = []
    still = []
    no_ask = 0
    try:
        steps = list(getattr(task, "steps", []) or [])
    except Exception:  # noqa: BLE001 — no steps, no plan
        steps = []
    for index, step in enumerate(steps):
        tool = str(getattr(step, "tool", "") or "")
        args = getattr(step, "args", {}) or {}
        label = str(getattr(step, "label", "") or tool)
        try:
            ok, reason = is_plan_approvable(tool, args, plan=steps)
        except Exception:  # noqa: BLE001 — undecidable: ask
            ok, reason = False, "asks individually"
        entry = {"index": index, "tool": tool, "label": label,
                 "summary": _summarize(args)}
        # N-22 (Batch 1): a `code -r` step carries the
        # workspace-swap warning into the dialog's covered-steps
        # list before the first `code` call. The dialog renders
        # it from this "warning" key — integration point:
        # gui/plan_approval_dialog.py's build_plan_approval_dialog,
        # which renders the covered-steps list from preview().
        try:
            from .click_anywhere_tool import (
                code_workspace_swap_warning as _swap_warning)
            _warning = _swap_warning(step)
        except Exception:  # noqa: BLE001 — no warning, no block
            _warning = ""
        if _warning:
            entry["warning"] = _warning
        if ok:
            approvable.append(entry)
        elif reason == _NO_ASK:
            no_ask += 1
        else:
            entry["reason"] = reason
            still.append(entry)
    return {"approvable": approvable, "still_individual": still,
            "no_ask": no_ask}


def _read_epoch():
    """The session epoch now, or None when it cannot be
    read (fail-closed: no epoch, no approval)."""
    try:
        from . import session_epoch as _epoch
        return _epoch.current_epoch()
    except Exception:  # noqa: BLE001 — unreadable: no approval
        return None


def approve_plan(task_id, steps, indices):
    """Record a plan approval, made by a human in the plan
    dialog. Returns {"approved": [indices], "refused":
    [{"index", "reason"}]}. Eligibility is re-checked here
    (not just at preview time): a file created between the
    dialog and this call, or an ineligible index smuggled
    in by any caller, is refused, never recorded. The
    record is keyed by task id and step index and carries
    the digest of the DECLARED args plus the session epoch
    at approve time. Overwrites any prior record for the
    same task (the dialog is shown once per run; a second
    approval replaces the first). Never raises."""
    task_id = str(task_id or "")
    approved = []
    refused = []
    if not task_id:
        return {"approved": approved,
                "refused": [{"index": i,
                             "reason": "no task id — asks individually"}
                            for i in (indices or [])]}
    epoch = _read_epoch()
    if epoch is None:
        return {"approved": approved,
                "refused": [{"index": i,
                             "reason": "session epoch unreadable — "
                                       "asks individually"}
                            for i in (indices or [])]}
    try:
        steps = list(steps or [])
    except Exception:  # noqa: BLE001 — no steps to approve
        steps = []
    record = {}
    for index in (indices or []):
        if not isinstance(index, int) or isinstance(index, bool) \
                or index < 0 or index >= len(steps):
            refused.append({"index": index,
                            "reason": "not a step of this plan"})
            continue
        step = steps[index]
        tool = str(getattr(step, "tool", "") or "")
        args = getattr(step, "args", {}) or {}
        try:
            ok, reason = is_plan_approvable(tool, args, plan=steps)
            route = _approvable_route(tool, args, plan=steps)
        except Exception:  # noqa: BLE001 — undecidable: refuse
            ok, reason, route = False, "asks individually", None
        if not ok:
            refused.append({"index": index, "reason": reason})
            continue
        record[index] = {
            "tool": tool,
            "digest": _digest(args),
            "label": str(getattr(step, "label", "") or tool),
            "epoch": epoch,
            # The leg that approved the step (Batch 0, D-19):
            # covering_call's still-eligible re-check needs to
            # know whether the approval came from the new-file
            # leg (void when the file now exists) or the
            # plan-context branch. In-memory only, like the
            # record itself.
            "route": route,
        }
        approved.append(index)
    if record:
        with _LOCK:
            existing = _APPROVALS.get(task_id, {})
            existing.update(record)
            _APPROVALS[task_id] = existing
    return {"approved": approved, "refused": refused}


def clear(task_id) -> None:
    """Drop a task's plan approval (terminal state, cancel).
    Never raises."""
    try:
        with _LOCK:
            _APPROVALS.pop(str(task_id or ""), None)
    except Exception:  # noqa: BLE001 — teardown never raises
        pass


def clear_all() -> None:
    """Drop every plan approval (tests only). Never raises."""
    try:
        with _LOCK:
            _APPROVALS.clear()
    except Exception:  # noqa: BLE001 — teardown never raises
        pass


def _injected_handle_args():
    """The §7 handle-arg names (window_handle_id /
    target_handle_id) — the keys _dispatch_args injects
    onto a copy of the declared args. Read from the tree's
    own STEP_HANDLE_ARG map at call time; on failure the
    known pair (a mismatch then only ever ASKS)."""
    try:
        from .desktop import s7_threading as _s7t
        return set(_s7t.STEP_HANDLE_ARG.values())
    except Exception:  # noqa: BLE001 — known pair, mismatch asks
        return {"window_handle_id", "target_handle_id"}


def begin_step_dispatch(task, step, final_args, index=None):
    """Arm the dispatch channel for ONE step's funnel call.

    Returns the contextvar token when this exact dispatch
    is covered by the task's plan approval, else None. The
    check: the record exists for (task id, current step
    index) with the same tool, the declared args digest
    matches the record, and the final args match the
    declared args modulo the injected handle keys. Any
    mismatch reads as "not covered" — the funnel then
    pops as usual. Never raises.

    B19: ``index`` names the step when the caller is not
    running it as the task's current step (a parallel-group
    branch runs at its own index while task._index still
    points at the group's first step). None means "the
    task's current step", exactly as before — sequential
    callers pass nothing and behaviour is unchanged.
    """
    try:
        task_id = str(getattr(task, "id", "") or "")
        if index is None:
            index = int(getattr(task, "_index", -1))
        else:
            index = int(index)
        if not task_id or index < 0:
            return None
        with _LOCK:
            record = dict(_APPROVALS.get(task_id, {}).get(index, {})
                          or {})
        if not record or record.get("tool") != getattr(step, "tool", ""):
            return None
        declared = dict(getattr(step, "args", {}) or {})
        final = dict(final_args or {})
        if _digest(declared) != record.get("digest"):
            return None
        injected = _injected_handle_args()
        stripped_declared = {k: v for k, v in declared.items()
                             if k not in injected}
        stripped_final = {k: v for k, v in final.items()
                          if k not in injected}
        if _digest(stripped_declared) != _digest(stripped_final):
            return None
        return _CURRENT.set({
            "task_id": task_id,
            "index": index,
            "tool": getattr(step, "tool", ""),
            "digest": _digest(final),
        })
    except Exception:  # noqa: BLE001 — undecidable: not covered
        return None


def end_step_dispatch(token) -> None:
    """Reset the dispatch channel. Never raises."""
    if token is None:
        return
    try:
        _CURRENT.reset(token)
    except Exception:  # noqa: BLE001 — teardown never raises
        pass


def arm_for_call(task_id, steps, tool, args):
    """Arm the dispatch channel for a call that matches an approved
    plan step by CONTENT (tool + args digest), not position.

    B23 (approve-plan-once coverage) — loophole family "an
    approval that doesn't travel": Fix 8 armed the channel
    positionally (begin_step_dispatch at the task's current
    step index), so only the coordinator's declared-step path
    was covered. A run_goal picked action has no position —
    the picker chooses freely — and a B22 re-plan appends
    steps at NEW indices, so a step identical to an approved
    one would still ask. ONE design: match by content — try
    each approved step's record through begin_step_dispatch
    itself, and arm the first that verifies. Every check
    stays where it already lives: begin_step_dispatch
    re-verifies tool + declared digest + final args (modulo
    injected handle keys), and the funnel's covering_call
    re-verifies everything again at dispatch (digest, epoch,
    taint, money, provenance, eligibility incl. the
    new-file leg). No new trust logic is invented here, and
    the digest is never weakened — a call whose args differ
    from every approved step matches nothing and asks, as
    today. ``steps`` are the approved plan's StepDef list
    (entries may be None — a step that failed to build
    never matches). Returns the contextvar token, or None
    when nothing covers this call. Never raises.

    Rejected: a spoken approval channel (C-14 — a spoken
    "yes" can never approve an action; the approval itself
    stays GUI-only, only its COVERAGE travels);
    auto-approving picked actions (the user never approved
    a step the picker invented — a match must be exact
    content); a second approval store for run_goal / the
    legacy loop (one approval, one home — this module, this
    in-memory record); weakening the digest to a
    tool-name-only match (a different call with the same
    tool would ride an approval it never earned)."""
    try:
        task_id = str(task_id or "")
        if not task_id:
            return None
        from types import SimpleNamespace as _NS
        _task = _NS(id=task_id)
        for index, step in enumerate(steps or []):
            if step is None:
                continue
            try:
                token = begin_step_dispatch(
                    _task, step, args, index=index)
            except Exception:  # noqa: BLE001 — undecidable: try next
                token = None
            if token is not None:
                return token
        return None
    except Exception:  # noqa: BLE001 — undecidable: not covered
        return None


def current():
    """The dispatch channel's current value (tests /
    introspection). None outside a covered dispatch."""
    try:
        return _CURRENT.get()
    except Exception:  # noqa: BLE001
        return None


def covering_call(tool_name: str, args):
    """The funnel confirm stage's question: is THIS call
    covered by a plan approval? Returns {"task_id",
    "index", "label"} when covered, else None. Every
    check below re-runs at dispatch time; any failure
    reads as "ask individually":
      * the dispatch channel is set, for this tool, with
        a digest matching the funnel's args;
      * the record still exists for (task, index) with
        the same tool;
      * the session epoch has not moved since approval;
      * no taint of any kind (a content-prompted call is
        never plan-approved — the browser_type hard line,
        applied to every kind);
      * the money guard does not refuse this call;
      * the session-provenance lock does not gate it;
      * the step is STILL eligible (including the
        new-file leg — a file created by an earlier step
        of the same task voids a later step's approval).
    Never raises."""
    try:
        cur = _CURRENT.get()
    except Exception:  # noqa: BLE001 — no channel: ask
        return None
    if not isinstance(cur, dict):
        return None
    tool = str(tool_name or "").strip()
    if cur.get("tool") != tool or not tool:
        return None
    try:
        if _digest(args if isinstance(args, dict) else {}) \
                != cur.get("digest"):
            return None
    except Exception:  # noqa: BLE001 — undecidable: ask
        return None
    task_id = str(cur.get("task_id") or "")
    index = cur.get("index")
    if not task_id or not isinstance(index, int):
        return None
    with _LOCK:
        record = dict(_APPROVALS.get(task_id, {}).get(index, {})
                      or {})
    if not record or record.get("tool") != tool:
        return None
    epoch_now = _read_epoch()
    if epoch_now is None or epoch_now != record.get("epoch"):
        return None
    try:
        from . import injection as _inj
        if _inj.tainted():
            return None
    except Exception:  # noqa: BLE001 — undecidable: ask
        return None
    try:
        from .funnel import money_guard as _mg
        if _mg.check(tool, args if isinstance(args, dict) else {}) \
                is not None:
            return None
    except Exception:  # noqa: BLE001 — undecidable: ask
        return None
    try:
        from .funnel import tools_gate as _gate
        if _gate._session_provenance_lock_gated(tool, None):
            return None
    except Exception:  # noqa: BLE001 — undecidable: ask
        return None
    try:
        # Still eligible, re-checked at dispatch (S-7): the
        # dispatch channel set by begin_step_dispatch IS the
        # plan evidence here — it already proved (tool, digest)
        # against the stored approval record — so the
        # plan-context branch is run against a one-step plan
        # built from the channel itself. The branch's kind
        # conditions (undo store still configured, site still
        # allowed, Tier 1 class, D-18 backstop) are genuine
        # re-checks; the digest match itself is already proven
        # by the channel check above.
        _channel_plan = [{"tool": tool,
                          "args": args if isinstance(args, dict) else {}}]
        ok, _reason = is_plan_approvable(tool, args, plan=_channel_plan)
    except Exception:  # noqa: BLE001 — undecidable: ask
        return None
    if not ok:
        return None
    if record.get("route") == "new-file":
        # A file created by an earlier step of the same task
        # voids a later step's new-file approval (§2.5) — the
        # plan-context branch must not resurrect it just
        # because the undo store is configured.
        _dest = _single_destination(tool, args)
        if _dest is None or not _destination_is_new(_dest):
            return None
    # Gate-side backstop (Q-12): a click whose args carry a
    # D-18 word in the element's name/text is refused coverage
    # whatever class the picker declared — the picker is the
    # primary enforcement point, the gate is the backstop.
    # Desktop click args carry the control's type + name; an
    # absent name matches nothing (the icon-only residual).
    # click_anywhere (Batch 1) matches the complete backstop
    # list (D-18 words PLUS Like/Follow/Save/Star); the other
    # tools match the D-18 list.
    if tool in ("browser_click", "uia_click_control",
                "click_by_mark", "click_element", "click_anywhere"):
        try:
            from . import browser_scope as _scope
        except Exception:  # noqa: BLE001
            try:
                from core import browser_scope as _scope
            except Exception:  # noqa: BLE001 — unreadable: ask
                return None
        match = (_scope.matches_backstop_words
                 if tool == "click_anywhere"
                 else _scope.matches_d18_words)
        for _key in ("name", "text"):
            try:
                if match(
                        (args if isinstance(args, dict) else {}).get(_key)):
                    return None
            except Exception:  # noqa: BLE001 — undecidable: ask
                return None
    return {"task_id": task_id, "index": index,
            "label": record.get("label") or tool}


# -- cross-thread ask (Fix 9: voice plan approval) -------------------------
# The typed path calls the plan dialog directly from the GUI
# thread (gui/window_session_typed.py). A voice routine runs
# on a worker thread — it cannot show a dialog itself. The
# pattern is the confirm gate's (core/funnel/
# tools_registry_exec_gates._exec_confirm -> gui/
# window_services_confirm._confirm_gate_handler): one
# registered handler, serialized by a lock, invoked on a
# daemon thread, waiting on an event the GUI slot sets when
# the human answers. The budgets match that handler's:
# HANDLER_TIMEOUT_S (55 s) here, the dialog's own 50 s
# inside — the dialog is always gone before this ask gives
# up, so a timeout here means the human truly did not
# answer. A late answer that lands after the timeout is
# DISCARDED: this function reads the handler's result only
# while the wait is open, and the coordinator pauses the
# task on a timeout (see task_coordinator_lifecycle) before
# any step runs — so a stale approval can never land on a
# task that already moved on, and a resumed task re-gates
# every step. Never raises.
HANDLER_TIMEOUT_S = 55.0
# The dialog's own budget (leaves a 5 s margin under the
# handler budget). Public: the GUI slot passes it to
# present_plan_approval_dialog so the two budgets live in
# one place (core), not a hardcoded 50 in the GUI.
DIALOG_TIMEOUT_S = 50.0
_DIALOG_TIMEOUT_S = DIALOG_TIMEOUT_S

_HANDLER = None
_HANDLER_LOCK = threading.Lock()


def register_plan_handler(handler) -> None:
    """Register the GUI's plan-approval handler (or None to
    clear). The handler receives (task, preview(task)) and
    returns the approved step indices as a list (or a dict
    with an "approved" list); anything else reads as "ask
    each time". One handler at a time — registering replaces.
    Never raises."""
    global _HANDLER
    try:
        with _HANDLER_LOCK:
            _HANDLER = handler
    except Exception:  # noqa: BLE001 — registration never raises
        pass


def has_plan_handler() -> bool:
    """True when a GUI plan-approval handler is registered
    (UI rethink Build 4 §4.5: the direct-plan helper skips
    its planning pass entirely without one — no model call,
    no dialog, today's behaviour — because a plan nobody
    can approve is pure overhead). Never raises."""
    return _current_handler() is not None


def _current_handler():
    try:
        with _HANDLER_LOCK:
            return _HANDLER
    except Exception:  # noqa: BLE001 — no handler: ask
        return None


def request_plan_decision_ex(task, timeout_s=HANDLER_TIMEOUT_S):
    """Ask the human to approve this task's plan ONCE, from
    a non-GUI thread — and say WHY the ask ended.

    Returns (indices, outcome). ``indices`` is the approved
    step list, exactly as request_plan_decision returns it;
    ``outcome`` is one of:

    * "approved" — the human approved at least one step;
    * "ask_each_time" — the human pressed "Ask me each
      time", closed the dialog, or the handler returned no
      indices without a timeout;
    * "timeout" — nobody answered inside the budget (the
      dialog's own timer fired, or the handler thread never
      came back). This is the silent third answer: it is NOT
      the same as "ask each time", and the caller must not
      treat it as one — the coordinator pauses the task on
      it instead of running step-by-step asks into a human
      who is not there;
    * "no_handler" — no GUI handler is registered;
    * "error" — no plan could be built, or the handler
      itself failed.

    A handler may return a dict {"approved": [...],
    "outcome": ...} (the GUI handler does — it knows
    whether its dialog timed out); a plain list keeps the
    old meaning (non-empty approved, empty ask-each-time).
    A late answer arriving after the wait timed out is
    discarded, never applied. Never raises."""
    try:
        plan = preview(task)
    except Exception:  # noqa: BLE001 — no plan: error
        return [], "error"
    handler = _current_handler()
    if handler is None:
        return [], "no_handler"
    _done = threading.Event()
    _result = {"indices": [], "outcome": None}

    def _run():
        try:
            got = handler(task, plan)
            if isinstance(got, dict):
                outcome = got.get("outcome")
                got = got.get("approved", [])
                if isinstance(outcome, str) and outcome in (
                        "approved", "ask_each_time", "timeout",
                        "no_handler", "error"):
                    _result["outcome"] = outcome
            if isinstance(got, (list, tuple)):
                _result["indices"] = [int(i) for i in got
                                      if isinstance(i, int)
                                      and not isinstance(i, bool)]
        except Exception:  # noqa: BLE001 — handler error
            _result["indices"] = []
            _result["outcome"] = "error"
        finally:
            _done.set()

    try:
        _thread = threading.Thread(target=_run, daemon=True,
                                    name="plan-approval-ask")
        _thread.start()
        answered = _done.wait(timeout=timeout_s)
    except Exception:  # noqa: BLE001 — ask failure: error
        return [], "error"
    if not answered:
        # The handler never came back inside the budget:
        # a timeout, whatever it does later. Its late
        # result (if any) is discarded by construction —
        # nobody reads _result after this return.
        return [], "timeout"
    indices = list(_result.get("indices", []))
    outcome = _result.get("outcome")
    if outcome is not None:
        if outcome == "timeout":
            # A timeout approves nothing, whatever indices
            # rode along with it: discard them.
            return [], "timeout"
        if outcome == "approved" and not indices:
            # A handler claiming approval with no indices
            # approved nothing: read it as ask-each-time,
            # never as an approval.
            return [], "ask_each_time"
        return indices, outcome
    return indices, ("approved" if indices else "ask_each_time")


def request_plan_decision(task, timeout_s=HANDLER_TIMEOUT_S) -> list:
    """Ask the human to approve this task's plan ONCE, from
    a non-GUI thread. Returns the approved step indices
    (recorded by the handler via approve_plan, or by this
    function when the handler returns indices without
    recording them). [] means "ask me each time" — no
    handler, handler error, timeout, or an explicit
    refusal all read the same way. Never raises.

    The [] contract is kept for the existing callers and
    pins; callers that must tell a timeout from an explicit
    refusal use request_plan_decision_ex instead."""
    indices, _outcome = request_plan_decision_ex(task, timeout_s)
    return indices
