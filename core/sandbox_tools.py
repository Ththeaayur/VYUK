"""Wave 7 Build 1 tools — SANDBOX-light completion as live tools.

Triage Wire pattern, as in core/decision_leaves_t4.py and
core/integration_harness_tools.py: this coordinator imports the
sandbox module, registers tools via register_all(registry),
each handler takes the named schema fields, forwards one
dict to the module, and returns the module result encoded
as a JSON string verbatim; refusal dicts flow the same
way, and module raises become an error JSON — handlers
never raise.

Wiring (Wave 7 Build 1, SANDBOX-light completion):
  * sandbox_run — confirm-gated, sandbox_dir in
    _WRITE_DESTINATION_ARGS. The funnel refuses it
    without a human confirm handler, before the handler
    runs, exactly as for the Wave 6 harness transitions.
    The handler creates the sandbox root directory only
    after that gate has passed (the module itself never
    creates a root, so the read-only tool below can
    never create a directory as a side effect).
  * sandbox_list — read-only. It lists files inside an
    existing sandbox directory and never creates, writes,
    or deletes anything; a missing root is an error
    JSON, not a created directory.

This coordinator is NOT a gate owner: it imports no
gate module. Confirm-gating is the funnel's job
(core/funnel/tools_gate.py classification). Promotion is
request-only in the module (core/sandbox_filesystem.py);
no tool here promotes, applies, or deploys anything —
plan-approval gate intact, no self-deployment, ever.
Full-VM universes (item 12) stay QUEUED; no VM form
is built or wired here.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import sandbox_filesystem as sandbox_mod


def _error(reason: str) -> str:
    return json.dumps({"ok": False, "reason": reason})


def _handler(fn, *args, **kwargs):
    try:
        return json.dumps(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")


def sandbox_run_handler(sandbox_dir, actions_json, sentinels_json="[]"):
    try:
        actions = json.loads(actions_json)
    except (ValueError, TypeError) as exc:
        return _error(f"actions_json is not a JSON list: {exc}")
    try:
        sentinels = json.loads(sentinels_json) if sentinels_json else []
    except (ValueError, TypeError) as exc:
        return _error(f"sentinels_json is not a JSON list: {exc}")
    if not isinstance(actions, list) or not isinstance(sentinels, list):
        return _error("actions_json and sentinels_json must be JSON lists")
    try:
        root = Path(sandbox_dir).expanduser()
        if not root.is_absolute():
            return _error("sandbox_dir must be an absolute path")
        # Created only here, after the funnel's confirm
        # gate has passed — never by the module, never
        # by the read-only list tool.
        root.mkdir(parents=True, exist_ok=True)
        sandbox = sandbox_mod.FileSystemSandbox(root)
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    # Wave 7 Build 2: snapshot priors BEFORE the run, then
    # record only the allowed write/delete actions AFTER
    # it. A refused action is never recorded, and when no
    # undo store is configured the run stands exactly as
    # it ran, unrecorded, with no undo claim.
    try:
        from . import undo_completion as _undo
    except Exception:  # noqa: BLE001
        try:
            from core import undo_completion as _undo
        except Exception:  # noqa: BLE001
            _undo = None
    priors = _undo.snapshot_sandbox_priors(sandbox, actions) \
        if _undo is not None else {}
    result = _handler(sandbox.run_actions, actions, sentinels)
    if _undo is not None:
        try:
            report = json.loads(result)
            if isinstance(report, dict):
                _undo.record_sandbox_run(sandbox.root, report, priors)
        except Exception:  # noqa: BLE001 — recording never breaks a run
            pass
    return result


def sandbox_list_handler(sandbox_dir):
    try:
        root = Path(sandbox_dir).expanduser()
        if not root.is_absolute():
            return _error("sandbox_dir must be an absolute path")
        if not root.is_dir():
            return _error(f"sandbox directory does not exist: {sandbox_dir}")
        sandbox = sandbox_mod.FileSystemSandbox(root)
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "sandbox_root": str(sandbox.root),
                       "files": sandbox.list_files()})


def register_all(registry) -> None:
    """Register the 2 Wave 7 Build 1 sandbox tools."""
    registry.register(
        name="sandbox_run",
        description="Run test/dry-run file actions inside a bounded light sandbox directory: writes, reads, and deletes confined to that directory (symlink-resolved), core/data/.env paths refused outright, send/network/purchase/execute kinds refused outright, promotion request-only and never performed here. Returns a report of allowed and refused actions with reasons. The run asks for confirmation first. Use when the user wants a dry-run action tested without touching anything outside the sandbox.",
        parameters={"type": "object", "properties": {
            "sandbox_dir": {"type": "string",
                            "description": "Absolute sandbox directory"},
            "actions_json": {"type": "string",
                             "description": "JSON list of {kind: read|write|delete, path: relative path, content: string for writes}"},
            "sentinels_json": {"type": "string",
                               "description": "Optional JSON list of outside file paths hashed before/after to prove they are unchanged"},
        }, "required": ["sandbox_dir", "actions_json"]},
        handler=sandbox_run_handler,
    )
    registry.register(
        name="sandbox_list",
        description="List files inside an existing light sandbox directory. Read-only; it never creates, writes, or deletes anything, and a missing directory is an error, not a created directory. Use when the user wants to inspect what a sandbox run changed inside the sandbox.",
        parameters={"type": "object", "properties": {
            "sandbox_dir": {"type": "string",
                            "description": "Absolute sandbox directory"},
        }, "required": ["sandbox_dir"]},
        handler=sandbox_list_handler,
    )
