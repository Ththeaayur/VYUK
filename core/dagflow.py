"""R7-7 taskflow — VYUK's own DAG workflow engine (own code, taskflow pattern).

The "automating everything" engine: a workflow is a directed acyclic
graph of funnel-tool calls — nodes are funnel tools (with their
existing gates, trust path and confirm rules untouched), edges are
dependencies, trigger rules decide which nodes run after a
predecessor's outcome, retries with backoff and per-task timeouts
are bounded, and every run is recorded in SQLite so the user can
read what happened instead of trusting a black box.

Pattern taken from hajirufai/taskflow (MIT, single commit 31f649f
2026-05-27, read in full before building — the design is sound:
Kahn's topological sort and DFS cycle detection were exercised
directly against the upstream graph.py and behave exactly as
documented). NOT vendored and NOT installed: the upstream needs
pydantic + aiosqlite + fastapi + rich + click + uvicorn + httpx,
none of which the tree has; this module is stdlib only
(sqlite3, plain dataclasses) and takes only the pattern —
DAG definition, topological order, cycle detection, parallel
levels, the four trigger rules, retries with backoff, timeouts,
run records, SQLite history.

What is deliberately NOT taken from the upstream:
  - No FastAPI REST API and no Rich CLI — VYUK has no HTTP
    surface for workflows and no CLI; the interface is funnel
    tools + the existing GUI surfaces.
  - No cron scheduler inside the engine — the tree already has
    apscheduler (B-D9); a schedule that calls run_workflow is a
    scheduler job, not a second scheduler.
  - No parallel execution within a level (yet): funnel tools are
    not proven thread-safe, so nodes run sequentially in
    topological order. The levels ARE computed (parallel_levels),
    so parallel execution can be added later without changing
    the model — the record names the level each node ran on.

Trust — the load-bearing rule, read before touching this file:
the DAG engine NEVER bypasses the funnel trust path. Every node
executes through the ONE funnel (core/brain/planner_plans.
_execute_tool — the registry's execute() with the full gate
stack, exactly as a live tool call gets it). A workflow cannot
grant a tool a permission its own gate would refuse: a node a
gate refuses is FAILED (never retried), a node that needs human
confirmation stops for the human exactly as a direct call
would, and the refusal is written into the run record.

Retries follow the funnel's own taxonomy (B12), never blind
retries: ONLY status "failure" with kind "handler_error"
(transient — the handler ran and raised) is retried, up to the
node's retry count with exponential backoff. A refusal (status
"refused", or a success carrying a REFUSAL_MARKERS sentence),
a timeout, an unknown outcome, and an "unavailable" failure
are NEVER retried — a retry could double-execute an action
that already landed. A per-task timeout is enforced only when
the node declares one, via a worker thread + join; the timed-out
node is TIMED_OUT and its outcome is recorded UNKNOWN (the call
may still land in its thread) — the funnel's own A5 timeout
semantics. Without a timeout the node runs inline, exactly as
run_goal runs a picked action.

Definitions are data, never code: a workflow is a JSON document
(workflow_id + tasks with tool/args/depends_on/retries/timeout/
trigger_rule). define_workflow validates the whole document
before anything is stored — unknown tool, missing dependency,
duplicate task id, or a cycle refuses the definition, never
the run. Definitions live under data/workflows/<id>.json;
run history lives in data/dagflow_runs.db (SQLite, WAL).

core/workflow_learning.py stays STAGING ONLY and is untouched
by this batch: it distills proposed loop plans from proven
jobs and never executes anything. Converting a distilled plan
into a DAG definition is a separate, small batch — this module
does not import it and does not promote it.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Bounds (a runaway workflow must not be constructible by accident)
# ---------------------------------------------------------------------------

_MAX_TASKS = 50
_MAX_RETRIES = 5
_MAX_RETRY_DELAY_S = 60.0
_MAX_RETRY_BACKOFF = 5.0
_MAX_RETRY_MAX_DELAY_S = 300.0
_MIN_TIMEOUT_S = 1.0
_MAX_TIMEOUT_S = 600.0
_DEFAULT_RUN_BUDGET_S = 600.0
_MIN_RUN_BUDGET_S = 60.0
_MAX_RUN_BUDGET_S = 1800.0
_WORKFLOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RUN_KEEP_DEFAULT = 100

# ---------------------------------------------------------------------------
# Pure graph core (taskflow's graph.py, re-implemented in stdlib)
# ---------------------------------------------------------------------------


class CycleError(Exception):
    """Raised when a cycle is detected; carries the cycle path."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        path = " → ".join(cycle)
        super().__init__(f"Cycle detected: {path}")


def topological_sort(nodes: list[str], edges: dict[str, list[str]]) -> list[str]:
    """Topological order (Kahn's algorithm). Raises CycleError."""
    in_degree: dict[str, int] = {n: 0 for n in nodes}
    for src, dsts in edges.items():
        for dst in dsts:
            if dst in in_degree:
                in_degree[dst] += 1

    queue = deque(n for n in nodes if in_degree[n] == 0)
    result: list[str] = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for downstream in edges.get(node, []):
            if downstream in in_degree:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

    if len(result) != len(nodes):
        cycle = detect_cycle(nodes, edges)
        raise CycleError(cycle or ["unknown"])

    return result


