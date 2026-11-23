"""Gate policy for the tool registry (v5.50, programme item 43).

Split out of core/tools.py: the classification sets and every
pure decision the executor consults before a handler may run —
confirmation gating (_needs_confirmation and its argument/token/
path analysis), read/write classification, taint/egress classes,
protected-path and install-tree checks, session provenance,
screen-scope grants, tiering and the hotkey gate classes.

core/tools.py was deleted in Refactor Phase 4 (S1c); the funnel package is now the only home. Bodies moved
verbatim; behaviour is unchanged.
"""

import ctypes
import os
import re
import subprocess
import sys
import time
from typing import Any
from .. import injection  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level
from .. import input_guard  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level
from .guard_state import GuardState, GuardVerdict  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level (Phase 3 F2b: guard_state now in core/funnel/, F1 interim closed)
from .tools_gate_confirm import (_CONFIRM_GATE_TOOLS)  # refactor P1 step 3 (confirm concern, moved verbatim)
from .tools_gate_shell import (_HOTKEY_SHELL_KEYS, _SHELL_LAUNCH_NAMES, _SHELL_FOREGROUND_PROCS, _SHELL_FOREGROUND_CLASSES, _EXECUTABLE_PATH_EXTS, _is_shell_launch, _HOST_FLAG_TOKENS, _argument_tokens, _arguments_invoke_interpreter, _is_executable_path, _foreground_process_name, _foreground_window_class, _platform_is_windows, _foreground_is_shell, _normalized_hotkey_tokens)  # refactor P1 step 3 (shell concern, moved verbatim)
from .tools_gate_readonly import (_READ_ONLY_TOOLS)  # refactor P1 step 3 (readonly concern, moved verbatim)
from .tools_gate_free import (_FREE_BY_DESIGN_CLASSES, _FREE_BY_DESIGN)  # refactor P1 step 3 (free concern, moved verbatim)
from .tools_gate_egress import (_EGRESS_TOOLS, _LOCAL_ONLY_READS, _PLUGIN_GATE_CLASS, _plugin_gate_class, _is_egress_read)  # refactor P1 step 3 (egress concern, moved verbatim)
from .tools_gate_taint import (_UNTRUSTED_PRODUCERS, _wrap_producer_result, wrap_mcp_content)  # refactor P1 step 3 (taint concern, moved verbatim)
from .tools_gate_paths import (_FILE_READ_PATH_ARGS, _WRITE_DESTINATION_ARGS, _FILE_CONTROL_WRITE_ACTIONS, _BROWSER_STORE_FILES, _PROTECTED_BASENAMES, _PROTECTED_SUFFIXES, _PROTECTED_DIR_FILE_PAIRS, _WIN_ENV_RE, _expand_env_vars, _is_protected_path, _protected_path_hit, _protected_destination_hit, _INSTALL_TREE_FOLDERS, _FILE_CONTROL_INSTALL_WRITE_ACTIONS, _package_root_parts, _install_tree_folder, _install_tree_write_hit, _handler_error_kind, _FOLDER_SCOPE_PATH_ARGS, _folder_scope_hit)  # refactor P1 step 3 (paths concern, moved verbatim) + PF-18 batch 7 (folder-scope re-export)


# ------------------------------------------------------------------
# Speaker lock (v5.45, PROGRAMME SPEAKER-LOCK) — session provenance.
#
# The gate decision in _needs_confirmation needs to know HOW the
# current session started, at execute time. The seam chosen here is
# a module-level provenance flag in this module, set by the window
# at session start (gui/main_window.py: _start_connection marks
# "user" only for an explicit Connect press; connect-on-launch,
# auto-reconnect and watcher starts stay "auto"; push-to-talk marks
# "user" for the live session; disconnect resets to "unknown").
# brain.py was rejected as the home for this fact: sessions there
# are rebuilt per engine/fallback, while execute() already reads
# its other session facts (injection taint) from module state in
# this layer — provenance belongs with the gate that consumes it.
#
# FAIL-CLOSED: the flag starts "unknown", and anything that is not
# exactly "user" counts as NOT user-started.
_session_provenance: str = "unknown"


def set_session_provenance(provenance: str) -> None:
    """Records how the current session started: "user" (explicitly
    started from the app — Connect button / push-to-talk), "auto"
    (wake word / autostart / auto-reconnect / watcher-initiated),
    or "unknown" (no session / not yet marked). Any other value is
    stored as "unknown" so a typo can never read as user-started.
    Owner proof follows the start: a user-started session is proven
    owner at start (a physical on-screen act); an auto/unknown
    session starts UNPROVEN and stays so until the owner unlocks
    it (on screen, or with the voice PIN) — loophole batch 4."""
    global _session_provenance
    _session_provenance = (
        provenance if provenance in ("user", "auto") else "unknown")
    if _session_provenance == "user":
        mark_session_owner_proven("user-started")
    else:
        mark_session_owner_unproven()


def session_provenance() -> str:
    """The recorded session provenance ("user" / "auto" /
    "unknown"); "unknown" until the window marks a session start."""
    return _session_provenance


