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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
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


class DoctorPage(QWidget):
    """The Doctor page widget. The main window owns it; it only
    asks the window to navigate (show_settings_card) or to run an
    existing action (_setup_local_brain)."""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._ran = False
        self._worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # v5.57.1 (item 2): the Doctor page has no sidebar entry
        # (owner override 2026-10-24 18:20 IST — that stays), so
        # without a way back the page is a trap: the only exit was
        # clicking Settings in the sidebar. A Back button at the
        # top returns to the Settings page; it navigates only,
        # never re-runs or resets the checks.
        self.btn_back = QPushButton("← Back to Settings")
        self.btn_back.setObjectName("chipBtn")
        self.btn_back.clicked.connect(self._on_back)
        layout.addWidget(self.btn_back, alignment=Qt.AlignmentFlag.AlignLeft)

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
        row = QVBoxLayout()
        self.btn_run = QPushButton("Run the checks")
        self.btn_run.clicked.connect(self.run)
        row.addWidget(self.btn_run, alignment=Qt.AlignmentFlag.AlignLeft)
        hero_layout.addLayout(row)
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

    def _on_back(self):
        """Return to the Settings page (index 4) — the page this
        Doctor view is reached from. Navigation only."""
        try:
            self._window._show_page(4)
        except Exception:  # noqa: BLE001 — a missing window method
            pass  # must never break the page itself

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
        break the Doctor page."""
        try:
            gate = self._resilience_gate()
            lines = gate.render_status_lines()
            self.resilience_label.setText("\n".join(lines))
        except Exception:  # noqa: BLE001
            try:
                self.resilience_label.setText(
                    "Resilience wiring: status unavailable.")
            except Exception:  # noqa: BLE001
                pass

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
        self._ran = True
        self.btn_run.setEnabled(False)
        self.btn_run.setText("Running…")
        self.hero_title.setText("Checking…")
        self.hero_title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.hero_counts.setText(
            "VYUK is running her checks. This takes a few seconds.")
        worker = _DoctorWorker(self)
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
            else:
                self.report_view.setPlainText(
                    "Doctor run failed: %s" % err)
            return
        counts = payload["counts"]
        n_pass, n_warn, n_fail = (counts["PASS"], counts["WARN"],
                                  counts["FAIL"])
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
        now = datetime.datetime.now().strftime("%d %b, %H:%M")
        try:
            settings = getattr(self._window, "settings", None)
            if settings is not None:
                qsettings_set("doctor/last_run", now, settings=settings)
        except Exception:  # noqa: BLE001
            pass
        self.hero_last.setText(f"Last run: {now}")
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
                for finding in group_findings:
                    self.findings_layout.addWidget(
                        self._finding_row(finding))
        self.findings_layout.addStretch()
        self.report_view.setPlainText(payload["text"])

    # ------------------------------------------------------------ cards
    def _clear_findings(self):
        while self.findings_layout.count():
            item = self.findings_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

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
        kickers above still say which rows are failures."""
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
        row_layout.addLayout(left, stretch=1)
        step_action = self._next_step(finding["label"])
        if step_action is not None:
            btn = QPushButton(step_action[0])
            btn.clicked.connect(step_action[1])
            row_layout.addWidget(btn)
        return row

    def _on_report_toggled(self):
        visible = not self.report_view.isVisible()
        self.report_view.setVisible(visible)
        self.report_toggle.setText(
            "Full text report ▾" if visible else "Full text report ▸")
