"""Phase 3 control-core hardening, Wave 2 (stdlib only).

Rules, enforced in code, not prose:
  * A control is usable only by control_id + a revalidation pass.
  * Coordinates-only operation is refused, always: a request with
    coordinates but no control_id returns a refusal, never an action.
  * A stale control (its current snapshot fingerprint differs from
    the recorded one, or it is absent from the snapshot) is refused
    with a stale-mark reason; the caller must re-resolve it.
  * Revalidation compares the caller's current snapshot, supplied
    as a list of {"control_id", "fingerprint"} dicts. Nothing here
    captures a screen itself -- snapshots are caller-supplied.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlRecord:
    control_id: str
    label: str
    fingerprint: str  # snapshot fingerprint at resolution time


def revalidate(record: ControlRecord, snapshot: list) -> dict:
    """Check a recorded control against a current snapshot."""
    if not record.control_id:
        return {"ok": False, "reason": "no control_id: cannot revalidate"}
    if not isinstance(snapshot, list):
        return {"ok": False, "reason": "snapshot is not a list"}
    for entry in snapshot:
        if isinstance(entry, dict) and entry.get("control_id") == record.control_id:
            if str(entry.get("fingerprint", "")) == record.fingerprint:
                return {"ok": True, "reason": "control revalidated"}
            return {"ok": False,
                    "reason": f"stale control '{record.control_id}': "
                             f"fingerprint changed since resolution"}
    return {"ok": False,
            "reason": f"stale control '{record.control_id}': "
                     f"absent from current snapshot"}


def use_control(record: ControlRecord | None, snapshot: list,
                *, coordinates: tuple | None = None) -> dict:
    """Gate an action on a control. Coordinates alone never suffice."""
    if record is None or not record.control_id:
        return {"ok": False, "action": None,
                "reason": "refused: coordinates-only operation is never "
                          "allowed; a control_id is required"
                + (" (coordinates supplied, ignored)" if coordinates else "")}
    check = revalidate(record, snapshot)
    if not check["ok"]:
        return {"ok": False, "action": None, "reason": check["reason"]}
    return {"ok": True, "action": {"control_id": record.control_id,
                                   "label": record.label},
            "reason": "control revalidated; action permitted by this gate "
                      "(caller's own C-14 gate still applies)"}
