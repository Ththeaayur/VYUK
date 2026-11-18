"""Funnel-wrapped replay programmes (v5.59, Wave 4 TC-2).

The OpenAdapt PATTERN, not OpenAdapt itself. OpenAdapt's
licence was verified MIT from its primary source (its
GitHub repo page + README, 2026-10-25) — but no OpenAdapt
code is imported, vendored or depended on here: this module
is stdlib-only, and the pattern is "compile a success into
a replayable programme". The programme's steps are HER OWN
tool names, recorded from HER OWN tools.execute successes.

SAFEGUARD S-2 IS THE PASS/FAIL CRITERION: a no-model-call
replay still executes THROUGH tools.execute. This module
never calls a handler, never imports a handler module, and
takes no model/brain argument at all — the only execution
path is the ``executor`` callable the caller supplies,
which in the shipped path IS ToolRegistry.execute. That
means every step is re-validated, re-gated (confirmation,
taint, failsafe, budget, deadline, protected paths) and
audited exactly like a live call. A step that returns
anything but success halts the replay immediately — no
retry, no argument repair, no skipping ahead — because a
replay that papers over a changed world is a lie about
what happened.

Nothing here is imported by any shipped default path: the
shipped tree does not import this module, and it imports
nothing from the shipped tree at module import time
(tools_gate is imported lazily inside the validators, so
importing this module can never drag the registry in).
No registry tool is added, no fitted declaration grows,
no dependency is added, and no data/ file is touched.
Password fields and CAPTCHAs are permanent walls: the
gate refuses programmes carrying password/captcha
arguments, and replay never answers either.
"""

from __future__ import annotations

import json
from typing import Any, Callable

MAX_STEPS = 50


def compile_trace(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Compile a recorded trace into a replay programme.

    Only successes compile: ``replay_trace_error`` in
    core/funnel/tools_gate.py refuses any trace containing a
    non-success entry. The programme is plain JSON data —
    a list of {"tool", "args"} steps — never code, never
    a callable, so a programme cannot smuggle a handler
    past the funnel: at replay time each step's tool name
    is resolved by execute() itself, or refused as unknown.
    """
    from .funnel.tools_gate import replay_trace_error  # Phase 3 F1: tools_gate moved to core/funnel/

    error = replay_trace_error(trace)
    if error is not None:
        raise ValueError(error)
    return {
        "version": 1,
        "steps": [{"tool": e["tool"], "args": dict(e.get("args") or {})}
                  for e in trace],
    }


def validate_program(program: Any) -> str | None:
    """One programme -> None if valid, else a plain-words reason."""
    from .funnel.tools_gate import replay_program_error  # Phase 3 F1: tools_gate moved to core/funnel/

    return replay_program_error(program)


def replay(program: dict[str, Any],
           executor: Callable[[str, dict[str, Any]], dict[str, Any]],
           ) -> dict[str, Any]:
    """Replay a programme with NO model call, THROUGH the funnel.

    ``executor`` must be tools.execute (or a test double
    standing in for it — the contract is identical: one
    call per step, in order, with the step's tool name and
    args, and a dict result whose "status" is "success"
    on success). The first non-success result halts the
    replay and is reported with its step index; later
    steps never run. The returned transcript records
    exactly which steps ran and what each returned — it
    is evidence, not a claim: a caller that needs proof
    reads the funnel's own audit log, not this dict.
    """
    error = validate_program(program)
    if error is not None:
        return {"status": "error", "kind": "validation",
                "message": error, "steps_run": 0, "results": []}
    results: list[dict[str, Any]] = []
    for index, step in enumerate(program["steps"]):
        try:
            result = executor(step["tool"], dict(step["args"]))
        except Exception as exc:  # noqa: BLE001 — an executor
            # that raises is a broken funnel, not a step result:
            # halt, and say so, rather than continuing blind.
            return {"status": "error", "kind": "handler_error",
                    "message": (f"Replay halted at step {index} "
                                f"({step['tool']}): the executor raised "
                                f"{type(exc).__name__}."),
                    "steps_run": index, "results": results,
                    "halted_step": index}
        if not isinstance(result, dict):
            result = {"status": "error", "message": "Non-dict result."}
        results.append({"step": index, "tool": step["tool"],
                        "status": result.get("status"),
                        "kind": result.get("kind")})
        if result.get("status") != "success":
            return {"status": "error",
                    "kind": result.get("kind") or "handler_error",
                    "message": (f"Replay halted at step {index} "
                                f"({step['tool']}): "
                                f"{result.get('message', 'not successful')}. "
                                "No later step ran."),
                    "steps_run": index + 1, "results": results,
                    "halted_step": index}
    return {"status": "success", "steps_run": len(results),
            "results": results}


def programme_json(program: dict[str, Any]) -> str:
    """Serialise a programme for storage (JSON only — a
    programme is data, and data is what gets stored)."""
    error = validate_program(program)
    if error is not None:
        raise ValueError(error)
    return json.dumps(program, sort_keys=True)
