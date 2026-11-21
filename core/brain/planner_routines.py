# planner_routines.py — planner family split out of
# core/brain/planner.py (skills split B41, part 2 of 2).
# Bodies below are AST-verbatim from the old module; only the
# import block above is new. Cross-family names are imported
# with INTENTIONAL comments, never redefined.

import datetime
import json
import re
import time
from .planner_storage import (
    MAX_ROUTINE_ACTIONS,
    _ROUTINES_PATH,
    _damaged_note,
    _load_routines,
    _normalise_on_fail,
    _normalise_when,
    _now,
    _save_list,
    _short,
    corrupt_note,
    take_quarantine_notice,
)
# INTENTIONAL (skills split B41): plans family lives in
# core/brain/planner_plans.py; imported, never redefined.
from .planner_plans import (
    _EXPECT_STRIKE_LIMIT,
    _eval_when,
    _expect_text,
    _normalise_check,
    _probe_snapshot,
    _runtime_on_fail,
    _runtime_when,
    _verify_sync,
    check_result,
)
from ..funnel.tool_result import is_refusal_text as _is_refusal_text




# ---------------------------------------------------------------------------
# Routines
# ---------------------------------------------------------------------------

def _parse_actions(actions) -> tuple[list[dict], str | None]:
    """Normalise routine actions: a JSON string (preferred — Gemini
    passes strings reliably) or a list of {tool, args} dicts.
    Returns (actions, error_or_None). Tool existence is NOT checked
    here — names are validated at run time against the live registry."""
    if actions is None:
        return [], "No actions were given."
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except Exception as e:  # noqa: BLE001
            return [], f"Actions must be a JSON list of {{\"tool\": ..., \"args\": {{...}}}} objects — could not parse: {e}"
    if not isinstance(actions, list) or not actions:
        return [], "Actions must be a non-empty list of {\"tool\": ..., \"args\": {...}} objects."
    if len(actions) > MAX_ROUTINE_ACTIONS:
        return [], f"Too many actions: {len(actions)}. A routine is capped at {MAX_ROUTINE_ACTIONS} actions."
    parsed: list[dict] = []
    for i, item in enumerate(actions):
        if not isinstance(item, dict):
            return [], f"Action {i + 1} is not an object: {item!r}"
        tool = item.get("tool")
        if not tool or not isinstance(tool, str):
            return [], f"Action {i + 1} has no 'tool' name."
        args = item.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return [], f"Action {i + 1} ('{tool}') has non-object 'args'."
        action = {"tool": tool.strip(), "args": args}
        check = _normalise_check(item)
        if check:
            action["check"] = check
        # v5.49 (programme items 31 + 32): free-text expect and
        # when/on_fail conditionals, same contract as plan steps —
        # invalid conditional values reject the save at parse.
        expect_text = _expect_text(item)
        if expect_text:
            action["expect"] = expect_text
        when, when_error = _normalise_when(item.get("when"))
        if when_error:
            return [], f"Action {i + 1}: {when_error}"
        if when:
            action["when"] = when
        on_fail, fail_error = _normalise_on_fail(item.get("on_fail"))
        if fail_error:
            return [], f"Action {i + 1}: {fail_error}"
        if on_fail is not None:
            action["on_fail"] = on_fail
        parsed.append(action)
    # v5.49 (programme item 32): a goto must land inside the routine.
    for i, action in enumerate(parsed):
        on_fail = action.get("on_fail")
        if isinstance(on_fail, dict) and not 1 <= on_fail["goto"] <= len(parsed):
            return [], (f"Action {i + 1} has on_fail goto {on_fail['goto']}, "
                        f"but the routine has only {len(parsed)} action(s).")
    return parsed, None



def _find_routine(routines: list, name) -> dict | None:
    wanted = str(name).strip().lower()
    for r in routines:
        if str(r.get("name", "")).lower() == wanted:
            return r
    return None



def get_routines() -> list:
    """Raw routine records (a list of dicts, as stored in
    data/routines.json) — for core/brain/routine_scheduler.py and the GUI,
    which need the schedule fields list_routines() only summarises.
    Damaged records (see _valid_routine) are excluded: a malformed
    record must never reach the scheduler's firing loop either."""
    routines, _damaged = _load_routines()
    return routines



# ---------------------------------------------------------------------------
# Routine schedules (v2.1) — parse_schedule() / next_run() are pure
# functions; core/brain/routine_scheduler.py builds its firing loop on them.
# ---------------------------------------------------------------------------

_SCHEDULE_HINT = (
    "Use 'daily HH:MM' (e.g. 'daily 07:30'), 'interval 45m' / "
    "'interval 2h', or 'once YYYY-MM-DD HH:MM' (e.g. 'once 2026-10-25 07:30')."
)



def _parse_hhmm(text: str) -> str:
    """Validate an 'HH:MM' string, returning it normalised. Raises ValueError."""
    try:
        parsed = datetime.datetime.strptime(text, "%H:%M")
    except ValueError:
        raise ValueError(f"'{text}' is not a valid time of day. {_SCHEDULE_HINT}") from None
    return parsed.strftime("%H:%M")