def _session_is_user_started() -> bool:
    return _session_provenance == "user"


# ------------------------------------------------------------------
# Owner proof (loophole batch 4 — #1, #20, L-27, #19).
#
# The provenance lock above only makes a state-changing tool ASK
# in a non-user-started session — and the ask itself was still
# answerable by voice (gui/window_services_confirm.py:
# _maybe_confirm_reply resolves a pending ask on a spoken "yes").
# So any voice in the room could approve anything. Owner proof
# closes that: a session is PROVEN only when the owner proved it
# — by starting it from the app UI (provenance "user": an explicit
# Connect press / push-to-talk, a physical on-screen act) or by
# unlocking it afterwards (on screen, or with the owner-set voice
# PIN). A wake-word / autostart / reconnect / watcher session
# starts UNPROVEN, and an unproven session's spoken yes/no never
# resolves a pending confirmation — the popup stays up for the
# owner to answer on screen, or it times out and the act is
# refused. Reads stay free in every state (reading is not acting).
#
# This is a session lock, not voiceprint verification: it does not
# recognise WHO is speaking. The on-device voiceprint module
# (core/voice_lock.py, PF-16) has no cleared engine and no cleared
# weights, so it is unavailable and never a pass — this control
# is the honest foundation until one is cleared. A visitor at the
# keyboard pressing Connect is the same boundary as a visitor at
# the keyboard clicking Yes on a popup: physical screen access,
# which the (approved, not yet built) app lock covers, not voice.
#
# FAIL-CLOSED: the flag starts False (unproven), and anything that
# is not an explicit owner proof reads as unproven.
_session_owner_proven: bool = False
_session_owner_proof_reason: str | None = None


def session_owner_proven() -> bool:
    """True when this session is proven owner: started from the
    app UI by the owner, or unlocked afterwards (on screen, or
    with the voice PIN). False until then — including "unknown"."""
    return bool(_session_owner_proven)


def session_owner_proof_reason() -> str | None:
    """How the session was proven ("user-started" / "screen" /
    "voice-pin"), or None while unproven."""
    return _session_owner_proof_reason


def mark_session_owner_proven(reason: str = "screen") -> None:
    """Marks this session proven owner. `reason` names the proof:
    "user-started" (set by set_session_provenance), "screen" (the
    owner unlocked it on screen), "voice-pin" (the owner-set PIN
    matched). Any other value is stored as "screen" so a typo can
    never invent a proof kind."""
    global _session_owner_proven, _session_owner_proof_reason
    _session_owner_proven = True
    _session_owner_proof_reason = (
        reason if reason in ("user-started", "screen", "voice-pin")
        else "screen")


def mark_session_owner_unproven() -> None:
    """Marks this session unproven (disconnect / new session /
    lock-out). The next voice answer is refused until the owner
    proves the session again."""
    global _session_owner_proven, _session_owner_proof_reason
    _session_owner_proven = False
    _session_owner_proof_reason = None


def session_owner_proof_status() -> dict:
    """The owner-proof state for display: proven, how, the lock,
    and the session provenance. Never raises (a broken read reads
    as unproven with the lock on — fail-closed)."""
    try:
        from ..config import config as _app_config  # Phase 3 F1

        lock_on = bool(
            getattr(_app_config, "session_provenance_lock", False))
    except Exception:  # noqa: BLE001 — unreadable config: lock on
        lock_on = True
    return {"proven": bool(_session_owner_proven),
            "reason": _session_owner_proof_reason,
            "lock_on": lock_on,
            "provenance": _session_provenance}


def voice_confirm_allowed() -> bool:
    """May a SPOKEN yes/no resolve a pending confirmation?
    True when the session is owner-proven, or when the lock is
    OFF (the owner explicitly disabled it in Settings). False
    in an unproven session under the lock — the voice answer is
    refused and the popup stays up for an on-screen answer.
    Fail-closed on an unreadable config (a broken read must never
    let a voice approve)."""
    if session_owner_proven():
        return True
    try:
        from ..config import config as _app_config  # Phase 3 F1

        if not bool(getattr(_app_config, "session_provenance_lock",
                            False)):
            return True
    except Exception:  # noqa: BLE001 — unreadable config: no voice answer
        return False
    return False


