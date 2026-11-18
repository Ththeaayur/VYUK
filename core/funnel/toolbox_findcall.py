"""Split out of core/funnel/toolbox.py (B37 part 2 of 2).

Find-call family: _find/_describe/_call dispatcher + use_toolbox.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

# B37 INTENTIONAL: cross-family names imported from their owning modules
# (same objects), except _embedder_singleton, which is read live through
# the embeddings module object in _find (see the INTENTIONAL comment there).
from . import toolbox_embeddings as _embeddings_mod
from .toolbox_declarations import (_declaration, _mcp_declaration,
                                  _mcp_scores, _unsupported_task_message,
                                  find_is_confident, find_may_inline)
from .toolbox_embeddings import ranking_scores
from .toolbox_tokens import core_tool_names


def _find(registry: Any, query: str) -> str:
    scores = ranking_scores(registry, query)
    # v5.55 (Wave 1, HE-1): MCP tools join the same ranking.
    # ensure_all_enabled connects idle servers once (bounded;
    # ready/dead servers answer immediately from cached state),
    # so find discovers whatever is actually reachable — and a
    # dead server contributes nothing, never a stale listing.
    # A bus failure degrades to the native catalogue alone.
    mcp_decls: dict[str, dict[str, Any]] = {}
    try:
        from .. import mcp_client as _mcp_mod  # Phase 3 F2a: toolbox now in core/funnel/; mcp_client stays flat

        _bus = _mcp_mod.get_bus()
        _bus.ensure_all_enabled()
        for _entry in _bus.list_tools():
            _decl = _mcp_declaration(_entry)
            mcp_decls[_decl["name"]] = _decl
        scores = {**scores, **_mcp_scores(query, _bus)}
    except Exception:  # noqa: BLE001 — MCP never breaks find
        mcp_decls = {}
    if not scores:
        return (f"No tool matches '{query}'. Try different "
                "words — a verb and the thing it acts on.")
    order = {d["name"]: i for i, d in enumerate(registry.get_declarations())}
    hits = sorted(scores,
                  key=lambda n: (-scores[n], order.get(n, 0)))[:8]
    core = set(core_tool_names())
    # Audit fix Batch 2 (ROUTE-01): the ranking mode is stated,
    # never left to look embedding-backed. ranking_scores()
    # above already probed the embedder, so the flags below
    # reflect this call, not a stale guess.
    # B37 INTENTIONAL: _embedder_singleton lives in the embeddings module and is
    # rebound by get_embedder() at call time; read it live through the module
    # object (a snapshot import would go stale at import-time None and the mode
    # would always read keyword-only). ranking_scores() above already probed it.
    _mode = ("keyword+embedding" if _embeddings_mod._embedder_singleton is not None
             else "keyword-only (no embedding model loaded)")
    # Abstention first (ROUTE-01b): a known unsupported task
    # gets a plain no-suitable-tool answer instead of a ranked
    # list inviting the model to call the least-bad tool — but
    # only when no tool may be inlined for it (an exact-name
    # query for a real tool is never an abstention).
    if hits[0] not in mcp_decls and not find_may_inline(query, scores, hits[0]):
        _unsupported = _unsupported_task_message(query)
        if _unsupported is not None:
            return (f"No suitable tool for '{query}' ({_mode}): "
                    f"{_unsupported}")
    lines = [f"Tools matching '{query}' (best first, {_mode}):"]
    for name in hits:
        if name in core:
            where = "call it directly — it is loaded this session"
        elif name in mcp_decls:
            _server = name[5:].split("__", 1)[0]
            where = (f"toolbox-only (external MCP server '{_server}'): "
                     "describe, then call")
        else:
            where = "toolbox-only: describe, then call"
        lines.append(f"- {name} — {where}")
    # v5.50 (programme item 45): a CONFIDENT top match (the rule
    # at find_is_confident) carries its full declaration inline,
    # so the model can call it next round with no describe
    # round. Ambiguous finds keep the list-only shape above.
    # Exactly ONE declaration is ever inlined — the result stays
    # bounded no matter how large the registry grows. An MCP
    # top match inlines its MCP declaration, same rule.
    # Audit fix Batch 2 (ROUTE-01a): inlining now passes the
    # stricter find_may_inline gate, not the ranking rule
    # alone — a confident-looking wrong tool no longer gets
    # its declaration handed over. MCP top matches keep the
    # old rule: the negative table names native tools only,
    # and an MCP name cannot share the gate's name-token
    # check against native declarations.
    if find_may_inline(query, scores, hits[0]) or (
            hits[0] in mcp_decls and find_is_confident(query, scores)):
        decl = _declaration(registry, hits[0]) or mcp_decls.get(hits[0])
        if decl is not None:
            lines.append(
                f"Top match '{hits[0]}' is a confident match — "
                "its full declaration follows, so you can call "
                "it now with action 'call' and these exact "
                "arguments, no separate 'describe' needed:")
            _decl_text = json.dumps(decl, indent=1)
            if hits[0] in mcp_decls:
                # Audit A3 (P0-6): the rendered declaration
                # JSON carries server prose in the description
                # AND in schema text, so the whole JSON goes
                # through the single MCP output seam (the
                # description inside is already seam-wrapped
                # by _mcp_declaration; re-wrapping is safe by
                # design — the outer envelope wins, the inner
                # markers are escaped).
                from . import tools_gate as _tg  # Phase 3 F1: tools_gate moved to core/funnel/; Phase 3 F2a: toolbox now in core/funnel/ (intra-package sibling)

                _server = hits[0][5:].split("__", 1)[0]
                _decl_text = _tg.wrap_mcp_content(_server, _decl_text)
            lines.append(_decl_text)
    return "\n".join(lines)


def _describe(registry: Any, name: str) -> str:
    decl = _declaration(registry, name)
    if decl is not None:
        return ("Full declaration for "
                f"{name}:\n{json.dumps(decl, indent=1)}\n"
                "Call it with use_toolbox action 'call', "
                "tool set to this name, and args matching "
                "the parameters above.")
    # v5.55 (Wave 1, HE-1): the MCP fallback — a namespaced
    # name resolves through the bus table (no connect: describe
    # reports what find already discovered).
    try:
        from .. import mcp_client as _mcp_mod  # Phase 3 F2a: toolbox now in core/funnel/; mcp_client stays flat

        _entry = _mcp_mod.get_bus().resolve(name)
    except Exception:  # noqa: BLE001 — MCP never breaks describe
        _entry = None
    if _entry is not None:
        decl = _mcp_declaration(_entry)
        # Audit A3 (P0-6): the rendered declaration JSON
        # (description + schema prose) goes through the single
        # MCP output seam; VYUK's own framing stays outside
        # the envelope.
        from . import tools_gate as _tg  # Phase 3 F1: tools_gate moved to core/funnel/; Phase 3 F2a: toolbox now in core/funnel/ (intra-package sibling)

        _decl_text = _tg.wrap_mcp_content(
            _entry["server"], json.dumps(decl, indent=1))
        return ("Full declaration for "
                f"{name}:\n{_decl_text}\n"
                "Call it with use_toolbox action 'call', "
                "tool set to this name, and args matching "
                "the parameters above.")
    return (f"Unknown tool: {name}. Use action 'find' with a "
            "few words of what you want to do.")


def _short_received(value: Any, limit: int = 1000) -> str:
    """repr() of a malformed bundle, capped so a hostile bundle
    cannot bloat the error record — the prefix, the type and
    the total length are always kept, so diagnosis is never
    misled about what arrived."""
    text = repr(value)
    if len(text) > limit:
        return f"{text[:limit]}... (truncated, {len(text)} chars total)"
    return text


def _coerce_call_args(dispatcher: str, name: str,
                      args: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Audit fix A4 (wiring audit WIR-06): distinguish 'argument
    omitted' from 'provided but malformed'. None is the omitted
    case here — use_toolbox's own default — and stays today's
    empty-arguments behaviour. A JSON string is parsed; an
    unparseable string, a JSON value that is not an object, or
    any other non-dict bundle is REJECTED with the dispatcher
    name and the inner tool name kept separate, and the received
    bundle preserved in the error — it must never silently
    become {} and dispatch a no-argument call. Returns
    (args, None) on success, (None, error) on rejection."""
    if args is None:
        return {}, None
    if isinstance(args, dict):
        return args, None
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError as exc:
            return None, (
                f"{dispatcher} call to '{name}' did not run: "
                f"arguments are not valid JSON ({exc}); "
                f"received: {_short_received(args)}")
        if not isinstance(parsed, dict):
            return None, (
                f"{dispatcher} call to '{name}' did not run: "
                "arguments must be an object "
                f"(parsed JSON is {type(parsed).__name__}); "
                f"received: {_short_received(args)}")
        return parsed, None
    return None, (
        f"{dispatcher} call to '{name}' did not run: arguments "
        f"must be an object (got {type(args).__name__}); "
        f"received: {_short_received(args)}")


