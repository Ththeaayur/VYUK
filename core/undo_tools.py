"""Wave 7 Build 2 tools — UNDO completion as live tools.

Triage Wire pattern, as in core/sandbox_tools.py and
core/integration_harness_tools.py: this coordinator imports the
completion module, registers tools via register_all(registry),
each handler takes the named schema fields, forwards to the
module, and returns the module result encoded as a JSON
string verbatim; refusal dicts flow the same way, and
module raises become an error JSON — handlers never raise.

Wiring (Wave 7 Build 2, UNDO completion):
  * undo_cannot_list — read-only. Returns the upfront
    cannot-take-back disclosure (Wave 3 list extended
    with extension-revoked and journal-appended) plus
    the covered undoable classes. It changes nothing,
    and it is the list to show BEFORE a change.
  * undo_status — read-only. Returns the undo store
    summary, the last undoable record, the covered
    classes, and the cannot-undo list. When no undo
    store is configured it says so, never a fabricated
    empty status.
  * undo_last_change — confirm-gated. The funnel refuses
    it without a human confirm handler, before the
    handler runs, exactly as for sandbox_run and the
    Wave 6 harness transitions. The handler then calls
    the shared gate's undo_last, which skips and lists
    non-undoable records, refuses on a missing restorer
    or a tripped failsafe, and reports a restoration
    only when the restorer verified it.

This coordinator is NOT a gate owner: it imports no
gate module directly for decisions — the gate is the
shared resilience_gate, reached through the
completion module. No tool here deploys, promotes, or
executes anything; undo restores a recorded before-state
only, through a registered restorer.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json

from . import undo_completion as undo_mod


def _error(reason: str) -> str:
    return json.dumps({"ok": False, "reason": reason})


def _handler(fn, *args, **kwargs):
    try:
        return json.dumps(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")


def undo_cannot_list_handler():
    return _handler(undo_mod.cannot_undo_disclosure)


def undo_status_handler():
    return _handler(undo_mod.undo_status)


def undo_last_change_handler():
    return _handler(undo_mod.undo_last_change)


def register_all(registry) -> None:
    """Register the 3 Wave 7 Build 2 undo tools."""
    registry.register(
        name="undo_cannot_list",
        description="Show, BEFORE any change, the list of change classes that cannot be taken back (sent messages, completed purchases, published posts, deleted files with no backup, system changes that already restarted the machine, extension revocations, and mission journal entries appended as audit records), plus the classes that CAN be taken back with a verified restorer. Read-only; it changes nothing. Use when the user asks what cannot be undone, or before any consequential change.",
        parameters={"type": "object", "properties": {}},
        handler=undo_cannot_list_handler,
    )
    registry.register(
        name="undo_status",
        description="Show the undo store summary: every recorded change, whether it can be taken back, whether it was taken back, the last undoable change, the covered undoable classes, and the cannot-undo list. Read-only; it changes nothing. When no undo store is configured in this session it says so. Use when the user asks what can be undone or what the last change was.",
        parameters={"type": "object", "properties": {}},
        handler=undo_status_handler,
    )
    registry.register(
        name="undo_last_change",
        description="Undo the last change that can be taken back, through the shared undo gate: non-undoable newer changes are skipped and listed, a missing restorer or a tripped failsafe refuses plainly, and a restoration is reported only when the restorer verified it by reading back. The undo asks for confirmation first. Use when the user asks to undo the last change.",
        parameters={"type": "object", "properties": {}},
        handler=undo_last_change_handler,
    )
