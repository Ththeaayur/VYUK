"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

from typing import Any



# ---------------------------------------------------------------------------
# Wave 4 (v5.59) funnel-first classifications — ADDITIVE ONLY
# ---------------------------------------------------------------------------
# v5.59 (Wave 4, TC-2 / PC-B10 / HE-4 / HE-6, safeguard S-2):
# classification FIRST, before the new lanes exist. Nothing in
# this section moves, renames or reclassifies an existing entry:
# every name below is NEW, and no existing set above gains or
# loses a member. The three Wave 4 surfaces are NOT registry
# tools — they never enter _tools, the fitted declarations or
# the 480-tool count — so they are classified here as lanes /
# programmes / trial configs, not as tool names. The funnel
# (ToolRegistry.execute) remains the only corridor: a replay
# step, a curl_cffi read and an MCP trial call are all judged
# by the predicates below AND, for replay/MCP, by re-entering
# execute() itself (see core/replay.py and core/mcp_trials.py).
# No password-field or CAPTCHA action is ever classified free
# by this section: replay validation refuses password/captcha
# arguments outright, and the lane/trial helpers never touch
# credentials (cookies are Playwright-context only, below).

# TC-2 replay (OpenAdapt PATTERN only — see core/replay.py).
# OpenAdapt itself is never imported by any shipped path:
# its licence was verified MIT (GitHub repo page + README
# "MIT licensed", 2026-10-25), but the Wave 4 item ships
# only the funnel-wrapped pattern, compiled from HER tools'
# successes, with zero new dependencies. A replay programme
# is a list of {"tool": name, "args": {...}} steps.
REPLAY_MAX_STEPS = 50
_REPLAY_FORBIDDEN_ARG_KEYS = frozenset({"password", "captcha"})


def replay_program_error(program: Any) -> str | None:
    """Validate one replay programme (pure, fail-closed).

    Returns None when valid, else a plain-words reason. A
    programme is valid only when it is a dict with a "steps"
    list of 1..REPLAY_MAX_STEPS steps, each step a dict with
    a non-empty string "tool" and a dict "args" that is
    JSON-serialisable and carries no password/captcha key
    (the permanent walls: no replay ever types a password
    or answers a CAPTCHA). Validity says NOTHING about
    permission: every step still re-enters tools.execute
    at replay time, so the gate, taint, failsafe, budget
    and confirmation rules judge it fresh — a step that
    needs confirmation and does not get it halts the
    replay (core/replay.py), it never bypasses the gate.
    A no-model-call replay is therefore still a funnel
    replay: the model is absent, the funnel is not."""
    import json as _json

    if not isinstance(program, dict):
        return "Replay programme must be an object."
    steps = program.get("steps")
    if not isinstance(steps, list) or not steps:
        return "Replay programme has no steps."
    if len(steps) > REPLAY_MAX_STEPS:
        return (f"Replay programme has {len(steps)} steps — "
                f"the cap is {REPLAY_MAX_STEPS}.")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"Replay step {i} is not an object."
        tool = step.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            return f"Replay step {i} has no tool name."
        args = step.get("args")
        if not isinstance(args, dict):
            return f"Replay step {i} args must be an object."
        if _REPLAY_FORBIDDEN_ARG_KEYS & set(args):
            return (f"Replay step {i} carries a forbidden "
                    "argument (password/captcha) — refused.")
        try:
            _json.dumps(args)
        except (TypeError, ValueError):
            return f"Replay step {i} args are not JSON-serialisable."
    return None


def replay_trace_error(trace: Any) -> str | None:
    """A trace compiles into a programme only if EVERY
    recorded call succeeded: a failed call is not a success
    to replay, and compiling it would replay the failure.
    Trace entries are {"tool", "args", "status"} dicts."""
    if not isinstance(trace, list) or not trace:
        return "Replay trace is empty."
    for i, entry in enumerate(trace):
        if not isinstance(entry, dict):
            return f"Trace entry {i} is not an object."
        if entry.get("status") != "success":
            return (f"Trace entry {i} ({entry.get('tool')}) did "
                    "not succeed — only successes compile.")
    return replay_program_error(
        {"steps": [{"tool": e.get("tool"), "args": e.get("args", {})}
                   for e in trace]})
