# Part of the core/w7_wiring.py split (skills split B13).
# Family: anticipation.

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from . import anticipation  # sibling module used by this family

# Field-check fix #15 (T10): the anticipation routine store used to be
# in-memory only — a routine created with plan_routine() vanished on
# restart. It is now persisted to data/anticipation_routines.json, a
# JSON list of {"name", "trigger", "actions"} in the planner's shape
# (atomic-ish temp-file + os.replace save, corrupt/missing file ->
# empty store, damaged entries skipped). A SEPARATE file from
# data/routines.json on purpose: the planner's file validates actions
# as a list of dicts (each with dict args), so an anticipation
# routine's plain-language string actions would read as damaged
# there and be dropped on the planner's next save.
_ANTICIPATION_ROUTINES_PATH = (
    Path(__file__).resolve().parent.parent / "data"
    / "anticipation_routines.json")

_PREDICTOR: Optional[anticipation.NextActionPredictor] = None

_CAPTURE_SESSION: Optional[anticipation.ActivityCaptureSession] = None

_LAST_FG_NAME: Optional[str] = None

_ROUTINE_STORE: Optional[anticipation.RoutineStore] = None

def anticipation_predictor() -> anticipation.NextActionPredictor:
    """The live AN-1 predictor singleton (in-memory only, like
    the module's own state — nothing is persisted)."""
    global _PREDICTOR
    if _PREDICTOR is None:
        _PREDICTOR = anticipation.NextActionPredictor()
    return _PREDICTOR

def anticipation_capture_session() -> anticipation.ActivityCaptureSession:
    """The live CA-1 capture session singleton. It starts
    REFUSING (no opt-in, no indicator) — see
    ANTICIPATION_OPT_IN_OWED."""
    global _CAPTURE_SESSION
    if _CAPTURE_SESSION is None:
        _CAPTURE_SESSION = anticipation.ActivityCaptureSession()
    return _CAPTURE_SESSION

def grant_anticipation_opt_in(indicator_kind: str = "activity_stream") -> dict:
    """Grant the CA-1 session's live per-session opt-in AND set
    its indicator kind. This is the ONLY grant path, and no live
    caller invokes it yet (opt-in owed) — calling it is always a
    deliberate decision, never a tick side effect."""
    session = anticipation_capture_session()
    session.grant_opt_in()
    session.set_indicator(indicator_kind)
    return {"opted_in": session.opted_in,
            "indicator_kind": session.indicator_kind,
            "denylist_applied": session.denylist_applied}

def revoke_anticipation_opt_in() -> dict:
    """Withdraw the CA-1 opt-in at once; emission refuses again."""
    session = anticipation_capture_session()
    session.revoke_opt_in()
    return {"opted_in": session.opted_in}

def _load_routine_entries() -> list:
    """Read the persisted anticipation routines as validated entries.

    Missing / corrupt / wrong-type file -> []. Each entry must pass
    Routine's own validation (non-empty name + trigger, at least one
    non-empty action); damaged entries are skipped, never raised on.
    """
    try:
        raw = _ANTICIPATION_ROUTINES_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 — corrupt state must never crash a load
        return []
    if not isinstance(data, list):
        return []
    entries = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            routine = anticipation.Routine(
                name=item.get("name"), trigger=item.get("trigger"),
                actions=item.get("actions"))
        except (ValueError, TypeError, AttributeError):
            continue
        entries.append(routine)
    return entries


def _save_routines(store: anticipation.RoutineStore) -> bool:
    """Write the store's routines atomically-ish (temp file +
    os.replace, the planner_storage pattern). Never raises: False
    means the routines live in memory for this process only and
    will not survive a restart."""
    try:
        path = _ANTICIPATION_ROUTINES_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [{"name": r.name, "trigger": r.trigger,
                 "actions": list(r.actions)}
                for r in store.list_routines()]
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data, indent=2, ensure_ascii=False))
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception:  # noqa: BLE001
        return False


class _PersistingRoutineStore(anticipation.RoutineStore):
    """A RoutineStore that persists after every mutation.

    The base store does no file I/O by design (persistence is the
    caller's job); this subclass is the caller. A failed save never
    raises into the mutation — the in-memory state stays correct
    for this process, and the next mutation retries the write.
    """

    def create(self, routine: anticipation.Routine) -> anticipation.Routine:
        created = super().create(routine)
        _save_routines(self)
        return created

    def update(self, name, *, trigger=None,
               actions=None) -> anticipation.Routine:
        updated = super().update(name, trigger=trigger, actions=actions)
        _save_routines(self)
        return updated

    def delete(self, name) -> bool:
        removed = super().delete(name)
        if removed:
            _save_routines(self)
        return removed


