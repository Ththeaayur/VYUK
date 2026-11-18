"""Wave 3 scoped undo 4.3 — the undo record IS the proof.
Stdlib only, no GUI, no network.

A change is recorded with its before-state, its after-state, and
the restorer that can put the before-state back. Undoing runs the
restorer through the same fence as any action: a tripped failsafe
refuses the undo rather than acting blindly, and a restorer
failure is reported as a failed restoration, never as a success.

Freedom earned, with the record shown: :meth:`UndoStore.summary`
renders every record in plain words — what changed, whether it
can be taken back, and whether it was. The CANNOT-UNDO list is
shown upfront by :func:`cannot_undo_list`; a change class on that
list is refused a record that pretends it is undoable — it is
recorded as non-undoable, with the reason, before it happens.

Two keys for big moves (P22, Tier 2 + confirmation): a
consequential step needs two DISTINCT confirmations (two keys
from two separate confirm actions). One key twice, or the same
key reused, never opens the gate. Keys expire; an expired gate
refuses, never silently re-arms.
"""

from __future__ import annotations

import json
import os
import time

CANNOT_UNDO = (
    "message sent to another person",
    "payment or purchase completed",
    "file deleted with no backup and no trash copy",
    "message or post published externally",
    "system change that already restarted the machine",
)


def cannot_undo_list() -> list:
    """The upfront cannot-take-back list. Show this BEFORE a
    change in one of these classes, never after it."""
    return list(CANNOT_UNDO)


class UndoStore:
    """Append-only undo records in a JSON file under a given dir.

    Records are never edited in place: an undo appends a
    'undone' outcome to the record's history. The restorer is
    injected by the caller (it knows how to restore that change
    class); this store never guesses a restoration."""

    def __init__(self, store_path: str):
        self.store_path = str(store_path)
        directory = os.path.dirname(os.path.abspath(store_path))
        if directory:
            os.makedirs(directory, exist_ok=True)

    def _load(self) -> list:
        try:
            with open(self.store_path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, records: list) -> None:
        with open(self.store_path, "w", encoding="utf-8") as handle:
            json.dump(records, handle)

    def record(self, change_id: str, *, change_class: str,
               before, after, undoable: bool = True) -> dict:
        """Record a change. If its class is on the cannot-undo
        list, ``undoable`` is forced False and the reason is
        written in the record — never a false promise."""
        if change_class in CANNOT_UNDO:
            undoable = False
            reason = f"class '{change_class}' cannot be taken back"
        else:
            reason = ""
        records = self._load()
        entry = {"id": change_id, "class": change_class,
                 "before": before, "after": after,
                 "undoable": bool(undoable), "reason": reason,
                 "history": [], "at": time.time()}
        records.append(entry)
        self._save(records)
        return entry

    def undo(self, change_id: str, restorer, *,
             failsafe_tripped=None) -> dict:
        """Undo one recorded change via its restorer.

        ``restorer(before)`` must return True on verified
        restoration. A tripped failsafe, a non-undoable record,
        a missing record, or a restorer returning False all
        refuse/fail plainly — none of them is reported as undone."""
        if failsafe_tripped is not None and failsafe_tripped():
            return {"undone": False, "reason": "failsafe-tripped"}
        records = self._load()
        for entry in records:
            if entry["id"] != change_id:
                continue
            if not entry["undoable"]:
                return {"undone": False,
                        "reason": entry.get("reason") or "not undoable"}
            try:
                ok = bool(restorer(entry["before"]))
            except Exception as exc:  # noqa: BLE001 — restorer failure is a failure
                ok = False
                entry["history"].append(
                    {"action": "undo-failed", "error": str(exc)})
                self._save(records)
                return {"undone": False, "reason": f"restorer failed: {exc}"}
            entry["history"].append(
                {"action": "undone" if ok else "undo-failed"})
            self._save(records)
            return {"undone": ok,
                    "reason": "" if ok else "restorer did not verify restoration"}
        return {"undone": False, "reason": "no such change record"}

    def summary(self) -> list:
        """Every record, in plain words, for the GUI to show."""
        out = []
        for entry in self._load():
            undone = any(h.get("action") == "undone"
                         for h in entry["history"])
            out.append({
                "id": entry["id"], "class": entry["class"],
                "can_be_taken_back": entry["undoable"],
                "taken_back": undone,
                "reason": entry.get("reason", ""),
            })
        return out


class TwoKeyGate:
    """Two keys for big moves (P22, Tier 2 + confirmation).

    ``present(key)`` collects distinct keys. The gate opens only
    when two DIFFERENT keys have been presented within the
    expiry window. Presenting the same key twice never opens it,
    and an expired gate refuses until two fresh keys arrive."""

    def __init__(self, expiry_s: float = 300.0, clock=None):
        self.expiry_s = float(expiry_s)
        self._clock = clock or time.time
        self._keys: list[tuple[str, float]] = []

    def present(self, key: str) -> dict:
        now = self._clock()
        self._keys = [(k, t) for k, t in self._keys
                      if now - t <= self.expiry_s]
        if key not in [k for k, _ in self._keys]:
            self._keys.append((key, now))
        distinct = len({k for k, _ in self._keys})
        return {"keys_present": distinct, "open": distinct >= 2,
                "note": "two DIFFERENT keys required; one key twice never opens"}

    def consume(self) -> dict:
        """Open the gate once, then clear the keys. A consumed
        gate never stays open for a second big move."""
        now = self._clock()
        self._keys = [(k, t) for k, t in self._keys
                      if now - t <= self.expiry_s]
        if len({k for k, _ in self._keys}) < 2:
            return {"opened": False, "reason": "two distinct keys required"}
        self._keys = []
        return {"opened": True}
