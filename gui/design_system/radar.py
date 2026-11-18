"""CommandRadar — the command palette panel (guide §8).

A centred panel (max 760 by 620) with a search field, scope chips,
result tags and a preview rail. Items are DATA ONLY (label, scope,
preview): the radar never holds a callable and never dispatches.
Enter or double-click previews the selected result; dispatch is
always the caller's job. Fuzzy matches never auto-dispatch.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from ..style import (
    ACCENT,
    INPUT_BG,
    LINE_SUBTLE,
    PRIMARY_WASH,
    SURFACE_1,
    SURFACE_2,
    SURFACE_3,
    TEXT,
    TEXT_MUTED,
)

SCOPES = ("PAGE", "ACTION", "SETTING", "TOOL", "RECENT", "SCREEN")


class CommandRadar(QDialog):
    """Search, scope and preview — never dispatch."""

    previewed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumSize(760, 620)
        self.setWindowTitle("Command")
        # Top-level dialogs get no sheet from the main window (the
        # same reason HudDialog carries its own): the radar must
        # never render in the default light palette.
        self.setStyleSheet(
            "QDialog { background-color: %s; }"
            "QDialog QLabel { color: %s; }"
            "QDialog QLineEdit { background-color: %s;"
            " border: 1px solid %s; border-radius: 2px;"
            " padding: 8px 12px; color: %s; }"
            "QDialog QPushButton { background-color: %s;"
            " border: 1px solid %s; border-radius: 2px;"
            " padding: 8px 14px; color: %s; }"
            "QDialog QPushButton:checked { background-color: %s;"
            " border: 1px solid %s; }"
            "QDialog QListWidget { background: transparent; border: none; }"
            "QDialog QListWidget::item { padding: 8px 12px; color: %s; }"
            "QDialog QListWidget::item:selected { background-color: %s;"
            " color: %s; }"
            % (
                SURFACE_1, TEXT, INPUT_BG, LINE_SUBTLE, TEXT,
                SURFACE_2, LINE_SUBTLE, TEXT_MUTED, PRIMARY_WASH,
                ACCENT, TEXT, SURFACE_3, TEXT,
            )
        )
        self._items = []
        layout = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search commands, pages, settings…")
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)
        chips = QHBoxLayout()
        self._chip_buttons = {}
        for scope in SCOPES:
            button = QPushButton(scope)
            button.setCheckable(True)
            button.setChecked(True)
            button.toggled.connect(self._apply_filter)
            chips.addWidget(button)
            self._chip_buttons[scope] = button
        chips.addStretch(1)
        layout.addLayout(chips)
        self._results = QListWidget()
        self._results.itemDoubleClicked.connect(self._on_activated)
        layout.addWidget(self._results)
        self._preview = QLabel("")
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)
        self._results.currentRowChanged.connect(self._on_row_changed)

    def set_items(self, items):
        """Install result data. Any callable under any key is ignored:
        the radar stores label, scope and preview strings only."""
        self._items = [
            {
                "label": str(item.get("label", "")),
                "scope": str(item.get("scope", "")),
                "preview": str(item.get("preview", "")),
            }
            for item in items
        ]
        self._apply_filter()

    def _active_scopes(self):
        return {
            scope
            for scope, button in self._chip_buttons.items()
            if button.isChecked()
        }

    def _apply_filter(self, *_args):
        query = self._search.text().strip().lower()
        scopes = self._active_scopes()
        self._results.clear()
        self._visible = []
        for item in self._items:
            if item["scope"] and item["scope"] not in scopes:
                continue
            if query and query not in item["label"].lower():
                continue
            self._visible.append(item)
            self._results.addItem(f"[{item['scope']}] {item['label']}")
        if self._visible:
            self._results.setCurrentRow(0)

    def _on_row_changed(self, row):
        if 0 <= row < len(getattr(self, "_visible", [])):
            self._preview.setText(self._visible[row]["preview"])

    def _on_activated(self, _item):
        row = self._results.currentRow()
        if 0 <= row < len(getattr(self, "_visible", [])):
            self.previewed.emit(self._visible[row]["label"])

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_activated(None)
            return
        super().keyPressEvent(event)

    @property
    def visible_count(self):
        """Results currently shown after search + scope filtering."""
        return len(getattr(self, "_visible", []))

    @property
    def preview_text(self):
        """The preview rail's text."""
        return self._preview.text()