def detect_cycle(nodes: list[str], edges: dict[str, list[str]]) -> Optional[list[str]]:
    """Detect a cycle by DFS colouring; returns the cycle path or None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    parent: dict[str, Optional[str]] = {n: None for n in nodes}

    def dfs(node: str) -> Optional[list[str]]:
        color[node] = GRAY
        for neighbor in edges.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                cycle = [neighbor, node]
                current = node
                while current != neighbor:
                    current = parent.get(current)
                    if current is None:
                        break
                    cycle.append(current)
                cycle.reverse()
                cycle.append(cycle[0])  # Close the cycle
                return cycle
            if color[neighbor] == WHITE:
                parent[neighbor] = node
                result = dfs(neighbor)
                if result:
                    return result
        color[node] = BLACK
        return None

    for node in nodes:
        if color[node] == WHITE:
            result = dfs(node)
            if result:
                return result
    return None


def parallel_levels(nodes: list[str], edges: dict[str, list[str]]) -> list[list[str]]:
    """Assign nodes to levels; level 0 = no dependencies. Nodes in
    one level have no edges between them (they could run in
    parallel — this engine runs them sequentially, see module
    docstring — but the level is recorded on each task run)."""
    reverse: dict[str, list[str]] = {}
    for src, dsts in edges.items():
        for dst in dsts:
            reverse.setdefault(dst, []).append(src)

    order = topological_sort(nodes, edges)
    levels_map: dict[str, int] = {}
    for node in order:
        upstreams = reverse.get(node, [])
        if not upstreams:
            levels_map[node] = 0
        else:
            levels_map[node] = max(levels_map.get(u, 0) for u in upstreams) + 1

    max_level = max(levels_map.values()) if levels_map else 0
    result: list[list[str]] = [[] for _ in range(max_level + 1)]
    for node in order:
        result[levels_map[node]].append(node)
    return result


def validate_edges(nodes: set[str], edges: dict[str, list[str]]) -> list[str]:
    """Endpoint checks: every edge endpoint must be a known node."""
    errors: list[str] = []
    for src, dsts in edges.items():
        if src not in nodes:
            errors.append(f"Source node '{src}' not found in workflow")
        for dst in dsts:
            if dst not in nodes:
                errors.append(
                    f"Dependency '{dst}' of task '{src}' not found in workflow"
                )
    return errors


def compute_delay(
    attempt: int,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 300.0,
    jitter: bool = True,
) -> float:
    """Retry delay: exponential backoff with ±25% jitter, capped."""
    delay = base_delay * (backoff ** attempt)
    delay = min(delay, max_delay)
    if jitter:
        jitter_range = delay * 0.25
        delay += random.uniform(-jitter_range, jitter_range)
        delay = max(0.0, delay)
    return delay


# ---------------------------------------------------------------------------
# Models (plain dataclasses — the upstream's pydantic models, stdlib)
# ---------------------------------------------------------------------------


class TaskState(str, Enum):
    """Task execution states (a subset of the upstream's — no
    UPSTREAM_FAILED: a node a trigger rule skips is SKIPPED with
    the rule and the upstream states named in its reason)."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in (
            TaskState.SUCCESS,
            TaskState.FAILED,
            TaskState.SKIPPED,
            TaskState.TIMED_OUT,
        )


class TriggerRule(str, Enum):
    """When a node runs, given its upstream states."""

    ALL_SUCCESS = "all_success"  # Run only if all upstreams succeeded
    ALL_DONE = "all_done"  # Run regardless of upstream status
    ONE_SUCCESS = "one_success"  # Run if at least one upstream succeeded
    NONE_FAILED = "none_failed"  # Run if no upstream failed (skipped OK)


@dataclass
class TaskSpec:
    """One node: a funnel-tool call with its DAG wiring."""

    task_id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    retries: int = 0
    retry_delay: float = 1.0
    retry_backoff: float = 2.0
    retry_max_delay: float = 300.0
    timeout: Optional[float] = None
    trigger_rule: TriggerRule = TriggerRule.ALL_SUCCESS

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trigger_rule"] = self.trigger_rule.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        rule = data.get("trigger_rule", TriggerRule.ALL_SUCCESS.value)
        if isinstance(rule, str):
            rule = TriggerRule(rule)
        return cls(
            task_id=str(data["task_id"]),
            tool=str(data["tool"]),
            args=dict(data.get("args") or {}),
            depends_on=list(data.get("depends_on") or []),
            retries=int(data.get("retries", 0)),
            retry_delay=float(data.get("retry_delay", 1.0)),
            retry_backoff=float(data.get("retry_backoff", 2.0)),
            retry_max_delay=float(data.get("retry_max_delay", 300.0)),
            timeout=(None if data.get("timeout") is None
                     else float(data["timeout"])),
            trigger_rule=rule,
        )


@dataclass
class TaskRun:
    """Record of one node's execution (one row in task_runs)."""

    run_id: str
    workflow_id: str
    task_id: str
    state: TaskState = TaskState.PENDING
    attempt: int = 1
    level: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    result_summary: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


