from ..funnel.tools_registry import (tools)
from .local_client_budgets import (_resident_priority_names, _tool_line)
from typing import (Any)
import difflib
import json
import re


_TYPE_MAP = {
    "OBJECT": "object", "STRING": "string", "NUMBER": "number",
    "INTEGER": "integer", "BOOLEAN": "boolean", "ARRAY": "array",
}


def _convert_schema(node: Any) -> Any:
    """Recursively rewrites Gemini-style schema types to JSON-Schema case
    (OBJECT→object, STRING→string, …) anywhere a "type" key appears,
    keeping description / enum / required untouched."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "type" and isinstance(value, str):
                out[key] = _TYPE_MAP.get(value.upper(), value.lower())
            else:
                out[key] = _convert_schema(value)
        return out
    if isinstance(node, list):
        return [_convert_schema(item) for item in node]
    return node


_OLLAMA_TOOLS_BUDGET = 80_000


_OLLAMA_FILL_DESC_CAP = 55


_OLLAMA_CORE_DESC_CAPS = (200, 150, 110, 80, 55, 30, 0)


def _to_ollama_tool(decl: dict[str, Any]) -> dict[str, Any]:
    """One declaration in Ollama /api/chat tool format — the exact
    conversion _to_ollama_tools has always emitted."""
    params = _convert_schema(decl.get("parameters") or {"type": "object", "properties": {}})
    if isinstance(params, dict):
        params.setdefault("type", "object")
        params.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": decl["name"],
            "description": decl.get("description", ""),
            "parameters": params,
        },
    }


def _cap_ollama_descriptions(node: Any, cap: int) -> Any:
    """A copy of `node` with every "description" string longer
    than `cap` shortened to cap chars + "." — the discipline of
    core/funnel/tools_registry.py's _cap_descriptions, but pure (the input is never
    mutated). cap 0 empties descriptions entirely."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "description" and isinstance(value, str) and len(value) > cap:
                out[key] = (value[:cap].rstrip() + ".") if cap > 0 else ""
            else:
                out[key] = _cap_ollama_descriptions(value, cap)
        return out
    if isinstance(node, list):
        return [_cap_ollama_descriptions(item, cap) for item in node]
    return node


def _ollama_list_size(items: list[dict[str, Any]]) -> int:
    """len(json.dumps(items)) computed incrementally: json.dumps
    joins list items with ", " (2 chars) inside the brackets."""
    if not items:
        return 2
    return 2 + sum(len(json.dumps(item)) for item in items) + 2 * (len(items) - 1)


def _to_ollama_tools(declarations: list[dict[str, Any]],
                     query: str | None = None) -> list[dict[str, Any]]:
    """Converts tools.get_declarations() into Ollama /api/chat tool
    format, fitted to _OLLAMA_TOOLS_BUDGET (see the section comment
    above). A declaration set whose conversion already fits is
    returned unchanged — byte-identical to the pre-v5.42
    conversion. Pure: no I/O, nothing mutated; the only globals
    read are the budget constants and the resident priority set
    (_resident_priority_names — CORE_TOOL_PRIORITY at the
    default profile)."""
    converted = [_to_ollama_tool(decl) for decl in declarations]
    if _ollama_list_size(converted) <= _OLLAMA_TOOLS_BUDGET:
        return converted

    by_name = {tool["function"]["name"]: tool for tool in converted}
    registry_order = [tool["function"]["name"] for tool in converted]
    # v5.45 (H13): the resident set is the configured tool
    # profile's — CORE_TOOL_PRIORITY exactly at the default.
    priority = set(_resident_priority_names())

    # (1) The core set first, at full size (registry order, as the
    # builtin renderer orders it). Only when the core ALONE busts
    # the budget are its descriptions capped — progressively,
    # deepest cap last — and as a final resort (schema bulk no
    # description cap can shrink) trailing core tools are dropped
    # so the never-exceed invariant holds unconditionally.
    core = [by_name[n] for n in registry_order if n in priority]
    if _ollama_list_size(core) > _OLLAMA_TOOLS_BUDGET:
        for cap in _OLLAMA_CORE_DESC_CAPS:
            capped = [_cap_ollama_descriptions(t, cap) for t in core]
            if _ollama_list_size(capped) <= _OLLAMA_TOOLS_BUDGET:
                core = capped
                break
        else:
            core = capped
            while core and _ollama_list_size(core) > _OLLAMA_TOOLS_BUDGET:
                core.pop()

    # (2) Query promotion, scored exactly as _render_tool_protocol
    # scores it (v5.29): name-token hits weigh double, description
    # hits single; ties keep registry order. Core tools already
    # hold full places, so only non-core tools are scored.
    match_score: dict[str, int] = {}
    if query:
        # B6(a) (v5.45): stemmed + synonym-expanded scoring, the
        # SAME keyword_scores core/funnel/toolbox uses for the router —
        # one scorer for every promotion path.
        from ..funnel.toolbox import keyword_scores as _kw_scores  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/

        match_score = {n: sc for n, sc in _kw_scores(tools, query).items()
                       if n not in priority}
    order_idx = {n: i for i, n in enumerate(registry_order)}
    promoted = sorted(match_score,
                      key=lambda n: (-match_score[n], order_idx[n]))

    fitted = list(core)
    size = _ollama_list_size(fitted)

    def _try_add(tool: dict[str, Any]) -> None:
        nonlocal size
        extra = len(json.dumps(tool)) + (2 if fitted else 0)
        if size + extra <= _OLLAMA_TOOLS_BUDGET:
            fitted.append(tool)
            size += extra

    # Promoted tools claim full-size places ahead of the fill.
    for name in promoted:
        _try_add(by_name[name])
    # (3) The rest fill what remains, in registry order, with
    # descriptions capped — a tool that does not fit is skipped,
    # not fatal: a smaller tool later in the order may still fit
    # (the builtin renderer's rule for lines that don't fit).
    for name in registry_order:
        if name in priority or name in match_score:
            continue
        _try_add(_cap_ollama_descriptions(by_name[name],
                                          _OLLAMA_FILL_DESC_CAP))
    return fitted


