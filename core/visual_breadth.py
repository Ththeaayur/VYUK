"""Wave 4 VIS breadth — on-demand frames, difference explanation, memory.

Stdlib only, no GUI, no capture. Every frame and element list here is
caller-supplied: this module never takes a screenshot, never records
a screen, and never watches in the background. On-demand means the
caller asks for one frame at a time; there is no continuous stream.

Rules, enforced in code:
  * A "frame" is an opaque caller-supplied identifier plus a list of
    visible elements {control_id, name, fingerprint}. The module
    explains differences between two such frames; it never renders
    pixels.
  * Element memory stores resolved controls and revalidates each
    one against the newest frame before it may be reused. A stale
    entry is marked stale and refused, never silently reused.
  * No background screen recording exists in this module, in any
    form. That form is TENSION (build-flow §3.1), not this cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .desktop.control_hardening import ControlRecord, revalidate  # Phase 3 D2b: control_hardening moved to core/desktop/


@dataclass
class Frame:
    """One caller-supplied on-demand frame."""

    frame_id: str
    elements: list  # list of {control_id, name, fingerprint} dicts


def explain_difference(before: Frame, after: Frame) -> dict:
    """Explain, in plain words, what changed between two frames.

    Returns added / removed / changed control_ids, plus a one-line
    plain-language summary. A fingerprint change on the same
    control_id is a change, never a silent re-identity.
    """
    before_map = {e["control_id"]: e for e in before.elements
                  if isinstance(e, dict) and e.get("control_id")}
    after_map = {e["control_id"]: e for e in after.elements
                 if isinstance(e, dict) and e.get("control_id")}
    added = sorted(set(after_map) - set(before_map))
    removed = sorted(set(before_map) - set(after_map))
    changed = sorted(cid for cid in set(before_map) & set(after_map)
                     if str(before_map[cid].get("fingerprint", "")) !=
                     str(after_map[cid].get("fingerprint", "")))
    parts = []
    if added:
        parts.append(f"{len(added)} control(s) appeared")
    if removed:
        parts.append(f"{len(removed)} control(s) disappeared")
    if changed:
        parts.append(f"{len(changed)} control(s) changed")
    summary = "; ".join(parts) if parts else "nothing changed"
    return {"added": added, "removed": removed, "changed": changed,
            "summary": summary}


@dataclass
class ElementMemory:
    """Remembered controls, each revalidated before reuse."""

    _records: dict = field(default_factory=dict)

    def remember(self, record: ControlRecord) -> None:
        self._records[record.control_id] = record

    def recall(self, control_id: str, current_frame: Frame) -> dict:
        """Recall a control, revalidated against the current frame.

        Returns the record only if it revalidates; otherwise a
        refusal with the stale reason, and the entry is dropped so
        a stale control can never be recalled twice.
        """
        record = self._records.get(control_id)
        if record is None:
            return {"ok": False,
                    "reason": f"no remembered control '{control_id}'"}
        check = revalidate(record, current_frame.elements)
        if not check["ok"]:
            del self._records[control_id]
            return {"ok": False, "reason": check["reason"]}
        return {"ok": True, "record": record}

    def forget(self, control_id: str) -> bool:
        return self._records.pop(control_id, None) is not None

    def known_ids(self) -> list:
        return sorted(self._records)