@dataclass
class DagRun:
    """Record of one workflow run (one row in dag_runs)."""

    run_id: str
    workflow_id: str
    state: TaskState = TaskState.RUNNING
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration: Optional[float] = None
    reason: str = ""
    task_runs: dict[str, TaskRun] = field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tr in self.task_runs.values():
            key = tr.state.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["task_runs"] = {k: v.as_dict() for k, v in self.task_runs.items()}
        return data

# ---------------------------------------------------------------------------
# Workflow definition (data, never code)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short(text: str, limit: int = 200) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _jsonable(value: Any) -> bool:
    """True when the value round-trips through JSON (args must)."""
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


class Workflow:
    """A DAG of funnel-tool calls. Built from a JSON document by
    from_dict(); validate() is the gate — a workflow with an
    unknown tool, a missing dependency, a duplicate task id, or
    a cycle is refused at define time, never at run time."""

    def __init__(
        self,
        workflow_id: str,
        tasks: dict[str, TaskSpec],
        description: str = "",
    ):
        self.workflow_id = workflow_id
        self.description = description
        self.tasks = tasks
        self._edges: dict[str, list[str]] = {}
        for tid, spec in tasks.items():
            for dep in spec.depends_on:
                self._edges.setdefault(dep, []).append(tid)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Workflow":
        workflow_id = str(data.get("workflow_id", ""))
        description = str(data.get("description", ""))
        raw_tasks = data.get("tasks") or []
        if not isinstance(raw_tasks, list):
            raise ValueError("tasks must be a list")
        tasks: dict[str, TaskSpec] = {}
        for raw in raw_tasks:
            if not isinstance(raw, dict):
                raise ValueError("each task must be an object")
            spec = TaskSpec.from_dict(raw)
            if spec.task_id in tasks:
                raise ValueError(f"duplicate task_id '{spec.task_id}'")
            tasks[spec.task_id] = spec
        return cls(workflow_id, tasks, description)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "description": self.description,
            "tasks": [spec.to_dict() for spec in self.tasks.values()],
        }

    @property
    def edges(self) -> dict[str, list[str]]:
        return dict(self._edges)

    def validate(self, known_tools: Optional[set] = None) -> list[str]:
        """All structural checks. Empty = valid. known_tools is the
        registry's tool set (None = skip the unknown-tool check,
        so pure-structure validation stays possible without the
        funnel loaded)."""
        errors: list[str] = []
        if not _WORKFLOW_ID_RE.match(self.workflow_id):
            errors.append(
                "workflow_id must match ^[a-z0-9][a-z0-9_-]{0,63}$"
            )
        if not self.tasks:
            errors.append("workflow has no tasks")
            return errors
        if len(self.tasks) > _MAX_TASKS:
            errors.append(f"workflow has more than {_MAX_TASKS} tasks")
        node_set = set(self.tasks.keys())
        errors.extend(validate_edges(node_set, self._edges))
        for tid, spec in self.tasks.items():
            for dep in spec.depends_on:
                if dep not in node_set:
                    errors.append(
                        f"Task '{tid}' depends on '{dep}' "
                        "which is not in the workflow"
                    )
                if dep == tid:
                    errors.append(f"Task '{tid}' depends on itself")
            if not spec.tool:
                errors.append(f"Task '{tid}' names no tool")
            elif known_tools is not None and spec.tool not in known_tools:
                errors.append(
                    f"Task '{tid}' names unknown tool '{spec.tool}'"
                )
            if not (0 <= spec.retries <= _MAX_RETRIES):
                errors.append(
                    f"Task '{tid}' retries must be 0-{_MAX_RETRIES}"
                )
            if not (0.1 <= spec.retry_delay <= _MAX_RETRY_DELAY_S):
                errors.append(
                    f"Task '{tid}' retry_delay must be "
                    f"0.1-{_MAX_RETRY_DELAY_S}"
                )
            if not (1.0 <= spec.retry_backoff <= _MAX_RETRY_BACKOFF):
                errors.append(
                    f"Task '{tid}' retry_backoff must be "
                    f"1.0-{_MAX_RETRY_BACKOFF}"
                )
            if not (1.0 <= spec.retry_max_delay <= _MAX_RETRY_MAX_DELAY_S):
                errors.append(
                    f"Task '{tid}' retry_max_delay must be "
                    f"1.0-{_MAX_RETRY_MAX_DELAY_S}"
                )
            if spec.timeout is not None and not (
                _MIN_TIMEOUT_S <= spec.timeout <= _MAX_TIMEOUT_S
            ):
                errors.append(
                    f"Task '{tid}' timeout must be "
                    f"{_MIN_TIMEOUT_S}-{_MAX_TIMEOUT_S} seconds"
                )
            if not _jsonable(spec.args):
                errors.append(f"Task '{tid}' args are not JSON-serializable")
        if not errors:
            try:
                topological_sort(list(self.tasks.keys()), self._edges)
            except CycleError as exc:
                errors.append(str(exc))
        return errors

    def get_execution_order(self) -> list[str]:
        return topological_sort(list(self.tasks.keys()), self._edges)

    def levels(self) -> list[list[str]]:
        return parallel_levels(list(self.tasks.keys()), self._edges)


