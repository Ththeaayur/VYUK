# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import (
    QEvent,
    QRectF,
    QSettings,
    QSize,
    QThread,
    Qt,
    QTimer,
    Signal,
)
from voice_assistant.core.gui_facade import scheduler


def _memory_store():
    """The shared memory store, resolved at CALL time (the
    gui/memory_view._default_store shape): the facade rebinds
    memory_store under the harness shim, and a statically
    imported reference would keep the stub — so export, import,
    forget and undo would write to a store the page never reads.
    In production this returns the one singleton, unchanged."""
    from voice_assistant.core.gui_facade import memory_store

    return memory_store


class ShellMemoryIOMixin:


    def _export_memory(self):
        import json as _json
        from datetime import datetime as _dt

        try:
            facts = _memory_store().get_all_facts()
        except Exception as e:  # noqa: BLE001
            self.mem_export_label.setText(f"Couldn't read memory: {e}")
            self.mem_export_label.setVisible(True)
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "Export memory", "visor-memory.json",
            "JSON files (*.json)")
        if not path:
            return
        payload = {
            "app": "VYUK",
            "exported_at": _dt.now().isoformat(timespec="seconds"),
            "facts": [
                {"category": f.get("category", ""),
                 "key": f.get("key", ""), "value": f.get("value", "")}
                for f in facts
            ],
        }
        try:
            Path(path).write_text(
                _json.dumps(payload, indent=1, ensure_ascii=False),
                encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self.mem_export_label.setText(f"Couldn't write the file: {e}")
            self.mem_export_label.setVisible(True)
            return
        self.mem_export_label.setText(
            f"Saved {len(facts)} remembered facts to "
            f"{Path(path).name}.")
        self.mem_export_label.setVisible(True)

    # ------------------------------------------------ S12 methods
    def _remove_selected_reminder(self):
        """S12 (R1): cancel the reminder picked in the list —
        the scheduler always could; the page never asked."""
        item = (self.reminders_list.currentItem()
                if hasattr(self, "reminders_list") else None)
        label = (item.data(Qt.ItemDataRole.UserRole)
                 if item is not None else None)
        if not label:
            self._notify("Pick a reminder in the list first.")
            return
        if scheduler.cancel_reminder(str(label)):
            self._refresh_reminders()
            self._notify("Reminder removed.")
        else:
            self._refresh_reminders()
            self._notify("That reminder is already gone.")


    def _import_memory(self):
        """S12 (M3): bring facts in from an export file —
        the §7 export's missing other half."""
        import json as _json

        path, _sel = QFileDialog.getOpenFileName(
            self, "Import memory", "",
            "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            payload = _json.loads(
                Path(path).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            self.mem_export_label.setText(
                f"Couldn't read that file: {e}")
            self.mem_export_label.setVisible(True)
            return
        rows = payload.get("facts") if isinstance(payload, dict) \
            else payload
        if not isinstance(rows, list):
            self.mem_export_label.setText(
                "That file doesn't hold a memory export — "
                "no facts list in it.")
            self.mem_export_label.setVisible(True)
            return
        saved = skipped = 0
        for row in rows:
            if not isinstance(row, dict):
                skipped += 1
                continue
            key = str(row.get("key") or "").strip()
            value = str(row.get("value") or "").strip()
            if not key or not value:
                skipped += 1
                continue
            category = str(row.get("category") or "general").strip() \
                or "general"
            try:
                if _memory_store().save_fact(category, key, value):
                    saved += 1
                else:
                    skipped += 1
            except Exception:  # noqa: BLE001
                skipped += 1
        self._refresh_memories()
        note = f"Imported {saved} memor{'y' if saved == 1 else 'ies'}"
        if skipped:
            note += f" · skipped {skipped} (no name or detail)"
        self.mem_export_label.setText(note + ".")
        self.mem_export_label.setVisible(True)


    def _forget_selected_memory(self):
        """Forget the list-picked facts. One tap — the delete
        happens at once — with one-tap undo on the bar below
        (Build 6 §6.5: delete is the one action that cannot be
        taken back by re-asking, so the undo lives on the page
        for a few seconds after the delete)."""
        if not hasattr(self, "memory_list"):
            return
        fact_ids = [item.data(Qt.ItemDataRole.UserRole)
                    for item in self.memory_list.selectedItems()]
        fact_ids = [fid for fid in fact_ids if fid is not None]
        if not fact_ids:
            self._notify("Pick a memory in the list first.")
            return
        self._forget_memory_facts(fact_ids)

    def _forget_memory_fact(self, fact_id):
        """Forget one fact (a row's hover action)."""
        if fact_id is None:
            return
        self._forget_memory_facts([fact_id])

    def _forget_memory_facts(self, fact_ids):
        by_id = getattr(self, "_mem_facts_by_id", None) or {}
        gone = []
        for fid in fact_ids:
            fact = by_id.get(fid)
            if fact is None:
                continue
            try:
                ok = bool(_memory_store().delete_fact(fid))
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                gone.append(dict(fact))
        self._refresh_memories()
        if gone:
            self._show_mem_undo(gone)
        else:
            self._notify("Those memories were already gone.")

    def _show_mem_undo(self, facts):
        """The one-tap undo bar: the deleted facts, restorable
        for a few seconds."""
        self._mem_undone_facts = list(facts)
        label = getattr(self, "_mem_undo_label", None)
        bar = getattr(self, "_mem_undo_bar", None)
        if label is None or bar is None:
            return
        if len(facts) == 1:
            label.setText(f"Forgot “{facts[0].get('key', '')}”.")
        else:
            label.setText(f"Forgot {len(facts)} memories.")
        bar.setVisible(True)
        QTimer.singleShot(8000, self._hide_mem_undo)

    def _hide_mem_undo(self):
        bar = getattr(self, "_mem_undo_bar", None)
        if bar is not None:
            bar.setVisible(False)
        self._mem_undone_facts = []

    def _undo_mem_forget(self):
        """Put the just-forgotten facts back, exactly as they
        were (same category, key and value)."""
        facts = list(getattr(self, "_mem_undone_facts", None) or [])
        self._hide_mem_undo()
        if not facts:
            return
        restored = 0
        for fact in facts:
            try:
                if _memory_store().save_fact(
                        str(fact.get("category") or "general"),
                        str(fact.get("key") or ""),
                        str(fact.get("value") or "")):
                    restored += 1
            except Exception:  # noqa: BLE001
                pass
        self._refresh_memories()
        if restored:
            self._notify(
                f"Restored {restored} memor"
                f"{'y' if restored == 1 else 'ies'}.")
        else:
            self._notify(
                "Couldn't restore those memories — "
                "the memory is full.")


    def _open_data_folder(self):
        """S12 (S1): open the folder her data lives in."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from voice_assistant.core.gui_facade import DATA_DIR

        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(DATA_DIR)))
