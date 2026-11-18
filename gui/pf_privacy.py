"""PF-3 privacy dashboard surface (GUI split #3)."""

from __future__ import annotations

import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .pf_surfaces_helpers import _card, _muted




# ================================================================ PF-3
# Privacy dashboard GUI over the Wave 6 dashboard API.
# The API's own contract is honoured, including its limits:
# delete_one deletes the file record only (DB rows are
# rollback material and are NOT deleted); wipe_all refuses
# unless confirm == "WIPE ALL" exactly; export_milestones
# is read-only. The widget never softens those notes.

KIND_LABELS = {
    "fact": "Fact",
    "opinion": "Opinion — VYUK's guess, not a fact",
    "inferred": "Inferred — expires after 90 days",
    "person": "Person",
    "promise": "Promise",
}

WIPE_CONFIRM_TEXT = "WIPE ALL"


def default_privacy_api():
    """The Wave 6 dashboard API, lazily imported so this
    module stays importable without the memory store.
    Callables take the same arguments as privacy's
    functions (list_stored_facts, delete_one, wipe_all,
    export_milestones), with base passed through by the
    caller-side wiring. The widget calls them without a
    base (production store); tests inject a fake api bound
    to a temp base instead."""
    from voice_assistant.core.gui_facade import privacy

    return {
        "list": lambda kinds=None: privacy.list_stored_facts(
            kinds=kinds),
        "delete": lambda category, key: privacy.delete_one(
            category, key),
        "wipe": lambda confirm: privacy.wipe_all(
            confirm=confirm),
        "export": lambda: privacy.export_milestones(),
    }


def kind_label(kind):
    return KIND_LABELS.get(kind, "Stored note")


def milestone_line(entry):
    """One readable line for a milestone-log entry. Entry
    shapes vary (the log is append-only and schemaless),
    so prefer a human field, fall back to compact JSON —
    never drop an entry silently."""
    if isinstance(entry, dict):
        for key in ("title", "text", "message", "event",
                    "summary", "name"):
            value = entry.get(key)
            if value:
                rest = {k: v for k, v in entry.items()
                        if k != key}
                suffix = ""
                for date_key in ("date", "ts", "timestamp",
                                 "created_at"):
                    if entry.get(date_key):
                        suffix = f" ({entry[date_key]})"
                        break
                _ = rest
                return f"{value}{suffix}"
        return json.dumps(entry, ensure_ascii=False)
    return str(entry)


