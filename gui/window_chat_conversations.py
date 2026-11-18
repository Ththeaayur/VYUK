"""Conversations dialog (chat UI build, 2026-11-16).

The conversation list (move 9): one row per day with the first
question that day as its title, newest first. Opening a day
loads that day's window into the transcript with a "Back to
latest" way home — no schema change, no new store table: the
days are derived from the stored log by grouping on the local
calendar date, and the title is the day's first user question
shortened to ~40 chars. A day with no user question (a day of
only VYUK replies, or only system lines) titles itself from its
first line instead, and never shows an empty title.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class ConversationsDialog(QDialog):
    """One row per day. ``days`` is a list of dicts with keys
    ``label`` (e.g. "Tue 16 Nov"), ``title`` (the day's first
    question), ``count`` (messages that day), and ``rows`` (the
    stored rows for that day, in time order). ``on_open`` is
    called with the chosen day's rows."""

    def __init__(self, parent, days, on_open):
        super().__init__(parent)
        self.setWindowTitle("Conversations")
        self.setModal(True)
        self._days = list(days or [])
        self._on_open = on_open
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        hint = QLabel("Pick a day to read it back.")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self._list = QListWidget()
        self._list.setObjectName("conversationsList")
        for day in self._days:
            title = (day.get("title") or "").strip()
            if len(title) > 60:
                title = title[:57] + "…"
            label = day.get("label") or ""
            count = int(day.get("count") or 0)
            item = QListWidgetItem(
                f"{label} — {title}" if title else label)
            item.setToolTip(
                f"{count} message{'s' if count != 1 else ''} "
                f"on {label}")
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(self._open_selected)
        layout.addWidget(self._list, 1)
        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("chipBtn")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        open_btn = QPushButton("Open")
        open_btn.setObjectName("chipBtn")
        open_btn.setDefault(True)
        open_btn.clicked.connect(self._open_selected)
        row.addWidget(open_btn)
        layout.addLayout(row)
        self.resize(460, 380)

    def _open_selected(self, *_args):
        try:
            idx = self._list.currentRow()
            if idx < 0 or idx >= len(self._days):
                return
            day = self._days[idx]
            self.accept()
            if self._on_open is not None:
                self._on_open(day.get("rows") or [])
        except Exception:  # noqa: BLE001 — the dialog never breaks chat
            pass