# ------------------------------------------------------------------
# Owner-only reads (loophole batch 5 — L-24, #16).
#
# Batch 4 proved a session is owner-proven or it is not — but it
# left READS free in every state ("reading is not acting"). A read
# is not acting, but it is still an ORACLE: any voice in the room
# could ask VYUK to read his calendar, inbox, files, contacts and
# notes aloud, and the answer is the owner's private material.
# So in an unproven session, reads of the owner's private material
# are REFUSED in plain words (not asked — a stranger gets no
# popup for "what is on his calendar"), while the owner in a
# proven session reads freely, and a typed turn (no live origin)
# is unaffected.
#
# THE BOUNDARY (explicit, reviewable — the suite pins it):
# Owner-only — the answer names the owner's private state:
#   calendar/schedule (today, upcoming, briefing, deadlines,
#     meetings, day review, timeline, pending, birthdays,
#     occasions, promises, expiries);
#   mail (unread, search, inbox sort, unsubscribe scan);
#   files (read, PDF, content/index search, find, recent,
#     largest, OCR, tail, sniff, age, hash, compare, folders,
#     CSV/JSON query, clipboard files);
#   clipboard text; browser history and bookmarks;
#   activity history (tool log, workflow runs, undo store);
#   memory and knowledge (recall, day journal, knowledge search);
#   plans and routines; personal records (habits, mood, social
#     balances, snippets, taught sequences, saved Wi-Fi networks
#     — location history); and the read actions of the
#     action-dispatched tools whose store is the owner's
#     (scheduled agents, journal, command output, health summary,
#     personal skills, billing, MCP registry, agent calls, prompt
#     tuning, localisation, media chat, invoices, translations,
#     filament, deals, routes, voice agents, wardrobe, ebooks,
#     lodging, game servers, lighting, scheduled tasks, env vars,
#     expenses, bills, subscriptions, reading list).
# Free — the answer is the same for a stranger, or is computed
# from what the speaker just supplied:
#   clock, world clock, weather, web search, stock/crypto prices,
#   public facts; system info (CPU/RAM/disk, battery, drives,
#   installed apps, services, drivers, fonts, printers, network
#   state); screen look (what is on screen, the active window,
#   window lists, pixel probes — visible to anyone in the room);
#   app chrome (menu/context-menu/surface listings); and every
#   validator/computer over caller-supplied input (drafts,
#   readiness checks, loan/split/savings maths, health extract,
#   wind-down routines, journal prompts — the fixed list by date).
# Deliberate exclusions, recorded so a reviewer can challenge
# them: app_settings get (machine config the live loop itself
# reads; a secret value inside it is still scrubbed by the
# secret guard); list_backup_snapshots (backup metadata; the
# snapshots are encrypted and restore is confirm-gated);
# site_permissions_review and privacy_usage (permission
# metadata, not content); list_users and session_identity
# (the machine's own identity, not the owner's content).
#
# #16 is the same boundary, not a second one: there is no
# standalone free-slot tool in the tree — the oracle is the
# calendar reads (get_upcoming_events / get_todays_schedule),
# and gating them gates the free-slot answer. When a free-slot
# finder IS built, it must be owner-only by construction.
#
# The gate fires only for a LIVE-ORIGIN call (the voice loop's
# marker) in an UNPROVEN session under the lock. A typed turn,
# an app-UI call, a proven session, or the lock OFF all pass
# through untouched. The check lives in _exec_owner_read (the
# funnel's exec stage, after live-staleness so route_/toolbox
# re-entry resolves the true tool name first).
_OWNER_ONLY_READS = frozenset({
    # Calendar / schedule.
    "get_todays_schedule", "get_upcoming_events", "find_free_slots",
    # Stage 0 features batch 2: find_free_slots answers free/busy
    # from the owner's calendar — the #16 oracle stays closed by
    # construction (it answers only for the owner, never a voice).
    "get_morning_briefing", "deadline_radar",
    "meeting_prep_card", "meeting_status", "day_review",
    "timeline_recap", "whats_pending", "birthday_radar",
    "social_occasion_upcoming", "overdue_promises",
    "social_promise_list", "expiry_radar",
    # Mail.
    "get_unread_email", "search_email", "inbox_zero_assist",
    "unsubscribe_finder",
    # Files.
    "read_file", "read_pdf_text", "search_file_contents",
    "search_indexed_files", "find_files", "recent_files",
    "largest_files", "ocr_file", "tail_file", "sniff_file",
    "file_age_report", "file_hash", "compare_files",
    "folder_size", "folder_outline", "folder_diff",
    "csv_preview", "csv_query", "json_query", "clipboard_files",
    # Clipboard and browser.
    "read_clipboard", "browser_history", "browser_bookmarks",
    # Activity history.
    "get_activity_log", "workflow_runs", "undo_status",
    # Memory and knowledge.
    "recall_memories", "recall_day", "knowledge_search",
    "semantic_search_knowledge",
    # Darwin D1: document-memory reads return the owner's stored
    # file content — owner-only like knowledge_search.
    "search_documents", "list_documents",
    # Plans and routines.
    "list_plans", "get_plan", "list_routines",
    # Personal records.
    "habit_summary", "mood_trend", "social_balances",
    "list_snippets", "teach_list_sequences",
    "saved_wifi_networks",
})

