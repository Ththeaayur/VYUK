"""v5.57 (redo): the Doctor page — VYUK's health at a glance.

Runs the existing doctor.py checks (untouched) in a worker thread,
captures their printed report verbatim, and renders it as: a status
hero (healthy / needs attention / broken), one card per warning or
failure, and the full text report one disclosure away.

Next steps are the doctor's own words first: a finding's `fix:` line
is shown on its card as the next step. Where a fix names a place
VYUK can open (her brain choice, her voice settings, the on-PC brain
download), the card also gets a button that opens it — navigation
only, no new behaviour. Findings with no fix line and no mapping
show their detail and point at the full report; nothing is invented.
"""
import datetime
import os
import re
import shutil
import subprocess
import sys
import time

from PySide6.QtCore import QThread, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .design_system.dialogs import HudDialog
from .style import (
    TYPE_CARD_TITLE,
    # Command Deck Phase E: Doctor carries no colour literals —
    # finding severities are the role colours.
    BG,
    TEXT,
    LINE_SUBTLE,
    SELECTION_BG,
    OK,
    AMBER,
    DANGER,
    RADIUS_BTN,
)

# GUI split #1 INTENTIONAL: worker extracted to doctor_worker.py.
from .doctor_worker import _DoctorWorker
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set

_HEALTHY = OK
_ATTENTION = AMBER
_BROKEN = DANGER


# UI Phase 4 (D-P4-17): the six finding categories, in the
# order the page renders them inside each severity. The
# checkbox labels are the same words, title-cased for the
# picker ("Model / provider" reads better than the bare
# "model/provider" key).
_CATEGORY_ORDER = ("connectivity", "permissions", "installation",
                   "model/provider", "runtime", "recovery")
_CATEGORY_LABELS = {
    "connectivity": "Connectivity",
    "permissions": "Permissions",
    "installation": "Installation",
    "model/provider": "Model / provider",
    "runtime": "Runtime",
    "recovery": "Recovery",
}
_RUN_SELECTION_KEY = "doctor/run_selection"


def _category_check_counts() -> dict:
    """Checks per category, from doctor._CHECK_CATEGORIES.

    Fix round (F-P4-03, 2026-11-26): the category tiles carry
    their count. Read from the same table the suite asserts
    covers _CHECKS exactly, so the counts cannot drift from the
    checks. An unimportable doctor module reads as no counts —
    the tiles fall back to plain labels, never a wrong number.
    """
    try:
        from voice_assistant import doctor as _doctor
    except Exception:  # noqa: BLE001 — test path second
        try:
            import doctor as _doctor
        except Exception:  # noqa: BLE001 — no counts
            return {}
    try:
        from collections import Counter
        return dict(Counter(_doctor._CHECK_CATEGORIES.values()))
    except Exception:  # noqa: BLE001 — no counts
        return {}


def _redact_report_text(text: str) -> str:
    """Redact key-shaped material from a report before export
    (D-P4-18): the checks already report presence only, never a
    key value — this is the same rule applied once more at the
    export boundary, so a diagnostic line that ever carried a
    token cannot leave the machine in a file. Receipt contents
    are never in the report at all (doctor.py never prints
    them), so there is nothing to scrub there."""
    if not text:
        return text
    redacted = re.sub(r"AIza[0-9A-Za-z_\-]{10,}", "AIza…(redacted)",
                      text)
    redacted = re.sub(r"\bsk-[A-Za-z0-9]{8,}\b", "sk-…(redacted)",
                      redacted)
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|secret)\s*[:=]\s*\S+",
        r"\1: (redacted)", redacted)
    return redacted


class _RepairTypedConfirmDialog(HudDialog):
    """Typed confirmation for a destructive repair (D-P4-16) —
    the W10 reset pattern (window_pages_system.py): the user
    types the action's name exactly as the dialog shows it, and
    the confirm button stays disabled until the typed word
    matches. Cancel holds the default, so Enter keeps things
    as they are."""

    def __init__(self, parent=None, title="", body="", word="Repair",
                 button_text="Repair"):
        super().__init__(parent, title=title,
                         eyebrow="Destructive action",
                         severity="danger", destructive=True)
        self._word = word
        self.add_title(title, eyebrow="Destructive action")
        self.add_body(body + f"\n\nType {word} to confirm.")
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(word)
        self.content_layout().addWidget(self._edit)
        buttons = self.add_buttons([
            ("cancel", "Cancel", "reject", True, False),
            ("confirm", button_text, "accept", False, True),
        ])
        self._confirm_btn = buttons["confirm"]
        self._confirm_btn.setEnabled(False)
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.returnPressed.connect(self._on_return_pressed)

    def _on_text_changed(self, text: str):
        self._confirm_btn.setEnabled(text.strip() == self._word)

    def _on_return_pressed(self):
        if self._confirm_btn.isEnabled():
            self.accept()


