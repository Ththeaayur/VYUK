"""Conversion pass — Wave 7 (v5.62) wiring coordinator.

Wires the staged Wave 7 modules into live paths WITHOUT new tool
declarations, WITHOUT a VERSION bump, WITHOUT a requirements.txt
change, WITHOUT touching the Phase 0 files, and WITHOUT touching
core/w8_wiring.py. Every seam here keeps the staged module's own
safety shape, and every gate below fails closed:

* Anticipation (AN-1/AN-2/PF-14): the watcher engine's live
  foreground feed (names only, already live as N13) is offered to
  the CA-1 capture session, which REFUSES it until a live
  per-session opt-in AND an indicator kind are granted — the
  session's own gate, never bypassed, never conflated with the
  N13 foreground_enabled seam. The predictor's silence floor is
  INVIOLABLE: suggestion() returns None below the evidence
  floor (no history, <5 scored samples, confidence <0.70) and
  the card poll below raises nothing in that case — silence,
  never a hedged card, never proactive speech. A card that DOES
  fire carries the real launch_application tool for the predicted
  app token, through the funnel's confirm gate, and it is never
  auto-acted (its class is not on the auto-act whitelist).
  PF-14 routines run ONLY through an injected executor; with
  none injected they plan only. The step-text -> tool resolution
  a production routine executor would need is OWED (see below).
* Reach, per surface, clamps intact: Apprise pin >=1.0,<2.0
  enforced (2.x refused) and only the caller's own per-service
  configuration; Home Assistant consume-only, LAN-only (the
  module's strict LAN set), expose-new OFF + per-room allowlist,
  request validated but NOT executed by this layer; HM-2
  toolbox-routing only (95+ tools never become declarations —
  declaration_surface() stays empty; filesystem/YAML flags OFF
  in the default profile). RB-6: injected-executor-only +
  per-joint clamps + 5.0 s dead-man, and the transport NEVER
  opens a port (ArmBridge has no raw-write method — kept that
  way). Bench calibration is OWED before ANY live arm movement
  (J1-J3 spans are conservative worker defaults; only J4 0..101
  is grounded) — this layer wires the validation path only and
  never performs a live move. PA-7 stays PILOT ONLY (never a
  sole gate; NER leg absent, OCR leg has no winner) and is NOT
  wired as a gate here.
* Daily, per surface: quiet hours gate the watcher engine's
  proactive notify path (WatcherEngine._emit holds, never
  drops, during quiet hours; alarms are exempt by construction
  — they never travel _emit — and the window persists across
  restarts via the caller's store file). Alarms tick on the
  live RoutineScheduler tick (first tick runs the missed
  start-up pass: missed alarms are REPORTED, never dropped and
  never fired late). SOS only with a caller-supplied sender —
  with none, or with an unknown status, the announcement is
  honest FAILURE, never success. Meeting notes ONLY behind
  MeetingGate (S-6 opt-in + indicator + denylist, no
  exceptions). Breach hash-prefix machinery ONLY — the live
  check is GATED on source terms (currently REFUSED) and no
  live check exists in this layer. Money paperwork is local-
  only organisation + discrepancy flagging (PNR redacted as a
  secret before any record is stored). Rail booking + portal
  login stay REFUSED BY DESIGN and are NOT wiring targets:
  their refusal functions are exposed only so callers get the
  refusal, never a booking or a login.
* Presence lock: caller seam ONLY. core/presence.py is NOT a
  presence signal (it is auto-start + single-instance); the
  real signal in this tree is the watchers' input-idle probe
  (Windows GetLastInputInfo). The poller below uses that probe
  as its default signal, but it is DISABLED by default: no
  live tick locks anything until enable_presence_lock() is
  called (owner decision owed — see PRESENCE_ACTIVATION_OWED).
  The lock itself refuses without a signal, never locks blind,
  is lock-ONLY (no unlock function exists), and still needs
  Windows validation.

GATED — deliberately NOT wired here, and no function below
touches them: live breach checking (source terms REFUSED) and
Q2 commercial flight/ship data (owner's ruling owed).

This module imports no brain, no tool registry, and no GUI at
module level (those are lazy, inside functions), so importing
it can never create a cycle or a live side effect. It declares
no tool and adds zero fitted bytes.
"""

