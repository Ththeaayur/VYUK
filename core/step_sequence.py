"""Canonical step sequences (GAPS-27, M-V5).

Five ways to say "do steps in order" lived in five stores:
routines (data/routines.json), anticipation routines
(data/anticipation_routines.json — persisted since field-check
fix #15, T10), staged plans (data/task_ledger.json), replay
programmes (plain JSON dicts, no persistence), DAG workflows
(data/workflows/<id>.json). All five already execute through
the funnel — that half was never the flaw. The flaw was only
the DEFINITION side: no one record, no one listing, so no
surface could ever show "everything that runs steps, in
order" without knowing five formats.

This module is the one record + one listing. The five stores
keep their semantics — nothing is migrated, nothing is
rewritten: converters are read-only and lenient (a damaged
record is excluded, like the native loaders already do), and
list_sequences() reads the three persisted stores plus
anticipation routines and replay programmes when the caller
supplies them. A step that
names no tool is dropped from its sequence (it cannot be a
Step); a record whose actions all drop out still lists, with
no steps — the listing never hides a record the native loader
keeps.

    Sequence  {id, name, kind, steps, source}
    Step      {tool, args, text, label, depends_on}
    kind      "routine" | "anticipation" | "staged" | "replay" | "dag"
    source    where this sequence was read from (a path, or
              "anticipation:<name>" / "replay:<n>" for the
              caller-supplied two)

A Step is either a tool call (tool set, args dict) or a
plain-language action (tool "", text set — anticipation
routines describe steps in words; the injected executor
interprets them). `label` is the native step label where the
store has one (staged plans, DAG task_ids); `depends_on` is
the DAG dependency list (empty elsewhere).
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

__all__ = ["Sequence", "Step", "list_sequences"]

_KINDS = ("routine", "anticipation", "staged", "replay", "dag")


@dataclass
class Step:
    """One step, in order. Either a tool call or a
    plain-language action — never both, never neither."""

    tool: str = ""
    args: dict = field(default_factory=dict)
    text: str = ""
    label: str = ""
    depends_on: list = field(default_factory=list)


@dataclass
class Sequence:
    """One "do steps in order" record, whatever store it came
    from. `source` names where it was read from."""

    id: str
    name: str
    kind: str
    steps: list = field(default_factory=list)
    source: str = ""


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tool_step(raw) -> Step | None:
    """One native tool-step dict -> Step, or None when the dict
    does not name a tool (damaged records are excluded)."""
    if not isinstance(raw, dict):
        return None
    tool = raw.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return None
    args = raw.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    label = raw.get("label")
    if not isinstance(label, str):
        label = raw.get("task_id") or ""
    if not isinstance(label, str):
        label = ""
    depends = raw.get("depends_on") or []
    if not isinstance(depends, list):
        depends = []
    return Step(tool=tool.strip(), args=args, label=label,
                depends_on=[str(d) for d in depends])


def _routines(data_dir: pathlib.Path) -> list[Sequence]:
    """data/routines.json — a list of {name, actions: [{tool,
    args}]} (see core/brain/planner_routines.get_routines)."""
    out: list[Sequence] = []
    raw = _read_json(data_dir / "routines.json")
    if not isinstance(raw, list):
        return out
    for record in raw:
        if not isinstance(record, dict):
            continue
        name = record.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        actions = record.get("actions")
        if not isinstance(actions, list):
            continue
        steps = [s for s in (_tool_step(a) for a in actions) if s]
        out.append(Sequence(id=f"routine:{name.strip()}",
                            name=name.strip(), kind="routine",
                            steps=steps,
                            source=str(data_dir / "routines.json")))
    return out


def _staged(data_dir: pathlib.Path) -> list[Sequence]:
    """data/task_ledger.json — {"version", "tasks": [{id, name,
    steps: [{tool, args, label, on_fail, goto, when, expect}]}]}.
    Only tool/args/label survive the conversion; the control
    fields (on_fail, goto, when, expect) stay in the native
    store — this listing shows WHAT runs, in order, not the
    plan's control flow."""
    out: list[Sequence] = []
    raw = _read_json(data_dir / "task_ledger.json")
    if not isinstance(raw, dict):
        return out
    tasks = raw.get("tasks")
    if not isinstance(tasks, list):
        return out
    for record in tasks:
        if not isinstance(record, dict):
            continue
        task_id = record.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            continue
        name = record.get("name")
        if not isinstance(name, str) or not name.strip():
            name = task_id.strip()
        raw_steps = record.get("steps")
        if not isinstance(raw_steps, list):
            continue
        steps = [s for s in (_tool_step(a) for a in raw_steps) if s]
        out.append(Sequence(id=f"staged:{task_id.strip()}",
                            name=name.strip(), kind="staged",
                            steps=steps,
                            source=str(data_dir / "task_ledger.json")))
    return out