def _known_tools() -> Optional[set]:
    """Registered tool names, or None when the registry cannot be
    read (callers then let validate() skip the unknown-tool check
    and execute() itself report the problem). Mirrors
    core/brain/planner_plans._known_tools."""
    try:
        from .funnel.tools_registry import tools as registry

        return {d["name"] for d in registry.get_declarations()}
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Definition store (data/workflows/<id>.json, atomic write + re-read)
# ---------------------------------------------------------------------------


def default_workflows_dir() -> Path:
    from .config import DATA_DIR

    return Path(DATA_DIR) / "workflows"


def default_db_path() -> Path:
    from .config import DATA_DIR

    return Path(DATA_DIR) / "dagflow_runs.db"


def _definition_path(workflows_dir: Path, workflow_id: str) -> Path:
    return Path(workflows_dir) / f"{workflow_id}.json"


def define_workflow(
    spec: dict[str, Any],
    workflows_dir: Optional[Path] = None,
) -> tuple[bool, str]:
    """Validate + store a workflow definition. Returns (ok, message).
    The stored file is re-read after the write and the success line
    names what the re-read found (A4-filestat); a refused
    definition stores nothing."""
    workflows_dir = Path(workflows_dir) if workflows_dir else default_workflows_dir()
    try:
        workflow = Workflow.from_dict(spec)
    except (ValueError, KeyError, TypeError) as exc:
        return False, f"definition refused: {exc}"
    if workflow.workflow_id in BUILTIN_WORKFLOWS:
        return False, (
            f"definition refused: '{workflow.workflow_id}' is a "
            "built-in workflow id"
        )
    errors = workflow.validate(known_tools=_known_tools())
    if errors:
        return False, "definition refused: " + "; ".join(errors)
    try:
        workflows_dir.mkdir(parents=True, exist_ok=True)
        path = _definition_path(workflows_dir, workflow.workflow_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(workflow.to_dict(), indent=1, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)
        reread = json.loads(path.read_text(encoding="utf-8"))
        found = Workflow.from_dict(reread)
        if found.workflow_id != workflow.workflow_id or len(found.tasks) != len(
            workflow.tasks
        ):
            return False, "definition refused: re-read mismatch after write"
    except Exception as exc:  # noqa: BLE001
        return False, f"definition refused: could not store: {exc}"
    return True, (
        f"workflow '{workflow.workflow_id}' defined with "
        f"{len(workflow.tasks)} tasks "
        f"({', '.join(workflow.get_execution_order())})"
    )


def load_workflow(
    workflow_id: str,
    workflows_dir: Optional[Path] = None,
) -> Optional[Workflow]:
    """A built-in by id, else a stored definition, else None."""
    if workflow_id in BUILTIN_WORKFLOWS:
        return Workflow.from_dict(BUILTIN_WORKFLOWS[workflow_id])
    workflows_dir = Path(workflows_dir) if workflows_dir else default_workflows_dir()
    path = _definition_path(workflows_dir, workflow_id)
    if not path.is_file():
        return None
    try:
        return Workflow.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def list_workflows(workflows_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """Built-ins first, then stored definitions (sorted by id).
    A stored file that no longer parses is listed with an error,
    never silently dropped."""
    workflows_dir = Path(workflows_dir) if workflows_dir else default_workflows_dir()
    out: list[dict[str, Any]] = []
    for workflow_id in sorted(BUILTIN_WORKFLOWS):
        spec = BUILTIN_WORKFLOWS[workflow_id]
        out.append(
            {
                "workflow_id": workflow_id,
                "description": spec.get("description", ""),
                "tasks": len(spec.get("tasks") or []),
                "builtin": True,
            }
        )
    if Path(workflows_dir).is_dir():
        for path in sorted(Path(workflows_dir).glob("*.json")):
            workflow_id = path.stem
            if workflow_id in BUILTIN_WORKFLOWS:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                workflow = Workflow.from_dict(data)
                out.append(
                    {
                        "workflow_id": workflow.workflow_id,
                        "description": workflow.description,
                        "tasks": len(workflow.tasks),
                        "builtin": False,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                out.append(
                    {
                        "workflow_id": workflow_id,
                        "description": "",
                        "tasks": 0,
                        "builtin": False,
                        "error": f"unparseable definition: {exc}",
                    }
                )
    return out


# ---------------------------------------------------------------------------
# Built-in workflows (in code, so they work on a fresh install)
# ---------------------------------------------------------------------------

BUILTIN_WORKFLOWS: dict[str, dict[str, Any]] = {
    "daily-status": {
        "workflow_id": "daily-status",
        "description": (
            "Read-only status sweep: companion, wake model, wake "
            "inventory, meeting — four status reports, chained so "
            "each depends on the one before it. Safe to run "
            "anywhere; it reads nothing but status."
        ),
        "tasks": [
            {
                "task_id": "companion",
                "tool": "companion_status",
                "args": {},
                "depends_on": [],
            },
            {
                "task_id": "wake",
                "tool": "custom_wake_model_status",
                "args": {},
                "depends_on": ["companion"],
            },
            {
                "task_id": "models",
                "tool": "list_custom_wake_models",
                "args": {},
                "depends_on": ["wake"],
            },
            {
                "task_id": "meeting",
                "tool": "meeting_status",
                "args": {},
                "depends_on": ["companion"],
                "trigger_rule": "all_success",
            },
        ],
    },
}

# ---------------------------------------------------------------------------
# Run history (stdlib sqlite3 — the upstream's aiosqlite store, sync)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dag_runs (
    run_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration REAL,
    reason TEXT,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempt INTEGER DEFAULT 1,
    level INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    duration REAL,
    error TEXT,
    result_summary TEXT,
    FOREIGN KEY (run_id) REFERENCES dag_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_dag_runs_workflow_id ON dag_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_run_id ON task_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_workflow_id ON task_runs(workflow_id);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def save_dag_run(dag_run: DagRun, db_path: Optional[Path] = None) -> None:
    """Persist a finished run + its task runs (one transaction)."""
    path = Path(db_path) if db_path else default_db_path()
    conn = _connect(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO dag_runs
                (run_id, workflow_id, state, started_at, finished_at,
                 duration, reason, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dag_run.run_id,
                dag_run.workflow_id,
                dag_run.state.value,
                dag_run.started_at,
                dag_run.finished_at,
                dag_run.duration,
                dag_run.reason,
                json.dumps(dag_run.summary()),
            ),
        )
        for task_run in dag_run.task_runs.values():
            conn.execute(
                """
                INSERT INTO task_runs
                    (run_id, workflow_id, task_id, state, attempt,
                     level, started_at, finished_at, duration, error,
                     result_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_run.run_id,
                    task_run.workflow_id,
                    task_run.task_id,
                    task_run.state.value,
                    task_run.attempt,
                    task_run.level,
                    task_run.started_at,
                    task_run.finished_at,
                    task_run.duration,
                    task_run.error,
                    task_run.result_summary,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_runs(
    workflow_id: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Recent runs, newest first (limit clamped 1-50)."""
    limit = max(1, min(50, int(limit)))
    path = Path(db_path) if db_path else default_db_path()
    if not path.is_file():
        return []
    conn = _connect(path)
    try:
        query = "SELECT * FROM dag_runs WHERE 1=1"
        params: list[Any] = []
        if workflow_id:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if state:
            query += " AND state = ?"
            params.append(state)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_run_detail(
    run_id: str, db_path: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    """One run with its task runs, or None."""
    path = Path(db_path) if db_path else default_db_path()
    if not path.is_file():
        return None
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM dag_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        task_rows = conn.execute(
            "SELECT * FROM task_runs WHERE run_id = ? ORDER BY started_at",
            (run_id,),
        ).fetchall()
        result["task_runs"] = [dict(r) for r in task_rows]
        return result
    finally:
        conn.close()


def cleanup_old_runs(
    workflow_id: str, keep: int = _RUN_KEEP_DEFAULT, db_path: Optional[Path] = None
) -> int:
    """Delete runs beyond the retention limit. Returns the count."""
    path = Path(db_path) if db_path else default_db_path()
    if not path.is_file():
        return 0
    conn = _connect(path)
    try:
        old = conn.execute(
            """
            SELECT run_id FROM dag_runs
            WHERE workflow_id = ?
            ORDER BY started_at DESC
            LIMIT -1 OFFSET ?
            """,
            (workflow_id, max(0, int(keep))),
        ).fetchall()
        if not old:
            return 0
        run_ids = [r["run_id"] for r in old]
        placeholders = ",".join("?" for _ in run_ids)
        conn.execute(
            f"DELETE FROM task_runs WHERE run_id IN ({placeholders})", run_ids
        )
        conn.execute(
            f"DELETE FROM dag_runs WHERE run_id IN ({placeholders})", run_ids
        )
        conn.commit()
        return len(run_ids)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Result classification (the funnel's own taxonomy, B12)
# ---------------------------------------------------------------------------


def _classify_result(result: Any) -> tuple[str, bool, str]:
    """Classify one funnel result for a node.

    Returns (outcome, retryable, reason) where outcome is one of
    "success" / "failed" / "refused". Only a transient
    handler_error is retryable; everything else is terminal.
    A lazy import keeps this module out of the funnel's import
    graph (the _execute_tool convention)."""
    try:
        from .funnel.tool_result import is_refusal_text
    except Exception:  # noqa: BLE001
        def is_refusal_text(text: Any) -> bool:  # type: ignore[no-redef]
            return False

    if isinstance(result, dict):
        status = result.get("status")
        if status == "success":
            text = str(result.get("result", "") or "")
            if is_refusal_text(text):
                return "refused", False, f"refused: {_short(text)}"
            return "success", False, ""
        if status == "refused":
            return "refused", False, _short(
                str(result.get("message", "gate refused"))
            )
        if status in ("timeout", "unknown") or result.get("outcome") == "unknown":
            return (
                "failed",
                False,
                "unknown outcome (timeout/unknown) — never retried; "
                "the action may already have landed",
            )
        if status == "failure":
            kind = str(result.get("kind", ""))
            if kind == "handler_error":
                return "failed", True, _short(
                    str(result.get("message", "handler error"))
                )
            return "failed", False, _short(
                f"{kind or 'failure'}: "
                f"{result.get('message', 'handler failed')}"
            )
        return "failed", False, _short(str(result.get("message", "failed")))
    # A non-dict result is a legacy/test shape: the funnel's
    # execute() returns dicts in production. Record it as a
    # success with the value stringified, never as a silent pass.
    return "success", False, ""


def _evaluate_trigger_rule(
    rule: TriggerRule, depends_on: list[str], dag_run: DagRun
) -> tuple[bool, str]:
    """Should the node run, given its upstream states? Returns
    (should_run, reason)."""
    if not depends_on:
        return True, ""
    upstream = [dag_run.task_runs[d].state for d in depends_on if d in dag_run.task_runs]
    if not upstream:
        return True, ""
    names = ", ".join(f"{d}={dag_run.task_runs[d].state.value}" for d in depends_on)
    if rule == TriggerRule.ALL_SUCCESS:
        ok = all(s == TaskState.SUCCESS for s in upstream)
    elif rule == TriggerRule.ALL_DONE:
        ok = all(s.is_terminal for s in upstream)
    elif rule == TriggerRule.ONE_SUCCESS:
        ok = any(s == TaskState.SUCCESS for s in upstream)
    elif rule == TriggerRule.NONE_FAILED:
        ok = not any(s in (TaskState.FAILED, TaskState.TIMED_OUT) for s in upstream)
    else:
        ok = True
    if ok:
        return True, ""
    return False, f"trigger {rule.value} not met ({names})"


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _execute_node(
    spec: TaskSpec,
    execute_fn=None,
) -> Any:
    """One node through the ONE funnel. execute_fn is the test seam
    (planner_plans._execute_tool by default, patched by suites —
    the established fake seam, see _execute_tool's docstring)."""
    if execute_fn is None:
        from .brain.planner_plans import _execute_tool

        execute_fn = _execute_tool
    return execute_fn(spec.tool, dict(spec.args or {}))


def _run_node_in_thread(spec: TaskSpec, timeout: float, execute_fn=None) -> Any:
    """Run a node in a worker thread with a join timeout. Only used
    when the node declares a timeout: without one the node runs
    inline, exactly as run_goal runs a picked action. On timeout
    the caller marks TIMED_OUT and records the outcome UNKNOWN —
    the call may still land in its (daemon) thread."""
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = _execute_node(spec, execute_fn)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(
            f"node timed out after {timeout}s (outcome unknown — "
            "the call may still land)"
        )
    if "error" in box:
        raise box["error"]
    return box.get("result")


def run_dag(
    workflow: Workflow,
    *,
    run_id: Optional[str] = None,
    run_budget_seconds: float = _DEFAULT_RUN_BUDGET_S,
    db_path: Optional[Path] = None,
    execute_fn=None,
    sleep_fn=None,
) -> DagRun:
    """Execute a validated workflow, level by level, sequentially
    within a level. Returns the DagRun (also persisted to SQLite
    by the caller-facing run_workflow handler — this function
    itself only executes and records in memory, so the suite can
    run it against a temp store without touching production)."""
    budget = max(_MIN_RUN_BUDGET_S, min(_MAX_RUN_BUDGET_S, float(run_budget_seconds)))
    sleep = sleep_fn or time.sleep
    started = time.monotonic()
    rid = run_id or uuid.uuid4().hex[:12]
    dag_run = DagRun(
        run_id=rid,
        workflow_id=workflow.workflow_id,
        state=TaskState.RUNNING,
        started_at=_now_iso(),
    )
    levels = workflow.levels()
    level_of: dict[str, int] = {}
    for level_idx, level_tasks in enumerate(levels):
        for tid in level_tasks:
            level_of[tid] = level_idx
    for tid in workflow.tasks:
        dag_run.task_runs[tid] = TaskRun(
            run_id=rid,
            workflow_id=workflow.workflow_id,
            task_id=tid,
            level=level_of.get(tid, 0),
        )

    def _budget_left() -> float:
        return budget - (time.monotonic() - started)

    def _finish(state: TaskState, reason: str = "") -> DagRun:
        dag_run.state = state
        dag_run.reason = reason
        dag_run.finished_at = _now_iso()
        start_dt = datetime.fromisoformat(dag_run.started_at)
        dag_run.duration = (
            datetime.fromisoformat(dag_run.finished_at) - start_dt
        ).total_seconds()
        for tr in dag_run.task_runs.values():
            if tr.state in (TaskState.PENDING, TaskState.QUEUED):
                tr.state = TaskState.SKIPPED
                tr.error = reason or "run ended before this node ran"
                tr.finished_at = dag_run.finished_at
        return dag_run

    for level_tasks in levels:
        for tid in level_tasks:
            if _budget_left() <= 0:
                return _finish(
                    TaskState.FAILED,
                    f"run budget ({budget:g}s) exhausted",
                )
            spec = workflow.tasks[tid]
            task_run = dag_run.task_runs[tid]
            should_run, why = _evaluate_trigger_rule(
                spec.trigger_rule, spec.depends_on, dag_run
            )
            if not should_run:
                task_run.state = TaskState.SKIPPED
                task_run.error = why
                task_run.started_at = _now_iso()
                task_run.finished_at = task_run.started_at
                task_run.duration = 0.0
                continue
            task_run.state = TaskState.RUNNING
            task_run.started_at = _now_iso()
            task_started = time.monotonic()
            last_error = ""
            attempts = 0
            while True:
                attempts += 1
                task_run.attempt = attempts
                try:
                    if spec.timeout is not None:
                        result = _run_node_in_thread(
                            spec, spec.timeout, execute_fn
                        )
                    else:
                        result = _execute_node(spec, execute_fn)
                except TimeoutError as exc:
                    task_run.state = TaskState.TIMED_OUT
                    task_run.error = str(exc)
                    break
                except Exception as exc:  # noqa: BLE001
                    # The funnel call itself raised outside its own
                    # taxonomy (a broken handler import, a bug):
                    # treat it as a transient handler error — one
                    # retry ladder, same as a handler_error result.
                    outcome, retryable, reason = (
                        "failed",
                        True,
                        f"handler raised: {type(exc).__name__}: {exc}",
                    )
                    result = None
                else:
                    outcome, retryable, reason = _classify_result(result)
                last_error = reason
                if outcome == "success":
                    task_run.state = TaskState.SUCCESS
                    task_run.result_summary = _short(
                        str(
                            (result or {}).get("result", "")
                            if isinstance(result, dict)
                            else result
                        )
                    )
                    break
                if outcome == "refused":
                    task_run.state = TaskState.FAILED
                    task_run.error = f"refused (never retried): {reason}"
                    break
                if not retryable or attempts > spec.retries:
                    task_run.state = TaskState.FAILED
                    task_run.error = reason or "node failed"
                    break
                # Transient handler_error, retries remain: back
                # off, but never past the run budget.
                if _budget_left() <= 0:
                    task_run.state = TaskState.FAILED
                    task_run.error = (
                        f"{reason} (run budget exhausted during retry)"
                    )
                    break
                delay = compute_delay(
                    attempt=attempts - 1,
                    base_delay=spec.retry_delay,
                    backoff=spec.retry_backoff,
                    max_delay=spec.retry_max_delay,
                )
                delay = min(delay, max(0.0, _budget_left()))
                if delay > 0:
                    sleep(delay)
            task_run.finished_at = _now_iso()
            task_run.duration = time.monotonic() - task_started

    states = {tr.state for tr in dag_run.task_runs.values()}
    if all(s == TaskState.SUCCESS for s in states):
        return _finish(TaskState.SUCCESS, "all nodes succeeded")
    if TaskState.FAILED in states or TaskState.TIMED_OUT in states:
        failed = sorted(
            t for t, tr in dag_run.task_runs.items()
            if tr.state in (TaskState.FAILED, TaskState.TIMED_OUT)
        )
        return _finish(
            TaskState.FAILED, f"failed nodes: {', '.join(failed)}"
        )
    # Everything else is skipped — nothing failed, nothing ran:
    # that is a completed run with nothing done, not a failure.
    # (The upstream reports this SUCCESS too; the record names
    # the skips, so the summary cannot be misread.)
    return _finish(TaskState.SUCCESS, "all nodes skipped by trigger rules")

# ---------------------------------------------------------------------------
# Funnel handlers
# ---------------------------------------------------------------------------


def _result(message: str, ok: bool = True, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": ok, "message": message}
    out.update(extra)
    return out


def define_workflow_handler(
    workflow_id: str = "",
    description: str = "",
    tasks: Any = None,
) -> dict[str, Any]:
    """Define a workflow from a JSON task list. Validates the whole
    document before anything is stored; a refused definition
    stores nothing. The definition file is re-read after the
    write (A4-filestat)."""
    if tasks is None:
        tasks = []
    if isinstance(tasks, str):
        try:
            tasks = json.loads(tasks)
        except Exception as exc:  # noqa: BLE001
            return _result(f"definition refused: tasks is not valid JSON: {exc}", ok=False)
    if not isinstance(tasks, list):
        return _result("definition refused: tasks must be a list", ok=False)
    ok, message = define_workflow(
        {
            "workflow_id": workflow_id,
            "description": description,
            "tasks": tasks,
        }
    )
    return _result(message, ok=ok)


def run_workflow_handler(
    workflow_id: str = "",
    run_budget_seconds: Any = _DEFAULT_RUN_BUDGET_S,
) -> dict[str, Any]:
    """Run a defined workflow end-to-end. Synchronous and bounded
    (default 600 s run budget); every node passes its own gate —
    the DAG adds no privilege. The run record is written to
    SQLite AND re-read before the summary is returned
    (A9-measure): a failed write yields the refusal, never a
    claimed run."""
    try:
        budget = float(run_budget_seconds)
    except (TypeError, ValueError):
        return _result("run refused: run_budget_seconds must be a number", ok=False)
    workflow = load_workflow(workflow_id)
    if workflow is None:
        return _result(
            f"run refused: no workflow '{workflow_id}' "
            "(see list_workflows)",
            ok=False,
        )
    errors = workflow.validate(known_tools=_known_tools())
    if errors:
        return _result("run refused: " + "; ".join(errors), ok=False)
    dag_run = run_dag(workflow, run_budget_seconds=budget)
    try:
        save_dag_run(dag_run)
        reread = get_run_detail(dag_run.run_id)
    except Exception as exc:  # noqa: BLE001
        return _result(
            f"run refused: run record could not be stored: {exc}", ok=False
        )
    if reread is None or reread.get("state") != dag_run.state.value:
        return _result(
            "run refused: run record re-read mismatch after write", ok=False
        )
    counts = dag_run.summary()
    summary = (
        f"workflow '{workflow_id}' run {dag_run.run_id}: "
        f"{dag_run.state.value} "
        f"({', '.join(f'{k}={v}' for k, v in sorted(counts.items()))})"
    )
    if dag_run.reason:
        summary += f" — {dag_run.reason}"
    return _result(
        summary,
        ok=dag_run.state == TaskState.SUCCESS,
        run_id=dag_run.run_id,
        state=dag_run.state.value,
        counts=counts,
        task_states={
            tid: tr.state.value for tid, tr in dag_run.task_runs.items()
        },
    )


def list_workflows_handler() -> dict[str, Any]:
    """List defined workflows (built-ins + stored). Read-only."""
    workflows = list_workflows()
    lines = [
        f"{w['workflow_id']} ({w['tasks']} tasks"
        + (" [built-in]" if w.get("builtin") else "")
        + (f" — {w['error']}" if w.get("error") else "")
        + f"): {w['description']}"
        for w in workflows
    ]
    return _result(
        "\n".join(lines) if lines else "no workflows defined",
        workflows=workflows,
    )


def workflow_runs_handler(
    workflow_id: str = "",
    limit: Any = 10,
) -> dict[str, Any]:
    """Recent runs for a workflow (or all workflows). Read-only."""
    try:
        limit_n = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        return _result("runs refused: limit must be a number", ok=False)
    runs = get_runs(
        workflow_id=workflow_id or None,
        limit=limit_n,
    )
    if not runs:
        return _result(
            f"no runs recorded"
            + (f" for workflow '{workflow_id}'" if workflow_id else ""),
            runs=[],
        )
    lines = []
    for run in runs:
        summary = run.get("summary") or "{}"
        lines.append(
            f"{run['run_id']} {run['workflow_id']} {run['state']} "
            f"started {run['started_at']} {summary}"
        )
    return _result("\n".join(lines), runs=runs)


def register_all(registry) -> None:
    """Register the four workflow tools (R7-7, taskflow pattern)."""
    registry.register(
        name="define_workflow",
        description="Define a workflow: a named graph of funnel-tool calls with dependencies, trigger rules, retries and timeouts — the 'when X, do Y' engine. The whole definition is validated before anything is stored (unknown tool, missing dependency or a cycle refuses it). Definitions are data, never code. See list_workflows for what exists, run_workflow to run one.",
        parameters={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow id, lowercase letters/digits/dashes/underscores, e.g. 'morning-prep'"},
                "description": {"type": "string", "description": "What this workflow does, in plain language"},
                "tasks": {"type": "array", "description": "Task list: each task is an object with task_id (string, required), tool (string, a registered funnel tool, required), args (object), depends_on (list of task ids), retries (0-5), retry_delay (seconds), retry_backoff (multiplier), retry_max_delay (seconds), timeout (seconds, 1-600, or null), trigger_rule (all_success, all_done, one_success, none_failed)", "items": {"type": "object", "properties": {"task_id": {"type": "string"}, "tool": {"type": "string"}, "args": {"type": "object"}, "depends_on": {"type": "array", "items": {"type": "string"}}, "retries": {"type": "integer"}, "retry_delay": {"type": "number"}, "retry_backoff": {"type": "number"}, "retry_max_delay": {"type": "number"}, "timeout": {"type": "number"}, "trigger_rule": {"type": "string", "enum": ["all_success", "all_done", "one_success", "none_failed"]}}, "required": ["task_id", "tool"]}},
            },
            "required": ["workflow_id", "tasks"],
        },
        handler=define_workflow_handler,
    )
    registry.register(
        name="run_workflow",
        description="Run a defined workflow end-to-end: each node calls its funnel tool through the normal gate stack (a workflow grants no tool any permission its own gate would refuse), with retries only for transient handler errors, per-task timeouts, and a bounded run budget. Returns the run summary with every node's state; the full record is in workflow_runs. A run that reaches a confirm-gated node stops for the human exactly as a direct call would.",
        parameters={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow id to run, e.g. 'daily-status' — see list_workflows"},
                "run_budget_seconds": {"type": "integer", "description": "Whole-run budget in seconds (default 600; 60-1800)"},
            },
            "required": ["workflow_id"],
        },
        handler=run_workflow_handler,
    )
    registry.register(
        name="list_workflows",
        description="List the defined workflows (built-ins first, then yours), with their task counts. It only reports — it never defines or runs anything.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=list_workflows_handler,
    )
    registry.register(
        name="workflow_runs",
        description="Show recent workflow runs with their states and per-node counts, newest first — the record of what actually happened, not a claim. It only reports — it never runs anything.",
        parameters={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Only runs for this workflow (default: all workflows)"},
                "limit": {"type": "integer", "description": "How many runs to show (default 10; 1-50)"},
            },
            "required": [],
        },
        handler=workflow_runs_handler,
    )
