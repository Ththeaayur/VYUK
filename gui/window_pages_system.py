"""MainWindow pages mixin (part): pulse + senses + doctor pages.

Split from gui/window_pages.py (GUI split #8, move only).
Pulse + senses + doctor page + doctor actions.
"""

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import vision_manager

from .design_system.dialogs import HudDialog
from .style import DANGER


class _TypedConfirmDialog(HudDialog):
    """Typed confirmation for the destructive Doctor reset
    (Build 6 §6.6 / §3.8): the user types the action's name
    exactly as the dialog shows it ("Reset"). The confirm button
    stays disabled until the typed word matches — a destructive
    action that looks identical to three safe ones is a trap,
    not a design. Cancel holds the default, so Enter keeps the
    settings."""

    _WORD = "Reset"

    def __init__(self, parent=None):
        super().__init__(parent, title="Reset settings",
                         eyebrow="Destructive action",
                         severity="danger", destructive=True)
        self.add_title("Reset settings",
                       eyebrow="Destructive action")
        self.add_body(
            "Clear all VYUK settings back to defaults? Your "
            "memories, milestones and licence file are kept. "
            "Your .env (keys) is NOT touched. VYUK will "
            "re-onboard on next launch.\n\n"
            "Type Reset to confirm.")
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("Reset")
        self.content_layout().addWidget(self._edit)
        buttons = self.add_buttons([
            ("cancel", "Cancel", "reject", True, False),
            ("reset", "Reset settings", "accept", False, True),
        ])
        self._reset_btn = buttons["reset"]
        self._reset_btn.setEnabled(False)
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.returnPressed.connect(self._on_return_pressed)

    def _on_text_changed(self, text: str):
        self._reset_btn.setEnabled(text.strip() == self._WORD)

    def _on_return_pressed(self):
        if self._reset_btn.isEnabled():
            self.accept()