def reset_anticipation() -> None:
    """Test seam: drop the predictor + session singletons and
    the foreground dedupe state."""
    global _PREDICTOR, _CAPTURE_SESSION, _LAST_FG_NAME, _ROUTINE_STORE
    _PREDICTOR = None
    _CAPTURE_SESSION = None
    _LAST_FG_NAME = None
    _ROUTINE_STORE = None

def note_foreground_app(name, title: str = "") -> dict:
    """Offer one live foreground observation (process NAME only,
    never a title from the watcher — the watcher reads names
    only) to the CA-1 session, then to the predictor.

    Change-only: a repeated name is not a new event (a tick
    that re-reads the same foreground app is not a transition).
    Refused/suppressed observations train nothing: the session
    returns (None, reason) and the predictor never sees them.
    Returns {"event": app|None, "reason": str|None}."""
    global _LAST_FG_NAME
    normalised = str(name or "").strip().lower()
    if not normalised or normalised == _LAST_FG_NAME:
        return {"event": None,
                "reason": "unchanged or empty foreground — no event"}
    _LAST_FG_NAME = normalised
    session = anticipation_capture_session()
    event, reason = session.emit(process_name=normalised, title=title)
    if event is not None:
        anticipation_predictor().observe_event(event)
    return {"event": event.app if event is not None else None,
            "reason": reason}

def anticipation_suggestion() -> dict:
    """The live AN-1 suggestion, WITH its silence floor intact:
    {"action": str|None, "reason": "ok"|"no-history"|
    "no-transition"|"insufficient-evidence"|"below-threshold"}.
    Callers must honour None — it is silence, never a hedged
    suggestion, and never proactive speech."""
    action, reason = anticipation_predictor().suggestion_with_reason()
    return {"action": action, "reason": reason,
            "stats": anticipation_predictor().stats()}

def poll_anticipation_suggestion(registry=None):
    """One anticipation card poll, for the watcher tick: when
    suggestion() is non-None (evidence floor + confidence both
    passed), raise ONE suggestion card through the live
    suggestions registry. The card's Act is the real
    launch_application tool for the predicted app token, run
    through the funnel's confirm gate; the card's class is not
    on the auto-act whitelist, so it NEVER auto-acts. When
    suggestion() is None, nothing is raised — silence, not a
    maybe-card. Returns the raised record or None."""
    suggestion = anticipation_suggestion()
    action = suggestion["action"]
    if action is None:
        return None
    try:
        from .brain import suggestions as _suggestions  # Phase 3 B1b: moved to core/brain/
    except Exception:  # noqa: BLE001 — no card without the engine
        return None
    try:
        return _suggestions.raise_suggestion(
            "anticipation_next_app", "anticipation_next_app",
            f"You often open {action} next — shall I open it?",
            {"tool": "launch_application", "args": {"app_name": action}},
            target=action,
            source_event={"kind": "anticipation",
                          "confidence": suggestion["stats"].get("accuracy"),
                          "scored": suggestion["stats"].get("scored")},
            registry=registry)
    except Exception:  # noqa: BLE001 — a card must not kill a tick
        return None

def mine_frequent_patterns(sequences, min_support: int,
                           max_pattern_len=None) -> list:
    """AN-2 PrefixSpan over caller-supplied sequences only (the
    module never reads a database). Returns FrequentPatterns."""
    return anticipation.prefix_span(
        sequences, min_support, max_pattern_len=max_pattern_len)

def routine_store() -> anticipation.RoutineStore:
    """The live PF-14 routine store singleton, persisted to
    data/anticipation_routines.json (field-check fix #15, T10).
    First access hydrates from disk; every create/update/delete
    writes back, so a routine survives a restart."""
    global _ROUTINE_STORE
    if _ROUTINE_STORE is None:
        _ROUTINE_STORE = _PersistingRoutineStore()
        for routine in _load_routine_entries():
            try:
                # Bypass the persisting override: one load writes
                # nothing (the file already holds these routines).
                anticipation.RoutineStore.create(_ROUTINE_STORE, routine)
            except ValueError:
                continue  # duplicate name in the file: first wins
    return _ROUTINE_STORE