class _RepairWorker(QThread):
    """Run one repair off the GUI thread (a pip install can take
    minutes) and report its result. The repair callable takes no
    arguments and returns a dict: {"ok": bool, "evidence": str}
    — evidence names what changed, never a secret value."""

    done = Signal(dict)

    def __init__(self, parent=None, fn=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn() if self._fn is not None else {
                "ok": False, "evidence": "no repair was supplied"}
        except Exception as exc:  # noqa: BLE001 — a repair that
            # raises reports, never crashes the page
            result = {"ok": False, "evidence": f"repair raised: {exc}"}
        self.done.emit(dict(result))


class DoctorPage(QWidget):
    """The Doctor page widget. The main window owns it; it only
    asks the window to navigate (show_settings_card) or to run an
    existing action (_setup_local_brain)."""

    # UI Phase 4 (D-P4-16, R-P4-B): the background download's
    # progress/done callbacks run on the download thread — these
    # signals hop them to the GUI thread (the window's own
    # brain_setup_progress pattern), so no widget is ever
    # touched from the worker thread.
    repair_download_progress = Signal(str)
    repair_download_done = Signal(list)

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._ran = False
        self._worker = None
        # UI Phase 4 (D-P4-16): one repair at a time — a second
        # Repair click while one runs is refused, never queued.
        self._repair_running = False
        self._repair_worker = None
        self._verify_worker = None
        self._last_report_text = ""
        self._run_started = None
        self._finding_cards: dict = {}
        self.repair_download_progress.connect(
            self._on_download_progress)
        self.repair_download_done.connect(self._on_download_done)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # UI Phase 4 (D-P4-28): the S1 truth — the Doctor HAS a
        # sidebar entry now (owner override 2026-10-24, sidebar
        # "Doctor", heartbeat, stack 7), so the page is not a
        # trap and the redundant "← Back to Settings" button is
        # gone. The sidebar is the way back; nothing here
        # re-runs or resets the checks on exit.

        # Status hero.
        self.hero = QFrame()
        self.hero.setObjectName("settingsCard")
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(4)
        self.hero_title = QLabel("Not run yet")
        # Build 6 (§6.6): the headline is the page's one large
        # element — 20 Sora semibold. It is a state, not a display
        # face: Sora, not Orbitron, because it is a sentence
        # fragment the user reads as news.
        self.hero_title.setStyleSheet("font-size: 20px; font-weight: 600;")
        hero_layout.addWidget(self.hero_title)
        self.hero_counts = QLabel(
            "Open this page and VYUK checks herself — no action needed.")
        self.hero_counts.setObjectName("muted")
        self.hero_counts.setWordWrap(True)
        hero_layout.addWidget(self.hero_counts)
        self.hero_last = QLabel("")
        self.hero_last.setObjectName("muted")
        hero_layout.addWidget(self.hero_last)
        self._read_last_run()
        # UI Phase 4 (D-P4-18): run selected checks — six
        # category checkboxes above Run ("Run" runs the
        # selection; "Run all" is the default, all six ticked).
        # The selection persists in doctor/run_selection
        # (QSettings, registered in core/config_spec.py).
        # A grid, not one row: six checkboxes in a row overflow
        # a 640 px window; two rows of three do not — but only
        # with the short Phase 4 labels. The fix-round labels
        # carry a glyph and a count ("✓ Connectivity · 4"), and
        # three of those columns overflow 640 again, so the grid
        # is responsive: three columns at >= 700 px page width,
        # two below (see _relayout_category_grid).
        self._category_boxes: dict = {}
        self._category_base_labels: dict = {}
        self._category_grid = None
        self._category_cols = None
        _cat_counts = _category_check_counts()
        cat_grid = QGridLayout()
        cat_grid.setContentsMargins(0, 6, 0, 0)
        cat_grid.setHorizontalSpacing(12)
        cat_grid.setVerticalSpacing(2)
        self._category_grid = cat_grid
        for _i, _cat in enumerate(_CATEGORY_ORDER):
            # Fix round (F-P4-03, 2026-11-26): each tile carries
            # its count ("Connectivity · 4") — a tile with no
            # number tells the user nothing. The glyph (✓ when
            # ticked) is synced by _sync_category_glyphs, which
            # runs on every toggle and after the stored selection
            # is applied.
            _n = _cat_counts.get(_cat)
            _base = (_CATEGORY_LABELS[_cat] if _n is None else
                     f"{_CATEGORY_LABELS[_cat]} · {_n}")
            self._category_base_labels[_cat] = _base
            _box = QCheckBox(_base)
            _box.setChecked(True)
            _box.toggled.connect(self._on_category_toggled)
            self._category_boxes[_cat] = _box
            cat_grid.addWidget(_box, _i // 3, _i % 3)
        hero_layout.addLayout(cat_grid)
        self._apply_stored_selection()
        self._sync_category_glyphs()
        self._relayout_category_grid()
        row = QHBoxLayout()
        self.btn_run = QPushButton("Run the checks")
        self.btn_run.clicked.connect(self.run)
        row.addWidget(self.btn_run, alignment=Qt.AlignmentFlag.AlignLeft)
        self.btn_run_all = QPushButton("Run all")
        self.btn_run_all.clicked.connect(self._on_run_all)
        row.addWidget(self.btn_run_all,
                      alignment=Qt.AlignmentFlag.AlignLeft)
        self.btn_export = QPushButton("Export report")
        self.btn_export.clicked.connect(self._on_export_clicked)
        row.addWidget(self.btn_export,
                      alignment=Qt.AlignmentFlag.AlignLeft)
        self.export_result = QLabel("")
        self.export_result.setObjectName("muted")
        self.export_result.setWordWrap(True)
        hero_layout.addLayout(row)
        hero_layout.addWidget(self.export_result)
        layout.addWidget(self.hero)

        # Wiring Build 3 — Resilience + undo, on this existing
        # page (the Doctor is the diagnostics/status page, so a
        # status surface with fix actions belongs here; no new
        # sidebar entry, no stack renumbering). The status lines
        # come from the existing renderer over the shared gate
        # payload — the same trip signal planner.run_goal halts
        # on — and the two buttons are the actions the audit
        # found missing: a re-arm button for a tripped fence,
        # and an undo button for the last undoable change. Both
        # are confirm-gated (default No); neither enables wake,
        # capture, or voiceprint, and neither runs a tool.
        self.resilience_card = QFrame()
        self.resilience_card.setObjectName("settingsCard")
        res_layout = QVBoxLayout(self.resilience_card)
        res_layout.setContentsMargins(16, 14, 16, 14)
        res_layout.setSpacing(4)
        res_title = QLabel("Resilience + undo")
        res_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        res_layout.addWidget(res_title)
        self.resilience_label = QLabel(
            "Resilience wiring: not yet configured in this session.")
        self.resilience_label.setObjectName("muted")
        self.resilience_label.setWordWrap(True)
        res_layout.addWidget(self.resilience_label)
        # Look-fix 2 (2026-11-24): the label used to carry the
        # verbatim status lines — a log-dump wall (the libraries
        # list alone is a 100+ char line). The card now shows a
        # short summary from the same payload, and the verbatim
        # lines live behind this disclosure — same facts, one tap
        # away, never hidden.
        self.resilience_details = QLabel("")
        self.resilience_details.setObjectName("muted")
        self.resilience_details.setWordWrap(True)
        self.resilience_details.hide()
        res_layout.addWidget(self.resilience_details)
        self.btn_resilience_details = QPushButton("Show full status")
        self.btn_resilience_details.setCheckable(True)
        self.btn_resilience_details.clicked.connect(
            self._on_resilience_details_toggled)
        res_layout.addWidget(self.btn_resilience_details,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        self._resilience_full_lines: list = []
        self.btn_resilience_rearm = QPushButton("Re-arm resilience")
        self.btn_resilience_rearm.clicked.connect(self._on_resilience_rearm)
        res_layout.addWidget(self.btn_resilience_rearm,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        # Code-Perfection Build 1 — "Reset failsafe", same card,
        # no new page, no new sidebar entry. Re-arm alone could
        # never resume control after a failsafe trip; this is the
        # action that clears the failsafe itself, through its own
        # reset path, confirm-gated like re-arm and undo.
        self.btn_failsafe_reset = QPushButton("Reset failsafe")
        self.btn_failsafe_reset.clicked.connect(self._on_failsafe_reset)
        res_layout.addWidget(self.btn_failsafe_reset,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        self.btn_undo_last = QPushButton("Undo last change")
        self.btn_undo_last.clicked.connect(self._on_undo_last)
        res_layout.addWidget(self.btn_undo_last,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        # Wave 7 Build 2 — upfront cannot-take-back disclosure,
        # on the same card, no new page, no renumbering. The
        # list is shown BEFORE any change, never after it;
        # a class on it is skipped and listed by undo, never
        # pretended undone.
        self.cannot_undo_label = QLabel(
            "Cannot be taken back: a message sent, a payment "
            "or purchase completed, a post published, a file "
            "deleted with no backup, a system change that "
            "already restarted the machine, an extension "
            "revocation, or a mission journal entry appended.")
        self.cannot_undo_label.setObjectName("muted")
        self.cannot_undo_label.setWordWrap(True)
        res_layout.addWidget(self.cannot_undo_label)
        self.resilience_result = QLabel("")
        self.resilience_result.setObjectName("muted")
        self.resilience_result.setWordWrap(True)
        res_layout.addWidget(self.resilience_result)
        layout.addWidget(self.resilience_card)
        self._refresh_resilience_status()

        # Triage Wire Build 3 — developer telemetry posture, on
        # this existing page (the triage row for developer_telemetry_doctor).
        # Report-only: posture_report() opens files read-only and
        # has no writer anywhere in its module, so this button can
        # never edit another product's settings. No new page, no
        # new sidebar entry.
        self.dev_posture_card = QFrame()
        self.dev_posture_card.setObjectName("settingsCard")
        dev_layout = QVBoxLayout(self.dev_posture_card)
        dev_layout.setContentsMargins(16, 14, 16, 14)
        dev_layout.setSpacing(4)
        dev_title = QLabel("Developer telemetry posture")
        dev_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        dev_layout.addWidget(dev_title)
        self.dev_posture_label = QLabel(
            "Report-only: VS Code, repo CLI, embedded-platform, "
            "agent, and Cline telemetry posture, as read from "
            "their own settings. Nothing here edits a setting.")
        self.dev_posture_label.setObjectName("muted")
        self.dev_posture_label.setWordWrap(True)
        dev_layout.addWidget(self.dev_posture_label)
        self.btn_dev_posture = QPushButton("Show telemetry posture")
        self.btn_dev_posture.clicked.connect(self._on_dev_posture)
        dev_layout.addWidget(self.btn_dev_posture,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        self.dev_posture_result = QLabel("")
        self.dev_posture_result.setObjectName("muted")
        self.dev_posture_result.setWordWrap(True)
        dev_layout.addWidget(self.dev_posture_result)
        layout.addWidget(self.dev_posture_card)

        # Wave 6 integration — Extensions (harness), on this
        # existing page (Doctor is the diagnostics/status page
        # with fix actions; extensions are a trust/status
        # surface, and a harness with no visible surface would
        # be library-only by another name). No new page, no
        # sidebar entry, no stack renumbering (Doctor stays
        # stack 7). The card calls the SAME shared harness
        # instance as the extension tools (same store), never
        # a second harness or store. Approve / Enable / Revoke
        # each ask HudDialog confirm Yes/No (default No) on top of
        # the harness's own rules; approve never enables, and
        # revoke is terminal. The harness governs extensions;
        # it never loads or runs extension code.
        self.ext_card = QFrame()
        self.ext_card.setObjectName("settingsCard")
        ext_layout = QVBoxLayout(self.ext_card)
        ext_layout.setContentsMargins(16, 14, 16, 14)
        ext_layout.setSpacing(4)
        ext_title = QLabel("Extensions (harness)")
        ext_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        ext_layout.addWidget(ext_title)
        self.ext_label = QLabel(
            "No extensions registered yet. Register an extension "
            "with the extension_register tool, then approve and "
            "enable it here or from the palette.")
        self.ext_label.setObjectName("muted")
        self.ext_label.setWordWrap(True)
        ext_layout.addWidget(self.ext_label)
        self.ext_name = QLineEdit()
        self.ext_name.setPlaceholderText("Extension name")
        ext_layout.addWidget(self.ext_name)
        self.btn_ext_refresh = QPushButton("Refresh extension status")
        self.btn_ext_refresh.clicked.connect(self._refresh_ext_status)
        ext_layout.addWidget(self.btn_ext_refresh,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        self.btn_ext_approve = QPushButton("Approve")
        self.btn_ext_approve.clicked.connect(self._on_ext_approve)
        ext_layout.addWidget(self.btn_ext_approve,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        self.btn_ext_enable = QPushButton("Enable")
        self.btn_ext_enable.clicked.connect(self._on_ext_enable)
        ext_layout.addWidget(self.btn_ext_enable,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        self.btn_ext_revoke = QPushButton("Revoke")
        self.btn_ext_revoke.clicked.connect(self._on_ext_revoke)
        ext_layout.addWidget(self.btn_ext_revoke,
                             alignment=Qt.AlignmentFlag.AlignLeft)
        self.ext_result = QLabel("")
        self.ext_result.setObjectName("muted")
        self.ext_result.setWordWrap(True)
        ext_layout.addWidget(self.ext_result)
        layout.addWidget(self.ext_card)
        self._refresh_ext_status()

        # Findings, grouped by severity (failures first).
        self.findings_scroll = QScrollArea()
        self.findings_scroll.setWidgetResizable(True)
        # UI Phase 4 (leg-found, render gate): the findings list
        # MUST have a usable height. The page's outer scroll area
        # sizes its content to the sections' size hints, and a
        # QScrollArea's hint is tiny — without a floor the list
        # was crushed to 56 px (measured on the live tree AND on
        # the pre-Phase-4 tree, so pre-existing, not a Phase 4
        # regression) and the 24 finding cards showed only as a
        # 2-line slit. 480 px shows 3+ cards at 1280 and 2+ at
        # 640; the outer page scrolls, so a taller list costs
        # nothing at any window height.
        self.findings_scroll.setMinimumHeight(480)
        self.findings_inner = QWidget()
        self.findings_layout = QVBoxLayout(self.findings_inner)
        self.findings_layout.setContentsMargins(0, 0, 0, 0)
        self.findings_layout.setSpacing(12)
        self.findings_scroll.setWidget(self.findings_inner)
        # QScrollArea.setWidget() flips the widget's autoFillBackground
        # on — an opaque white fill that would wash out the cards
        # (their solid surface over the dark page).
        # The Settings content widget runs with autoFill off; match
        # it, as the codebase already does for its suggestion row.
        self.findings_inner.setAutoFillBackground(False)
        layout.addWidget(self.findings_scroll, stretch=1)

        # The full text report, one disclosure away.
        self.report_toggle = QPushButton("Full text report ▸")
        self.report_toggle.setStyleSheet(
            f"text-align: left; font-weight: 600; color: {TEXT};"
            " background: transparent; border: none; padding: 8px 2px;")
        self.report_toggle.clicked.connect(self._on_report_toggled)
        layout.addWidget(self.report_toggle)
        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setFontFamily("monospace")
        # QTextEdit keeps its own white base colour (PREMIUM_STYLE
        # has no QTextEdit rule); paint it in the page language
        # explicitly — same BG, text, rim and selection as the app.
        self.report_view.setStyleSheet(
            f"QTextEdit {{ background-color: {BG}; color: {TEXT};"
            f" border: 1px solid {LINE_SUBTLE};"
            f" border-radius: {RADIUS_BTN}px;"
            f" selection-background-color: {SELECTION_BG};"
            f" selection-color: {TEXT}; }}")
        self.report_view.setVisible(False)
        layout.addWidget(self.report_view, stretch=1)

    # ------------------------------------------- run selection
    def _selected_categories(self) -> list:
        """The ticked categories, in _CATEGORY_ORDER."""
        return [c for c in _CATEGORY_ORDER
                if self._category_boxes.get(c) is not None
                and self._category_boxes[c].isChecked()]

    def _apply_stored_selection(self):
        """Tick the boxes from doctor/run_selection ("all" or a
        comma list); a missing or unreadable value reads as all.
        Called once, at build — the boxes own the state after."""
        try:
            settings = getattr(self._window, "settings", None)
            if settings is None:
                return
            stored = str(qsettings_get(
                _RUN_SELECTION_KEY, settings=settings) or "all")
        except Exception:  # noqa: BLE001 — default is all
            return
        if stored.strip().lower() == "all":
            wanted = set(_CATEGORY_ORDER)
        else:
            wanted = {c.strip().lower() for c in stored.split(",")
                      if c.strip()}
        for cat, box in self._category_boxes.items():
            try:
                box.setChecked(cat in wanted)
            except Exception:  # noqa: BLE001 — one box never breaks the rest
                pass

    def _store_selection(self):
        """Persist the ticked categories ("all" when all six are
        ticked, else the comma list) — the next visit, and the
        Pulse Connection row's last-run read, see the same run."""
        try:
            settings = getattr(self._window, "settings", None)
            if settings is None:
                return
            selected = self._selected_categories()
            value = ("all" if len(selected) == len(_CATEGORY_ORDER)
                     else ",".join(selected))
            qsettings_set(_RUN_SELECTION_KEY, value, settings=settings)
        except Exception:  # noqa: BLE001 — persistence is best-effort
            pass

    def _sync_category_glyphs(self):
        """Fix round (F-P4-03): the checked state shows a visible
        check glyph — the global checkbox style paints a checked
        indicator as a solid cyan block with no glyph, so the
        glyph lives in the label: "✓ Connectivity · 4" ticked,
        "Connectivity · 4" unticked. Checked and unchecked are
        distinguishable at a glance, and the count stays visible
        in both states."""
        for cat, box in self._category_boxes.items():
            try:
                _base = self._category_base_labels.get(
                    cat, _CATEGORY_LABELS[cat])
                box.setText(("✓ " if box.isChecked() else "") + _base)
            except Exception:  # noqa: BLE001 — one tile never breaks the rest
                pass

    def _relayout_category_grid(self):
        """Fix round (F-P4-03): the tile grid is responsive —
        three columns at >= 700 px page width, two below. The
        glyph + count labels are wider than the Phase 4 labels,
        and three of them overflow a 640 px window (the hero card
        grew a horizontal scrollbar). A no-op unless the column
        count actually changes, so a resize cannot loop."""
        grid = getattr(self, "_category_grid", None)
        if grid is None or not self._category_boxes:
            return
        cols = 3 if self.width() >= 700 else 2
        if cols == getattr(self, "_category_cols", None):
            return
        self._category_cols = cols
        for i in reversed(range(grid.count())):
            grid.takeAt(i)
        for i, cat in enumerate(_CATEGORY_ORDER):
            grid.addWidget(self._category_boxes[cat],
                           i // cols, i % cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_category_grid()

    def _on_category_toggled(self, _checked=False):
        self._sync_category_glyphs()
        self._store_selection()

    def _on_run_all(self):
        """Tick every category and run — "Run all" is the
        default selection, and this restores it after the user
        unticked some."""
        for box in self._category_boxes.values():
            try:
                box.setChecked(True)
            except Exception:  # noqa: BLE001 — best-effort
                pass
        self._store_selection()
        self.run()

    # ------------------------------------------- Wiring Build 3
    def _resilience_gate(self):
        """The shared gate module, app import path first, test
        path second; configuring it with the app data directory
        is idempotent, so a status refresh never replaces (and
        un-trips) the shared wiring latch."""
        try:
            from voice_assistant.core.gui_facade import resilience_gate as gate  # Phase 3 R1: moved to core/resilience/
        except Exception:  # noqa: BLE001
            from core.gui_facade import resilience_gate as gate  # Phase 3 R1: moved to core/resilience/
        try:
            from voice_assistant.core.gui_facade import DATA_DIR
        except Exception:  # noqa: BLE001
            try:
                from core.gui_facade import DATA_DIR
            except Exception:  # noqa: BLE001
                DATA_DIR = None
        if DATA_DIR is not None:
            try:
                gate.configure_resilience(str(DATA_DIR / "resilience_w3"))
                gate.configure_undo(str(DATA_DIR / "undo_w3.json"))
            except Exception:  # noqa: BLE001 — status still renders
                pass
        # Code-Perfection Build 1: register the undo producers'
        # restorers, so the Undo button below can actually undo
        # a recorded settings change or file write instead of
        # reporting "no restorer registered" for every change.
        try:
            from voice_assistant.core.gui_facade import undo_producers_p1 as _prod
        except Exception:  # noqa: BLE001
            try:
                from core.gui_facade import undo_producers_p1 as _prod
            except Exception:  # noqa: BLE001
                _prod = None
        if _prod is not None:
            try:
                _prod.ensure_producers_registered()
            except Exception:  # noqa: BLE001 — status still renders
                pass
        # Wave 7 Build 2: register the completion restorers
        # too (sandbox files, extension state), so the Undo
        # button below can actually undo those classes
        # instead of reporting "no restorer registered".
        try:
            from voice_assistant.core.gui_facade import undo_completion as _undo7
        except Exception:  # noqa: BLE001
            try:
                from core.gui_facade import undo_completion as _undo7
            except Exception:  # noqa: BLE001
                _undo7 = None
        if _undo7 is not None:
            try:
                _undo7.ensure_undo_completion_registered()
            except Exception:  # noqa: BLE001 — status still renders
                pass
        return gate

    def _shared_harness(self):
        """The shared Wave 6 harness (same store as the tools)."""
        try:
            from voice_assistant.core.gui_facade import integration_harness_tools as _w6
        except Exception:  # noqa: BLE001
            from core.gui_facade import integration_harness_tools as _w6
        return _w6.shared_harness()

    def _refresh_ext_status(self):
        """Render every registered extension's harness status."""
        try:
            harness = self._shared_harness()
            entries = harness.entries
            if not entries:
                self.ext_label.setText("No extensions registered yet.")
                return
            lines = []
            for name in sorted(entries):
                status = harness.status(name) or {}
                lines.append(
                    f"{name}: {status.get('state', 'unknown')} "
                    f"(licence: {status.get('licence', 'unknown')}; "
                    f"permissions: "
                    f"{', '.join(status.get('permissions') or []) or 'none'})")
            self.ext_label.setText("\n".join(lines))
        except Exception:  # noqa: BLE001
            try:
                self.ext_label.setText(
                    "Extensions status unavailable.")
            except Exception:  # noqa: BLE001
                pass

    def _ext_action(self, verb, fn):
        """One harness action behind a Yes/No confirm (default No)."""
        name = self.ext_name.text().strip()
        if not name:
            self.ext_result.setText("Enter an extension name first.")
            return
        try:
            if not HudDialog.confirm(
                self, f"{verb} extension",
                f"{verb} extension {name!r}? "
                + ("Revocation is terminal — it can never be "
                   "re-approved, re-enabled, or re-registered."
                   if verb == "Revoke" else ""),
                destructive=(verb == "Revoke")):
                self.ext_result.setText(f"{verb} cancelled.")
                return
            result = fn(name)
            if result.get("ok"):
                self.ext_result.setText(
                    f"{verb} succeeded: {name} is now "
                    f"{result.get('state', 'unknown')}.")
            else:
                self.ext_result.setText(
                    f"{verb} refused: "
                    f"{result.get('reason') or result.get('errors')}")
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self.ext_result.setText(f"{verb} failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
        self._refresh_ext_status()

    def _on_ext_approve(self):
        """Approve button: confirm first, then human approval."""
        harness = self._shared_harness()
        self._ext_action(
            "Approve",
            lambda name: harness.approve(name, approved=True, human=True))

    def _on_ext_enable(self):
        """Enable button: confirm first; refuses unless approved."""
        harness = self._shared_harness()
        self._ext_action("Enable", harness.enable)

    def _on_ext_revoke(self):
        """Revoke button: confirm first; revocation is terminal."""
        harness = self._shared_harness()
        self._ext_action(
            "Revoke",
            lambda name: harness.revoke(
                name, reason="revoked from Doctor page"))

    def _refresh_resilience_status(self):
        """Render the shared gate payload through the existing
        renderer. Never raises: a broken status surface must not
        break the Doctor page.

        Look-fix 2 (2026-11-24): the card shows a short summary
        derived from the structured payload (never invented), and
        the verbatim lines live behind "Show full status" — the
        wall of log lines is gone, the facts are one tap away.
        """
        try:
            gate = self._resilience_gate()
            lines = gate.render_status_lines()
            self._resilience_full_lines = list(lines or [])
            try:
                status = gate.resilience_status()
            except Exception:  # noqa: BLE001 — fall back to the lines
                status = None
            self.resilience_label.setText(
                self._resilience_summary(status, lines))
            self._sync_resilience_details()
        except Exception:  # noqa: BLE001
            try:
                self.resilience_label.setText(
                    "Resilience wiring: status unavailable.")
                self._resilience_full_lines = []
                self._sync_resilience_details()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _resilience_summary(status, lines):
        """Short human summary of the gate payload.

        The summary names the state (healthy / tripped / not
        configured), safe mode, and the counts — the library names,
        fence wordings and pilot-only items stay in the verbatim
        lines behind the disclosure, so nothing is lost. When the
        structured payload is unavailable, the summary IS the
        verbatim lines (the old behaviour), never a guess.
        """
        if not isinstance(status, dict):
            return "\n".join(lines or [])
        if not status.get("configured"):
            summary = ["Resilience wiring: not yet configured in this session."]
            if status.get("tripped"):
                summary.append("Control is paused: "
                               + (status.get("trip_reason")
                                  or "failsafe tripped"))
            return "\n".join(summary)
        if status.get("tripped"):
            summary = ["Resilience wiring: TRIPPED — "
                       + (status.get("trip_reason")
                          or "failsafe tripped")]
        else:
            summary = ["Resilience wiring: healthy"]
        summary.append("Safe mode: "
                       + ("engaged" if status.get("safe_mode")
                          else "not engaged"))
        fences = status.get("fences") or []
        # Fix round (F-04, parent wording 2026-11-26): the summary
        # card speaks in plain words — the numbers stay live.
        summary.append(f"Safety guards on: {len(fences)}")
        libraries = status.get("libraries") or []
        summary.append(f"Libraries protected: {len(libraries)}")
        owed = status.get("owed_reads") or {}
        if owed:
            summary.append(
                f"Licence checks waiting on you: {len(owed)}")
        proof = status.get("field_proof") or ""
        if proof:
            summary.append(proof)
        return "\n".join(summary)

    def _sync_resilience_details(self):
        """Show or hide the verbatim lines behind the disclosure."""
        try:
            show = bool(
                getattr(self.btn_resilience_details, "isChecked", lambda: False)())
            self.resilience_details.setVisible(show)
            if show:
                self.resilience_details.setText(
                    "\n".join(self._resilience_full_lines)
                    or "No further status lines.")
            self.btn_resilience_details.setText(
                "Hide full status" if show else "Show full status")
        except Exception:  # noqa: BLE001 — the toggle never breaks the page
            pass

    def _on_resilience_details_toggled(self):
        """The disclosure toggle — re-sync the details label."""
        self._sync_resilience_details()

    def _on_resilience_rearm(self):
        """Re-arm button: confirm first (default No), then the
        gate's human + approved re-arm. The result says plainly
        whether control actually resumed or the failsafe still
        holds it paused — re-arming the wiring never claims to
        clear the failsafe."""
        try:
            if not HudDialog.confirm(
                self, "Re-arm resilience",
                "Re-arming resumes control after a trip. "
                "Only re-arm if you know why it tripped."):
                self.resilience_result.setText("Re-arm cancelled.")
                return
            gate = self._resilience_gate()
            result = gate.rearm_resilience(approved=True, human=True)
            if result.get("rearmed"):
                if result.get("failsafe_still_tripped"):
                    self.resilience_result.setText(
                        "Re-armed, but the failsafe is still tripped — "
                        "control stays paused until the failsafe is reset.")
                else:
                    self.resilience_result.setText(
                        "Resilience re-armed — control resumed.")
            else:
                self.resilience_result.setText(
                    f"Re-arm refused: {result.get('reason', 'unknown')}")
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self.resilience_result.setText(f"Re-arm failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
        self._refresh_resilience_status()

    def _on_failsafe_reset(self):
        """Reset failsafe button: confirm first (default No),
        then the gate's reset through the failsafe module's own
        reset path. The result names each source separately: a
        reset that leaves the wiring latch tripped still pauses
        control, and only re-arm plus reset together resume it."""
        try:
            if not HudDialog.confirm(
                self, "Reset failsafe",
                "Reset the failsafe watchdog? Only reset if you "
                "know why it tripped."):
                self.resilience_result.setText("Failsafe reset cancelled.")
                return
            gate = self._resilience_gate()
            result = gate.reset_failsafe(approved=True, human=True)
            if result.get("reset"):
                if result.get("wiring_still_tripped"):
                    self.resilience_result.setText(
                        "Failsafe reset, but the wiring latch is still "
                        "tripped — control stays paused until re-arm.")
                else:
                    self.resilience_result.setText(
                        "Failsafe reset — control resumed.")
            else:
                self.resilience_result.setText(
                    f"Failsafe reset refused: "
                    f"{result.get('reason', 'unknown')}")
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self.resilience_result.setText(
                    f"Failsafe reset failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
        self._refresh_resilience_status()

    def _on_undo_last(self):
        """Undo button: confirm first (default No), then the
        gate's undo of the last undoable recorded change. A
        non-undoable latest change, a missing restorer, or a
        tripped failsafe is reported honestly, never as undone."""
        try:
            if not HudDialog.confirm(
                self, "Undo last change",
                "Undo the last change that can be taken back? "
                "Changes that cannot be taken back are left alone."):
                self.resilience_result.setText("Undo cancelled.")
                return
            gate = self._resilience_gate()
            result = gate.undo_last(approved=True)
            skipped = result.get("skipped_non_undoable") or []
            note = ""
            if skipped:
                note = (" (skipped changes that cannot be taken back: "
                        + ", ".join(s.get("id", "?") for s in skipped)
                        + ")")
            if result.get("undone"):
                self.resilience_result.setText(
                    f"Undone: {result.get('id')} "
                    f"({result.get('class')}).{note}")
            else:
                self.resilience_result.setText(
                    f"Nothing undone: {result.get('reason', 'unknown')}"
                    f"{note}")
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self.resilience_result.setText(f"Undo failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
        self._refresh_resilience_status()

    def _on_dev_posture(self):
        """Telemetry posture button: report-only, never edits.

        Calls developer_telemetry_doctor.posture_report() against the real
        home directory and renders its verdicts as text. The
        leaf module contains no writer, so nothing here can
        change another product's settings; a missing file
        reports as unknown/absent, never as an invented state.
        """
        try:
            from voice_assistant.core.gui_facade import developer_telemetry_doctor as _doctor

            report = _doctor.posture_report(os.path.expanduser("~"))
            gh = report.get("gh", {})
            pio = report.get("platformio", {})
            aider = report.get("aider_analytics", {})
            lines = [
                "VS Code telemetryLevel: %s"
                % (report.get("vscode_telemetryLevel"),),
                "Repo CLI forced off: %s"
                % (gh.get("forced_off"),),
                "Embedded-platform forced off: %s"
                % (pio.get("env_forced_off"),),
                "Agent analytics persisted: %s (%s)"
                % (aider.get("persisted"), aider.get("state")),
                "Cline capture checklist items: %d (report only)"
                % (len(report.get("cline_capture_checklist", [])),),
            ]
            self.dev_posture_result.setText("\n".join(lines))
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            try:
                self.dev_posture_result.setText(
                    f"Posture report unavailable: {exc}")
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ run
    def _read_last_run(self):
        try:
            settings = getattr(self._window, "settings", None)
            if settings is not None:
                last = str(qsettings_get("doctor/last_run", settings=settings) or "")
                if last:
                    self.hero_last.setText(f"Last run: {last}")
        except Exception:  # noqa: BLE001
            pass

    def ensure_initial_run(self):
        """First visit runs the checks; later visits show the last
        result until Run again is pressed (idempotent)."""
        if not self._ran:
            self.run()

    def run(self):
        # v5.57.1 (item 1): the worker reference goes stale. The
        # finished signal is wired to deleteLater, so once Qt has
        # processed the deletion the stored wrapper's C++ object
        # is gone — and calling isRunning() on it raises
        # RuntimeError (libshiboken: Internal C++ object already
        # deleted), which Qt swallows inside the slot, so a later
        # Run-again click visibly does NOTHING (proven by a
        # micro-probe in this exact Qt build; the earlier
        # full-page probe missed it by racing the deletion —
        # it clicked milliseconds after completion, before the
        # DeferredDelete was processed, while a real user clicks
        # minutes later, after it). A dead reference reads as
        # "not running" here, and _on_done clears the reference
        # outright — the same pattern the briefing worker already
        # follows (window_shell.py clears _briefing_worker in its
        # done handler).
        if self._worker is not None:
            try:
                if self._worker.isRunning():
                    return
            except RuntimeError:  # noqa: BLE001 — stale worker
                self._worker = None
        # UI Phase 4 (D-P4-18): "Run" runs the selection — with
        # nothing ticked there is nothing to run, and the hero
        # says so instead of running everything behind the
        # user's back.
        categories = self._selected_categories()
        if not categories:
            self.hero_title.setText("Nothing selected")
            self.hero_title.setStyleSheet(
                "font-size: 20px; font-weight: 600;")
            self.hero_counts.setText(
                "Tick at least one category above, or press "
                "Run all.")
            return
        self._store_selection()
        self._ran = True
        # UI Phase 4 (D-P4-19): the run's duration, measured
        # from here to _on_done.
        self._run_started = time.monotonic()
        self._run_categories = list(categories)
        self.btn_run.setEnabled(False)
        self.btn_run.setText("Running…")
        self.hero_title.setText("Checking…")
        self.hero_title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.hero_counts.setText(
            "VYUK is running her checks. This takes a few seconds.")
        worker = _DoctorWorker(self, categories=categories)
        worker.done.connect(self._on_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def stop_checks(self):
        """Stop a running checks worker and wait until its thread
        is finished. Field-check fix #3 (U7): closing the window
        while the worker runs used to abort the process — Qt
        destroys a QThread still running with SIGABRT — so the
        window's closeEvent calls this first, before any teardown
        touches the page. Idempotent and never raises: a missing
        or already-finished worker is a no-op, and a stale
        reference (C++ object already deleted) reads as not
        running. The bounded wait covers the normal stop (the
        child process is killed, so communicate() returns at
        once); terminate() is only the last resort for a worker
        stuck inside one hanging in-process check — at close
        nothing reads its result afterwards, and terminating it
        beats the guaranteed abort of destroying it running."""
        worker = self._worker
        if worker is None:
            return
        try:
            if not worker.isRunning():
                self._worker = None
                return
        except RuntimeError:  # noqa: BLE001 — stale worker
            self._worker = None
            return
        try:
            worker.stop()
        except Exception:  # noqa: BLE001 — stop must never raise
            pass
        try:
            worker.wait(5000)
        except Exception:  # noqa: BLE001 — wait must never raise
            pass
        try:
            if worker.isRunning():
                worker.terminate()
                worker.wait(1000)
        except Exception:  # noqa: BLE001 — last resort, never raise
            pass
        self._worker = None

    def _on_done(self, payload: dict):
        # v5.57.1 (item 1): clear the worker reference the moment
        # the run is done — finished→deleteLater will delete the
        # C++ object, and run() must never touch the stale
        # wrapper again (see run()).
        self._worker = None
        self.btn_run.setEnabled(True)
        self.btn_run.setText("Run again")
        self._clear_findings()
        if not payload.get("ok"):
            self.hero_title.setText("Doctor couldn't run")
            self.hero_title.setStyleSheet(
                "font-size: 20px; font-weight: 600; color: %s;" % _BROKEN)
            err = str(payload.get("error", "unknown"))
            self.hero_counts.setText(
                f"Doctor couldn't finish: {err[:120]} "
                "Press Run again to retry.")
            if payload.get("text"):
                self.report_view.setPlainText(payload["text"])
                self._last_report_text = str(payload["text"])
            else:
                self.report_view.setPlainText(
                    "Doctor run failed: %s" % err)
            return
        # Fix round (F-P4-02, parent ruling 2026-11-26): the hero
        # counts CHECKS — one outcome per check, the worst verdict
        # that check produced — so the three numbers sum to the
        # number of checks run (37 for a full run). The worker
        # carries them as "check_counts"; a payload without them
        # (an old cached result) falls back to the verdict counts.
        _hero = payload.get("check_counts") or payload["counts"]
        n_pass, n_warn, n_fail = (_hero["PASS"], _hero["WARN"],
                                  _hero["FAIL"])
        self.hero_counts.setText(
            f"{n_pass} checks passed · {n_warn} warnings · "
            f"{n_fail} failures")
        # Build 6 (§6.6): one headline — "Healthy" or "N issues".
        # The counts line above keeps the breakdown.
        n_issues = n_warn + n_fail
        if n_issues:
            self.hero_title.setText(f"{n_issues} issues")
            color = _BROKEN if n_fail else _ATTENTION
        else:
            self.hero_title.setText("Healthy")
            color = _HEALTHY
        self.hero_title.setStyleSheet(
            "font-size: 20px; font-weight: 600; color: %s;" % color)
        # UI Phase 4 (D-P4-19): the hero stamp carries the year,
        # the seconds, and the run duration — stored in the SAME
        # doctor/last_run key (no third QSettings key), displayed
        # verbatim. The Pulse Connection row strips the
        # " (took …)" suffix when it reads the same key.
        _took = ""
        if self._run_started is not None:
            _secs = max(0, int(round(
                time.monotonic() - self._run_started)))
            _took = f" (took {_secs} s)"
        self._run_started = None
        now = datetime.datetime.now().strftime("%d %b %Y, %H:%M:%S")
        stamp = f"{now}{_took}"
        try:
            settings = getattr(self._window, "settings", None)
            if settings is not None:
                qsettings_set("doctor/last_run", stamp, settings=settings)
        except Exception:  # noqa: BLE001
            pass
        self.hero_last.setText(f"Last run: {stamp}")
        self._last_report_text = str(payload.get("text") or "")
        findings = payload["findings"]
        # M-58 confirm popup (click-anywhere Batch 0, Plan 9 §7.2):
        # the write proof asks him to confirm the RESULT in the
        # dialog — this runs on the GUI thread (_on_done is a slot),
        # after the proof ran in the worker. His answer is written
        # onto the finding's card, next to the receipt the proof
        # recorded. A dialog failure is a No, as before.
        for finding in findings:
            if str(finding.get("label", "")).strip() != "Write proof":
                continue
            try:
                confirmed = HudDialog.confirm(
                    self, "Write proof",
                    "%s\n\nConfirm this result?"
                    % str(finding.get("detail", "") or ""))
            except Exception:  # noqa: BLE001 — no dialog: No
                confirmed = False
            finding["detail"] = (
                "%s — You confirmed: %s."
                % (str(finding.get("detail", "") or "").rstrip(),
                   "yes" if confirmed else "no"))
            break
        self._finding_cards = {}
        if not findings:
            card = QFrame()
            card.setObjectName("settingsCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 12, 16, 12)
            ok = QLabel("No findings — every check passed.")
            ok.setObjectName("muted")
            ok.setWordWrap(True)
            card_layout.addWidget(ok)
            self.findings_layout.addWidget(card)
        else:
            for group, wanted in (("FAILURES", "FAIL"),
                                  ("WARNINGS", "WARN")):
                group_findings = [f for f in findings
                                  if f["status"] == wanted]
                if not group_findings:
                    continue
                kicker = QLabel(group)
                kicker.setStyleSheet(f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600;"
                                     f" color: {TEXT};")
                self.findings_layout.addWidget(kicker)
                # UI Phase 4 (D-P4-17): group by category inside
                # severity, in _CATEGORY_ORDER, with a category
                # kicker. A finding whose marker is missing (an
                # old cached report) lands under OTHER, last —
                # never silently dropped, never mis-grouped.
                for cat in list(_CATEGORY_ORDER) + [""]:
                    cat_findings = [
                        f for f in group_findings
                        if (f.get("category") or "") == cat]
                    if not cat_findings:
                        continue
                    cat_kicker = QLabel(cat.upper() if cat else "OTHER")
                    cat_kicker.setStyleSheet(
                        f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600;"
                        f" color: {TEXT};")
                    self.findings_layout.addWidget(cat_kicker)
                    for finding in cat_findings:
                        card = self._finding_row(finding)
                        self._finding_cards[
                            str(finding.get("label", ""))] = card
                        self.findings_layout.addWidget(card)
        self.findings_layout.addStretch()
        self.report_view.setPlainText(payload["text"])

    # ------------------------------------------------------------ cards
    def _clear_findings(self):
        while self.findings_layout.count():
            item = self.findings_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # ------------------------------------------------------------ repairs
    # UI Phase 4 (D-P4-16): the pack findings whose repair is the
    # background download — only when the detail says the pack
    # is NOT there yet. A partial pack ("downloads resume where
    # they left off") is already downloading; starting another
    # would be a second downloader, which the brief forbids.
    _PACK_DOWNLOAD_LABELS = (
        "Offline pack", "STT tier (Whisper)", "Local vision (VLM)",
        "Voice (Kokoro)", "Fallback voice (Piper)")
    _PACK_DOWNLOAD_PHRASES = (
        "not downloaded yet", "isn't downloaded yet",
        "isn't provisioned yet")

    def _pip_repair_specs(self):
        """doctor.py's allowlist (R-P4-A) — the single source of
        truth; a missing import reads as no repair, never as an
        open pip install."""
        try:
            from voice_assistant import doctor as _doctor
        except ImportError:  # running un-packaged, next to doctor.py
            import doctor as _doctor
        return dict(getattr(_doctor, "_PIP_REPAIR_SPECS", {}) or {})

    def _repair_for(self, finding: dict):
        """The safe repair for one finding, or None (no safe
        repair — the card says so with the reason). The ruled
        table (D-P4-16); anything outside it is None, and a pip
        repair only ever names a package in the allowlist."""
        label = str(finding.get("label", ""))
        status = str(finding.get("status", ""))
        detail = str(finding.get("detail", ""))
        # Missing dependency — pip install the named requirement,
        # bounded to the declared set (R-P4-A). Required deps
        # FAIL; optional ones WARN and the repair is offered as
        # optional (the verb says so).
        if (label.startswith("Dependency: ")
                or label.startswith("Local brain dependency: ")):
            import_name = label.split(": ", 1)[1].strip()
            spec = self._pip_repair_specs().get(import_name)
            if spec is None:
                return None
            optional = status == "WARN"
            return {
                "kind": "pip",
                "verb": (f"Install {import_name} (optional)"
                         if optional else f"Install {import_name}"),
                "spec": spec, "import_name": import_name,
                "scope": (f"Installs {spec} into this Python "
                          "environment with pip. Nothing else "
                          "changes; no other package is touched."),
                "verify_check": ("check_deps"
                                 if label.startswith("Dependency: ")
                                 else "check_brain"),
                "verify_label": label,
            }
        # Unwritable data dir — only when the cause is known
        # (a permission error naming the folder).
        if label == "Data directory":
            if ("Permission denied" in detail or "Errno 13" in detail
                    or "[WinError 5]" in detail):
                path = detail.split(" is not writable", 1)[0].strip()
                if not path:
                    return None
                return {
                    "kind": "chmod",
                    "verb": "Fix folder permissions",
                    "path": path,
                    "scope": (f"Makes VYUK's own data folder ({path}) "
                              "writable again (owner write on the "
                              "folder). Nothing outside VYUK's data "
                              "folder is touched."),
                    "verify_check": "check_data",
                    "verify_label": label,
                }
            return None
        # Missing model/voice pack — the existing background
        # download (the same path Connect uses), never a second
        # downloader.
        if label in self._PACK_DOWNLOAD_LABELS and any(
                p in detail for p in self._PACK_DOWNLOAD_PHRASES):
            return {
                "kind": "download",
                "verb": "Download the pack",
                "scope": ("Starts the same background download "
                          "Connect uses (ears, then voice, then "
                          "brain). It resumes from partial files "
                          "if it was interrupted; nothing is "
                          "downloaded twice."),
                "verify_check": ("check_piper_voice"
                                 if label == "Fallback voice (Piper)"
                                 else "check_brain"),
                "verify_label": label,
            }
        # Corrupt receipt store — back it up and let VYUK
        # recreate it. Destructive (the store is removed), so
        # the confirm is typed, and it carries the
        # unknown-outcome warning.
        if label == "Tool receipt store" and status == "FAIL":
            return {
                "kind": "receipt",
                "verb": "Back up + recreate the store",
                "scope": ("Backs the corrupt store up beside itself "
                          "and removes it, so VYUK recreates an "
                          "empty store on the next tool call."),
                "warning": ("Recreating loses the unknown-outcome "
                            "guard history — check no interrupted "
                            "tool call is still outstanding first. "
                            "The backup keeps the corrupt file for "
                            "inspection."),
                "verify_check": "check_receipt_store",
                "verify_label": label,
            }
        # Stale .env / unsaved brain — navigation IS the repair
        # (D-P4-16): the card keeps its "Open How VYUK thinks"
        # button, and no separate Repair button appears.
        if label == "Stored brain":
            return {"kind": "nav"}
        return None

    # Reasons a finding has no automatic repair (D-P4-16): the
    # card shows "No automatic repair — this one needs you"
    # with the reason. Exact label first, then the longest
    # matching prefix, then the generic fallback.
    _NO_REPAIR_REASONS = {
        "Brain": "needs your key or your Ollama install — a repair never touches credentials or installs a server.",
        "API key": "needs your key — a repair never touches credentials.",
        "Cloud doors (REST + Live)": "needs a network or account look — nothing on this machine to install or fix.",
        "Gemini key tier": "lives in your Google AI Studio project's billing — VYUK can't read it from here.",
        "Data directory": "the check doesn't say why the folder is unwritable — only a known permission cause can be fixed automatically.",
        "Tool receipt store": "the store is missing, not corrupt — run a tool once and it comes back on its own.",
        "Write proof": "the proof ran and the write failed — fix the cause (see Data directory) and re-run.",
        "Your world": "needs you to connect Google in Settings — a repair can't sign you in.",
        "Watchers": "the watcher code itself needs a look — open core/watchers.py.",
        "Syntax check": "a code error — open the file the check names and fix it.",
        "Tool registry": "a registration failed — the code needs a look, not a reinstall.",
        "Control pack": "a pack is missing tools — the code needs a look.",
        "Tool count": "a registration block didn't run — the code needs a look.",
        "Module imports": "already reported under Dependency — fix that line first.",
        "Pack registrations": "a pack failed to register — the code needs a look.",
        "Microphone": "needs a microphone connected or enabled in the OS sound settings.",
        "Microphone drops": "close other apps using the microphone, or pick a different one in Settings.",
        "Microphone (deep check)": "needs a finished session to measure against.",
        "Adaptive VAD floor": "needs a live session — the floor is estimated while VYUK listens.",
        "Install-tree integrity": "the tree disagrees with the signed manifest — repair from the pristine source instead of an automatic fix.",
        "GUI assets": "shipped files are missing — repair from the pristine source on the Distribution & repair card below.",
        "Secrets at rest": "needs a protection backend on this machine — installing one is a system change, not a VYUK repair.",
        "Presence": "the auto-start state couldn't be read — nothing safe to change automatically.",
        "Failure clusters": "diagnostics only — a cluster is a report, not something to fix.",
        "Learning": "the extractor code needs a look.",
        "Platform backend": "the platform backend is an environment choice, not a repair.",
        "MCP server": "fix the entry in mcp_servers.json.",
        "MCP toolbox servers": "fix mcp_servers.json; the built-in defaults still apply.",
        "Home Assistant": "configure the instance in Settings — a repair can't reach your home server.",
        "OCR engine": "the Tesseract binary arrives with the installer — a repair can't fetch a system binary.",
        "Real Chrome (CDP)": "needs Chrome running with remote debugging — start it and re-run.",
        "Capability probes": "diagnostics only — the probes report, they don't fix.",
        "Tool payload": "the offered set is over budget — shrink DESKTOP_TOOL_PRIORITY first.",
        "Tool router": "the routed set is wrong — the code needs a look.",
        "VYUK memory": "the tier couldn't be resolved — the code needs a look.",
        "VYUK process load": "the load reading came back unusable — reinstall psutil (see Dependency) or ignore it.",
        "Activity log": "the log rotates itself — nothing to fix.",
        "Folder name": "renaming the folder VYUK runs from can't be done while she's running.",
        "Python version": "needs a newer Python installed — a system change, not a VYUK repair.",
        "Lite session": "the lite client must import cleanly — the code needs a look.",
        "Doctor check": "the check itself is broken — a repair can't verify against a broken check.",
        "Tool state files": "the packs recreate these on next use.",
    }

    def _no_repair_reason(self, finding: dict) -> str:
        label = str(finding.get("label", ""))
        detail = str(finding.get("detail", ""))
        # A fix naming a package outside the declared set (R-P4-A).
        if (label.startswith("Dependency: ")
                or label.startswith("Local brain dependency: ")):
            return ("the fix names a package outside VYUK's declared "
                    "dependency set — a repair never installs an "
                    "undeclared package.")
        # A Watchers WARN that is really the missing psutil.
        if label == "Watchers" and "psutil is not installed" in detail:
            return ("install psutil — see the Dependency: psutil "
                    "finding above.")
        if label in self._NO_REPAIR_REASONS:
            return self._NO_REPAIR_REASONS[label]
        best = ""
        for key, reason in self._NO_REPAIR_REASONS.items():
            if label.startswith(key) and len(key) > len(best):
                best = key
        if best:
            return self._NO_REPAIR_REASONS[best]
        return ("no safe automatic repair exists for this finding — "
                "it needs a person to look at the fix text.")

    def _next_step(self, label: str):
        """A navigation button for findings that name a place VYUK
        can open, or None — the fix text (when present) is the step
        for everything else. Pack/download first: it is the most
        specific match, ahead of the broader brain/key match."""
        low = label.lower()
        if "pack" in low or "download" in low:
            return ("Download her on-PC brain",
                    lambda: self._window._setup_local_brain())
        if "brain" in low or "key" in low:
            return ("Open How VYUK thinks",
                    lambda: self._window.show_settings_card(
                        "How VYUK thinks"))
        if "voice" in low or "piper" in low:
            return ("Open Voice",
                    lambda: self._window.show_settings_card("Voice"))
        if "mic" in low or "audio" in low:
            return ("Open Microphone",
                    lambda: self._window.show_settings_card(
                        "Microphone"))
        return None

    def _finding_row(self, finding: dict) -> QFrame:
        """One issue as a row (Build 6 §6.6): title + detail + next
        step on the left, the fix action on the right. The grouped
        kickers above still say which rows are failures.

        UI Phase 4 (D-P4-16/D-P4-27): the repair action (or the
        honest "no automatic repair") and the W10 links live
        under the fix text — never beside it at narrow widths
        (§6.1: the repair button sits below the fix text under
        640 px; under the text it is below it at every width).
        The result label under the repair button carries the
        progress, the evidence, and the re-check verdict."""
        row = QFrame()
        row.setObjectName("settingsCard")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 8, 12, 8)
        row_layout.setSpacing(12)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        title = QLabel(finding["label"])
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        title.setWordWrap(True)
        left.addWidget(title)
        if finding["detail"]:
            detail = QLabel(finding["detail"])
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            left.addWidget(detail)
        if finding["fix"]:
            step = QLabel(f"Next step: {finding['fix']}")
            step.setWordWrap(True)
            left.addWidget(step)
        repair = self._repair_for(finding)
        result_label = QLabel("")
        result_label.setObjectName("muted")
        result_label.setWordWrap(True)
        result_label.hide()
        if repair is not None and repair.get("kind") != "nav":
            repair_btn = QPushButton(repair["verb"])
            repair_btn.clicked.connect(
                lambda _checked=False, f=finding, s=repair,
                rl=result_label, rb=repair_btn:
                    self._on_repair_clicked(f, s, rl, rb))
            left.addWidget(repair_btn,
                           alignment=Qt.AlignmentFlag.AlignLeft)
        elif repair is None:
            no_repair = QLabel(
                "No automatic repair — this one needs you. "
                + self._no_repair_reason(finding))
            no_repair.setObjectName("muted")
            no_repair.setWordWrap(True)
            left.addWidget(no_repair)
        # UI Phase 4 (D-P4-27): the W10 card, linked from
        # findings — a dependency FAIL gains "Repair from
        # pristine…" beside its fix text; an integrity finding
        # gains "Verify install". (check_integrity verdicts
        # WARN, never FAIL, by design — so the link is keyed
        # to the integrity finding at either severity; a
        # FAIL-only rule would show it never.)
        if (str(finding.get("status", "")) == "FAIL"
                and str(finding.get("label", "")).startswith(
                    "Dependency: ")):
            w10_btn = QPushButton("Repair from pristine…")
            w10_btn.clicked.connect(
                lambda _checked=False, rl=result_label:
                    self._on_w10_repair(rl))
            left.addWidget(w10_btn,
                           alignment=Qt.AlignmentFlag.AlignLeft)
        elif str(finding.get("label", "")) == "Install-tree integrity":
            w10_btn = QPushButton("Verify install")
            w10_btn.clicked.connect(
                lambda _checked=False, rl=result_label:
                    self._on_w10_verify(rl))
            left.addWidget(w10_btn,
                           alignment=Qt.AlignmentFlag.AlignLeft)
        left.addWidget(result_label)
        row_layout.addLayout(left, stretch=1)
        # The navigation button is the repair for "Stored brain"
        # (kind "nav"); for a finding with a real repair it is
        # suppressed — the repair IS the action, and a second
        # button starting the same download would be a trap.
        step_action = self._next_step(finding["label"])
        if repair is not None and repair.get("kind") != "nav":
            step_action = None
        if step_action is not None:
            btn = QPushButton(step_action[0])
            btn.clicked.connect(step_action[1])
            row_layout.addWidget(btn)
        return row

    def _on_repair_clicked(self, finding, spec, result_label, button):
        """Scope first, confirm (default No; typed-confirm for
        destructive ones), progress while it runs, result, then
        verification by re-running the check. One repair at a
        time; no repair-all button (D-P4-16)."""
        if self._repair_running:
            result_label.setText(
                "A repair is already running — one at a time.")
            result_label.show()
            return
        title = f"Repair: {finding.get('label', '')}"
        body = spec["scope"]
        if spec.get("warning"):
            body += "\n\n" + spec["warning"]
        if spec["kind"] == "receipt":
            dialog = _RepairTypedConfirmDialog(
                self, title=title, body=body, word="Repair",
                button_text="Back up + recreate")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                result_label.setText("Repair cancelled.")
                result_label.show()
                return
        else:
            try:
                ok = HudDialog.confirm(self, title, body)
            except Exception:  # noqa: BLE001 — no dialog: No
                ok = False
            if not ok:
                result_label.setText("Repair cancelled.")
                result_label.show()
                return
        self._repair_running = True
        button.setEnabled(False)
        button.setText("Working…")
        result_label.setText("Working…")
        result_label.show()
        # The download repair is async (R-P4-B): it starts the
        # background download and returns at once — the card
        # shows PENDING until the download finishes, then
        # verifies. Every other repair runs in a worker thread.
        if spec["kind"] == "download":
            self._start_download_repair(finding, spec, result_label,
                                        button)
            return
        worker = _RepairWorker(
            self, fn=lambda: self._run_sync_repair(spec))
        worker.done.connect(
            lambda result, f=finding, s=spec, rl=result_label,
            b=button: self._on_repair_done(result, f, s, rl, b))
        worker.finished.connect(worker.deleteLater)
        self._repair_worker = worker
        worker.start()

    def _run_sync_repair(self, spec):
        """One synchronous repair, off the GUI thread. Returns
        {"ok", "evidence"} — evidence names what changed,
        never a secret value (D-P4-23)."""
        kind = spec["kind"]
        if kind == "pip":
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install",
                     spec["spec"]],
                    capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                return {"ok": False,
                        "evidence": f"pip install {spec['spec']} "
                                    "timed out after 10 minutes"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False,
                        "evidence": f"pip could not start: {exc}"}
            if proc.returncode == 0:
                return {"ok": True,
                        "evidence": f"Installed {spec['spec']}"}
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return {"ok": False,
                    "evidence": f"pip install {spec['spec']} failed"
                    + (f": {tail[-1][:160]}" if tail else "")}
        if kind == "chmod":
            path = self._data_dir_repair_path(spec)
            if path is None:
                return {"ok": False,
                        "evidence": "the folder in the finding "
                                    "could not be resolved"}
            try:
                os.chmod(path, 0o755)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False,
                        "evidence": f"could not change permissions "
                                    f"on {path}: {exc}"}
            return {"ok": True,
                    "evidence": f"Set {path} writable (owner write)"}
        if kind == "receipt":
            return self._run_receipt_repair()
        return {"ok": False, "evidence": "unknown repair kind"}

    def _data_dir_repair_path(self, spec):
        """The folder a data-dir repair may chmod: the one the
        finding names, and only if it IS VYUK's own data folder
        (doctor.py's ROOT/data). Anything else reads as
        unresolvable — a repair never chmods a path the check
        didn't name."""
        raw = str(spec.get("path", "")).strip()
        if not raw:
            return None
        try:
            from pathlib import Path as _Path
            path = _Path(raw)
            from voice_assistant import doctor as _doctor
            data_dir = _Path(getattr(_doctor, "ROOT", "")) / "data"
        except Exception:  # noqa: BLE001 — unresolvable
            return None
        try:
            if path.resolve() != data_dir.resolve():
                return None
        except Exception:  # noqa: BLE001 — unresolvable
            return None
        if not path.is_dir():
            return None
        return str(path)

    def _run_receipt_repair(self):
        """Back the corrupt receipt store up beside itself and
        remove it, so VYUK recreates an empty store on the next
        tool call. The backup keeps the corrupt file for
        inspection; a failed back-up removes nothing."""
        try:
            from voice_assistant import doctor as _doctor
            store = _doctor._receipt_store_path()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False,
                    "evidence": f"could not resolve the receipt "
                                f"store: {exc}"}
        if not store.is_file():
            return {"ok": False,
                    "evidence": "the receipt store is already gone "
                                "— nothing to back up"}
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = store.with_name(store.name + f".bak-{stamp}")
        try:
            shutil.copy2(store, backup)
            store.unlink()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False,
                    "evidence": f"back-up failed, nothing removed: "
                                f"{exc}"}
        return {"ok": True,
                "evidence": f"Backed up to {backup.name} and removed "
                            "the corrupt store — VYUK recreates it "
                            "on the next tool call"}

    def _on_repair_done(self, result, finding, spec, result_label,
                        button):
        self._repair_worker = None
        button.setText(spec["verb"])
        button.setEnabled(True)
        # D-P4-23: the result names what changed — and the
        # verification (which check re-ran, its new verdict, its
        # timestamp) follows on the same card. A repair that
        # itself failed has nothing to verify; the result says
        # it failed, and the card stops there.
        if result.get("ok"):
            result_label.setText(
                f"Done: {result.get('evidence', '')}")
        else:
            result_label.setText(
                f"Repair failed: {result.get('evidence', '')}")
            self._repair_running = False
            return
        result_label.show()
        self._verify_repair(finding, spec, result_label)

    def _verify_repair(self, finding, spec, result_label):
        """Re-run the finding's check and write its new verdict
        onto the card (D-P4-16/D-P4-23)."""
        base = result_label.text()
        self._verify_base = (result_label, base)
        result_label.setText(base + "\nRe-checking…")
        worker = _DoctorWorker(self, checks=[spec["verify_check"]])
        worker.done.connect(
            lambda payload, f=finding, s=spec, rl=result_label:
                self._on_verify_done(payload, f, s, rl))
        worker.finished.connect(worker.deleteLater)
        self._verify_worker = worker
        worker.start()

    def _on_verify_done(self, payload, finding, spec, result_label):
        self._verify_worker = None
        self._repair_running = False
        _when = datetime.datetime.now().strftime("%H:%M:%S")
        base = ""
        if getattr(self, "_verify_base", None) is not None:
            _rl, base = self._verify_base
            self._verify_base = None
        else:
            base = result_label.text()
        if not payload.get("ok"):
            # A re-run that itself fails to run reports
            # "verification couldn't run" (D-P4-23) — never a
            # verdict the check didn't produce.
            result_label.setText(
                base + "\nVerification couldn't run: "
                + str(payload.get("error", "unknown"))[:120])
            return
        label = spec["verify_label"]
        new = None
        for f in payload.get("findings", []):
            if str(f.get("label", "")) == label:
                new = f
                break
        if new is None:
            # The finding is gone from the findings list — the
            # check now passes it.
            result_label.setText(
                base + f"\nRe-checked: PASS — {label} "
                f"({spec['verify_check']}, {_when})")
        else:
            result_label.setText(
                base + f"\nRe-checked: {new.get('status', '?')} — "
                f"{label} ({spec['verify_check']}, {_when})")

    # ------------------------------------------- download repair
    def _start_download_repair(self, finding, spec, result_label,
                               button):
        """Start the Connect-path download (R-P4-B): async, so
        the card shows PENDING until the download finishes and
        only then verifies — never a mid-download verdict."""
        try:
            from voice_assistant.core.gui_facade import downloads
        except Exception as exc:  # noqa: BLE001
            result_label.setText(f"Download failed to start: {exc}")
            self._repair_running = False
            button.setEnabled(True)
            button.setText(spec["verb"])
            return
        self._download_repair = (finding, spec, result_label, button)
        self._download_last_line = ""

        def progress(line):
            # Runs on the download thread — hop to the GUI thread
            # through the page's signal.
            try:
                self.repair_download_progress.emit(str(line))
            except Exception:  # noqa: BLE001 — a progress line
                pass  # never kills the run

        def done(lines):
            try:
                self.repair_download_done.emit(list(lines or []))
            except Exception:  # noqa: BLE001
                pass

        try:
            started = downloads.start(progress, done)
        except Exception as exc:  # noqa: BLE001
            result_label.setText(f"Download failed to start: {exc}")
            self._repair_running = False
            button.setEnabled(True)
            button.setText(spec["verb"])
            return
        if not started:
            # A download is already running — the same path
            # Connect uses refuses a second one. The card stays
            # PENDING: it re-verifies when the running download
            # finishes or on the next visit (this repair's done
            # callback only fires for a run it started).
            result_label.setText(
                "A download is already running — verification "
                "PENDING until it lands; re-check on the next visit.")
            self._repair_running = False
            button.setEnabled(True)
            button.setText(spec["verb"])
            return
        result_label.setText(
            "Download started — re-check after it lands "
            "(verification PENDING).")

    @Slot(str)
    def _on_download_progress(self, line):
        if getattr(self, "_download_repair", None) is None:
            return
        _finding, _spec, result_label, _button = self._download_repair
        self._download_last_line = line
        result_label.setText(
            "Downloading… " + str(line)[:120]
            + "\n(verification PENDING — re-check after it lands)")

    @Slot(list)
    def _on_download_done(self, lines):
        if getattr(self, "_download_repair", None) is None:
            return
        finding, spec, result_label, button = self._download_repair
        self._download_repair = None
        button.setEnabled(True)
        button.setText(spec["verb"])
        last = ""
        for line in reversed(lines or []):
            if str(line).strip():
                last = str(line).strip()
                break
        if (last.lower().startswith("error")
                or "paused" in last.lower()):
            result_label.setText(f"Download ended: {last[:160]}")
        else:
            result_label.setText("Download finished.")
        self._verify_repair(finding, spec, result_label)

    # ------------------------------------------------------------ W10 links
    def _on_w10_verify(self, result_label):
        """D-P4-27: an integrity finding's "Verify install" link
        runs the W10 card's verify and mirrors its status line
        onto the finding card."""
        try:
            self._window._w10_doctor_verify()
            _label = getattr(self._window, "_w10_doctor_status", None)
            status = str(_label.text() or "") if _label is not None else ""
        except Exception as exc:  # noqa: BLE001
            status = f"Install verify failed: {exc}"
        result_label.setText(
            status or "Verify install ran — see the Distribution "
                     "& repair card below.")
        result_label.show()

    def _on_w10_repair(self, result_label):
        """D-P4-27: a dependency FAIL's "Repair from pristine…"
        link runs the W10 card's repair (caller-supplied
        pristine source) and mirrors its status line."""
        try:
            self._window._w10_doctor_repair()
            _label = getattr(self._window, "_w10_doctor_status", None)
            status = str(_label.text() or "") if _label is not None else ""
        except Exception as exc:  # noqa: BLE001
            status = f"Repair failed: {exc}"
        result_label.setText(
            status or "Repair ran — see the Distribution "
                     "& repair card below.")
        result_label.show()

    # ------------------------------------------------------------ export
    def _on_export_clicked(self):
        """D-P4-18/D-P4-24: export the full text report to a file
        the user picks, redacted (the same presence-only rule
        the checks apply, re-applied at the boundary). Blocked
        in private mode — content goes, statuses stay."""
        try:
            from voice_assistant.core.gui_facade import (
                private_mode as _private_mode)
            _private = bool(_private_mode.private_mode_on())
        except Exception:  # noqa: BLE001 — a broken read reads public
            _private = False
        if _private:
            self.export_result.setText(
                "Export is blocked while private mode is on.")
            return
        if not self._last_report_text:
            self.export_result.setText(
                "Run the checks first — there is no report to "
                "export yet.")
            return
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Doctor report",
                "vyuk-doctor-report.txt",
                "Text files (*.txt);;All files (*)")
        except Exception:  # noqa: BLE001 — no dialog, no export
            return
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_redact_report_text(self._last_report_text))
            self.export_result.setText(f"Report exported to {path}.")
        except Exception as exc:  # noqa: BLE001
            self.export_result.setText(f"Export failed: {exc}")

    # ------------------------------------------------------------ Pulse hook
    def highlight_finding(self, label: str):
        """Pulse's "Open in Doctor ›" hook (D-P4-07): scroll the
        finding's card into view and edge it briefly. A label
        with no card (no finding by that name) does nothing —
        the word stays text on Pulse, as the mapping table says."""
        card = self._finding_cards.get(str(label or ""))
        if card is None:
            return
        try:
            self.findings_scroll.ensureWidgetVisible(card)
        except Exception:  # noqa: BLE001 — best-effort
            pass
        try:
            card.setStyleSheet(
                f"border: 2px solid {AMBER}; border-radius: 8px;")
            QTimer.singleShot(
                2000, lambda: self._clear_highlight(card))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def _clear_highlight(self, card):
        try:
            card.setStyleSheet("")
        except Exception:  # noqa: BLE001 — the card may be gone
            pass

    def _on_report_toggled(self):
        visible = not self.report_view.isVisible()
        self.report_view.setVisible(visible)
        self.report_toggle.setText(
            "Full text report ▾" if visible else "Full text report ▸")