def parse_schedule(text) -> dict | None:
    """Parse a schedule string into a schedule dict.

    Accepts (case-insensitive, extra whitespace tolerated):
      "" or None            -> None (no schedule)
      "daily HH:MM"         -> {"kind": "daily", "time": "07:30"}
      "interval 45m" / "interval 2h"
                            -> {"kind": "interval", "minutes": 45 / 120}
      "once YYYY-MM-DD HH:MM"
                            -> {"kind": "once", "at": "2026-10-25 07:30"}
    Anything else raises ValueError with a human-readable message —
    callers (save_routine) catch it and turn it into a refusal string,
    so a typo can never silently save an unscheduled routine.
    """
    if text is None:
        return None
    cleaned = " ".join(str(text).strip().lower().split())
    if not cleaned:
        return None
    head, _, rest = cleaned.partition(" ")
    rest = rest.strip()
    if head == "daily":
        return {"kind": "daily", "time": _parse_hhmm(rest)}
    if head == "interval":
        match = re.fullmatch(r"(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)", rest)
        if not match:
            raise ValueError(f"'{text}' is not a valid interval. {_SCHEDULE_HINT}")
        amount = int(match.group(1))
        minutes = amount * 60 if match.group(2).startswith("h") else amount
        if minutes <= 0:
            raise ValueError(f"An interval must be at least 1 minute. {_SCHEDULE_HINT}")
        return {"kind": "interval", "minutes": minutes}
    if head == "once":
        try:
            moment = datetime.datetime.strptime(rest, "%Y-%m-%d %H:%M")
        except ValueError:
            raise ValueError(f"'{text}' is not a valid one-off date/time. {_SCHEDULE_HINT}") from None
        return {"kind": "once", "at": moment.strftime("%Y-%m-%d %H:%M")}
    raise ValueError(f"Could not understand the schedule '{text}'. {_SCHEDULE_HINT}")



def _schedule_suffix(routine: dict) -> str:
    """' — runs daily at 07:30' style suffix for list_routines()."""
    sched = routine.get("schedule") or {}
    kind = sched.get("kind")
    if kind == "daily":
        return f" — runs daily at {sched.get('time')}"
    if kind == "interval":
        minutes = sched.get("minutes", 0)
        every = f"{minutes // 60}h" if minutes and minutes % 60 == 0 else f"{minutes}m"
        return f" — runs every {every}"
    if kind == "once":
        return f" — runs once at {sched.get('at')}"
    return ""



