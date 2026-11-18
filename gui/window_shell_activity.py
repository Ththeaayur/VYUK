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


class ShellActivityMixin:


    def _show_recent_activity(self):
        """S12 (S2): the activity log, readable in-app — the
        same lines the get_activity_log skill reads."""
        from voice_assistant.core.gui_facade import DATA_DIR

        dlg = QDialog(self)
        dlg.setWindowTitle("Recent activity")
        dlg.resize(720, 480)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(view, stretch=1)
        row = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        row.addWidget(btn_refresh)
        row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        # UI interaction slice: the read-only dialog's safe button
        # is the default — Enter closes.
        btn_close.setDefault(True)
        row.addWidget(btn_close)
        lay.addLayout(row)

        def _fill():
            log = Path(DATA_DIR) / "activity.log"
            try:
                lines = log.read_text(
                    encoding="utf-8", errors="replace").splitlines()
            except Exception:  # noqa: BLE001
                lines = []
            view.setPlainText(
                "\n".join(lines[-200:]) if lines else
                "No activity logged yet.")
            bar = view.verticalScrollBar()
            bar.setValue(bar.maximum())

        btn_refresh.clicked.connect(_fill)
        _fill()
        dlg.exec()


    def _export_chat(self):
        """S12 (C1): save the conversation as a readable
        Markdown file — who said what, and when."""
        bubbles = self._chat_bubbles()
        if not bubbles:
            self._notify(
                "Nothing to export yet — the conversation is empty.")
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "Export conversation", "visor-conversation.md",
            "Markdown files (*.md);;Text files (*.txt)")
        if not path:
            return
        parts = ["# Conversation with VYUK", ""]
        for b in bubbles:
            who = b.who_lbl.text()
            when = b.time_lbl.text()
            head = f"**{who}**" + (f" · {when}" if when else "")
            parts.append(head)
            parts.append("")
            parts.append(b._plain_text)
            parts.append("")
        try:
            Path(path).write_text(
                "\n".join(parts), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self._notify(f"Couldn't write the file: {e}")
            return
        self._notify(
            f"Conversation saved to {Path(path).name}.")


    def _composer_recall(self, step):
        """S12 (C2): walk the sent-message history for the
        composer's Up/Down keys. Returns the line to show, or
        None to leave the keypress alone (a draft is being
        typed, or there is nothing to recall)."""
        hist = getattr(self, "_sent_history", None) or []
        if not hist:
            return None
        idx = getattr(self, "_recall_idx", None)
        if idx is None:
            if self.input_cmd.text().strip():
                return None
            idx = len(hist)
        idx = max(0, min(len(hist), idx + step))
        self._recall_idx = idx
        if idx >= len(hist):
            self._recall_idx = None
            return ""
        return hist[idx]


    def _toggle_summon(self):
        """Ctrl+Shift+Space (v4.1): hide VYUK if she is front and centre,
        otherwise summon her. The hotkey daemon calls this via the
        summon_requested signal, so it always runs on the GUI thread."""
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.hide()
        else:
            if self.isMinimized():
                self.showNormal()
            self._tray_open()
            # Conversion Wave 9: the floating summon window, placed
            # on the cursor's monitor work area (re-validated on
            # every summon). Dispatch from it is gated, never
            # executed by the window itself.
            try:
                from .w9_wiring_summon import show_summon_window
                show_summon_window(self)
            except Exception:  # noqa: BLE001 — summon never breaks summon
                pass