# Action-dispatched tools whose READ actions return the owner's
# stored content (the read determination is _is_read_action's,
# except the three tools below whose read set is broader than
# their owner-content set — see _OWNER_ONLY_READ_ACTIONS).
_OWNER_ONLY_DISPATCHED = frozenset({
    "scheduled_agents", "journal_action", "run_command",
    "health", "personal_skills", "billing", "mcp_registry",
    "agent_call", "prompt_tune", "audio_localise", "media_chat",
    "invoice", "translations", "filament", "deals",
    "vehicle_routes", "voice_agent", "wardrobe", "ebook_library",
    "lodging", "game_server", "stage_lighting",
    "task_scheduler_control", "env_var", "expense_log",
    "bill_calendar", "subscription_audit", "reading_list",
    # Stage 0 features batch 3 (Group F, 2026-11-22): the money
    # alerts check reads the owner's budgets, watches and
    # subscriptions; the portfolio's list/total read the owner's
    # holdings — both owner-only, the subscription_audit precedent.
    "check_money_alerts", "portfolio_tracker",
    # Stage 0 features batch 4 (Group G, 2026-11-22): notes,
    # wishlist, watchlist, goals and voice memos are the owner's
    # private material — their list/read actions are owner-only,
    # the reading_list precedent. And the owner-gate extension
    # (parent ruling, batch 3): the budget report and the
    # price-watch list/check read the same stores
    # check_money_alerts reads, so they are owner-only too.
    "notes", "wishlist", "watchlist", "goal_tracker",
    "voice_memos", "budget_envelope", "price_watch",
})

# Tools whose owner-content read actions are a SUBSET of their
# _is_read_action set — the rest compute over caller-supplied
# input and stay free: health extract reads pasted record text
# (summary reads the stored records); invoice template lists
# the built-in templates (list/get/status/events read his
# invoices). voice_agent is handled in the function (its
# agent/flow list/get are mode-gated, and run is a simulation).
_OWNER_ONLY_READ_ACTIONS = {
    "health": {"summary"},
    "invoice": {"list", "get", "status", "events"},
}


def _is_owner_only_read(name: str,
                        args: dict[str, Any] | None) -> bool:
    """True when this call reads the owner's private material
    (the L-24 boundary above). A pure-read tool in the set is
    owner-only on every call; an action-dispatched tool in the
    set is owner-only only on its read actions (a write action
    is not a read, and it reaches its own confirm gate). Never
    raises — an unjudgeable call reads as NOT owner-only, so a
    broken check can never refuse a read the owner asked for;
    the refusal path is the gate's, not this predicate's."""
    if name in _OWNER_ONLY_READS:
        return True
    if name not in _OWNER_ONLY_DISPATCHED:
        return False
    action = str((args or {}).get("action", "")).strip().lower()
    if name in _OWNER_ONLY_READ_ACTIONS:
        return action in _OWNER_ONLY_READ_ACTIONS[name]
    if name == "voice_agent":
        # agent/flow list/get (mode-gated) and status/events are
        # reads of his agents and flows; run is a local text
        # simulation over supplied input, not a stored read.
        if action in ("status", "events"):
            return True
        mode = str((args or {}).get("mode", "")).strip().lower()
        return action in ("agent", "flow") and mode in ("list", "get")
    try:
        return bool(_is_read_action(name, args))
    except Exception:  # noqa: BLE001 — a broken split reads as free
        return False
from .tools_gate_readclass import (_is_read_action, _is_read_class)  # refactor P1 step 3 (readclass concern, moved verbatim)
from .tool_classification import classify_call  # GAPS-23 (F-V3): the canonical per-call classifier


def _session_provenance_lock_gated(name: str, args: dict[str, Any] | None) -> bool:
    """Session provenance lock (v5.45 as the "speaker lock"; renamed
    v5.48, programme item 24 — the old name promised voiceprint
    recognition this never did): while
    config.session_provenance_lock is ON and the current session
    was NOT started by the user from the app, every state-changing
    tool needs confirmation — including tools that normally run
    free (plain typing, ordinary app launches). The state-changing
    boundary is this module's own classification: _READ_ONLY_TOOLS
    stay free (reads are not acting), the read actions of
    action-dispatched tools stay free, and everything else gates.
    In a user-started session, or with the lock off, nothing
    changes. Unknown provenance gates (fail-closed)."""
    try:
        from ..config import config as _app_config  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level

        if not bool(getattr(_app_config, "session_provenance_lock", False)):
            return False
    except Exception as exc:  # noqa: BLE001
        # v5.48 (programme item 24): FAIL CLOSED. Pre-v5.48 this
        # path returned False — an unreadable/broken config was
        # treated as lock-off, silently dropping the very
        # protection the user switched on, at exactly the moment
        # something was already wrong. A config that cannot be
        # read now gates (True) and logs loudly instead.
        try:
            import logging

            logging.getLogger("nova.tools").error(
                "session provenance lock: config unreadable "
                "(%s: %s) — gating state-changing tools "
                "(fail-closed, v5.48 programme item 24)",
                type(exc).__name__, exc)
        except Exception:  # noqa: BLE001 — logging must never crash the gate
            pass
        return True
    if _session_is_user_started():
        return False
    if name in _READ_ONLY_TOOLS:
        return False
    if _is_read_action(name, args):
        return False
    return True
from .tools_gate_tier import (_TYPE_FAMILY, _TIER1_NAV_TOOLS, _hotkey_gate_classes_fire, _tool_tier)  # refactor P1 step 3 (tier concern, moved verbatim)


