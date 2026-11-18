"""Local operator-reviewed capability policy for MCP tools.

Audit A3 (master audit P0-5): a remote MCP server's annotations
(readOnlyHint / destructiveHint / openWorldHint) must NEVER decide a
tool's permission class. They are advisory only — recorded on the
tool-table entry for display and diagnostics, never consulted for
classification.

The ONLY source of an MCP tool's class is this local policy, which
an operator reviews. A tool is granted its policy class only when
the policy record's declaration digest still matches the tool's
current declaration (name + description + parameters, the same
canonical form as core.mcp_client._tool_hash). An unknown tool, a
tool with no record, a record for a different declaration, or a
record naming an unknown class is UNREVIEWED, and the caller
(core.funnel.tools_gate.mcp_tool_class) defaults it to "confirm"
(confirmation-gated, never read-free, in every session state).

Review records live in data/mcp_tool_policy.json (the tree's data/
convention, like the extension harness store). The file is loaded
once per process and cached; an operator edits it and restarts, or
a future doctor action calls reload_policy(). A broken or missing
store reads empty — every tool unreviewed, never a silent grant.
File writes are best-effort and never raise into a call path.

Tests use set_store() to install an in-memory policy without
touching data/; reset_store() restores file-backed behaviour.
Production code never calls set_store().
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

CLASSES = ("read", "egress", "confirm")

_POLICY_FILENAME = "mcp_tool_policy.json"

_STORE: dict[str, Any] | None = None
_STORE_PATH: str | None = None


def _default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / _POLICY_FILENAME


def policy_key(entry: dict[str, Any] | None) -> str:
    """The policy key for one MCP tool-table entry.

    Bus entries carry "composed" already; fake/test entries may
    carry only server + tool, so derive the composed name the
    same way core.mcp_client.compose_tool_name does. An entry
    with neither yields "" (never a policy hit).
    """
    entry = entry or {}
    composed = entry.get("composed")
    if isinstance(composed, str) and composed:
        return composed
    server = entry.get("server")
    tool = entry.get("tool") or entry.get("name")
    if isinstance(server, str) and server and isinstance(tool, str) and tool:
        if tool.startswith("mcp__"):
            return tool
        return f"mcp__{server}__{tool}"
    if isinstance(tool, str) and tool:
        return tool
    return ""


def declaration_digest(entry: dict[str, Any] | None) -> str:
    """Canonical digest of one tool declaration.

    Identical form to core.mcp_client._tool_hash (name +
    description + parameters, canonical JSON, SHA-256) so a
    policy digest and the bus hash agree on the same entry.
    Deliberately duplicated here (not imported) so this module
    stays stdlib-only and import-cycle-free; the A3 suite pins
    the two functions equal on sample entries.
    """
    entry = entry or {}
    blob = json.dumps(
        [entry.get("name"), entry.get("description"),
         entry.get("parameters")],
        sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a broken store reads empty
        return {}


def load(path: str | Path | None = None) -> dict[str, Any]:
    """(Re)load the policy store from disk into the module cache."""
    global _STORE, _STORE_PATH
    p = Path(path) if path is not None else _default_path()
    _STORE = _read_file(p)
    _STORE_PATH = str(p)
    return _STORE


def reload_policy() -> dict[str, Any]:
    """Reload from the last path (or the default) after an edit."""
    return load(_STORE_PATH)


def set_store(store: dict[str, Any] | None) -> None:
    """Install an in-memory policy (tests only). None clears it."""
    global _STORE
    _STORE = dict(store) if isinstance(store, dict) else None


def reset_store() -> None:
    """Drop any in-memory policy; next lookup loads from disk."""
    global _STORE, _STORE_PATH
    _STORE = None
    _STORE_PATH = None


def _store() -> dict[str, Any]:
    global _STORE
    if _STORE is None:
        load()
    return _STORE if isinstance(_STORE, dict) else {}


def policy_class(entry: dict[str, Any] | None,
                 store: dict[str, Any] | None = None) -> str | None:
    """The reviewed class for one entry, or None if unreviewed.

    Unreviewed means: no record, a record naming an unknown
    class, or a record whose digest no longer matches the
    entry's current declaration (the tool changed since review
    — it IS a new tool until re-reviewed). Annotations are
    never consulted here, under any session state.
    """
    key = policy_key(entry)
    if not key:
        return None
    data = store if isinstance(store, dict) else _store()
    rec = data.get(key)
    if not isinstance(rec, dict):
        return None
    klass = rec.get("class")
    if klass not in CLASSES:
        return None
    if rec.get("digest") != declaration_digest(entry):
        return None
    return str(klass)


def review_tool(composed: str, klass: str, entry: dict[str, Any] | None,
                reviewer: str = "operator",
                path: str | Path | None = None,
                store: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record an operator review for one tool declaration.

    Returns the record. With an explicit store dict the record
    is written there only (tests); otherwise it is written
    through to the policy file (best-effort, atomic tmp +
    replace, never raising into a call path). A bad class is a
    programmer error and raises ValueError.
    """
    if klass not in CLASSES:
        raise ValueError(f"unknown MCP policy class: {klass!r}")
    import datetime

    record = {
        "class": klass,
        "digest": declaration_digest(entry),
        "reviewer": reviewer,
        "reviewed_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    if isinstance(store, dict):
        store[composed] = record
        return record
    data = _store()
    data[composed] = record
    try:
        p = Path(path) if path is not None else _default_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, p)
        global _STORE
        _STORE = data
    except Exception:  # noqa: BLE001 — policy writes never break a call
        pass
    return record