def _call(registry: Any, name: str, args: Any,
          cancel_token=None) -> str:
    if not name:
        return "Say which tool to call: tool is required for action 'call'."
    if name == "use_toolbox":
        return "Refused: use_toolbox cannot call itself."
    known = {d["name"] for d in registry.get_declarations()}
    # v5.55 (Wave 1, HE-1): MCP names bypass the native known
    # check — execute()'s MCP branch resolves (and, when the
    # server is idle, connects) them itself, and reports an
    # unknown-or-unconnected MCP tool in its own words.
    if name not in known and not name.startswith("mcp__"):
        # Voice-control batch (2026-11-16, C2): an unknown tool
        # is a funnel dict, not a string — the funnel wraps a
        # string return as success, so the string form told
        # Gemini the call succeeded when nothing ran.
        return {"status": "error", "kind": "unknown_tool",
                "message": (f"Unknown tool: {name}. Use action "
                            "'find' with a few words of what you "
                            "want to do.")}
    args, problem = _coerce_call_args("use_toolbox", name, args)
    if problem is not None:
        # VYUK's own rejection, not server-sourced content, so
        # it does not go through the A3 MCP wrap below.
        return problem
    # B20: the inner call gets the token — a Stop pressed while
    # it runs ends it at its next yield point. A cancelled
    # inner result is returned as the cancelled DICT itself,
    # never as a string: the funnel wraps a string return as
    # success, so the string form ("{name} did not run") would
    # record a stopped call as a success. The token is passed
    # ONLY when supplied: test fakes of registry.execute take
    # two arguments, and an unconditional third argument would
    # break every one of them (the coordinator's
    # _executor_accepts_token rule, same reason).
    if cancel_token is None:
        out = registry.execute(name, args)
    else:
        out = registry.execute(name, args, cancel_token=cancel_token)
    if out.get("status") == "cancelled":
        return out
    result = out.get("result") or out.get("message") or ""
    if out.get("status") == "success":
        return str(result)
    # Voice-control batch (2026-11-16, C2): EVERY non-success
    # inner outcome returns the inner funnel dict itself,
    # mirroring the cancelled branch above — never a string.
    # The funnel wraps a string return as success, so the old
    # "{name} did not run: ..." text told Gemini (and the
    # receipt/ledger) that a PF-18 denial, validation failure,
    # unknown tool, timeout or handler error SUCCEEDED. The
    # dict carries status/kind/message (and the validation
    # schema, when execute() attached one) verbatim, so the
    # model can repair its arguments in one round and a plan
    # step can never auto-complete on a call that did not run.
    if name.startswith("mcp__"):
        # Audit A3 (P0-6): failure text is server-sourced —
        # wrap the dict's text fields through the same single
        # seam the old string form used.
        from . import tools_gate as _tg  # Phase 3 F1: tools_gate moved to core/funnel/; Phase 3 F2a: toolbox now in core/funnel/ (intra-package sibling)

        _server = name[5:].split("__", 1)[0]
        out = dict(out)
        for _key in ("message", "result"):
            if isinstance(out.get(_key), str):
                out[_key] = _tg.wrap_mcp_content(_server, out[_key])
    return out