# Scoped approval (v5.46, programme item 2, D-C1): one bounded
# grant that frees Tier-1 (navigation) tools under SOFT taint.
# Module state, mirroring the _session_provenance pattern above:
# the gate consumes it at execute time, the grant is made by
# execute() itself (on approval of a scope-ask popup) or by
# core/planner.run_plan (running a plan IS the approval for the
# plan's duration). A grant is bound to the injection taint
# SEGMENT that was current when it was made — a taint clear
# closes the segment and the grant with it.
_screen_scope: dict[str, Any] | None = None
# Clock seam: suites monkeypatch this name with a controllable
# callable to advance time without sleeping.
_scope_clock = time.monotonic


def grant_screen_scope(app: str | None = None, minutes: float = 15,
                       plan: str | None = None) -> None:
    """Grant the screen scope: for the next `minutes`, Tier-1
    tools run free under soft taint. `app` binds the grant to
    one foreground app (the app the human approved acting in);
    `plan` binds it to a plan run (active regardless of
    foreground — the plan itself was the approval). The grant
    records the current taint segment and dies with it."""
    global _screen_scope
    try:
        span = float(minutes)
    except (TypeError, ValueError):
        span = 15.0
    _screen_scope = {
        "app": app,
        "expires": _scope_clock() + max(0.0, span) * 60.0,
        "plan": plan,
        "segment": injection.taint_segment(),
    }


def revoke_screen_scope(plan: str | None = None) -> None:
    """Drop the screen scope. With `plan`, only THAT plan's
    grant is cleared (a finishing plan must not revoke an app
    grant the human made separately); without, any grant goes."""
    global _screen_scope
    if plan is None:
        _screen_scope = None
    elif _screen_scope is not None \
            and _screen_scope.get("plan") == plan:
        _screen_scope = None


def screen_scope_active() -> bool:
    """Whether a screen scope currently frees Tier-1 tools.
    False when: no grant, the grant expired, or the grant's
    taint segment is not the current one (a clear closed it).
    A plan grant is active regardless of foreground. An app
    grant is active while the foreground process is
    undeterminable or IS the granted app; a determinable
    DIFFERENT foreground revokes the grant (the human approved
    acting in one app, not in whatever popped up since)."""
    global _screen_scope
    grant = _screen_scope
    if not grant:
        return False
    if _scope_clock() >= float(grant.get("expires") or 0):
        _screen_scope = None
        return False
    if grant.get("segment") != injection.taint_segment():
        _screen_scope = None
        return False
    if grant.get("plan"):
        return True
    foreground = _foreground_process_name()
    if foreground is None:
        return True
    app = grant.get("app")
    if not app:
        return True
    if str(foreground).lower() == str(app).lower():
        return True
    _screen_scope = None
    return False


# ---------------------------------------------------------------------------
# MN-5.14 never-automate money guard (v5.66, Money track leaf 2)
#
# A guarded call is NEVER confirmable: _needs_confirmation
# returns True for it (routing it into the confirm ask even
# when the tool itself is normally ungated), and
# core/confirm_gate.request_confirmation refuses it before
# any handler runs — so a human yes can never approve it.
# The consult is lazy + guarded, like the funnel's other
# consults: an unimportable guard module reads as "no
# refusal" and the call falls through to its ordinary gate
# decision (the leaf suite pins the module importable, so
# a broken guard fails the suite, not silently at runtime).
# ---------------------------------------------------------------------------


def _money_guard_refusal(name: str, args: dict[str, Any] | None):
    """core/funnel/money_guard's verdict for one call, as a
    GuardVerdict (audit fix A2, P0-2). ALLOWED = the guard ran
    and cleared; DENIED = the guard refused (message carried);
    UNAVAILABLE = the guard errored (import or check failure).
    UNAVAILABLE is never "no refusal" and never "disabled":
    there is no explicit disabled state for the money guard,
    and an exception is not a declaration. Callers MUST treat
    anything but ALLOWED as force-gate: the confirm ask it
    routes into denies on unavailable (core/confirm_gate)
    and refuses outright on denied, so the two layers compose
    to deny — no routing difference can bypass the guard.
    Never raises."""
    try:
        from . import money_guard as _mg  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level (Phase 3 F2b: money_guard now in core/funnel/, F1 interim closed)

        refusal = _mg.check(name, args if isinstance(args, dict) else {})
        if refusal is not None:
            return GuardVerdict.denied(str(refusal.message))
        return GuardVerdict.allowed()
    except Exception as exc:  # noqa: BLE001 — A2 fail-closed
        return GuardVerdict.unavailable(
            f"money guard unavailable ({type(exc).__name__})")