def _dag(data_dir: pathlib.Path) -> list[Sequence]:
    """data/workflows/<id>.json — {"workflow_id", "tasks":
    [{task_id, tool, args, depends_on, ...}]} (see
    core/dagflow.Workflow.from_dict). A damaged file (bad JSON,
    missing workflow_id, a task without a tool) is excluded,
    like the native loader's ValueError path."""
    out: list[Sequence] = []
    workflows_dir = data_dir / "workflows"
    try:
        files = sorted(workflows_dir.glob("*.json"))
    except OSError:
        return out
    for path in files:
        raw = _read_json(path)
        if not isinstance(raw, dict):
            continue
        workflow_id = raw.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            continue
        tasks = raw.get("tasks")
        if not isinstance(tasks, list):
            continue
        steps = []
        damaged = False
        for record in tasks:
            if not isinstance(record, dict):
                damaged = True
                break
            step = _tool_step(record)
            if step is None:
                damaged = True
                break
            if not step.label:
                step.label = str(record.get("task_id") or "")
            steps.append(step)
        if damaged:
            continue
        name = raw.get("description")
        if not isinstance(name, str) or not name.strip():
            name = workflow_id.strip()
        out.append(Sequence(id=f"dag:{workflow_id.strip()}",
                            name=name.strip(), kind="dag",
                            steps=steps, source=str(path)))
    return out


def _anticipation(routines) -> list[Sequence]:
    """Anticipation routines (core/anticipation_
    routines.Routine: name, trigger, actions as plain-language
    strings). Persisted at data/anticipation_routines.json, but
    this module does not read that file — the caller passes
    the live Routine objects. Each action becomes a text step
    (tool ""); the trigger rides the name, so the listing shows
    what fires it."""
    out: list[Sequence] = []
    for routine in routines or []:
        name = getattr(routine, "name", "") or ""
        trigger = getattr(routine, "trigger", "") or ""
        actions = getattr(routine, "actions", None) or []
        if not isinstance(name, str) or not name.strip():
            continue
        steps = [Step(text=str(a).strip())
                 for a in actions if str(a).strip()]
        if not steps:
            continue
        label = f"{name.strip()} (when {trigger.strip()})" \
            if trigger.strip() else name.strip()
        out.append(Sequence(id=f"anticipation:{name.strip()}",
                            name=label, kind="anticipation",
                            steps=steps,
                            source=f"anticipation:{name.strip()}"))
    return out


def _replay(programmes) -> list[Sequence]:
    """Replay programmes — plain JSON dicts {steps: [{tool,
    args}]} with no persistence (see core/replay.py). The
    caller passes the live programme dicts."""
    out: list[Sequence] = []
    for index, programme in enumerate(programmes or []):
        if not isinstance(programme, dict):
            continue
        raw_steps = programme.get("steps")
        if not isinstance(raw_steps, list):
            continue
        steps = [s for s in (_tool_step(a) for a in raw_steps) if s]
        if not steps:
            continue
        name = programme.get("name")
        if not isinstance(name, str) or not name.strip():
            name = f"Replay programme {index + 1}"
        out.append(Sequence(id=f"replay:{index + 1}",
                            name=name.strip(), kind="replay",
                            steps=steps, source=f"replay:{index + 1}"))
    return out


def list_sequences(data_dir=None, anticipation=None,
                   replay=None) -> list[Sequence]:
    """Every "do steps in order" record, in one listing: the
    three persisted stores (routines, staged plans, DAG
    workflows, read from `data_dir`) plus anticipation routines
    and replay programmes when the caller supplies them.
    Never raises; a missing or damaged store
    contributes nothing. The order is stable: routines, staged,
    DAG, anticipation, replay — each group in file order."""
    from .config import DATA_DIR

    base = pathlib.Path(data_dir) if data_dir is not None else DATA_DIR
    sequences: list[Sequence] = []
    sequences.extend(_routines(base))
    sequences.extend(_staged(base))
    sequences.extend(_dag(base))
    sequences.extend(_anticipation(anticipation))
    sequences.extend(_replay(replay))
    return sequences
