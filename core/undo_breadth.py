"""Wave 4 UNDO item 13 — universal undo across change classes.

Builds on Wave 3 scoped undo (core/undo_store.py): that module's
cannot-undo list is reused verbatim, and its UndoStore is the
per-class store. This module adds the cross-class layer: one
registry of change classes, each with its own restorer, plus a
summary that shows, upfront, which classes can and cannot be
taken back.

Rules, enforced in code:
  * The cannot-undo list is shown upfront (cannot_undo_list from
    Wave 3) and is never shortened here.
  * A change class on that list is recorded as non-undoable with
    its reason, before it happens — never as a record pretending
    it can be undone. And per v6 UNDO item 13, an irreversible
    change needs explicit authorisation first: recording one
    without authorised=True is refused outright.
  * Never permanent delete by default: this layer performs no
    deletion at all, and a deletion class with no recoverable
    (trash/backup) form is treated as irreversible under the
    rule above.
  * Undo runs the class restorer and reports success or failure
    exactly as the restorer reports it; a restorer failure is a
    failed restoration, never a success.
  * Restorers are caller-injected (the caller knows how to restore
    that class). This module never guesses a restoration.
"""

from __future__ import annotations

from .undo_store import cannot_undo_list


class UniversalUndo:
    """Cross-class undo registry. Records are in-memory; the Wave 3
    store remains the per-class persistent store, and this layer
    reuses its cannot-undo list verbatim."""

    def __init__(self):
        self._restorers = {}
        self._records = []

    def register_class(self, change_class: str, restorer) -> dict:
        if not change_class:
            return {"ok": False, "reason": "change class name is empty"}
        if not callable(restorer):
            return {"ok": False, "reason": "restorer must be callable"}
        self._restorers[change_class] = restorer
        return {"ok": True, "change_class": change_class}

    def record_change(self, change_class: str, before, after,
                      *, authorised: bool = False) -> dict:
        """Record a change, stating upfront whether it can be undone."""
        if change_class in cannot_undo_list():
            if not authorised:
                return {"ok": False, "change_class": change_class,
                        "undoable": False,
                        "reason": f"class '{change_class}' is on the "
                                  "cannot-undo list and needs explicit "
                                  "authorisation first: refused"}
            return {"ok": True, "change_class": change_class,
                    "undoable": False,
                    "reason": f"class '{change_class}' is on the "
                              "cannot-undo list: it cannot be taken back, "
                              "and explicit authorisation was supplied"}
        if change_class not in self._restorers:
            return {"ok": False, "change_class": change_class,
                    "reason": f"no restorer registered for '{change_class}'; "
                              "change not recorded as undoable"}
        record = {"change_class": change_class, "before": before,
                  "after": after, "undone": False}
        self._records.append(record)
        return {"ok": True, "change_class": change_class, "undoable": True,
                "reason": "recorded; restorer registered"}

    def undo(self, change_class: str) -> dict:
        """Undo the most recent recorded change of a class."""
        records = getattr(self, "_records", [])
        for record in reversed(records):
            if record["change_class"] == change_class and not record["undone"]:
                restorer = self._restorers.get(change_class)
                try:
                    result = restorer(record["before"])
                except Exception as exc:
                    return {"ok": False, "change_class": change_class,
                            "reason": f"restoration failed: {exc}"}
                if result is False:
                    return {"ok": False, "change_class": change_class,
                            "reason": "restorer reported failure"}
                record["undone"] = True
                return {"ok": True, "change_class": change_class,
                        "reason": "restoration reported success"}
        return {"ok": False, "change_class": change_class,
                "reason": "no undoable recorded change for this class"}

    def summary(self) -> dict:
        records = getattr(self, "_records", [])
        return {"cannot_undo": cannot_undo_list(),
                "registered_classes": sorted(self._restorers),
                "recorded": len(records),
                "undone": sum(1 for r in records if r["undone"])}
