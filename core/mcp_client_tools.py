"""MCP client — tool naming, hashing, result normalisation (split from core/mcp_client.py, Phase B B23)."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any
# INTENTIONAL cross-part import (bare-name rule): _load_hashes and
# _save_hashes resolve hashes_path by bare name; it lives in
# mcp_client_config.
from .mcp_client_config import hashes_path

# --- SPLIT BODY BELOW (verbatim from core/mcp_client.py) ---


def compose_tool_name(server: str, tool: str) -> str:
    """The toolbox-visible name for one MCP tool. Namespaced so
    an MCP tool can never collide with a native tool, and
    resolved through the bus's map (never parsed back), so any
    server/tool spelling survives."""
    return f"mcp__{server}__{tool}"


def _tool_hash(tool: dict[str, Any]) -> str:
    """Description-hash for one tool (SE-5.12 runtime half)."""
    blob = json.dumps(
        [tool.get("name"), tool.get("description"),
         tool.get("parameters")],
        sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_hashes() -> dict[str, str]:
    try:
        data = json.loads(hashes_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a broken store reads empty
        return {}


def _save_hashes(hashes: dict[str, str]) -> None:
    try:
        path = hashes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(hashes, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — hashes never break a call
        pass


# ---------------------------------------------------------------------------
# Tool result normalisation
# ---------------------------------------------------------------------------

def _result_to_text(result: Any) -> tuple[str, bool]:
    """One MCP call result -> (plain text, is_error).

    MCP tool errors arrive as results with isError set, NOT as
    exceptions — that distinction is load-bearing for the bus:
    an isError result is a failed TOOL call (the session
    survives); an exception is a broken SESSION (the server
    goes dead). Non-text content is named, never silently
    dropped or hallucinated into text.
    """
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        kind = getattr(block, "type", None)
        if kind == "text":
            parts.append(getattr(block, "text", "") or "")
        elif kind == "image":
            parts.append("[image content — not shown as text]")
        elif kind == "resource":
            parts.append("[resource content — not shown as text]")
        else:
            parts.append(f"[{kind or 'unknown'} content — not shown as text]")
    return "\n".join(parts), bool(getattr(result, "isError", False))


def _hint(ann: Any, camel: str, snake: str) -> bool:
    """One annotation hint, tolerant of the three shapes a tool
    object can arrive in: an mcp.types.ToolAnnotations (snake_case
    attributes: read_only_hint), a FastMCP/dict-shaped object
    (camelCase keys), or a plain dict. v5.55 fix: the first
    version read only camelCase attributes, which mcp.types
    ToolAnnotations does NOT expose — every tool silently
    classified as unhinted (fail-closed, but wrong: read-only
    tools would have confirmed every time)."""
    if ann is None:
        return False
    if isinstance(ann, dict):
        return bool(ann.get(camel, ann.get(snake, False)))
    return bool(getattr(ann, snake, getattr(ann, camel, False)))


def _annotations_of(tool: Any) -> dict[str, bool]:
    """ToolAnnotations -> the three classification hints. Missing
    annotations mean fail-closed (see tools_gate's MCP rules):
    no hint is NOT read-only, and NOT local."""
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return {"readOnlyHint": False, "destructiveHint": False,
                "openWorldHint": False}
    return {
        "readOnlyHint": _hint(ann, "readOnlyHint", "read_only_hint"),
        "destructiveHint": _hint(ann, "destructiveHint",
                                  "destructive_hint"),
        "openWorldHint": _hint(ann, "openWorldHint",
                                "open_world_hint"),
    }


def _parameters_of(tool: Any) -> dict[str, Any]:
    """One tool's input schema, tolerant of the shapes a tool
    object can arrive in: mcp.types.Tool (input_schema),
    a FastMCP/dict-shaped object (inputSchema), or a plain
    dict (parameters). v5.55 fix: the first version read only
    inputSchema, which mcp.types.Tool does NOT expose — every
    tool published with parameters={} and the shared validator
    would have passed any arguments. A missing schema here is
    a bus bug, never a reason to validate loosely."""
    params = getattr(tool, "input_schema", None)
    if params is None:
        params = getattr(tool, "inputSchema", None)
    if params is None:
        params = getattr(tool, "parameters", None)
    if isinstance(tool, dict):
        params = tool.get("inputSchema",
                          tool.get("input_schema",
                                   tool.get("parameters")))
    return params if isinstance(params, dict) else {}