def _needs_confirmation(name: str, args: dict[str, Any] | None) -> bool:
    """Args-aware gate decision (v4.4): most gated tools are gated for
    every call, but window_control is only destructive for its
    "close" action — minimizing or listing windows must not pop a
    confirmation — and press_hotkey is gated only for shell-escape
    combos (v5.3, see _HOTKEY_SHELL_KEYS). Everything else defers to
    _CONFIRM_GATE_TOOLS.

    TAINT (v5.3, Hardening H2) outranks all of the above: while
    core.injection reports the session tainted — untrusted content
    (a page, an email, screen text) tried to instruct VYUK — every
    state-changing tool needs the human's OK, including input tools
    that are normally free (type_text, clicks). Pure reads
    (_READ_ONLY_TOOLS) stay ungated: looking is not acting.
    EGRESS (v5.43, H17) is the one exception to that exemption: a
    read that sends model-chosen bytes to a model-chosen host
    is gated under taint exactly like a state-changing tool,
    because the request itself is the leak channel. Untainted,
    egress tools behave exactly as before. v5.46 (programme
    item 4, A2): the egress class is no longer the hand-kept
    13-tool _EGRESS_TOOLS list alone — _is_egress_read() inverts
    it, so every built-in read-only tool NOT in the audited
    provably-local _LOCAL_ONLY_READS set gates under taint
    (the plugin gate-class path is unchanged). The egress test
    sits inside this taint branch only, so the untainted gate
    decision for every tool is unchanged.

    SOFT TAINT (v5.46, programme item 2, D-C1/D-C2): when the
    taint kind is "soft" (provenance only — no pattern hit), the
    branch tiers instead (see _tool_tier): Tier 0 falls through
    ungated, Tier 2 gates, Tier 1 gates unless a screen scope is
    active (grant_screen_scope). Falling through means the
    tool's OWN branches below still apply — a Tier-1 tool whose
    normal branch gates (window_control close is Tier 2 anyway;
    paste-family tools in _CONFIRM_GATE_TOOLS) keeps its gate;
    Tier-1 tools whose normal branch returns False are not
    double-gated. HARD taint (pattern hit) takes the original
    branch below, unchanged."""
    # Plugin gate classes (v5.45, H19b): a plugin gated by its
    # capability declaration (or by having none) is gated for
    # every call, exactly like a _CONFIRM_GATE_TOOLS member.
    #
    # MN-5.14 (v5.66) FIRST: a never-automate money call is
    # gated here whatever its ordinary classification — the
    # confirm ask it routes into refuses it outright (see
    # core/confirm_gate.request_confirmation), so gating is
    # the funnel's way of refusing an otherwise-ungated tool.
    if _money_guard_refusal(name, args).state != GuardState.ALLOWED:
        return True
    _pclass = _plugin_gate_class(name)
    if _pclass == "gated":
        return True
    if injection.tainted():
        if injection.taint_kind() == "soft":
            # v5.46 (programme item 2): provenance-tiered taint.
            _tier = _tool_tier(name, args)
            if _tier == 2:
                return True
            if _tier == 1 and not screen_scope_active():
                return True
            # Tier 0, or Tier 1 inside an active screen scope:
            # no taint gate — fall through to the ordinary
            # branches below.
        else:
            _read_only = name in _READ_ONLY_TOOLS or _pclass == "read"
            # v5.46 (A2): _is_egress_read subsumes the old
            # `name in _EGRESS_TOOLS or _pclass == "egress"` test and
            # adds the _LOCAL_ONLY_READS inversion for built-in reads.
            _egress = _is_egress_read(name)
            # v5.45 (H12 corollary): a LOOK is a read — injection.py's
            # own contract ("reading a malicious page must not block
            # reading the next one") applies to the frame tools, or a
            # vision frame's image-taint would dead-lock the look loop
            # itself. take_screenshot WITH save_path is a file write,
            # not a look, and stays gated.
            if name == "screen_look":
                _read_only = True
            elif name == "take_screenshot" and not str(
                    (args or {}).get("save_path", "") or "").strip():
                _read_only = True
            elif name == "get_pixel_color" and _is_read_action(name, args):
                # Audit fix Batch 1 (SEC-01): the read action is
                # no longer in _READ_ONLY_TOOLS (the click action
                # must not be), so without this line the hard-taint
                # branch would gate pure pixel probes — the exact
                # regression the review warned about. The click
                # action keeps _read_only False and gates here.
                _read_only = True
            if not _read_only or _egress:
                return True
    # SESSION PROVENANCE LOCK (v5.45 as speaker lock; renamed
    # v5.48, programme item 24): a non-user-started session under
    # the lock gates every state-changing tool (see
    # _session_provenance_lock_gated — the boundary is this
    # module's own read-only classification, and unknown
    # provenance gates).
    if _session_provenance_lock_gated(name, args):
        return True
    # v5.47 (programme item 12): the fall-through IS the
    # free-by-design decision for _FREE_BY_DESIGN members — they
    # reach this line only after every gate branch above has had
    # its say (launch/open_path/hotkey branches included), and
    # membership in _FREE_BY_DESIGN deliberately adds NO gate
    # here, exactly as unclassified tools behaved before v5.47.
    # A name in NO classification source reaching this line is a
    # completeness bug (tests/test_classification.py fails on
    # it), never a silent fourth freedom.
    # GAPS-23 (F-V3): the base classification is the canonical
    # classifier — one source of truth (core/funnel/
    # tool_classification.py). The per-tool action splits and
    # the confirm-set fall-through live there as _gate_action /
    # classify(); this function adds only the overlays above
    # (money, plugin, taint, provenance lock). The v5.47
    # fall-through comment above still describes the semantics
    # classify_call preserves (the launch / open_path / hotkey
    # branches have had their say inside _gate_action).
    return classify_call(name, args).needs_confirmation