def plan_routine(name: str, trigger: str, actions) -> anticipation.RoutinePlan:
    """Create + plan a PF-14 routine: planned steps, never
    executed. Execution needs run_routine() with an executor."""
    routine = anticipation.Routine(name=name, trigger=trigger,
                                        actions=list(actions))
    routine_store().create(routine)
    return anticipation.RoutineRunner().plan(routine)

def run_routine(name: str, executor=None) -> anticipation.RoutineResult:
    """Run a stored PF-14 routine ONLY through the injected
    executor (step_text, routine_name) -> truthy/False/raise.
    WITHOUT an executor this returns plan-only with zero
    executed steps — routines never execute directly and never
    bypass the confirm gate. The production executor (a
    step-text -> tool resolution) is OWED: plain-language steps
    are not tool calls, and this layer invents no mapping."""
    routine = routine_store().get(name)
    if routine is None:
        raise KeyError(f"no routine named {name!r}")
    return anticipation.RoutineRunner(executor=executor).run(routine)


# ---------------------------------------------------------------------------
# Field-check fix #32 (round-2 N5): the production executor.
#
# Anticipation routines describe their steps in plain language
# ("tell me the time", "open Notepad"); nothing turned a step
# into a tool call, and no funnel tool ran a routine, so
# routines could only ever be planned, never run. This executor
# is the step-text -> tool resolution, and run_anticipation_routine
# (below) is the funnel tool that runs a named routine through it.
#
# Resolution, in order — deterministic, no guessing:
#   1. EXPLICIT form. A step that IS a JSON object
#      {"tool": name, "args": {...}}, a bare tool name, or a
#      tool name followed by a JSON args object (with or
#      without a colon) resolves to exactly that tool with
#      those args. A step whose first token IS a tool name but
#      whose args do not parse as a JSON object is REFUSED
#      (never silently re-read as prose).
#   2. RANKED form. Otherwise the step is ranked with the
#      funnel's own ranking_scores() (keyword + embedding when
#      loaded) and the top match is accepted ONLY when the
#      pinned find_is_confident() rule holds — the same rule
#      _find uses to judge ranking confidence. Anything else
#      refuses honestly, naming the step.
#   3. ARGS. The ranked path never invents arguments: a tool
#      with no required params runs with {}; a tool with
#      required params refuses, naming the tool, the missing
#      params, and the explicit form to use. The executor never
#      extracts arguments from free text.
# Every resolved call goes through registry.execute(), so the
# funnel's own gates (validation, permission, confirm,
# failsafe, deadline, budget) apply exactly as to a spoken
# step — a confirm-gated step with no handler returns
# not_confirmed and the routine halts at that step, never
# auto-confirmed. The executor translates a non-success funnel
# dict into a raise (the runner's False branch would drop the
# reason); the raise carries the funnel's kind + message, so
# the routine result names the failed step AND why.
# A step may never resolve to a routine-management tool
# (nested routines, routine creation/deletion, or the toolbox
# itself, which would smuggle arbitrary calls past this
# resolution gate) or to an MCP tool (native declarations
# only). The voice call approves STARTING the routine, never
# its steps: no plan-approval arming happens here, so each
# step gates on its own.
# ---------------------------------------------------------------------------

class AnticipationStepError(Exception):
    """A routine step the executor refused or failed, in plain
    words. The runner catches it, stops at that step, and
    carries this message in the result."""

_FORBIDDEN_STEP_TOOLS = frozenset({
    "run_routine", "run_anticipation_routine", "run_plan", "run_goal",
    "save_routine", "delete_routine", "use_toolbox",
    # Round-2 N5 follow-up (parent ruling): scheduled_agents is
    # the scheduler engine — a step naming it with a run_routine
    # action would smuggle routine execution past this list, one
    # hop removed (the smuggled routine's steps would run through
    # the scheduler, outside this executor's per-step gates). A
    # routine step has no legitimate need to schedule agents.
    "scheduled_agents",
})


def _native_declarations():
    """The live registry's native declarations, by name (lazy
    import — the registry imports this module's pack at
    registration time, so a module-level import would be
    circular)."""
    from .funnel.tools_registry import tools as registry
    return {d["name"]: d
            for d in registry.get_declarations()}


