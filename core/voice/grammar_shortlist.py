"""Per-turn routed-shortlist grammar (v5.55, Wave 1 SC-1).

The idea: a grammar over all 480 tool schemas is a practical
blow-up and is explicitly NOT this item. What IS built is
the per-turn ROUTED shortlist — the router's top tools for
this turn's query, the same ranker and the same limit the
planner path already uses — compiled to ONE grammar (a
Guidance JSON program over a single envelope schema), plus
a strict validator that judges a reply against that schema.

What v1 does, exactly (read this before extending it):

* shortlist_for_turn(): the router's rank_tools, limit 8.
* shortlist_schema(): one JSON Schema for the envelope
  {"tool": enum, "arguments": ...} with oneOf branches, one
  per shortlisted tool, each branch pinning its tool name
  (const) and its full argument schema from the registry
  declaration. A name missing from the registry is skipped,
  never inlined blind.
* guidance_json_program(): the compiled Guidance program
  (guidance.json(schema=...)), built LAZILY — Guidance is
  imported only here, so the module imports cleanly with
  the library absent, and absence degrades to today's path
  (the function returns None). Building the program proves
  the integration path is real; it does not, by itself,
  constrain a generation (see below).
* constrained_parse(): the strict judge — scans a reply
  for JSON objects (balanced-brace scan, same tolerance
  for code fences and prose as the builtin parser) and
  returns (tool, arguments) for the first object that is a
  shortlist tool AND whose arguments pass the shared
  registry validator against that tool's schema. Anything
  else is None: a non-shortlist tool, a wrong type, a
  missing required field, unparseable JSON — all None.

The turn-loop hook (core/local_client._brain_turn_builtin,
opt-in ONLY via VYUK_GRAMMAR_SHORTLIST=1, default OFF) runs
in CHECK mode: after each reply, constrained_parse judges
it; a grammar-valid shortlist call is used directly; any
other reply falls back to today's parse, LOGGED. The
default path is byte-unchanged when the flag is off, and
unchanged in outcome when it is on (the check only ever
selects an already-valid call, or defers to today's path).

What v1 does NOT do (stated, not silently dropped):
sampling THROUGH the grammar inside the llama.cpp turn
loop (passing GBNF to create_chat_completion, or running
generation through a Guidance LlamaCpp model) is the
remaining integration. It needs a live-model A/B on real
hardware plus the GBNF/compiler work, and it is gated on
the measured evidence this module's corpus suite produces
— not on a mock. Until then, no claim is made that a
generation was grammar-constrained; only that the grammar
compiles, the judge is strict, and the hook measures.

The grammar is NOT a security control. It can only ever
narrow WHAT the model emits; the confirmation gate, the
taint tiers, the Rule of Two and the funnel still decide
WHAT RUNS. A grammar-valid call to a gated tool still
asks a human. (Plan safeguard, repeated because it is the
kind of thing that gets forgotten once the word "grammar"
starts sounding like "guarantee".)

The Outlines cloud PILOT seat (SC-2) is HELD, not adopted:
if Guidance disappoints on the measured evidence, the
pilot is evaluated then — Outlines is not adopted locally
on speculation.

Qt-free, stdlib-only at import (INV-12): Guidance is a
lazy import inside guidance_json_program only.
"""

from __future__ import annotations

import json
import time
from typing import Any

#: The routed set size — the planner path's limit (see
#: local_client's rank_tools(limit=8) call sites). One
#: ranker, one limit, so the grammar shortlist and the
#: planner shortlist can never drift apart.
SHORTLIST_LIMIT = 8

#: Opt-in flag (default OFF — the default path is
#: byte-unchanged). Checked per turn, so flipping it needs
#: no restart of the process, only of the turn.
GRAMMAR_ENV_FLAG = "VYUK_GRAMMAR_SHORTLIST"


def shortlist_for_turn(registry: Any, query: str,
                       limit: int = SHORTLIST_LIMIT) -> list[str]:
    """This turn's routed shortlist: the router's ranking for
    the query, top `limit` names. Empty query ranks nothing
    (the router scores on tokens; no tokens, no shortlist)."""
    from ..funnel.toolbox import rank_tools  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/

    if not (query or "").strip():
        return []
    return list(rank_tools(registry, query, limit=limit))