class PrivacyDashboardWidget(QWidget):
    """The PF-3 surface: list stored facts by category,
    delete one, wipe all (exact-confirm, fail-closed), and
    export the milestone log. ``api`` is the provider seam
    (see default_privacy_api); with no api, the Wave 6 API
    is used. The widget writes nothing itself — every
    mutation goes through the api, and every api note is
    shown, including the DB-not-deleted note."""

    def __init__(self, api=None, parent=None):
        super().__init__(parent)
        self.api = api if api is not None else default_privacy_api()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card, card_layout = _card(
            layout, "Your stored facts",
            "Everything VYUK stores about you, on this PC. "
            "Opinions are labelled as opinions, never as "
            "facts. Inferred traits expire after 90 days. "
            "Deleting a fact removes its file record; if "
            "the same fact also lives in the frozen "
            "database, the result says so plainly — that "
            "row is rollback material and is not deleted "
            "here.")
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Category:"))
        self.category_box = QComboBox()
        self.category_box.addItem("All")
        self.category_box.currentTextChanged.connect(
            self._on_filter_changed)
        filter_row.addWidget(self.category_box, stretch=1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        filter_row.addWidget(self.refresh_btn)
        card_layout.addLayout(filter_row)
        # UI settings slice: same nested-scroller removal as
        # HelpWidget above — the facts list previously scrolled
        # inside the settings page's own scroll area. Fact rows
        # now flow into the page scroll; the page scroll is the
        # one scroller.
        inner = QWidget()
        self.facts_layout = QVBoxLayout(inner)
        self.facts_layout.setContentsMargins(0, 0, 0, 0)
        self.facts_layout.setSpacing(10)
        inner.setAutoFillBackground(False)
        card_layout.addWidget(inner)
        self.facts_empty = _muted("")
        self.facts_empty.setVisible(False)
        card_layout.addWidget(self.facts_empty)
        self.status_label = _muted("")
        self.status_label.setVisible(False)
        card_layout.addWidget(self.status_label)
        # Wipe: exact-confirm, fail-closed, mirroring the API.
        wipe_title = QLabel("Wipe all stored facts")
        wipe_title.setStyleSheet("font-weight: 600;")
        card_layout.addWidget(wipe_title)
        card_layout.addWidget(_muted(
            "This deletes every file-store fact. It cannot "
            "be undone. Type WIPE ALL exactly to confirm — "
            "anything else refuses and deletes nothing."))
        wipe_row = QHBoxLayout()
        self.wipe_confirm = QLineEdit()
        self.wipe_confirm.setPlaceholderText("Type WIPE ALL exactly")
        wipe_row.addWidget(self.wipe_confirm, stretch=1)
        self.wipe_btn = QPushButton("Wipe all")
        self.wipe_btn.clicked.connect(self._on_wipe)
        wipe_row.addWidget(self.wipe_btn)
        card_layout.addLayout(wipe_row)
        # Milestone export: read-only, readable lines.
        export_title = QLabel("Milestone export")
        export_title.setStyleSheet("font-weight: 600;")
        card_layout.addWidget(export_title)
        self.export_btn = QPushButton("Export milestone log")
        self.export_btn.clicked.connect(self._on_export)
        card_layout.addWidget(
            self.export_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self.export_view = QLabel("")
        self.export_view.setObjectName("muted")
        self.export_view.setWordWrap(True)
        self.export_view.setVisible(False)
        card_layout.addWidget(self.export_view)
        self._facts = []
        self.refresh()

    # ------------------------------------------------------------ data
    def refresh(self):
        try:
            facts = self.api["list"]()
        except Exception as exc:  # noqa: BLE001 — widget says so
            self._facts = []
            self._render_facts()
            self._show_status(
                f"Stored facts could not be listed "
                f"({type(exc).__name__}). Next step: "
                "press Refresh to try again, or open the "
                "Doctor from Advanced.")
            return
        self._facts = list(facts or [])
        categories = sorted({str(item.get("category", ""))
                             for item in self._facts if item.get("category")})
        current = self.category_box.currentText()
        self.category_box.blockSignals(True)
        self.category_box.clear()
        self.category_box.addItem("All")
        for category in categories:
            self.category_box.addItem(category)
        if current in categories or current == "All":
            self.category_box.setCurrentText(current)
        self.category_box.blockSignals(False)
        self._render_facts()

    def _visible_facts(self):
        chosen = self.category_box.currentText()
        if chosen == "All":
            return self._facts
        return [item for item in self._facts
                if str(item.get("category", "")) == chosen]

    def _render_facts(self):
        while self.facts_layout.count():
            item = self.facts_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        visible = self._visible_facts()
        for fact in visible:
            row = QFrame()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(2)
            title = QLabel(
                f"{fact.get('category', '')} · {fact.get('key', '')}")
            title.setStyleSheet("font-weight: 600;")
            title.setWordWrap(True)
            row_layout.addWidget(title)
            value = QLabel(str(fact.get("value", "")))
            value.setObjectName("muted")
            value.setWordWrap(True)
            row_layout.addWidget(value)
            row_layout.addWidget(_muted(
                kind_label(str(fact.get("kind", "")))))
            delete_btn = QPushButton("Delete this fact")
            category = fact.get("category", "")
            key = fact.get("key", "")
            delete_btn.clicked.connect(
                lambda _=False, c=category, k=key:
                    self.delete_fact_row(c, k))
            row_layout.addWidget(
                delete_btn, alignment=Qt.AlignmentFlag.AlignLeft)
            self.facts_layout.addWidget(row)
        self.facts_layout.addStretch()
        self.facts_empty.setVisible(not visible)
        if not visible:
            if not self._facts:
                self.facts_empty.setText(
                    "VYUK stores no facts about you yet. "
                    "Next step: tell her something worth "
                    "remembering, then press Refresh.")
            else:
                self.facts_empty.setText(
                    "No stored facts in this category. "
                    "Next step: pick All above to see "
                    "every category.")

    def _show_status(self, text):
        self.status_label.setVisible(True)
        self.status_label.setText(text)

    def _on_filter_changed(self, _text):
        self._render_facts()

    # ------------------------------------------------------------ actions
    def delete_fact_row(self, category, key):
        try:
            result = self.api["delete"](category, key)
        except Exception as exc:  # noqa: BLE001 — widget says so
            self._show_status(
                f"That fact could not be deleted "
                f"({type(exc).__name__}). Next step: press "
                "Refresh and try again.")
            return {"deleted": False, "error": type(exc).__name__}
        if result.get("deleted"):
            self._show_status(
                f"Deleted: {category} · {key}. "
                f"{result.get('note', '')}")
        else:
            self._show_status(
                f"Not deleted: {category} · {key}. "
                f"{result.get('note', '')} Next step: if "
                "this fact must go everywhere, full "
                "everywhere-delete is still an open "
                "decision — the file record is deleted, "
                "but a matching database row, if any, is "
                "rollback material and is not deleted here.")
        self.refresh()
        return result

    def _on_wipe(self):
        confirm = self.wipe_confirm.text()
        if confirm != WIPE_CONFIRM_TEXT:
            self._show_status(
                "Wipe refused: the confirmation did not "
                "read WIPE ALL exactly, so nothing was "
                "deleted. Next step: type WIPE ALL exactly "
                "if you mean to wipe everything.")
            return
        try:
            result = self.api["wipe"](confirm)
        except Exception as exc:  # noqa: BLE001 — widget says so
            self._show_status(
                f"Wipe could not run ({type(exc).__name__}). "
                "Nothing was deleted. Next step: press "
                "Refresh and try again.")
            return
        if result.get("refused"):
            self._show_status(
                f"Wipe refused: {result.get('reason', '')} "
                "Nothing was deleted.")
        else:
            self._show_status(
                f"Wiped {result.get('wiped', 0)} stored "
                "fact files. This cannot be undone.")
        self.wipe_confirm.clear()
        self.refresh()

    def _on_export(self):
        try:
            result = self.api["export"]()
        except Exception as exc:  # noqa: BLE001 — widget says so
            self.export_view.setVisible(True)
            self.export_view.setText(
                f"Milestone export could not run "
                f"({type(exc).__name__}). Next step: press "
                "Refresh, then try the export again.")
            return
        if not result.get("available"):
            self.export_view.setVisible(True)
            self.export_view.setText(
                "No milestone log is available to export "
                f"({result.get('reason', 'absent')}). Next "
                "step: milestones appear as VYUK reaches "
                "them; try again after she records one.")
            return
        entries = result.get("entries", [])
        lines = [milestone_line(entry) for entry in entries]
        self.export_view.setVisible(True)
        if not lines:
            self.export_view.setText(
                "The milestone log is empty — nothing to "
                "export yet. Next step: try again after "
                "VYUK records a milestone.")
        else:
            self.export_view.setText(
                f"{len(lines)} milestone entries:\n"
                + "\n".join(lines))