def _required_params(declaration: dict) -> list:
    """The tool's required parameter names, in schema order."""
    params = (declaration or {}).get("parameters") or {}
    required = params.get("required") or []
    return [str(p) for p in required if isinstance(p, str)]


def _parse_explicit_step(step: str):
    """Parse an explicit step form.

    Returns (tool, args, error): (tool, args, None) on an
    explicit form, (None, None, None) when the step is not an
    explicit form at all (the ranked path owns it), and
    (None, None, error) when the step LOOKS explicit but is
    malformed — a first token that is a tool name followed by
    unparseable args, or a JSON object without a usable tool.
    """
    text = (step or "").strip()
    if not text:
        return None, None, None
    # Whole-step JSON object: {"tool": name, "args": {...}} —
    # the plan world's step shape, as one string.
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            return None, None, None  # not JSON at all: prose
        if not isinstance(parsed, dict):
            return None, None, None
        tool = parsed.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            return None, None, (
                "the step is a JSON object but names no tool — "
                "write it as {\"tool\": \"<tool name>\", "
                "\"args\": {...}}")
        args = parsed.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, None, (
                f"the step names tool {tool.strip()!r} but its "
                "\"args\" is not an object")
        return tool.strip(), args, None
    # Bare tool name, or a name followed by a JSON args object
    # ("name {...}" or "name: {...}").
    first = text.split(None, 1)[0].rstrip(":")
    declarations = _native_declarations()
    if first not in declarations:
        return None, None, None
    rest = text[len(first):].strip().lstrip(":").strip()
    if not rest:
        return first, {}, None
    try:
        args = json.loads(rest)
    except ValueError as exc:
        return None, None, (
            f"the step names tool {first!r} but its arguments "
            f"are not valid JSON ({exc}) — write them as "
            f"{first} {{\"arg\": \"value\"}}")
    if not isinstance(args, dict):
        return None, None, (
            f"the step names tool {first!r} but its arguments "
            "must be an object")
    return first, args, None


def _resolve_step(step: str, step_kind: str = "routine"):
    """Resolve one plain-language step to (tool, args).

    Raises AnticipationStepError with a plain-words reason
    when the step cannot be resolved honestly. `step_kind`
    names the step's owner in the refusal text ("routine" for
    anticipation routines, "plan" for planner plans — the
    resolver is shared by both runners; the default keeps the
    routine wording exactly).
    """
    text = (step or "").strip()
    if not text:
        raise AnticipationStepError(
            f"the step is empty — a {step_kind} step must name "
            "something to do")
    tool, args, error = _parse_explicit_step(text)
    if error is not None:
        raise AnticipationStepError(error)
    if tool is not None:
        if tool in _FORBIDDEN_STEP_TOOLS or tool.startswith("mcp__"):
            raise AnticipationStepError(
                f"a {step_kind} step may never run {tool!r} — "
                f"{step_kind}s cannot run other routines, create "
                "or delete routines, or call the toolbox")
        if tool not in _native_declarations():
            raise AnticipationStepError(
                f"I couldn't find a tool named {tool!r} — check "
                "the spelling")
        return tool, args
    # Ranked path: the funnel's own ranking + confidence rule.
    from .funnel.toolbox_declarations import find_is_confident
    from .funnel.toolbox_embeddings import ranking_scores
    from .funnel.tools_registry import tools as registry
    scores = ranking_scores(registry, text)
    if not scores:
        raise AnticipationStepError(
            f"I couldn't tell which tool {text!r} means — no "
            "tool matches those words. Try a verb and the thing "
            "it acts on, or name the tool directly.")
    order = {d["name"]: i
             for i, d in enumerate(registry.get_declarations())}
    top = sorted(scores,
                 key=lambda n: (-scores[n], order.get(n, 0)))[0]
    if top in _FORBIDDEN_STEP_TOOLS or top.startswith("mcp__"):
        raise AnticipationStepError(
            f"a {step_kind} step may never run {top!r} — "
            f"{step_kind}s cannot run other routines, create "
            "or delete routines, or call the toolbox")
    if not find_is_confident(text, scores):
        raise AnticipationStepError(
            f"I couldn't tell which tool {text!r} means — the "
            "best matches are too close to call. Name the tool "
            "directly, e.g. 'tell me the time' or "
            "'get_current_time_date'.")
    declaration = _native_declarations().get(top)
    required = _required_params(declaration)
    if required:
        missing = ", ".join(required)
        raise AnticipationStepError(
            f"{text!r} means {top}, but {top} needs {missing} "
            "and the step doesn't say what — write the step as "
            f"{top} {{\"<arg>\": \"<value>\"}} with the arguments "
            "filled in.")
    return top, {}