class PagesSystemMixin:
    # ------------------------------------------------------- pulse

    def _build_pulse_page(self) -> QWidget:
        # Wiring Build 7: disambiguated label only — this page shows
        # system probes (CPU/memory/disk/network/battery), NOT the
        # entity cards in core/pulse_w5.py. No data path changed.
        page, layout = self._page_shell(
            "System Pulse",
            "Live machine vitals — system probes only, "
            "not news or entity cards", kicker=True)
        self.pulse_page = None
        try:
            from voice_assistant.gui.pulse_page import PulsePage

            self.pulse_page = PulsePage()
            layout.addWidget(self.pulse_page, stretch=1)
            # Conversion Wave 9: the fixed-layout overview panel
            # (pyqtgraph lazy-optional, fallback otherwise) fed
            # from the live activity log. This IS the shipped
            # overview surface — docking stays gated.
            try:
                from .w9_wiring_proof import build_overview_panel
                self._w9_overview_panel = build_overview_panel()
                layout.addWidget(self._w9_overview_panel)
            except Exception:  # noqa: BLE001 — Pulse must still build
                pass
        except Exception as e:  # noqa: BLE001 — the page must still build
            print(f"[VYUK] Pulse page unavailable: {e}")
            unavailable = QLabel(
                "System Pulse is unavailable on this machine.")
            unavailable.setObjectName("muted")
            layout.addWidget(unavailable)
        # UI breakpoints slice (2026-10-30): Pulse is the one stack
        # page that could still exceed a short viewport without
        # scrolling — its meter grid reflows by width but has no
        # scroll area, so at one column on a 500 px window the
        # grid would shrink toward slivers (the Doctor defect's
        # mechanism: a tall page with no page-level scroll). Wrap
        # the whole page the way the Doctor page does (outer
        # QScrollArea, widgetResizable, frameless): the grid keeps
        # its natural heights at any window height and the page
        # scrolls instead of shrinking. Stack index, the PulsePage
        # instance, and all widget names are unchanged; only the
        # returned wrapper is new. Pages that already absorb
        # short viewports are NOT wrapped: Activity / Memory /
        # Skills lists shrink their viewports (rows keep height),
        # Settings and Senses already carry outer scroll areas,
        # Doctor is wrapped, Globe is a fluid web view, and the
        # assistant page's transcript scrolls while its stage
        # shrinks to the hero floor — wrapping any of those would
        # nest scrollers for no gain.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        page.setAutoFillBackground(False)
        # E-F209: the page is built with the house tokens — apply
        # the saved theme now (the load-time _apply_appearance may
        # have run before this page existed).
        try:
            self._apply_theme_to_painted_pages()
        except Exception:  # noqa: BLE001 — Pulse must still build
            pass
        return scroll

    # ------------------------------------------------------- senses

    def _senses_state(self) -> dict:
        """The Senses page's cheap state poll (v5.20): battery from
        psutil, connection kind from the up interfaces, camera/mic
        users from the watchers' privacy probe. Every leg guarded —
        a missing sensor is a None, never a crash or a claim."""
        state = {"ssid": None, "connection": "offline",
                 "battery_pct": None, "battery_plugged": None,
                 "camera_user": None, "mic_user": None}
        try:
            import psutil

            batt = psutil.sensors_battery()
            if batt is not None:
                state["battery_pct"] = float(batt.percent)
                state["battery_plugged"] = bool(batt.power_plugged)
            stats = psutil.net_if_stats()
            up = [n for n, st in stats.items()
                  if st.isup and not n.lower().startswith(("lo", "loopback"))]
            if up:
                wifiish = ("wi", "wlan", "wireless")
                state["connection"] = (
                    "wifi" if any(any(w in n.lower() for w in wifiish)
                                  for n in up) else "ethernet")
        except Exception:  # noqa: BLE001
            pass
        try:
            from voice_assistant.core.gui_facade import watchers

            active = watchers._probe_privacy()
            if active:
                for entry in active:
                    kind, _, app = str(entry).partition(":")
                    if kind == "webcam":
                        state["camera_user"] = app
                    elif kind == "microphone":
                        state["mic_user"] = app
        except Exception:  # noqa: BLE001
            pass
        return state

    def _senses_brightness(self, done_cb) -> None:
        """Room brightness on demand (v5.20): one webcam frame on a
        worker thread; the page routes the callback back to the GUI
        thread. No camera → done_cb(None) → the honest line."""
        def _work():
            result = None
            try:
                from voice_assistant.core.gui_facade import senses
                from voice_assistant.core.gui_facade import vision_manager

                frame = vision_manager.capture_webcam()
                if frame is not None:
                    result = senses.frame_brightness(frame[0])
            except Exception:  # noqa: BLE001
                result = None
            done_cb(result)

        threading.Thread(target=_work, daemon=True).start()

    def _build_senses_page(self) -> QWidget:
        page, layout = self._page_shell(
            "Senses", "What she notices", kicker=True)
        self.senses_page = None
        try:
            from voice_assistant.core.gui_facade import senses, senses_log
            from voice_assistant.gui.senses_page import SensesPage

            providers = {
                "state": self._senses_state,
                "wifi_scan": senses.wifi_scan,
                "quality": senses.network_quality,
                "events": lambda: senses_log.read_events(limit=100),
                "brightness": self._senses_brightness,
            }
            self.senses_page = SensesPage(
                providers=providers,
                on_open_permissions=self._open_senses_permissions)
            layout.addWidget(self.senses_page, stretch=1)
            # E-F209: the page is built with the house tokens —
            # apply the saved theme now (the load-time
            # _apply_appearance may have run before this page
            # existed).
            try:
                self._apply_theme_to_painted_pages()
            except Exception:  # noqa: BLE001 — Senses must still build
                pass
        except Exception as e:  # noqa: BLE001 — the page must still build
            print(f"[VYUK] Senses page unavailable: {e}")
            unavailable = QLabel("Senses is unavailable on this machine.")
            unavailable.setObjectName("muted")
            layout.addWidget(unavailable)
        return page

    def _open_senses_permissions(self):
        """Senses row -> the exact Permissions card (§6.8)."""
        try:
            self.show_settings_card("Permissions")
        except Exception:  # noqa: BLE001 — the tap stands
            pass

    # ------------------------------------------------------- doctor
    def _build_doctor_page(self) -> QWidget:
        """v5.57 (redo): the Doctor page — the status hero, grouped
        findings with next steps, and the full text report one
        disclosure away. See gui/doctor_page.py."""
        page, layout = self._page_shell(
            "Doctor",
            "Her health, at a glance — what passed, what needs a "
            "look, and what to do next.",
            kicker=True)
        from voice_assistant.gui.doctor_page import DoctorPage
        self.doctor_page = DoctorPage(self)
        layout.addWidget(self.doctor_page, stretch=1)
        # v5.65 (Wave 10 — PF-11 / EY-H / MN-1.3 / A8): Distribution
        # & repair — verify the install against the signed
        # manifest, repair from a pristine source, reset settings,
        # check machine health, and read licence + backup status.
        # Repair runs ONLY from here (or the installer), never at
        # startup; nothing here downloads or fabricates bytes.
        try:
            _dcard = QFrame()
            _dcard.setObjectName("card")
            _d_layout = QVBoxLayout(_dcard)
            _d_layout.setContentsMargins(16, 12, 16, 12)
            _d_title = QLabel("Distribution & repair")
            _d_title.setObjectName("sectionTitle")
            _d_layout.addWidget(_d_title)
            _d_hint = QLabel(
                "Install integrity, machine health, licence and "
                "backups — plus real repairs, not just a restart.")
            _d_hint.setObjectName("muted")
            _d_hint.setWordWrap(True)
            _d_layout.addWidget(_d_hint)
            self._w10_doctor_status = QLabel("")
            self._w10_doctor_status.setWordWrap(True)
            _d_layout.addWidget(self._w10_doctor_status)
            _drow = QHBoxLayout()
            self.btn_w10_verify = QPushButton("Verify install")
            self.btn_w10_verify.clicked.connect(
                self._w10_doctor_verify)
            _drow.addWidget(self.btn_w10_verify)
            self.btn_w10_repair = QPushButton("Repair from pristine…")
            self.btn_w10_repair.clicked.connect(
                self._w10_doctor_repair)
            _drow.addWidget(self.btn_w10_repair)
            self.btn_w10_machine = QPushButton("Machine health")
            self.btn_w10_machine.clicked.connect(
                self._w10_doctor_machine)
            _drow.addWidget(self.btn_w10_machine)
            _d_layout.addLayout(_drow)
            # Build 6 (§6.6 / §3.8): the destructive action is not
            # a fourth equal button — it is red TEXT at the bottom
            # of the card, and the tap opens a typed confirmation.
            # The button object keeps its name (btn_w10_reset) so
            # the reset seam stays one widget.
            self.btn_w10_reset = QPushButton("Reset settings")
            self.btn_w10_reset.setFlat(True)
            self.btn_w10_reset.setStyleSheet(
                "background: transparent; border: none; "
                "text-align: left; padding: 6px 0px; "
                "color: %s;" % DANGER)
            self.btn_w10_reset.clicked.connect(
                self._w10_doctor_reset)
            _d_layout.addWidget(
                self.btn_w10_reset,
                alignment=Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(_dcard)
            self._w10_doctor_refresh_status()
        except Exception:  # noqa: BLE001 — Doctor must still build
            pass
        # Doctor UI garble fix (field evidence 2026-10-30, lab
        # repro doctor_repro.py): this page had NO page-level
        # scroll area — only the findings list scrolls — so on
        # any window shorter than the ~1570px content, the VBox
        # compressed every card widget below its minimum, down
        # to 1-6px strips (the owner's screenshot: squashed
        # titles, labels, and buttons). The S5 audit table's
        # "Doctor: outer QScrollArea" line mistook the inner
        # findings_scroll for page-level scrolling. Wrap the
        # whole page the way the reminders page does (outer
        # QScrollArea, widgetResizable, frameless): content
        # keeps its natural heights at any window height and
        # the page scrolls instead of squashing. Stack index,
        # the DoctorPage instance, and all widget names are
        # unchanged; only the returned wrapper is new.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        # setWidget() flips the content widget's
        # autoFillBackground on; match the findings_inner
        # handling so no opaque fill washes out the
        # translucent cards over the dark page.
        page.setAutoFillBackground(False)
        return scroll

    def _w10_doctor_refresh_status(self):
        """v5.65 (Wave 10): licence + backup + safe-mode one-liner."""
        try:
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            from voice_assistant.core.gui_facade import w10_wiring as _w10
            _lic = _w10.licence_status(str(_cfg_mod.DATA_DIR))
            _bak = _w10.backup_status(str(_cfg_mod.DATA_DIR))
            _safe = "SAFE MODE active — local brain, watchers off." \
                if _w10.is_safe_mode_active(
                    data_dir=str(_cfg_mod.DATA_DIR)) else ""
            self._w10_doctor_status.setText(
                f"Licence: {_lic['message']} "
                f"Backup: {_bak.get('last_status', 'never')}"
                + (f" (last: {_bak.get('last_bundle') or '—'})"
                   if _bak.get('last_bundle') else "")
                + (f" {_bak.get('last_failure', {}).get('error', '')}"
                   if _bak.get('last_status') == 'failed' else "")
                + (f" {_safe}" if _safe else ""))
        except Exception:  # noqa: BLE001
            pass

    def _w10_doctor_verify(self):
        """v5.65 (Wave 10 — EY-H): manifest hash-check, reported."""
        try:
            from voice_assistant.core.gui_facade import asset_repair as _rep  # Phase 3 H1: moved to core/health/
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            from pathlib import Path as _Path
            _root = str(_Path(str(_cfg_mod.DATA_DIR)).parent)
            _man = str(_Path(_root) / "install_manifest.json")
            _v = _rep.verify_tree(_root, _man)
            if not _v.mismatches:
                self._w10_doctor_status.setText(
                    f"Install verified: {_v.ok}/{_v.checked} files match "
                    "the signed manifest.")
            else:
                _names = ", ".join(
                    getattr(m, "file", "?") for m in _v.mismatches[:5])
                self._w10_doctor_status.setText(
                    f"Install check: {_v.ok}/{_v.checked} match; "
                    f"{len(_v.mismatches)} mismatch(es): {_names}"
                    + ("…" if len(_v.mismatches) > 5 else "")
                    + ". Use 'Repair from pristine…' to restore them.")
        except Exception as exc:  # noqa: BLE001
            self._w10_doctor_status.setText(
                f"Install verify failed: {exc}")

    def _w10_doctor_repair(self):
        """v5.65 (Wave 10 — EY-H): repair from a caller-supplied
        pristine directory or ZIP; unrepairable files are listed."""
        try:
            from PySide6.QtWidgets import QFileDialog as _QFD
            from voice_assistant.core.gui_facade import asset_repair as _rep  # Phase 3 H1: moved to core/health/
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            from pathlib import Path as _Path
            _src, _ = _QFD.getOpenFileName(
                self, "Pristine source (directory or ZIP)",
                "", "All files (*)")
            if not _src:
                _src = _QFD.getExistingDirectory(
                    self, "Pristine source directory")
                if not _src:
                    return
            _root = str(_Path(str(_cfg_mod.DATA_DIR)).parent)
            _man = str(_Path(_root) / "install_manifest.json")
            _r = _rep.repair_tree(_root, _man, _src)
            _unrep = "; ".join(
                f"{getattr(u, 'file', '?')} "
                f"({getattr(u, 'reason', '?')})"
                for u in getattr(_r, "unrepairable", [])[:5])
            self._w10_doctor_status.setText(
                f"Repaired {len(getattr(_r, 'repaired', []))} file(s); "
                f"{len(getattr(_r, 'unrepairable', []))} unrepairable"
                + (f": {_unrep}" if _unrep else "."))
            self._w10_doctor_refresh_status()
        except Exception as exc:  # noqa: BLE001
            self._w10_doctor_status.setText(
                f"Repair failed: {exc}")

    def _w10_doctor_machine(self):
        """v5.65 (Wave 10 — A8): machine health — conservative
        thresholds, every warning citing its measurement."""
        try:
            import sys as _sys
            from voice_assistant.core.gui_facade import w10_wiring as _w10
            if _sys.platform == "win32":
                _readers = _w10.windows_machine_readers()
            else:
                _readers = None
            _rep = _w10.machine_report(_readers)
            _warns = _rep.get("warnings", []) or []
            _batt = _rep.get("battery", {})
            _disk = _rep.get("disk", {})
            _gpus = ", ".join(
                f"{g.get('name', '?')} "
                f"({g.get('driver_version') or 'unknown driver'})"
                for g in _rep.get("gpus", [])) or "no GPU data"
            self._w10_doctor_status.setText(
                f"Machine: battery {_batt.get('status', 'unknown')}"
                + (f" (wear {_batt.get('wear_pct'):.1f}%)"
                   if _batt.get('wear_pct') is not None else "")
                + f"; disk {_disk.get('status', 'unknown')}"
                + f"; GPUs: {_gpus}"
                + (f"; warnings: " + "; ".join(
                    w.get("message", "") for w in _warns)
                   if _warns else "; no warnings."))
        except Exception as exc:  # noqa: BLE001
            self._w10_doctor_status.setText(
                f"Machine health failed: {exc}")

    def _w10_doctor_reset(self):
        """v5.65 (Wave 10 — PF-11): reset QSettings to defaults
        with typed confirmation (Build 6 §6.6: the red text opens
        a dialog where the user types the action's name). .env
        (keys) is NOT touched."""
        try:
            dialog = _TypedConfirmDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self.settings.clear()
            self._w10_doctor_status.setText(
                "Settings reset to defaults. Restart VYUK to "
                "re-onboard; memories, milestones and .env kept.")
        except Exception as exc:  # noqa: BLE001
            try:
                self._w10_doctor_status.setText(
                    f"Reset failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