from __future__ import annotations  # INTENTIONAL (B13 split): old module carried it (B1 lesson).

# INTENTIONAL (B13 split): leaked stdlib imports re-exported so the
# facade namespace matches the old module's (files3/files2 lesson).
import datetime  # noqa: F401  (re-export)
import json  # noqa: F401  (re-export)
import urllib.request  # noqa: F401  (re-export)
from pathlib import Path  # noqa: F401  (re-export)
from typing import Any, Callable, Optional  # noqa: F401  (re-export)

# INTENTIONAL (B13 split): sibling-module imports re-exported (the old
# module bound these names; same module objects, no behaviour change).
from . import anticipation  # noqa: F401  (re-export)
from . import daily  # noqa: F401  (re-export)
from . import reach  # noqa: F401  (re-export)

# INTENTIONAL (B13 split): re-exports from .w7_wiring_constants.
from .w7_wiring_constants import (
    WIRING_VERSION,
    ANTICIPATION_OPT_IN_OWED,
    APPRISE_CONFIG_OWED,
    HA_EXECUTION_OWED,
    HAMCP_EXECUTION_OWED,
    SOS_SENDER_OWED,
    MEETING_TRANSCRIPT_OWED,
    MONEY_ENTRY_OWED,
    PRESENCE_ACTIVATION_OWED,
    DAILY_ADD_TRIGGER_OWED,
)

# INTENTIONAL (B13 split): re-exports from .w7_wiring_anticipation.
from .w7_wiring_anticipation import (
    _PREDICTOR,
    _CAPTURE_SESSION,
    _LAST_FG_NAME,
    _ROUTINE_STORE,
    anticipation_predictor,
    anticipation_capture_session,
    grant_anticipation_opt_in,
    revoke_anticipation_opt_in,
    reset_anticipation,
    note_foreground_app,
    anticipation_suggestion,
    poll_anticipation_suggestion,
    mine_frequent_patterns,
    routine_store,
    plan_routine,
    run_routine,
)

# INTENTIONAL (B13 split): re-exports from .w7_wiring_notify.
from .w7_wiring_notify import (
    RB6_CALIBRATION_OWED,
    notify_via_apprise,
    home_assistant_request,
    home_assistant_scope_proof,
    route_hamcp_tool,
    hamcp_declaration_surface,
    validate_arm_move,
    arm_bridge,
    arm_status,
    redaction_gate_status,
)

# INTENTIONAL (B13 split): re-exports from .w7_wiring_daily.
from .w7_wiring_daily import (
    _DAILY_STORE_NAME,
    daily_store_path,
    _DAILY_STORE_OVERRIDE,
    configure_daily_store,
    quiet_hours,
    set_quiet_window,
    clear_quiet_window,
    speak_allowed,
    notify_allowed,
    quiet_gate_for_watchers,
    _DAILY_STARTUP_DONE,
    _LAST_DAILY_TICK,
    _DAILY_ON_FIRE,
    _DAILY_ON_MISSED,
    register_daily_alarm_handlers,
    reset_daily_state,
    last_daily_tick_report,
    add_daily_alarm,
    list_daily_alarms,
    dismiss_daily_alarm,
    snooze_daily_alarm,
    cancel_daily_alarm,
    daily_tick,
)

# INTENTIONAL (B13 split): re-exports from .w7_wiring_misc.
from .w7_wiring_misc import (
    BREACH_TERMS_GATE,
    send_sos_via,
    meeting_notes_for,
    detect_meeting_for,
    breach_prefix_for,
    breach_live_check_refused,
    record_money_event_local,
    organise_money_document,
    flag_money_discrepancies,
    refuse_portal_login_for,
    refuse_rail_booking_for,
)

# INTENTIONAL (B13 split): re-exports from .w7_wiring_presence.
from .w7_wiring_presence import (
    PRESENCE_WINDOWS_VALIDATION_OWED,
    _PRESENCE_IDLE_PRESENT_SECONDS,
    _PRESENCE_LOCK,
    _PRESENCE_ENABLED,
    presence_signal_from_idle,
    _idle_probe_signal,
    presence_lock,
    enable_presence_lock,
    disable_presence_lock,
    presence_enabled,
    reset_presence,
    poll_presence_lock,
)
