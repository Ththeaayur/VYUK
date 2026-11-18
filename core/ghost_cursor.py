"""Wave 4 P7 slices — ghost cursor intent preview, numbered marks, rewind.

Stdlib only, no GUI, no real cursor movement, no real actions.
Everything here is a preview or a record: a ghost cursor shows
what WOULD happen, numbered marks map numbers to controls, and
rewind replays a record as a dry run.

Rules, enforced in code:
  * Intent preview never executes: preview() returns the action
    description and the control it would target, and records
    nothing as done. Execution is the caller's C-14 action, outside
    this module.
  * Numbered marks map a number to a control_id from a
    caller-supplied snapshot. A number with no control, or a
    control that has gone stale, is refused, never guessed.
  * Rewind replays a recorded step list as a dry run only: it
    lists what would be re-checked, and never re-executes a
    consequential step. Consequential steps are flagged for
    fresh confirmation on any real replay.
"""

from __future__ import annotations

from .desktop.control_hardening import ControlRecord, revalidate  # Phase 3 D2b: control_hardening moved to core/desktop/


def preview_intent(action: str, record: ControlRecord | None,
                   snapshot: list) -> dict:
    """Preview an intended action. Executes nothing."""
    if not action:
        return {"ok": False, "reason": "no action to preview"}
    if record is None or not record.control_id:
        return {"ok": False, "reason": "preview needs a control_id; "
                "coordinates-only preview is refused"}
    check = revalidate(record, snapshot)
    if not check["ok"]:
        return {"ok": False, "reason": check["reason"]}
    return {"ok": True, "preview": f"would {action} on "
            f"'{record.label or record.control_id}'",
            "control_id": record.control_id,
            "note": "preview only: nothing was executed"}


class NumberedMarks:
    """Number -> control_id marks from a caller-supplied snapshot."""

    def __init__(self, snapshot: list):
        self._marks = {}
        for entry in snapshot:
            if isinstance(entry, dict) and entry.get("control_id"):
                self._marks[len(self._marks) + 1] = entry

    def mark(self, number: int) -> dict:
        entry = self._marks.get(number)
        if entry is None:
            return {"ok": False, "reason": f"no mark {number}"}
        return {"ok": True, "control_id": entry["control_id"],
                "name": entry.get("name", "")}

    def marks(self) -> dict:
        return {n: e.get("control_id") for n, e in self._marks.items()}


def rewind_dry_run(recorded_steps: list) -> dict:
    """Replay a recorded step list as a dry run only.

    Consequential steps are flagged for fresh confirmation and are
    never replayed here. Non-consequential steps are listed as
    would-be-rechecked.
    """
    would_recheck = []
    needs_confirmation = []
    for step in recorded_steps:
        if not isinstance(step, dict) or not step.get("name"):
            continue
        if step.get("consequential"):
            needs_confirmation.append(step["name"])
        else:
            would_recheck.append(step["name"])
    return {"ok": True, "would_recheck": would_recheck,
            "needs_fresh_confirmation": needs_confirmation,
            "note": "dry run only: nothing was re-executed"}
