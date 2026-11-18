"""Plugin isolation model — what the funnel guarantees, and what it
does NOT (gaps batch, honest scope).

A plugin is a Python file in plugins/ that, after Gate C
pre-import validation (static manifest + name-collision check
BEFORE import, hash verification, shipped-manifest signature,
user-store trust for unlisted files), is imported in-process
and its ``run`` function is registered as an ordinary tool.

THE MODEL (what is enforced):
1. Every plugin-registered tool is dispatched ONLY through
   ``ToolRegistry.execute`` — the same single funnel as every
   built-in tool. There is no second door: schema validation
   runs before the handler, the confirm gate consults the
   plugin's capability gate class (a plugin with no capability
   declaration, or a "gated" one, gates on EVERY call exactly
   like a _CONFIRM_GATE_TOOLS member), taint, money guard,
   failsafe, kill-chain, budget, and deadline all apply, and
   exactly one redacted audit record is written per call.
   Product dispatch paths (Live native calls, text brains,
   toolbox dispatch, routines) all call execute(); none of
   them invokes a plugin handler directly.
2. Pre-import validation (Gate C) means a colliding,
   hash-mismatched, unsigned-shipped, or untrusted plugin file
   is NEVER imported — its top-level code never runs.

WHAT THIS DOES NOT PROVIDE (named limitation, not buried):
In-process execution means a plugin that HAS passed
validation runs with the application's full privileges: its
import-time code and its handler body share the process's
filesystem, network, and memory. The funnel gates ACTIONS
(tool calls); it does not contain code. A trusted-but-
malicious plugin, or a hash-manifest compromise, is NOT
contained at the OS level by anything in this module.
Full process isolation (an out-of-process plugin host with
IPC, capability-dropped) is FUTURE WORK, recorded here and
in THREAT_MODEL.md — it is not claimed anywhere.

Stdlib only. No plugin code is imported by this module.
"""

from __future__ import annotations

from typing import Any

# Pinned honesty text: the suite asserts these phrases survive,
# so the limitation cannot be silently edited away.
ISOLATION_LIMITS = (
    "Plugin handlers run in-process with the application's full "
    "privileges. The funnel gates tool-call actions; it does not "
    "contain code. A trusted-but-malicious plugin is not contained "
    "at the OS level. Full process isolation is future work."
)


def funnel_routing_check(tool_table: dict, plugin_gate_class: dict) -> dict:
    """Check the funnel-routing invariant for plugin tools.

    ``tool_table`` is the registry's tool table (name -> entry),
    ``plugin_gate_class`` maps plugin tool names to their gate
    class. Returns {"ok": bool, "problems": [...]}. The invariant:
    every plugin-gated name MUST be present in the shared
    registry table — i.e. dispatchable only via execute(), never
    via a side table or a direct-call path. A name missing from
    the table, or a table entry without a callable handler, is
    a problem, not a silent skip.
    """
    problems = []
    for name in sorted(plugin_gate_class):
        entry = tool_table.get(name)
        if entry is None:
            problems.append(
                f"plugin tool {name!r} has a gate class but is not "
                f"in the registry tool table")
        elif not callable(entry.get("handler")):
            problems.append(
                f"plugin tool {name!r} is in the registry table "
                f"without a callable handler")
    return {"ok": not problems, "problems": problems}


def describe_model() -> dict:
    """The isolation model as data (for doctor/diagnostics)."""
    return {
        "dispatch": "ToolRegistry.execute only — no second door",
        "pre_import": ("static manifest + collision before import, "
                       "hash verification, shipped signature, "
                       "user-store trust for unlisted files"),
        "gates_apply": ("schema validation, confirm gate (capability "
                        "class), taint, money guard, failsafe, "
                        "kill-chain, budget, deadline, audit log"),
        "not_provided": ISOLATION_LIMITS,
        "future_work": ("out-of-process plugin host with IPC and "
                        "capability-dropped execution"),
    }