def _balanced_json_object(text: str, start: int) -> str | None:
    """Returns the balanced {...} substring starting at text[start]
    (which must be "{"), honouring string literals and escapes — or
    None when the braces never balance."""
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
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _parse_tool_candidate(obj: Any, valid_names: set[str]) -> tuple[str, dict[str, Any]] | None:
    """Pulls (name, args) out of one parsed JSON object, accepting the
    shapes small models actually emit: {"tool": …, "arguments": …},
    a {"tool_call": {…}} wrapper, and the {"name": …, "arguments": …}
    variant. arguments may itself be a JSON string. The name must be a
    registered tool — anything else is not a tool call."""
    if not isinstance(obj, dict):
        return None
    wrapper = obj.get("tool_call")
    if isinstance(wrapper, dict):
        parsed = _parse_tool_candidate(wrapper, valid_names)
        if parsed is not None:
            return parsed
    name = obj.get("tool") or obj.get("name")
    if not isinstance(name, str) or name not in valid_names:
        return None
    args = obj.get("arguments", obj.get("args", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return (name, args)


def _extract_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Finds the first valid tool call in a builtin-model reply.

    Pure function (registry read aside): scans every "{" with a brace
    matcher, tolerantly json.loads each balanced candidate, and returns
    (name, arguments) for the first that names a real tool. Code fences
    and prose around the JSON are fine. Returns None when the reply is
    plain text (or unparseable) — the caller then treats it as speech.
    """
    if not text:
        return None
    valid_names = {decl["name"] for decl in tools.get_declarations()}
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
        parsed = _parse_tool_candidate(obj, valid_names)
        if parsed is not None:
            return parsed
    return None


def _balanced_json_array(text: str, start: int) -> str | None:
    """The balanced [...] substring starting at text[start] (which
    must be "[") — _balanced_json_object's array sibling, honouring
    string literals, escapes and nested objects/arrays."""
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
                return text[start:i + 1] if ch == "]" else None
    return None


def _parse_goal_action_reply(text: str) -> dict[str, Any] | None:
    """Parse ONE run_goal action from a local-model reply
    (v5.49, programme item 29).

    JSON-in-text tolerant, mirroring _extract_tool_call's scan:
    the first decisive JSON object wins — {"done": summary}
    ends the goal, {"tool"/"name": ..., "arguments"/"args": {...}}
    (optionally with a free-text "expect") is the next action.
    An object that NAMES a tool is decisive: an unknown name
    returns None (the planner then stops honestly) rather than
    letting a later, unrelated object be mistaken for the pick.
    With no decisive object, falls back to _extract_tool_call;
    plain speech / unparseable replies return None."""
    if not text:
        return None
    try:
        valid_names: set[str] | None = {
            decl["name"] for decl in tools.get_declarations()}
    except Exception:  # noqa: BLE001 — the planner validates too
        valid_names = None

    def _from_obj(obj: Any) -> tuple[dict[str, Any] | None, bool]:
        """(action, decisive) for one parsed JSON object."""
        if not isinstance(obj, dict):
            return None, False
        if obj.get("done"):
            return {"done": str(obj["done"])}, True
        wrapper = obj.get("tool_call")
        if isinstance(wrapper, dict):
            parsed, decisive = _from_obj(wrapper)
            if decisive:
                return parsed, True
        name = obj.get("tool") or obj.get("name")
        if not isinstance(name, str) or not name:
            return None, False
        if valid_names is not None and name not in valid_names:
            return None, True  # unknown tool → None, per item 29
        args = obj.get("arguments", obj.get("args", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return None, True
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, True
        action: dict[str, Any] = {"tool": name, "args": args}
        expect = obj.get("expect")
        if isinstance(expect, str) and expect.strip():
            action["expect"] = expect
        return action, True

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
        parsed, decisive = _from_obj(obj)
        if decisive:
            return parsed
    fallback = _extract_tool_call(text)
    if fallback is not None:
        name, args = fallback
        return {"tool": name, "args": args}
    return None


def _extract_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]] | None:
    """The batched form of _extract_tool_call (v5.44, B7): a reply
    carrying a JSON ARRAY of tool-call objects — independent calls
    the model wants run in one round. Returns the calls in array
    order when the WHOLE reply parses as such an array (every
    element a valid registered call, via the same
    _parse_tool_candidate the single path uses); None otherwise —
    the caller then falls back to the single-call path, which for
    a lone JSON object behaves byte-for-byte as before."""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    if not stripped.startswith("["):
        return None
    candidate = _balanced_json_array(stripped, 0)
    if candidate is None:
        return None
    try:
        obj = json.loads(candidate)
    except ValueError:
        return None
    if not isinstance(obj, list) or not obj:
        return None
    valid_names = {decl["name"] for decl in tools.get_declarations()}
    calls: list[tuple[str, dict[str, Any]]] = []
    for element in obj:
        parsed = _parse_tool_candidate(element, valid_names)
        if parsed is None:
            return None
        calls.append(parsed)
    return calls


_TOOL_NAME_PROBE_RE = re.compile(r'"tool"\s*:\s*"([A-Za-z0-9_]+)"')


def _tool_match_score(invented: str, decl: dict) -> float:
    """How likely `decl` is the tool a model meant by an invented
    name (v5.29). Plain string similarity alone ranks
    'recall_memories' over 'remember_user_fact' for the invented
    'store_memory' and misses the verb entirely, so the score also
    rewards stem matches (≥4 shared leading chars) against the
    candidate's name tokens and — weighted most — its description
    tokens: the real memory tool's description talks about
    storing, which is the bridge the model actually needs."""

    def stem_hit(token: str, words) -> bool:
        for w in words:
            common = 0
            for a, b in zip(token, w):
                if a == b:
                    common += 1
                else:
                    break
            if common >= 4:
                return True
        return False

    inv_tokens = [t for t in invented.split("_") if len(t) >= 3]
    score = difflib.SequenceMatcher(
        None, invented, decl["name"]).ratio()
    score += 0.25 * sum(
        1 for t in inv_tokens if stem_hit(t, decl["name"].split("_")))
    desc_tokens = set(re.findall(r"[a-z]+",
                                 (decl.get("description") or "").lower()))
    score += 0.6 * sum(
        1 for t in inv_tokens if stem_hit(t, desc_tokens))
    return score


def _closest_tool_hints(name: str) -> str:
    """' Did you mean X? Its exact schema is: …' naming the most
    likely intended tools for an invented name — the repair
    feedback for a model that hallucinated a tool (v5.29). Up to
    three suggestions, and none below the plausibility floor: a
    wild guess would teach the model new wrong names."""
    ranked = sorted(tools.get_declarations(),
                    key=lambda d: -_tool_match_score(name, d))
    hints = []
    for decl in ranked[:3]:
        if _tool_match_score(name, decl) < 0.8:
            break
        cand_line = _tool_line(decl["name"])
        if cand_line:
            hints.append(f"Did you mean {decl['name']}? Its exact "
                         f"schema is: {cand_line}")
    return (" " + " ".join(hints)) if hints else ""


def _invented_tool_name(text: str) -> str | None:
    """The tool name a reply TRIED to call that doesn't exist, or
    None. _extract_tool_call only accepts registered names, so an
    invented name otherwise looks like malformed JSON and earns a
    generic repair that never tells the model the NAME is wrong
    (v5.29 — the brain proof's store_memory failure: the model
    apologised for its JSON, kept the fantasy tool, and finally
    claimed success in plain text)."""
    if not text:
        return None
    m = _TOOL_NAME_PROBE_RE.search(text)
    if not m:
        return None
    name = m.group(1)
    return None if _tool_line(name) is not None else name


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str) -> str:
    """Removes <think>…</think> reasoning blocks from a model reply.

    Reasoning-style models (the Qwen3 family included) can leak their
    chain-of-thought into the visible reply when a chat template
    doesn't suppress it — the user must never hear VYUK think out loud
    before answering. Closed blocks are removed wherever they sit; an
    unclosed trailing <think>… (a reply cut off mid-thought) removes
    everything from the tag on. Applied to the plain-text reply path
    and to history appends in _brain_turn_builtin — tool-call
    extraction runs on the RAW text first, because the brace matcher
    finds a tool JSON just as well after a think block."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    unclosed = cleaned.find("<think>")
    if unclosed != -1:
        cleaned = cleaned[:unclosed]
    return cleaned.strip()


def _looks_like_tool_attempt(text: str) -> bool:
    """True when a reply was *trying* to be a tool call (starts with a
    brace or a code fence) but didn't parse as one — the trigger for
    the builtin brain's repair attempts."""
    stripped = (text or "").strip()
    return stripped.startswith("{") or stripped.startswith("```")
