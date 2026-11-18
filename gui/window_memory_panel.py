"""What I remember about you — panel (GAPS-27, M-V6).

The unified memory read (core/memory.read_memory) rendered as
a panel: every fact with WHY it is remembered (its source —
"You told me" / "You corrected me" / "Learned from use" /
"Recorded before sources were tracked"), the recent episodes
journal, and a per-item Delete button (MemoryStore.delete_fact).

SAFETY (the retention-loopholes memory privacy paradox):
credential-like facts are excluded by the read itself — this
panel never sees them, so it can never render them. The panel
says HOW MANY were hidden, never which. Deleting a fact is
the only write this surface performs, and only for the fact
the user selected.

Like gui/window_activity, this module imports no Qt at the
top: pure functions first, dialog builders (lazy Qt) at the
bottom.
"""

from __future__ import annotations

__all__ = ["memory_summary", "delete_memory_fact",
           "render_memory_text", "build_memory_dialog"]


def _read_memory_fn():
    """The core/memory/read_memory.read_memory function, or None
    when absent / unimportable. Never raises. Reached through
    the GUI facade (core/gui_facade — GUI files never import
    core directly)."""
    try:
        from voice_assistant.core.gui_facade import read_memory

        return read_memory
    except Exception:  # noqa: BLE001
        return None


def memory_summary(db_path=None, limit=100):
    """{"available", "facts", "episodes", "excluded", "error"}.
    Never raises."""
    fn = _read_memory_fn()
    if fn is None:
        return {"available": False, "facts": [], "episodes": [],
                "excluded": 0,
                "error": "memory not available yet"}
    try:
        if db_path is not None:
            mem = fn(db_path=db_path, limit=int(limit))
        else:
            mem = fn(limit=int(limit))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "facts": [], "episodes": [],
                "excluded": 0, "error": str(exc)}
    if not isinstance(mem, dict):
        return {"available": False, "facts": [], "episodes": [],
                "excluded": 0, "error": "read_memory() did not "
                                        "return a dict"}
    return {"available": True,
            "facts": mem.get("facts") or [],
            "episodes": mem.get("episodes") or [],
            "excluded": int(mem.get("excluded") or 0),
            "error": None}


def delete_memory_fact(fact_id, db_path=None):
    """Delete one fact by id. Returns {"ok", "fact_id",
    "error"}. Never raises; the only write this surface
    performs."""
    try:
        fact_id = int(fact_id)
    except (TypeError, ValueError):
        return {"ok": False, "fact_id": fact_id,
                "error": "no fact selected"}
    try:
        from voice_assistant.core.gui_facade import MemoryStore
    except Exception:  # noqa: BLE001
        return {"ok": False, "fact_id": fact_id,
                "error": "memory not available yet"}
    try:
        store = MemoryStore(db_path=db_path) if db_path is not None \
            else MemoryStore()
        ok = bool(store.delete_fact(fact_id))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "fact_id": fact_id, "error": str(exc)}
    return {"ok": ok, "fact_id": fact_id,
            "error": None if ok else "fact not found"}


def render_memory_text(summary):
    """A memory summary as plain display text. Never raises."""
    try:
        if not summary.get("available"):
            return ("Not yet available: "
                    + str(summary.get("error") or "memory "
                                                 "not available yet"))
        lines = []
        facts = summary.get("facts") or []
        episodes = summary.get("episodes") or []
        if not facts and not episodes:
            lines.append("Nothing remembered yet.")
        for fact in facts:
            label = fact.get("source_label") or fact.get("source") or ""
            lines.append(f"{fact.get('key', '')}: "
                         f"{fact.get('value', '')} — {label}")
        for episode in episodes:
            lines.append(f"{episode.get('date', '')} "
                         f"[{episode.get('kind', '')}]: "
                         f"{episode.get('text', '')}")
        excluded = summary.get("excluded") or 0
        if excluded:
            lines.append(
                f"{excluded} item(s) hidden because they look like "
                "credentials — they are never shown here.")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return "Not yet available."


# ---------------------------------------------------------------------------
# Dialog builder — Qt imported lazily here (see window_activity
# for the pattern). Returns (dialog, widgets) without exec()ing.
# ---------------------------------------------------------------------------

def build_memory_dialog(parent=None, db_path=None):
    """The memory panel: facts with their source, recent
    episodes, and a Delete button for the selected fact.
    Read-only except the explicit per-item delete. Returns
    (dialog, widgets)."""
    from PySide6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QPushButton,
        QVBoxLayout,
    )
    from voice_assistant.gui.style import DIALOG_STYLE

    dlg = QDialog(parent)
    dlg.setWindowTitle("What I remember about you")
    dlg.setStyleSheet(DIALOG_STYLE)
    dlg.resize(720, 480)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 20, 20, 16)
    lay.setSpacing(10)
    status = QLabel("")
    status.setObjectName("muted")
    status.setWordWrap(True)
    lay.addWidget(status)
    view = QListWidget()
    lay.addWidget(view, stretch=1)
    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    btn_delete = QPushButton("Delete selected")
    btn_refresh = QPushButton("Refresh")
    btn_close = QPushButton("Close")
    btn_row.addWidget(btn_delete)
    btn_row.addWidget(btn_refresh)
    btn_row.addWidget(btn_close)
    # UI interaction slice: the read-only dialog's safe button is
    # the default — Enter closes, never deletes.
    btn_close.setDefault(True)
    lay.addLayout(btn_row)

    def _fill():
        summary = memory_summary(db_path=db_path)
        if not summary.get("available"):
            status.setText("Memory: unavailable.")
            view.clear()
            view.addItem(render_memory_text(summary))
            _fill.fact_ids = []
            return
        excluded = summary.get("excluded") or 0
        hidden = (f" {excluded} credential-like item(s) hidden."
                  if excluded else "")
        status.setText(
            f"{len(summary.get('facts') or [])} fact(s), "
            f"{len(summary.get('episodes') or [])} recent "
            f"episode(s).{hidden}")
        view.clear()
        _fill.fact_ids = []
        for fact in summary.get("facts") or []:
            label = fact.get("source_label") or fact.get("source") or ""
            view.addItem(f"{fact.get('key', '')}: "
                         f"{fact.get('value', '')} — {label}")
            _fill.fact_ids.append(fact.get("id"))
        for episode in summary.get("episodes") or []:
            view.addItem(f"{episode.get('date', '')} "
                         f"[{episode.get('kind', '')}]: "
                         f"{episode.get('text', '')}")
            _fill.fact_ids.append(None)
        if not (summary.get("facts") or summary.get("episodes")):
            view.addItem("Nothing remembered yet — things you tell "
                         "VYUK to remember will appear here.")

    def _do_delete():
        row = view.currentRow()
        if row < 0 or row >= len(_fill.fact_ids):
            return {"ok": False, "error": "no fact selected"}
        fact_id = _fill.fact_ids[row]
        if fact_id is None:
            return {"ok": False, "error": "episodes cannot be "
                                          "deleted from here"}
        result = delete_memory_fact(fact_id, db_path=db_path)
        if result.get("ok"):
            _fill()
        return result

    _fill.fact_ids = []
    btn_refresh.clicked.connect(_fill)
    btn_delete.clicked.connect(_do_delete)
    btn_close.clicked.connect(dlg.accept)
    _fill()
    widgets = {"status": status, "view": view,
               "delete": btn_delete, "refresh": btn_refresh,
               "close": btn_close, "fill": _fill,
               "do_delete": _do_delete}
    return dlg, widgets