# ---------------------------------------------------------------------------
# MCP tool classification (v5.55, Wave 1 HE-1; policy-only since A3)
# ---------------------------------------------------------------------------
# MCP tools are UNTRUSTED BY DEFAULT: the server is code VYUK
# did not write, running outside her audit. Since audit A3
# (master audit P0-5) their class comes ONLY from the local,
# operator-reviewed capability policy (core.mcp_policy) — never
# from the server's own MCP tool annotations. The annotations
# (readOnlyHint / destructiveHint / openWorldHint) are advisory
# only: they stay on the tool-table entry for display and
# diagnostics, but no classification path reads them. A remote
# readOnlyHint=true on a sensitive tool earns NOTHING: without
# a matching policy record the tool is "confirm" (asks every
# call) in every session state, tainted or untainted. A tool
# whose declaration changed since review is unreviewed again
# until re-reviewed (the policy digest catches it).

def mcp_tool_class(entry: dict[str, Any]) -> str:
    """One MCP tool table entry -> "read" | "egress" | "confirm".

    Audit A3 (P0-5): the class comes ONLY from the local
    operator-reviewed policy (core.mcp_policy.policy_class).
    Remote annotations are advisory and never decisive:
    * a policy record whose digest still matches the entry's
      current declaration -> that record's class;
    * anything else (unknown tool, no record, changed
      declaration, unknown recorded class, unreadable policy)
      -> "confirm" (asks every call, never read-free).

    The old annotation ladder (destructive -> confirm,
    openWorld -> egress, readOnly -> read) is GONE: a server
    marking its own sensitive tool readOnlyHint=true used to
    earn read treatment with no confirmation untainted. It
    now earns "confirm" unless an operator reviewed exactly
    this declaration as read.
    """
    try:
        from .. import mcp_policy as _mp  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level

        cls = _mp.policy_class(entry)
    except Exception:  # noqa: BLE001 — an unreadable policy grants nothing
        cls = None
    if cls in ("read", "egress", "confirm"):
        return cls
    return "confirm"


def mcp_is_read(entry: dict[str, Any]) -> bool:
    """True when one MCP tool is read-class (the failsafe
    exemption: while tripped, only reads run)."""
    return mcp_tool_class(entry) == "read"


def _mcp_provenance_lock_active() -> bool:
    """The provenance lock's active test, class-agnostic (the
    native _session_provenance_lock_gated judges by native
    tool name, which cannot classify an MCP tool — so the
    MCP predicate does the lock test itself and applies the
    class boundary itself). Fail-closed on an unreadable
    config, mirroring the native function."""
    try:
        from ..config import config as _app_config  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level

        if not bool(getattr(_app_config, "session_provenance_lock", False)):
            return False
    except Exception:  # noqa: BLE001 — fail-closed, as native
        return True
    try:
        return not _session_is_user_started()
    except Exception:  # noqa: BLE001 — fail-closed, as native
        return True


def mcp_needs_confirmation(entry: dict[str, Any],
                           args: dict[str, Any] | None,
                           changed: bool) -> bool:
    """Gate decision for one MCP tool call (v5.55, HE-1).
    Mirrors the native rules at the altitude MCP tools live
    at, in order:
    * changed description hash (SE-5.12 runtime half) ->
      always asks (a tool whose description changed since
      approval IS a new tool).
    * "confirm" class -> always asks.
    * "egress"/"read" -> free untainted; ask under ANY
      taint. MCP tools have no soft/hard tier — the server
      is untrusted, so provenance-only (soft) taint still
      asks. This is deliberately stricter than native
      Tier-0 soft-taint pass-through.
    * provenance lock (on + session not user-started) ->
      "egress"/"confirm" ask; "read" stays free (looking
      is not acting — the same boundary as native reads).
    Two hard lines, shared with the registry branch: MCP
    tools are NEVER confirm-allowlistable (the v5.54.1
    allowlist covers seven native navigation kinds only)
    and NEVER failsafe-exempt (while tripped, only "read"
    runs — the branch enforces that separately, before
    this predicate, exactly like the native failsafe block).
    """
    if changed:
        return True
    # MN-5.14 (v5.66): a never-automate money MCP call asks
    # (and the confirm ask then refuses it outright — see
    # core/confirm_gate.request_confirmation), exactly like a
    # native guarded call above.
    _mcp_name = (entry or {}).get("name") or (entry or {}).get("tool") or ""
    if _mcp_name and _money_guard_refusal(
            _mcp_name, args).state != GuardState.ALLOWED:
        return True
    cls = mcp_tool_class(entry)
    if cls == "confirm":
        return True
    try:
        tainted = bool(injection.tainted())
    except Exception:  # noqa: BLE001 — A1 fail-closed: unconsultable
        # reads TAINTED, so an MCP call whose taint state cannot be
        # proven clean asks, exactly like a tainted one (the
        # docstring above: egress/read ask under ANY taint).
        tainted = True
    if tainted:
        return True
    if cls != "read" and _mcp_provenance_lock_active():
        return True
    return False