def _dispatch_step(tool: str, args: dict, step: str) -> bool:
    """Run one resolved step through the funnel's own gates.

    Returns True on success; raises AnticipationStepError
    (carrying the funnel's kind + message) on any non-success
    — including a confirm-gated step refused for want of a
    confirmation, which pauses the routine at that step and
    never auto-confirms it.
    """
    from .funnel.tools_registry import tools as registry
    result = registry.execute(tool, dict(args))
    if isinstance(result, dict) and result.get("status") == "success":
        return True
    if isinstance(result, dict):
        kind = result.get("kind") or "error"
        message = (result.get("message") or result.get("result")
                   or "no reason given")
        raise AnticipationStepError(
            f"step {step!r} -> {tool} {kind}: {message}")
    raise AnticipationStepError(
        f"step {step!r} -> {tool} did not return a funnel "
        "result — refusing to claim it ran")


def production_anticipation_executor(step_text, routine_name) -> bool:
    """The production executor for RoutineRunner: resolve one
    plain-language step to a tool call at RUN time and run it
    through the funnel's own gates. Returns True when the step
    ran; raises AnticipationStepError when it refused or
    failed (the runner stops at that step and names it)."""
    tool, args = _resolve_step(step_text)
    return _dispatch_step(tool, args, step_text)


def _format_routine_result(result, resolutions) -> str:
    """A RoutineResult as plain words: each step's resolution
    named, a failed step named with its reason, the routine
    stopped at that step. `resolutions` is the (step, tool,
    args) list the run recorded, in order."""
    planned = list(result.planned_steps)
    lines = [f"Running routine '{result.routine_name}' "
             f"({len(planned)} step(s)):"]
    resolved = {step: tool for step, tool, _args in resolutions}
    for index, step in enumerate(planned):
        label = f"Step {index + 1}/{len(planned)}: {step!r}"
        if resolved.get(step) is not None:
            label += f" → {resolved[step]}"
        if result.status == "failed" and index == result.failed_step_index:
            lines.append(f"  ✗ {label} — halted: {result.error}")
        elif index < len(result.executed_steps):
            lines.append(f"  ✓ {label}")
        elif result.status == "plan-only":
            lines.append(f"  · {label} — planned, not run")
        elif result.status == "failed":
            # A step after the failed one: never attempted, and
            # named as such — the run's summary says so, and the
            # step list must show it too (a silently absent step
            # reads as a step that vanished).
            lines.append(f"  · {label} — did not run")
    if result.status == "completed":
        lines.append(f"Routine '{result.routine_name}' completed: "
                     f"{len(result.executed_steps)} of {len(planned)} "
                     "steps ran.")
    elif result.status == "failed":
        failed = (result.failed_step_index or 0) + 1
        lines.append(f"Routine stopped at step {failed} of "
                     f"{len(planned)}. Steps after it did not run.")
    else:  # plan-only
        lines.append(f"Routine '{result.routine_name}' is planned "
                     "only — nothing ran (no executor).")
    return "\n".join(lines)


def run_anticipation_routine(name: str) -> str:
    """Funnel-tool handler: run a stored anticipation routine
    through the production executor, one step at a time, in
    order, stopping at the first step that refuses or fails.
    The call approves STARTING the routine, never its steps:
    each step keeps its own confirm/allowlist gate, and a
    step that needs a confirmation pauses the routine there
    (it is never auto-confirmed). A routine that does not
    exist is an honest refusal, not a raise."""
    routine = routine_store().get(name)
    if routine is None:
        return (f"No anticipation routine named {name!r} — check "
                "the name (routines are listed by the routines "
                "listing).")
    resolutions: list = []

    def _recording(step, routine_name):
        tool, args = _resolve_step(step)
        resolutions.append((step, tool, args))
        return _dispatch_step(tool, args, step)

    result = anticipation.RoutineRunner(executor=_recording).run(routine)
    return _format_routine_result(result, resolutions)
