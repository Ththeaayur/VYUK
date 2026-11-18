"""Wellbeing suggestions as live tools.

Triage Wire pattern, as in core/firstopen_tools.py:
this coordinator imports the wellbeing module, registers
tools via register_all(registry), each handler takes the
named schema fields, forwards to the module, and returns
the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become
an error JSON — handlers never raise.

Wiring (Wave 9 Build 1, WELL — wellbeing, human-first,
sensing-free suggestions only):
  * well_break_plan — read-only. It returns a break-plan
    descriptor from caller-supplied minutes. A suggestion
    the user asked for, never a prescription, and it
    claims no health benefit.
  * well_reminder — read-only. It returns a reminder
    descriptor from caller-supplied words. It schedules
    NOTHING by itself — scheduling is the caller's
    action, and the descriptor says so.
  * well_winddown — read-only. It returns a wind-down
    routine from caller-chosen steps, each from the fixed
    non-medical list. An unknown step refuses the whole
    routine.
  * well_day_summary — read-only. It totals caller-supplied
    numbers only. Nothing here was measured, sensed, or
    inferred, and the summary says so. Totals only — no
    average, no score, no rating.
  * well_record_plan — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the plan
    itself (it never trusts a caller-supplied plan JSON,
    which could claim anything) and writes only the plan
    record to a caller-chosen .json file in an
    already-existing folder — no folder creation, no
    overwrite of a core/data path, no .env/.pem/.key
    target. The funnel refuses it without a human confirm
    handler, before the handler runs. Recording a plan
    schedules nothing, measures nothing, and prescribes
    nothing — the record is a suggestion the user chose.

This coordinator is NOT a health instrument: it imports
no camera, microphone, keyboard, or sensor code, starts
no job, schedules no notification, and measures nothing.
No diagnosis, no mood inference, no biometric input or
output, and no prevention or treatment claim is built or
wired here; any such input refuses whole in the module.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import well as well_mod


def _error(reason: str) -> str:
    return json.dumps({"ok": False, "reason": reason})


def _handler(fn, *args, **kwargs):
    try:
        return json.dumps(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")


def _parse_json(text, field):
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, f"{field} is not valid JSON: {exc}"
    return value, None


def _int_field(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number")
    return value


def well_break_plan_handler(work_minutes, break_minutes, focus_minutes=25):
    return _handler(well_mod.build_break_plan, work_minutes,
                    break_minutes, focus_minutes)


def well_reminder_handler(trigger, time_text):
    return _handler(well_mod.build_reminder, trigger, time_text)


def well_winddown_handler(steps_json):
    steps, err = _parse_json(steps_json, "steps_json")
    if err:
        return _error(err)
    return _handler(well_mod.build_winddown, steps)


def well_day_summary_handler(work_minutes, breaks_taken, break_minutes_total):
    return _handler(well_mod.summarize_day, work_minutes,
                    breaks_taken, break_minutes_total)


def _check_output_path(output_path):
    try:
        target = Path(output_path).expanduser()
        if target.suffix != ".json":
            return None, "output_path must end in .json"
        if not target.parent.is_dir():
            return None, "output_path parent folder must already exist"
        if target.name in (".env", "assistant.db") \
                or target.name.endswith((".pem", ".key")):
            return None, "credential target refused"
        tree = Path(__file__).resolve().parent.parent
        try:
            resolved = target.resolve()
            if resolved.is_relative_to(tree / "core") \
                    or resolved.is_relative_to(tree / "data"):
                return None, "app core/data paths refused"
        except (OSError, ValueError):
            return None, "output_path cannot be resolved"
    except Exception as exc:  # noqa: BLE001
        return None, f"output_path cannot be resolved: {exc}"
    return target, None


def well_record_plan_handler(plan_kind, plan_json, output_path):
    fields, err = _parse_json(plan_json, "plan_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("plan_json must be a JSON object")
    try:
        rebuilt = well_mod.validate_recordable_plan(plan_kind, fields)
    except (ValueError, TypeError) as exc:
        return _error(f"plan does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "WELLBEING_PLAN",
        "plan_kind": plan_kind.strip(),
        "plan": rebuilt,
        "scheduled": False,
        "measured": False,
        "note": ("A wellbeing suggestion the user chose, "
                 "recorded only. Recording schedules nothing, "
                 "measures nothing, and prescribes nothing, "
                 "and claims no health benefit."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "WELLBEING_PLAN",
                       "executed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "scheduled": False,
                       "note": "Plan recorded; nothing was "
                               "scheduled, measured, or started."})


def register_all(registry) -> None:
    """Register the 5 wellbeing tools."""
    registry.register(
        name="well_break_plan",
        description="SUGGESTION ONLY, NEVER A PRESCRIPTION: return a break-plan descriptor from caller-supplied work minutes, break minutes, and focus-stretch minutes — work in focus stretches with a rest moment between them, then the break. The plan is a suggestion the user asked for, claims no health benefit, and takes a break whenever the user needs one, not only when the plan says so. No diagnosis, no mood inference, no sensing, no scheduling.",
        parameters={"type": "object", "properties": {
            "work_minutes": {"type": "integer",
                             "description": "Caller-supplied work-block minutes"},
            "break_minutes": {"type": "integer",
                              "description": "Caller-supplied break minutes"},
            "focus_minutes": {"type": "integer",
                              "description": "Caller-supplied focus-stretch minutes, default 25"},
        }, "required": ["work_minutes", "break_minutes"]},
        handler=well_break_plan_handler,
    )
    registry.register(
        name="well_reminder",
        description="WORDS ONLY, NOTHING SCHEDULED: return a reminder descriptor from caller-supplied trigger words and a time — the reminder wording, in plain words. This tool schedules nothing by itself; scheduling is the caller's action, and the descriptor says so. Any diagnosis, mood, biometric, or treatment-claim wording refuses the whole call.",
        parameters={"type": "object", "properties": {
            "trigger": {"type": "string",
                        "description": "Caller-supplied reminder trigger words"},
            "time_text": {"type": "string",
                          "description": "Caller-supplied time words"},
        }, "required": ["trigger", "time_text"]},
        handler=well_reminder_handler,
    )
    registry.register(
        name="well_winddown",
        description="ROUTINE ONLY, FIXED NON-MEDICAL STEPS: return a wind-down routine from caller-chosen steps, each from the fixed non-medical list (dim the lights, stretch gently, step away from the screen, drink some water, take a few slow breaths, tidy one small thing, put the phone in another room), in the order given. An unknown step refuses the whole routine. The steps are plain things to do, not treatments, and claim no health benefit.",
        parameters={"type": "object", "properties": {
            "steps_json": {"type": "string",
                           "description": "JSON list of caller-chosen step names from the fixed list"},
        }, "required": ["steps_json"]},
        handler=well_winddown_handler,
    )
    registry.register(
        name="well_day_summary",
        description="TOTALS ONLY, NOTHING MEASURED: total caller-supplied day numbers — work minutes, breaks taken, break minutes — and return the totals. The numbers are the numbers the caller told it; nothing here was measured, sensed, or inferred. No average, no score, no rating, no mood, no health claim of any kind. A missing number refuses the whole summary.",
        parameters={"type": "object", "properties": {
            "work_minutes": {"type": "integer",
                             "description": "Caller-supplied work minutes"},
            "breaks_taken": {"type": "integer",
                             "description": "Caller-supplied number of breaks taken"},
            "break_minutes_total": {"type": "integer",
                                    "description": "Caller-supplied total break minutes"},
        }, "required": ["work_minutes", "breaks_taken", "break_minutes_total"]},
        handler=well_day_summary_handler,
    )
    registry.register(
        name="well_record_plan",
        description="RECORD ONLY, NOTHING SCHEDULED, NOTHING MEASURED: re-validate a caller-supplied wellbeing plan (break, reminder, or wind-down) by re-running the builder that made it, and write only the plan record to a caller-chosen .json file in an already-existing folder. A plan that does not re-build is refused, never written on trust. Recording schedules nothing, measures nothing, and prescribes nothing. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "plan_kind": {"type": "string",
                          "description": "One of break, reminder, winddown"},
            "plan_json": {"type": "string",
                          "description": "JSON object of the plan fields to re-validate"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["plan_kind", "plan_json", "output_path"]},
        handler=well_record_plan_handler,
    )