def _confirmation_args(name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """The arg view the human actually sees in the confirm popup
    (v5.2). confirm_gate.summarize_args lists keys alphabetically
    under a total-length budget, so a tool whose decision-critical
    fields sort late (an event's title behind its description) could
    have them truncated out of the popup. For the two Google write
    tools the view is curated to what the human is deciding on —
    recipient + subject for a send, title + when for an event —
    and nothing else about the gate changes: the handler still
    receives (tool_name, summary) and the decision is logged the
    same way. run_command is curated too (v5.3, H1): the view is
    the command alone, and execute() asks confirm_gate to show it
    verbatim — the human approves the FULL command string, never a
    truncated preview of it. type_text / paste_text are curated
    the same way (v5.42, H11): the popup shows the full text being
    typed or pasted. Every other tool passes its args through
    untouched."""
    args = args or {}
    if name == "run_command":
        return {"command": str(args.get("command", ""))}
    if name in ("type_text", "paste_text") and "text" in args:
        # v5.42 (H11): same verbatim precedent as run_command —
        # the human approves the EXACT text that will land in the
        # focused app (a shell, when these gate), never a preview.
        return {"text": str(args.get("text", ""))}
    if name == "send_email":
        return {
            "to": args.get("to", ""),
            "subject": args.get("subject", ""),
            "body": args.get("body", ""),
        }
    if name == "create_calendar_event":
        return {
            "title": args.get("title", ""),
            "start": args.get("start", ""),
            "end": args.get("end", ""),
            "location": args.get("location", ""),
        }
    if name == "cancel_calendar_event":
        # Stage 0 features batch 2 (Group E): the popup names
        # the event (and the day) so the human knows what is
        # about to be deleted.
        return {
            "event": args.get("event", "") or args.get("event_id", ""),
            "date": args.get("date", ""),
        }
    if name == "reschedule_calendar_event":
        # Stage 0 features batch 2 (Group E): the popup names
        # the event, the day and the new start, so the human can
        # see the move before approving it.
        return {
            "event": args.get("event", "") or args.get("event_id", ""),
            "date": args.get("date", ""),
            "new_start": args.get("new_start", ""),
            "new_end": args.get("new_end", ""),
        }
    if name == "scheduled_agents" and args.get("action") == "save_routine":
        # RET-2: a save confirms the routine's SHAPE — the human
        # approves the name, the schedule, and the FULL step list
        # (tool + redacted args per step), never a name alone. The
        # renderer lives in scheduled_agents (lazy import: this
        # module is imported by the registry at startup).
        # Saving over an existing name must say so: the view
        # carries a "replaces" note when the name is already saved
        # (read at confirm time; the handler still reports
        # "updated" in its result).
        from core.scheduled_agents import (
            render_routine_steps_for_confirmation as _render_steps)
        view = {
            "routine": str(args.get("name") or ""),
            "schedule": str(args.get("schedule") or "(none)"),
            "steps": _render_steps(args.get("steps")),
        }
        try:
            from core.brain import planner_routines as _proutines
            if any(r.get("name") == view["routine"]
                   for r in _proutines.get_routines()):
                view["replaces"] = ("this replaces '%s'"
                                    % view["routine"])
        except Exception:  # noqa: BLE001 — no read, no note; the
            pass  # handler still reports the overwrite.
        return view
    return args


def _send_key_event(vk_code: int):
    # GAPS-17: was two deprecated keybd_event calls — the last
    # input dispatch in the funnel outside the shared hub. A
    # media/volume key is a plain VK down/up, exactly what the
    # hub's _sendinput_key sends; same wire events. The hub also
    # answers False (instead of raising) off Windows, so this
    # helper is no longer unguarded — though its only caller
    # (_tool_adjust_volume) already guards on sys.platform.
    from ..desktop.cursor_input import _sendinput_key

    _sendinput_key(vk_code)
    _sendinput_key(vk_code, key_up=True)
from .tools_gate_replay import (REPLAY_MAX_STEPS, _REPLAY_FORBIDDEN_ARG_KEYS, replay_program_error, replay_trace_error)  # refactor P1 step 3 (replay concern, moved verbatim)
from .tools_gate_netlane import (CURL_CFFI_LANE_NAME, CURL_CFFI_LANE_CLASS, CURL_CFFI_IMPERSONATE_ALLOWLIST, CURL_CFFI_COOKIE_SOURCE, curl_cffi_lane_class, curl_cffi_lane_needs_confirmation, playwright_cookies_ok)  # refactor P1 step 3 (netlane concern, moved verbatim)
from .tools_gate_trial import (TRIAL_BLOCKED_TOOL_FRAGMENTS, trial_tool_blocked, windows_mcp_telemetry_preset_ok)  # refactor P1 step 3 (trial concern, moved verbatim)