def next_run(routine: dict, now: datetime.datetime,
             last_run: datetime.datetime | None = None) -> datetime.datetime | None:
    """The next slot at which `routine` should fire, or None.

    Pure. `last_run` is when the routine last fired (None = never).
    Edge semantics, by kind:

    - daily "HH:MM": today's slot if `now` is before it. If `now` is
      at/past today's slot: the slot itself when it hasn't fired yet
      (last_run is None or before the slot) — i.e. the routine is DUE
      and the returned datetime is in the past, which is how the
      scheduler (and a "next run" display) can tell; tomorrow's slot
      when last_run is at/after today's slot (already fired today).
    - interval N minutes: last_run + N. With last_run=None there is no
      anchor in the past, so the anchor is `now` itself and the first
      fire is one interval from now — the scheduler passes its own
      start time as last_run for never-run routines so "now" means
      "when the scheduler started", not "whenever this is called".
    - once "YYYY-MM-DD HH:MM": that moment, unless it already fired
      (last_run at/after it) — then None, forever. A past, never-fired
      once is returned as-is (due immediately).

    A routine with no (or an unparseable) schedule returns None.
    """
    sched = (routine or {}).get("schedule") if isinstance(routine, dict) else None
    if not isinstance(sched, dict):
        return None
    kind = sched.get("kind")
    if kind == "daily":
        try:
            hour, minute = (int(part) for part in str(sched.get("time", "")).split(":"))
            slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (TypeError, ValueError):
            return None
        if now < slot:
            return slot
        if last_run is not None and last_run >= slot:
            return slot + datetime.timedelta(days=1)
        return slot
    if kind == "interval":
        try:
            minutes = int(sched.get("minutes", 0))
        except (TypeError, ValueError):
            return None
        if minutes <= 0:
            return None
        anchor = last_run if last_run is not None else now
        return anchor + datetime.timedelta(minutes=minutes)
    if kind == "once":
        try:
            moment = datetime.datetime.strptime(str(sched.get("at", "")), "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        if last_run is not None and last_run >= moment:
            return None
        return moment
    return None



def _save_provenance() -> dict:
    """The provenance record stored with a routine at save time
    (v5.47, programme item 13, C3).

    A routine is stored instructions that can fire headless
    later, so WHO saved it and in WHAT session state matters
    when it fires. This records only what the save path can
    honestly see from where it sits, read lazily at save time
    (module-level imports of the funnel registry / core.injection would
    be circular — the funnel registry imports this module at registration):

      * saved_at — this module's own timestamp (_now()).
      * via — always "save_routine": the handler/function name,
        the one entry fact every caller shares.
      * entry — "tool_execute" when the save arrived through
        ToolRegistry.execute() (core.injection's in-flight
        counter is non-zero while a handler runs — the executor
        brackets every handler call with begin/end_execution),
        else "direct_call" (the Settings GUI's editor and direct
        Python callers invoke this function themselves; a human
        at the GUI is present by construction, and those saves
        never pass the executor's new save_routine gate).
      * session_tainted / taint_kind / taint_source — the
        injection session's state at save time, so a routine
        saved while the session was tainted says so on its face.
      * session_provenance — how the live session started
        ("user" / "auto" / "unknown", core/funnel/tools_gate.py's speaker-lock
        provenance flag).

    Anything unreadable degrades to "unknown" / None / False —
    provenance must never break a save. The record is additive:
    routines saved before v5.47 simply have no "provenance" key
    and load and run exactly as before (_valid_routine does not
    require it, list_routines does not print it)."""
    provenance: dict = {
        "saved_at": _now(),
        "via": "save_routine",
        "entry": "direct_call",
        "session_tainted": False,
        "taint_kind": None,
        "taint_source": None,
        "session_provenance": "unknown",
    }
    try:
        from .. import injection as _injection

        if _injection.in_flight() > 0:
            provenance["entry"] = "tool_execute"
        provenance["session_tainted"] = bool(_injection.tainted())
        if provenance["session_tainted"]:
            provenance["taint_kind"] = _injection.taint_kind()
            provenance["taint_source"] = _injection.taint_source()
    except Exception:  # noqa: BLE001 — provenance never breaks a save
        pass
    try:
        from ..funnel import tools_gate as _tools_mod  # Phase 4 S1b INTENTIONAL: gate rebind; sole use is session_provenance() (tools_gate)

        provenance["session_provenance"] = _tools_mod.session_provenance()
    except Exception:  # noqa: BLE001 — same rule
        pass
    return provenance



def save_routine(name: str, actions=None, schedule=None) -> str:
    """Save (or overwrite) a named routine: an ordered list of tool calls.

    `schedule` is an optional schedule STRING ('daily 07:30',
    'interval 45m', 'once 2026-10-25 07:30' — see parse_schedule()).
    It is deliberately not part of the save_routine tool schema in
    core/funnel/tools_registry.py, so Gemini-driven saves never set one; the GUI /
    scheduler / direct callers pass it here. An unparseable schedule
    refuses the whole save. Re-saving WITHOUT a schedule preserves
    any schedule already stored on the routine (so a voice-driven
    re-save can't silently unschedule it); there is no ""-means-clear
    shortcut — remove the fields from data/routines.json to clear one.

    v5.47 (programme item 13): every save also stores a fresh
    "provenance" record on the routine (see _save_provenance) —
    timestamp, entry point and the injection/session state the
    save path could honestly see. Re-saving replaces the record
    (it describes THIS save, not the routine's first creation).
    """
    name = str(name or "").strip()
    if not name:
        return "Cannot save a routine without a name."
    parsed, error = _parse_actions(actions)
    if error:
        return f"Could not save routine '{name}': {error}"
    schedule_dict = None
    if schedule is not None and str(schedule).strip():
        try:
            schedule_dict = parse_schedule(schedule)
        except ValueError as e:
            return f"Could not save routine '{name}': {e}"
    routines, damaged = _load_routines()
    existing = _find_routine(routines, name)
    # v5.47 (programme item 13): provenance rides with the record
    # (see _save_provenance) — additive key, old files unaffected.
    record = {"name": name, "created_at": _now(), "actions": parsed,
              "provenance": _save_provenance()}
    if schedule_dict is not None:
        record["schedule"] = schedule_dict
        record["schedule_text"] = str(schedule).strip()
    elif existing is not None:
        if existing.get("schedule"):
            record["schedule"] = existing["schedule"]
        if existing.get("schedule_text"):
            record["schedule_text"] = existing["schedule_text"]
    if existing is not None:
        routines[routines.index(existing)] = record
        verb = "updated"
    else:
        routines.append(record)
        verb = "saved"
    if not _save_list(_ROUTINES_PATH, routines):
        return "Could not save the routine: failed to write data/routines.json."
    tool_list = ", ".join(a["tool"] for a in parsed)
    suffix = f" It runs on a schedule: '{record['schedule_text']}'." if record.get("schedule_text") else ""
    result = f"Routine '{name}' {verb} with {len(parsed)} action(s): {tool_list}.{suffix}"
    notes = []
    if damaged:
        notes.append(_damaged_note(damaged))
    qnote = take_quarantine_notice(_ROUTINES_PATH)
    if qnote:
        notes.append(qnote)
    return f"{result}\n" + "\n".join(notes) if notes else result



def list_routines() -> str:
    """List saved routines with action counts (and schedules, if any).

    Only well-formed records are listed and counted — a damaged
    record is skipped with a disclosure note, never reported with a
    phantom action count. A corrupt FILE is disclosed the same way
    (never "No routines saved yet" in silence)."""
    routines, damaged = _load_routines()
    cnote = corrupt_note(_ROUTINES_PATH)
    if not routines:
        base = "No routines saved yet. Create one with save_routine."
        notes = []
        if damaged:
            notes.append(_damaged_note(damaged))
        if cnote:
            notes.append(cnote)
        return f"{base}\n" + "\n".join(notes) if notes else base
    lines = [f"{len(routines)} routine(s):"]
    for r in routines:
        lines.append(f"  {r.get('name')}: {len(r.get('actions', []))} action(s){_schedule_suffix(r)}")
    if damaged:
        lines.append(_damaged_note(damaged))
    if cnote:
        lines.append(cnote)
    return "\n".join(lines)



def run_routine_as_task(name: str, verify_expect=None) -> str:
    """Run a saved routine as a coordinator task (Fix 9).

    THE GAP (proved from the tree): the voice path's
    run_routine tool called planner.run_routine directly —
    no task id, no ledger, no receipt, no verification
    phase, no retry, no plan approval, no pause/resume.
    The typed path's "run routine X" already goes through
    TurnCoordinator.handle_turn with all of that. This
    function is the voice path joining the same surface:
    ONE task surface for every multi-step run, no second
    goal loop. Steps execute through the funnel (the
    coordinator's default executor) with every gate
    intact; the plan hook is Fix 8's cross-thread ask
    (request_plan_decision — the GUI shows the plan once,
    safe steps approved together, destructive steps still
    ask one by one; no handler registered means no dialog
    and no approval, exactly as before); the task honours
    the planner's stop signals at step boundaries — a
    session restart (including a reconnect after a drop)
    PAUSES it, leaving a resumable "paused" ledger record,
    while a goal-pill Stop CANCELS it; the task is written
    to the default ledger, so a drop mid-routine leaves an
    interrupted record resumable via resume_ledger_task
    instead of an orphaned run.

    Coordinator-only semantics fall back to the legacy
    loop, honestly and by name: a nested
    run_routine/run_plan action has no coordinator
    equivalent (a nested run would recurse the task), so
    such a routine runs on planner.run_routine with a
    one-line note saying so. A goto jump DOES have a
    coordinator equivalent since B4 (StepDef accepts
    {"goto": n}; the coordinator jumps with per-step
    strikes, a global budget, and D4/C-14 refusal), so
    goto routines run as tasks here, not on the legacy
    loop. verify_expect is accepted
    for signature parity with run_routine and IGNORED
    here — the coordinator's verification phase is the
    verifier for task runs (a routine's free-text expect
    is judged there, by the phase's own verifier, with
    its own bounded retry — not by a caller callback).
    The returned string is the task receipt (same
    per-step marks as the legacy loop), so the caller
    hears what finished and what did not, never a bare
    "done".
    """
    routines, damaged = _load_routines()
    routine = _find_routine(routines, name)
    if routine is None:
        msg = f"No routine named '{name}'. Use list_routines to see saved routines."
        return f"{msg}\n{_damaged_note(damaged)}" if damaged else msg
    actions = routine.get("actions", [])[:MAX_ROUTINE_ACTIONS]
    if not actions:
        return f"Routine '{routine.get('name')}' has no actions."
    # Lazy imports (same circularity reason as run_routine's
    # registry import above): the coordinator + plan-approval
    # modules are reached through the facade at call time.
    try:
        from .task_coordinator_tail import _default_coordinator
        from .task_coordinator_steps import StepDef, format_receipt
        from .. import plan_approval as _pa
    except Exception as exc:  # noqa: BLE001
        return (f"Could not run routine '{name}': the task "
                f"coordinator is unavailable ({exc}).")

    def _plan_hook(task, _preview):
        # R5: the dict form carries the ask's outcome —
        # a "timeout" must reach the coordinator as a
        # timeout (it pauses the task), not collapse into
        # the [] that also means "ask each time".
        try:
            indices, outcome = _pa.request_plan_decision_ex(task)
            return {"approved": list(indices or []),
                    "outcome": outcome or "ask_each_time"}
        except Exception:  # noqa: BLE001 — a broken hook asks per step
            return []

    # Coordinator-only semantics: fall back to the legacy
    # loop (with the reason named) rather than failing the
    # task on a step the coordinator cannot speak. (B4: a
    # goto jump is no longer such a step — the coordinator
    # implements it, so only nesting falls back.)
    # B23 (approve-plan-once coverage): the legacy loop is a
    # voice path too, and its steps hit the same confirm
    # gate — so it gets the SAME plan dialog once for the
    # routine's steps, on a lightweight task-like (id +
    # title + steps: all the dialog and approve_plan read),
    # and the approval context rides into run_routine. A
    # timeout / no handler / error / empty approval reads
    # as no approval — the routine runs with per-step asks,
    # exactly as before this batch. The dialog is the GUI's,
    # shown not spoken (C-14: the approval itself stays
    # GUI-only).
    for action in actions:
        if str(action.get("tool", "")).strip() in ("run_routine",
                                                   "run_plan"):
            note = ("Routine runs on the legacy loop: a nested "
                    "routine/plan has no coordinator equivalent.")
            _legacy_plan = None
            try:
                from types import SimpleNamespace as _NS
                import uuid as _uuid
                _legacy_steps = []
                for _a in actions:
                    try:
                        _legacy_steps.append(StepDef.from_mapping(_a))
                    except Exception:  # noqa: BLE001 — unbuildable: asks
                        _legacy_steps.append(None)
                _legacy_task = _NS(
                    id=_uuid.uuid4().hex,
                    title=f"Routine '{routine.get('name')}'",
                    steps=_legacy_steps)
                _hook_result = _plan_hook(_legacy_task, None)
                if isinstance(_hook_result, dict):
                    _approved = _hook_result.get("approved") or []
                    _outcome = _hook_result.get("outcome") or "ask_each_time"
                else:
                    _approved, _outcome = [], "ask_each_time"
                if _outcome == "approved" and _approved:
                    _pa.approve_plan(_legacy_task.id, _legacy_steps,
                                     list(_approved))
                    _legacy_plan = (_legacy_task.id, _legacy_steps)
            except Exception:  # noqa: BLE001 — no approval: ask per step
                _legacy_plan = None
            # B20: the legacy loop returns the cancelled dict
            # itself on a stop — convert it to its message
            # here, or the string concatenation below would
            # break on a dict.
            _legacy = run_routine(name, verify_expect=verify_expect,
                                  plan_approval=_legacy_plan)
            if isinstance(_legacy, dict):
                _legacy = str(_legacy.get("message", "") or
                              _legacy.get("status", "cancelled"))
            return f"{note}\n" + _legacy

    # The coordinator comes from the ONE shared construction
    # site (task_coordinator_tail._default_coordinator — the
    # same site handle_typed_turn uses): default ledger
    # (durable — a drop leaves a resumable record) and the
    # companion pair when both legs are real (else None —
    # the second-opinion check stays OFF exactly as before).
    # This function constructs NO coordinator of its own:
    # the plumbing-blocker pin
    # (tests/test_second_opinion_plumbing_blocker.py) allows
    # exactly one production site supplying
    # second_opinion_brains, in the tail.
    coord = _default_coordinator()
    result = coord.handle_turn(
        f"run routine {name}",
        routine_names=(name,),
        routine_steps_for=lambda _n: [dict(a) for a in actions],
        plan_hook=_plan_hook,
        task_flags={"honor_stop_signals": True},
    )
    receipt = result.get("receipt")
    if receipt is None:
        return (f"Routine '{name}' did not run: "
                f"{result.get('error') or 'no receipt'}.")
    lines = [f"Running routine '{routine.get('name')}' "
             f"({len(actions)} action(s)) as a task:"]
    lines.append(format_receipt(receipt))
    return "\n".join(lines)


def run_routine(name: str, verify_expect=None, cancel_token=None,
                plan_approval=None):
    """Run a saved routine, step by step, through tools.execute().

    SAFETY: steps go through the same registry + trust layer as any
    live tool call — nothing is auto-confirmed. A step that errors,
    is refused by the trust layer, names an unknown tool, or tries to
    nest another routine HALTS the run immediately and reports why.

    v5.49 (programme items 31 + 32 — legacy contract change: the
    optional verify_expect parameter and the when/on_fail/expect
    step fields are additive; a routine without them behaves
    exactly as before): a step's `when` probe is evaluated through
    tools.execute before the step runs — a failed probe follows
    the step's on_fail (default "skip"); a failed ACTION keeps the
    halt behaviour above unless its on_fail says "skip" or
    {"goto": n}. A step's free-text `expect` is judged by the
    injected verify_expect callable (see the item-31 helpers);
    three consecutive "not-met" verdicts on one step halt the run.

    B20: ``cancel_token`` is the step's cooperative token when
    the routine runs as a coordinator step (threaded through
    _funnel_execute). It is checked between sub-steps and passed
    to each sub-step's execute, so a Stop pressed mid-routine
    ends the routine promptly. A routine stopped this way
    returns the CANCELLED funnel dict itself (not a string):
    the funnel wraps a string return as success, so returning
    the dict is what makes the coordinator record the step as
    cancelled instead of successful. Callers that concatenate
    the return (run_routine_as_task's nesting fallback) convert
    the dict to its message first.

    B23 (approve-plan-once coverage): ``plan_approval`` is an
    optional (task id, approved StepDef list) pair — the plan
    the human already approved in the GUI plan dialog, as
    recorded by core.plan_approval.approve_plan. Each step is
    matched by CONTENT against the approved steps (tool +
    full args digest, through plan_approval's arm_for_call);
    a match arms the dispatch channel so the funnel's confirm
    stage sees the plan approval instead of popping again,
    and a miss asks as today. The approval is CONSULTED,
    never re-issued, and the ``when`` probe above still runs
    without it (a probe always gates normally). With none
    supplied — the GUI button, any direct caller — behaviour
    is byte-identical.
    """
    routines, damaged = _load_routines()
    routine = _find_routine(routines, name)
    if routine is None:
        msg = f"No routine named '{name}'. Use list_routines to see saved routines."
        return f"{msg}\n{_damaged_note(damaged)}" if damaged else msg
    actions = routine.get("actions", [])[:MAX_ROUTINE_ACTIONS]
    if not actions:
        return f"Routine '{routine.get('name')}' has no actions."
    # B23: the approved plan as StepDefs, aligned with
    # ``actions`` by index — a step that fails to build
    # becomes None and never matches (fail-closed: it asks).
    # Built once, not per step: the plan does not change
    # mid-run.
    _pa_task_id = None
    _pa_steps = None
    if plan_approval is not None:
        try:
            from .task_coordinator_steps import StepDef as _StepDef
            _pa_task_id, _pa_steps_in = plan_approval
            _pa_steps = []
            for _a in actions:
                try:
                    _pa_steps.append(_StepDef.from_mapping(_a))
                except Exception:  # noqa: BLE001 — unbuildable: asks
                    _pa_steps.append(None)
        except Exception:  # noqa: BLE001 — no plan context: ask normally
            _pa_task_id, _pa_steps = None, None

    # Lazy import: the funnel registry imports this module at registration time,
    # so importing it at module level here would be circular.
    try:
        from ..funnel.tools_registry import tools as registry  # Phase 4 S1b INTENTIONAL: registry split to funnel
        known_tools = {d["name"] for d in registry.get_declarations()}
    except Exception as e:  # noqa: BLE001
        return f"Could not run routine: the tool registry is unavailable ({e})."

    def _halt(i: int, label: str, detail: str) -> str:
        lines.append(f"  ✗ {label} — halted: {detail}")
        lines.append(f"Routine stopped at step {i + 1} of {len(actions)}. Steps after it did not run.")
        return "\n".join(lines)

    lines = [f"Running routine '{routine.get('name')}' ({len(actions)} action(s)):"]
    jumps = 0
    max_jumps = len(actions) * 3 + 10
    i = 0
    while i < len(actions):
        # B20: the token is checked between sub-steps — a Stop
        # pressed while the previous sub-step ran ends the
        # routine here, before the next sub-step starts. Only
        # a user stop halts it (cancelled_by == "stop"): a
        # sub-step that TIMED OUT cancels the token with
        # reason "deadline", and that step's own on_fail
        # (skip / goto / halt) already decided what happens
        # next — the deadline must not masquerade as a stop.
        if cancel_token is not None and cancel_token.cancelled \
                and cancel_token.cancelled_by == "stop":
            from ..funnel.tool_result import cancelled_result
            return cancelled_result(
                f"Routine '{routine.get('name')}' stopped at step "
                f"{i + 1} of {len(actions)} at the user's request. "
                f"Steps after it did not run.")
        action = actions[i]
        tool = action.get("tool", "")
        args = action.get("args", {}) or {}
        label = f"Step {i + 1}/{len(actions)}: {tool}"
        if tool == "run_routine":
            lines.append(f"  ✗ {label} — halted: routines cannot run other routines.")
            lines.append(f"Routine stopped at step {i + 1} of {len(actions)}. Steps after it did not run.")
            return "\n".join(lines)
        if tool not in known_tools:
            lines.append(f"  ✗ {label} — halted: '{tool}' is not a registered tool.")
            lines.append(f"Routine stopped at step {i + 1} of {len(actions)}. Steps after it did not run.")
            return "\n".join(lines)
        on_fail, on_fail_error = _runtime_on_fail(action)
        if on_fail_error:
            return _halt(i, label, f"the step's on_fail is invalid ({on_fail_error})")
        # v5.49 (programme item 32): the `when` probe runs through
        # tools.execute (all gates intact) BEFORE the action. A
        # failed probe follows on_fail, defaulting to "skip".
        when, when_error = _runtime_when(action)
        if when_error:
            return _halt(i, label, f"the step's 'when' is invalid ({when_error})")
        if when is not None:
            passed_when, when_reason = _eval_when(when, known_tools)
            if not passed_when:
                decision = on_fail if on_fail is not None else "skip"
                if decision == "skip":
                    lines.append(f"  – {label} — skipped: 'when' condition not met ({when_reason})")
                    i += 1
                    continue
                if decision == "halt":
                    return _halt(i, label, f"'when' condition not met ({when_reason})")
                jumps += 1
                if jumps > max_jumps:
                    return _halt(i, label, "'when' condition not met and the goto jump limit was reached — refusing to loop")
                lines.append(f"  ↪ {label} — 'when' condition not met ({when_reason}); jumping to step {decision['goto']}")
                i = decision["goto"] - 1
                continue
        # Action execution. v5.49 (programme item 31): a declared
        # free-text expect is verified per attempt; "not-met"
        # retries the SAME action until the three-strikes limit.
        expect_text = _expect_text(action)
        strikes = 0
        next_i = None
        while True:
            before_snap = _probe_snapshot() if expect_text else None
            started = time.perf_counter()
            # B20: the sub-step gets the routine's token — a
            # Stop pressed while it runs ends it at its next
            # yield point. The funnel passes it ONLY to
            # handlers that declare cancel_token; every other
            # handler runs exactly as today. The token is
            # passed ONLY when supplied: test fakes of
            # registry.execute take two arguments, and an
            # unconditional third argument would break every
            # one of them (the coordinator's
            # _executor_accepts_token rule, same reason).
            # B23: arm the plan-approval channel for this
            # step when it matches an approved step by
            # content; the when-probe above deliberately
            # runs without it (a probe always gates
            # normally). The token resets right after the
            # dispatch, so it can never leak to the next
            # step, an expect retry of a different step,
            # or the snapshot below. A retry of THIS step
            # re-arms fresh on its next attempt.
            _plan_token = None
            if _pa_steps is not None:
                try:
                    from .. import plan_approval as _pa
                    _plan_token = _pa.arm_for_call(
                        _pa_task_id, _pa_steps, tool, dict(args))
                except Exception:  # noqa: BLE001 — no plan context: ask normally
                    _plan_token = None
            try:
                if cancel_token is None:
                    result = registry.execute(tool, dict(args))
                else:
                    result = registry.execute(tool, dict(args),
                                              cancel_token=cancel_token)
            finally:
                if _plan_token is not None:
                    try:
                        from .. import plan_approval as _pa2
                        _pa2.end_step_dispatch(_plan_token)
                    except Exception:  # noqa: BLE001 — teardown never breaks a step
                        pass
            elapsed = f"({time.perf_counter() - started:.1f}s)"
            after_snap = _probe_snapshot() if expect_text else None
            if result.get("status") == "cancelled":
                # B20: a sub-step stopped by the user ends the
                # whole routine as cancelled — never as a
                # failure (nothing failed), never skipped (a
                # skip would run the next step past the stop),
                # never jumped (a jump would do more work after
                # the stop). The cancelled dict is returned
                # itself so the coordinator records the step as
                # cancelled; the transcript lines above it
                # stand as the partial progress.
                from ..funnel.tool_result import cancelled_result
                lines.append(
                    f"  ⦸ {label} — cancelled at the user's request "
                    f"{elapsed}")
                lines.append(
                    f"Routine stopped at step {i + 1} of "
                    f"{len(actions)}. Steps after it did not run.")
                return cancelled_result(
                    f"Routine '{routine.get('name')}' stopped at step "
                    f"{i + 1} of {len(actions)} at the user's request: "
                    f"{result.get('message', '') or 'cancelled'}")
            if result.get("status") != "success":
                reason = result.get("message", "unknown error")
                if on_fail == "skip":
                    lines.append(f"  – {label} — skipped after failure: {reason} {elapsed}")
                    next_i = i + 1
                    break
                if isinstance(on_fail, dict):
                    jumps += 1
                    if jumps > max_jumps:
                        return _halt(i, label, f"{reason} (and the goto jump limit was reached — refusing to loop)")
                    lines.append(f"  ↪ {label} — failed: {reason} {elapsed}; jumping to step {on_fail['goto']}")
                    next_i = on_fail["goto"] - 1
                    break
                lines.append(f"  ✗ {label} — halted: {reason} {elapsed}")
                lines.append(f"Routine stopped at step {i + 1} of {len(actions)}. Steps after it did not run.")
                return "\n".join(lines)
            result_text = str(result.get("result", ""))
            if _is_refusal_text(result_text):
                # A handler REFUSAL returned as a success string (the
                # skills packs' confirm sentence, a trust-layer block):
                # the step did NOT run. Say exactly that — never let it
                # be counted among the completed steps.
                if on_fail == "skip":
                    lines.append(f"  – {label} — skipped after refusal: {_short(result_text)} {elapsed}")
                    next_i = i + 1
                    break
                if isinstance(on_fail, dict):
                    jumps += 1
                    if jumps > max_jumps:
                        return _halt(i, label, "refused (and the goto jump limit was reached — refusing to loop)")
                    lines.append(f"  ↪ {label} — refused: {_short(result_text)} {elapsed}; jumping to step {on_fail['goto']}")
                    next_i = on_fail["goto"] - 1
                    break
                lines.append(f"  ✗ {label} — refused: {_short(result_text)} {elapsed}")
                lines.append(
                    f"Routine stopped at step {i + 1} of {len(actions)}: step {i + 1} "
                    f"('{tool}') was refused, so it did not run. Steps after it did not "
                    "run either. Destructive steps are never auto-confirmed — ask the "
                    "user to confirm that step separately, then re-run."
                )
                return "\n".join(lines)
            # Result check (v5.45, B12): a declared expectation that
            # the result does not meet HALTS the routine like a
            # refusal — marching on would report a failed step as done.
            # v5.49 (programme item 32): the step's on_fail may
            # override that halt with skip/goto.
            passed, reason = check_result(action.get("check"), result)
            if not passed:
                if on_fail == "skip":
                    lines.append(f"  – {label} — skipped: result check failed ({reason}) {elapsed}")
                    next_i = i + 1
                    break
                if isinstance(on_fail, dict):
                    jumps += 1
                    if jumps > max_jumps:
                        return _halt(i, label, f"result check failed ({reason}) (and the goto jump limit was reached — refusing to loop)")
                    lines.append(f"  ↪ {label} — result check failed ({reason}) {elapsed}; jumping to step {on_fail['goto']}")
                    next_i = on_fail["goto"] - 1
                    break
                lines.append(f"  ✗ {label} — halted: result check failed ({reason}) {elapsed}")
                lines.append(
                    f"Routine stopped at step {i + 1} of {len(actions)}: step {i + 1} "
                    f"('{tool}') ran, but its result did not match the check it "
                    "declared, so the routine does not trust it as done. Steps "
                    "after it did not run.")
                return "\n".join(lines)
            # v5.49 (programme item 31): free-text expect verdict.
            # "unverifiable" (including no callback injected) keeps
            # today's behaviour exactly and never strikes.
            if expect_text:
                verdict, vreason = _verify_sync(
                    verify_expect, expect_text, before_snap, after_snap, result)
                if verdict == "not-met":
                    strikes += 1
                    if strikes < _EXPECT_STRIKE_LIMIT:
                        lines.append(f"  ↻ {label} — verification not met (strike {strikes}/{_EXPECT_STRIKE_LIMIT}): {vreason} — retrying {elapsed}")
                        continue
                    if on_fail == "skip":
                        lines.append(f"  – {label} — skipped: verification not met three times ({vreason}) {elapsed}")
                        next_i = i + 1
                        break
                    if isinstance(on_fail, dict):
                        jumps += 1
                        if jumps > max_jumps:
                            return _halt(i, label, "verification not met three times (and the goto jump limit was reached — refusing to loop)")
                        lines.append(f"  ↪ {label} — verification not met three times ({vreason}) {elapsed}; jumping to step {on_fail['goto']}")
                        next_i = on_fail["goto"] - 1
                        break
                    return _halt(i, label, f"verification not met three times (three-strikes): the step's expect was \"{expect_text}\" — {vreason}")
                lines.append(f"  ✓ {label} — {_short(result_text)} {elapsed} [expect {verdict}]")
            else:
                lines.append(f"  ✓ {label} — {_short(result_text)} {elapsed}")
            next_i = i + 1
            break
        i = next_i if next_i is not None else i + 1
    # v5.49 (programme item 32): when steps were skipped/jumped the
    # historic "all completed" line would lie — report the honest
    # tally instead. The no-skip path keeps the original line.
    skipped_lines = sum(1 for l in lines if l.strip().startswith("–"))
    if skipped_lines:
        done_lines = sum(1 for l in lines if l.strip().startswith("✓"))
        lines.append(f"Routine '{routine.get('name')}' finished: {done_lines} action(s) completed, {skipped_lines} skipped, of {len(actions)}.")
    else:
        lines.append(f"Routine '{routine.get('name')}' finished: all {len(actions)} action(s) completed.")
    return "\n".join(lines)

def delete_routine(name: str) -> str:
    """Delete a saved routine."""
    routines, damaged = _load_routines()
    routine = _find_routine(routines, name)
    if routine is None:
        msg = f"No routine named '{name}'. Use list_routines to see saved routines."
        notes = []
        if damaged:
            notes.append(_damaged_note(damaged))
        cnote = corrupt_note(_ROUTINES_PATH)
        if cnote:
            notes.append(cnote)
        # F-F501: nothing was deleted, so the verdict is failed,
        # not a plain string the receipts would read "succeeded".
        # (The identical sentence at :473/:637 belongs to the
        # run-routine path, not this delete — left alone.)
        from .. import tool_outcome as _outcome  # R1b: seam wiring
        text = f"{msg}\n" + "\n".join(notes) if notes else msg
        return _outcome.failed(text, "routine missing")
    routines.remove(routine)
    if not _save_list(_ROUTINES_PATH, routines):
        return "Could not delete the routine: failed to write data/routines.json."
    result = f"Routine '{routine.get('name')}' deleted."
    qnote = take_quarantine_notice(_ROUTINES_PATH)
    return f"{result}\n{qnote}" if qnote else result