def use_toolbox(action: str = "", query: str = "", tool: str = "",
                args: dict[str, Any] | None = None,
                cancel_token=None, **_extra: Any):
    """The dispatcher handler. Resolves the live registry
    singleton at call time (the registration happens while
    the registry is still being built).

    B20: ``cancel_token`` is the step's cooperative token —
    threaded to the inner call, so a Stop pressed while the
    inner tool runs ends it at its next yield point. On a
    cancelled inner call the cancelled dict is returned
    itself (see _call), never wrapped as a string.
    """
    from .tools_registry import tools as registry  # Phase 3 F2a: toolbox now in core/funnel/; tools.py stays flat; Phase 4 S1b INTENTIONAL: registry split to funnel (intra-package)

    act = (action or "").strip().lower()
    if act == "find":
        return _find(registry, query or tool)
    if act == "describe":
        return _describe(registry, (tool or query or "").strip())
    if act == "call":
        return _call(registry, (tool or "").strip(), args,
                     cancel_token=cancel_token)
    return ("use_toolbox needs an action: 'find' (query = a few "
            "words of what you want to do), 'describe' (tool = "
            "the tool's name) or 'call' (tool + args).")


def register_all(registry: Any) -> list[str]:
    """Registers use_toolbox on the given registry."""
    registry.register(
        name="use_toolbox",
        description=(
            "Reach every tool that is not loaded directly this "
            "session. action 'find' (query: a few words of what "
            "you want to do) ranks all of VYUK's tools; "
            "action 'describe' (tool: a tool name) returns its "
            "full argument schema; action 'call' (tool + args) "
            "runs it for real, with the same confirmations a "
            "direct call would need. Resident core tools can "
            "also be called directly without this."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["find", "describe", "call"],
                    "description": "Which step: search, inspect, or run",
                },
                "query": {
                    "type": "string",
                    "description": "For find: a few words of intent",
                },
                "tool": {
                    "type": "string",
                    "description": "For describe/call: the tool's exact name",
                },
                "args": {
                    "type": "object",
                    "description": "For call: the tool's arguments, as its describe shows",
                },
            },
            "required": ["action"],
        },
        handler=use_toolbox,
    )
    return ["use_toolbox"]
