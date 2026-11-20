"""
Mind pack for VYUK (assistant-style planning): multi-step plans and
saved routines, as plain functions returning human-readable strings.

Plans live in data/plans.json, routines in data/routines.json —
plain JSON files next to data/activity.log, so a human can read or
edit them with any text editor. Persistence is corruption-tolerant:
a missing file simply reads as "empty", never a crash, and saves
are atomic-ish (write to a temp file, then replace). A corrupt
FILE is different from a missing one: it reads as empty too, but
the damage is disclosed (log line + a note in the list/save
results) and the next save sets the damaged file aside first —
never silently replaced, never deleted (round-3 C3-F3).
Corrupt ENTRIES are tolerated the same way: _load_plans() /
_load_routines() validate every record at the load boundary and
skip the malformed ones, disclosing how many were ignored — one
bad hand-edit can never take the plan/routine surface down.

SAFETY: routines execute through the exact same tools.execute()
path as a live Gemini tool call — same activity log, same trust
layer in core/desktop/control.py. This module NEVER injects confirm=True
and never bypasses a refusal: if a routine step is refused or
errors, the routine HALTS at that step and says why.

Schedules (v2.1, Presence pack): a routine record may also carry a
"schedule" dict + "schedule_text" (the original string), parsed by
parse_schedule() and interpreted by next_run(). The schedule field
is NOT part of the save_routine tool schema in core/funnel/tools_registry.py (that
schema declares only name + actions, and the registry calls handlers
with schema kwargs only) — save_routine() accepts it as an optional
Python-level parameter for the Settings GUI / scheduler / direct
callers, and preserves it when a routine is re-saved without one.
core/brain/routine_scheduler.py is what actually fires scheduled routines.

Standard library only, plus one funnel import (core.funnel.tool_result,
itself stdlib-only and cycle-free); imports cleanly on any OS.
"""

import datetime
import inspect
import json
import os
import re
import tempfile
import time
from pathlib import Path

from ..state_bus_producers import (
    emit_step,
    emit_tool_finished,
    emit_tool_started,
)
from ..funnel.tool_result import REFUSAL_MARKERS as _REFUSAL_MARKERS

# INTENTIONAL (skills split B40, updated B41): storage +
# load-boundary validation family lives in
# core/brain/planner_storage.py; re-exported here so every
# importer and every facade caller keeps resolving the same
# objects. _load_plans/_load_routines moved to storage in B41
# (B23 live-read of the facade paths); re-exported here too.
from .planner_storage import (
    MAX_PLAN_STEPS,
    MAX_ROUTINE_ACTIONS,
    _DATA_DIR,
    _PLANS_PATH,
    _ROUTINES_PATH,
    _conditional_fields_valid,
    _damaged_note,
    _json_args,
    _load_list,
    _load_plans,
    _load_records,
    _load_routines,
    _normalise_on_fail,
    _normalise_when,
    _save_list,
    _short,
    _valid_plan,
    _valid_routine,
    _now,
)

# INTENTIONAL (skills split B41): plans family lives in
# core/brain/planner_plans.py; re-exported here.
from .planner_plans import (
    _normalise_check,
    check_result,
    _EXPECT_STRIKE_LIMIT,
    _runtime_when,
    _runtime_on_fail,
    _expect_text,
    _normalise_verdict,
    _verify_sync,
    _verify_async,
    _record_expect_verdict,
    _probe_snapshot,
    _execute_tool,
    _known_tools,
    _is_unknown_outcome,
    _failure_reason,
    _eval_when,
    _parse_steps,
    _next_plan_id,
    _find_plan,
    _progress,
    create_plan,
    list_plans,
    get_plan,
    _RESUMABLE_STATUSES,
    plan_progress,
    _set_step_status,
    complete_step,
    skip_step,
    update_plan_step,
    delete_plan,
    # Round-3 fix #4 (T12): the funnel-tool handler for running a
    # saved plan (core/brain/planner_plans.run_plan_tool).
    run_plan_tool,
    _execution_succeeded,
)

# INTENTIONAL (skills split B41): routines family lives in
# core/brain/planner_routines.py; re-exported here.
from .planner_routines import (
    _parse_actions,
    _find_routine,
    get_routines,
    _SCHEDULE_HINT,
    _parse_hhmm,
    parse_schedule,
    _schedule_suffix,
    next_run,
    _save_provenance,
    save_routine,
    list_routines,
    run_routine,
    # Fix 9: the voice path's run_routine tool now routes
    # through the coordinator (run_routine_as_task); the
    # legacy direct loop stays for nested fallbacks (B4:
    # goto routines run as tasks now, only nesting falls
    # back).
    run_routine_as_task,
    delete_routine,
)

# INTENTIONAL (skills split B41): checkpoint family lives in
# core/brain/planner_checkpoint.py; re-exported here.
from .planner_checkpoint import (
    verify_steps_against_live_tools,
    execute_step_with_reflection,
    run_steps_checkpointed,
    resume_checkpointed_run,
    run_routine_checkpointed,
    observe_tool_execution,
    _failsafe_tripped,
    run_plan,
)

# INTENTIONAL (skills split B41): goalhelpers family lives in
# core/brain/planner_goalhelpers.py; re-exported here.
from .planner_goalhelpers import (
    _goal_budget,
    _goal_budget_int,
    _goal_budget_seconds,
    _epoch_stamp,
    _epoch_changed,
    _maybe_await,
    _default_observe,
    _observation_changed,
    _ladder_hint,
    _parse_goal_action,
    _REFERENCE_WORDS,
    _CONTROL_TOOLS,
    BRIEFING_CONTEXT_FIELD,
    _BRIEFING_CONTEXT_CAP,
    _BRIEFING_CONTEXT_FRAME,
    _picker_observation,
    _j1_context,
    _reference_in_action,
    _apply_j1,
    _control_snapshot_for,
    _control_gate,
)

# INTENTIONAL (skills split B41): rungoal family lives in
# core/brain/planner_rungoal.py; re-exported here.
from .planner_rungoal import (
    run_goal,
    resume_goal,
)
