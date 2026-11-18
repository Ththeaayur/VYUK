"""Wave 4 CTRL breadth — control-ID + accessibility locators + revalidation.

Builds on Wave 2 control hardening (core/desktop/control_hardening.py), never
a second loop. Stdlib only, no GUI, no screen capture, no coordinates.

Rules, enforced in code:
  * A control is addressed by control_id, optionally with an
    accessibility locator (role, name, automation_id). A locator
    alone is a hint for resolution, never permission to act.
  * Every reuse revalidates against the caller-supplied snapshot
    before the action is permitted. A stale or absent control is
    refused with a stale-mark reason; the caller must re-resolve.
  * Coordinates-only operation is refused, always — a request with
    coordinates but no control_id returns a refusal, never an action.
  * Snapshots are caller-supplied. Nothing here captures a screen,
    reads an accessibility tree, or drives a real control; this is
    the gate that a real driver must pass through first.
"""

from __future__ import annotations

from dataclasses import dataclass

from .control_hardening import ControlRecord, revalidate, use_control


@dataclass(frozen=True)
class AccessibilityLocator:
    """A caller-supplied accessibility hint for one control."""

    role: str = ""
    name: str = ""
    automation_id: str = ""

    def is_empty(self) -> bool:
        return not (self.role or self.name or self.automation_id)


def resolve_locator(locator: AccessibilityLocator, snapshot: list) -> dict:
    """Resolve a locator against a caller-supplied snapshot.

    Matching is exact on the fields the caller filled in; an empty
    locator resolves to nothing, never to a guessed control. Returns
    a ControlRecord on a unique match, otherwise a refusal dict.
    """
    if not isinstance(snapshot, list):
        return {"ok": False, "reason": "snapshot is not a list"}
    if locator.is_empty():
        return {"ok": False, "reason": "empty locator: cannot resolve a control"}
    matches = []
    for entry in snapshot:
        if not isinstance(entry, dict):
            continue
        if locator.role and entry.get("role") != locator.role:
            continue
        if locator.name and entry.get("name") != locator.name:
            continue
        if locator.automation_id and entry.get("automation_id") != locator.automation_id:
            continue
        matches.append(entry)
    if len(matches) == 0:
        return {"ok": False, "reason": "no control matches the locator"}
    if len(matches) > 1:
        return {"ok": False,
                "reason": f"locator is ambiguous: {len(matches)} controls match; "
                          "refine it before acting"}
    entry = matches[0]
    if not entry.get("control_id"):
        return {"ok": False, "reason": "matched control has no control_id"}
    return {"ok": True,
            "record": ControlRecord(control_id=str(entry["control_id"]),
                                    label=str(entry.get("name", "")),
                                    fingerprint=str(entry.get("fingerprint", "")))}


def reuse_control(record: ControlRecord, snapshot: list,
                  *, coordinates: tuple | None = None) -> dict:
    """Revalidate-before-reuse: the only permitted reuse path.

    Delegates to the Wave 2 gate, so the refusal wording and the
    coordinates-only refusal stay identical to Wave 2.
    """
    return use_control(record, snapshot, coordinates=coordinates)


def revalidate_record(record: ControlRecord, snapshot: list) -> dict:
    """Expose the Wave 2 revalidation check for breadth callers."""
    return revalidate(record, snapshot)