def shortlist_schema(registry: Any, names: list[str]) -> dict[str, Any]:
    """One envelope schema for a shortlist. The top-level
    "tool" enum names the shortlist; each oneOf branch pins
    one tool (const) with that tool's full registry argument
    schema, so a validator (or a grammar engine) can judge a
    call without consulting anything else. Unknown names are
    skipped — a shortlist entry that is not in the registry
    is a ranker bug, not a schema to invent."""
    decls = {d["name"]: d for d in registry.get_declarations()}
    branches: list[dict[str, Any]] = []
    kept: list[str] = []
    for name in names:
        decl = decls.get(name)
        if decl is None:
            continue
        params = decl.get("parameters") or {"type": "object"}
        branches.append({
            "type": "object",
            "properties": {
                "tool": {"const": name},
                "arguments": params,
            },
            "required": ["tool", "arguments"],
            "additionalProperties": False,
        })
        kept.append(name)
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": kept},
            "arguments": {"type": "object"},
        },
        "required": ["tool", "arguments"],
        "oneOf": branches,
    }


def guidance_json_program(schema: dict[str, Any]) -> Any | None:
    """Compile one envelope schema to a Guidance JSON program.
    Returns the program, or None when Guidance is unavailable
    or the build fails — the caller falls back to today's
    path and logs it (never raises into a turn)."""
    try:
        import guidance

        return guidance.json(schema=schema)
    except Exception:  # noqa: BLE001 — absence/failure = fallback
        return None


def _balanced_json_object(text: str, start: int) -> str | None:
    """The balanced {...} substring starting at text[start]
    (which must be "{"), honouring string literals, escapes
    and nesting. Self-contained (local_client has its own
    copy; importing it here would cycle)."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1] if ch == "}" else None
    return None


def constrained_parse(text: str, schema: dict[str, Any]
                      ) -> tuple[str, dict[str, Any]] | None:
    """Judge a reply against one shortlist schema. Returns
    (tool, arguments) for the first JSON object that names a
    shortlisted tool AND whose arguments pass the shared
    registry validator against that tool's schema — else
    None. First-valid-wins, like the builtin parser, so
    prose and code fences around the call are fine, but a
    call the grammar could never have emitted (wrong tool,
    wrong type, missing required field) is never accepted
    on the grammar path's say-so."""
    if not text or not isinstance(schema, dict):
        return None
    branches = schema.get("oneOf") or []
    by_tool: dict[str, dict[str, Any]] = {}
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        props = branch.get("properties") or {}
        tool_spec = props.get("tool") or {}
        tool_name = tool_spec.get("const")
        if isinstance(tool_name, str):
            by_tool[tool_name] = (props.get("arguments") or {})
    if not by_tool:
        return None
    try:
        from ..funnel.tools_registry import _validate_args_against_schema  # Phase 3 V1b: moved to core/voice/ (Phase 3 F3: moved to core/funnel/)
    except Exception:  # noqa: BLE001 — no validator, no verdict
        return None
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        candidate = _balanced_json_object(text, start)
        if candidate is None:
            continue
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        tool = obj.get("tool")
        args = obj.get("arguments")
        if not isinstance(tool, str) or tool not in by_tool:
            continue
        if not isinstance(args, dict):
            continue
        if _validate_args_against_schema(tool, args, by_tool[tool]) is None:
            return tool, args
    return None


def measure_build(registry: Any, query: str,
                  limit: int = SHORTLIST_LIMIT) -> dict[str, Any]:
    """Measure one shortlist build (for the suite + the ship
    report): shortlist, schema size, Guidance compile time
    and outcome. Pure measurement — no turn state touched."""
    t0 = time.perf_counter()
    names = shortlist_for_turn(registry, query, limit=limit)
    schema = shortlist_schema(registry, names)
    t1 = time.perf_counter()
    program = guidance_json_program(schema)
    t2 = time.perf_counter()
    return {
        "shortlist": names,
        "schema_bytes": len(json.dumps(schema)),
        "schema_build_ms": (t1 - t0) * 1000.0,
        "guidance_built": program is not None,
        "guidance_build_ms": (t2 - t1) * 1000.0,
    }
