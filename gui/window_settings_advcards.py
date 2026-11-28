# Refactor Phase 1 step 5: SettingsMixin card-group mixin, moved verbatim from gui/window_settings.py.
from .window_widgets import (
    SHORTCUT_ROWS,
    _KOKORO_VOICE_ROSTER,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QSettings,
    Qt,
    QTime,
    QTimer,
)
from voice_assistant.core.gui_facade import VERSION
from .design_system.flowlayout import FlowLayout
from .design_system.switches import HudSwitch, toggle_row

from .style import (
    # E-F201: the Theme-packs box is a QPlainTextEdit, and the app
    # sheet styles the QLineEdit family only — the same tokens the
    # Voice card's hand-fix uses (gui/window_settings_voice.py),
    # so the two boxes match.
    TEXT,
    INPUT_BG,
    INPUT_BG_FOCUS,
    LINE_SUBTLE,
    FOCUS_BORDER,
    SELECTION_BG,
    RADIUS_BTN,
)



class SettingsAdvCardsMixin:

    def _build_adv_cards1(self, _g_appearance, more_layout):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        settings_file = self._settings_card(
            more_layout, "Your settings file",
            "Take your choices to another PC, or keep a backup. "
            "Keys and secrets are never included.")
        sf_row = QHBoxLayout()
        self.btn_export_settings = QPushButton("Export settings")
        self.btn_export_settings.setObjectName("chipBtn")
        self.btn_export_settings.clicked.connect(self._export_settings)
        sf_row.addWidget(self.btn_export_settings)
        self.btn_import_settings = QPushButton("Import settings")
        self.btn_import_settings.setObjectName("chipBtn")
        self.btn_import_settings.clicked.connect(self._import_settings)
        sf_row.addWidget(self.btn_import_settings)
        sf_row.addStretch()
        settings_file.addLayout(sf_row)
        self.settings_file_label = QLabel("")
        self.settings_file_label.setObjectName("cardHint")
        self.settings_file_label.setWordWrap(True)
        self.settings_file_label.setVisible(False)
        settings_file.addWidget(self.settings_file_label)
        # Loophole batch 3 (#10): the backup passphrase card.
        # Backups are encrypted with a passphrase the user sets;
        # without one, every backup (scheduled or exported)
        # refuses. The passphrase itself is protected by the
        # secrets store and never enters a backup.
        backup_pp = self._settings_card(
            more_layout, "Backup passphrase",
            "Backups are encrypted with this passphrase — without "
            "one, no backup runs. Keep it somewhere safe: a "
            "backup cannot be restored without it, and VYUK "
            "cannot recover it for you.")
        pp_row = QHBoxLayout()
        self.btn_set_backup_passphrase = QPushButton("Set passphrase")
        self.btn_set_backup_passphrase.setObjectName("chipBtn")
        self.btn_set_backup_passphrase.clicked.connect(
            self._set_backup_passphrase)
        pp_row.addWidget(self.btn_set_backup_passphrase)
        self.btn_clear_backup_passphrase = QPushButton("Clear passphrase")
        self.btn_clear_backup_passphrase.setObjectName("chipBtn")
        self.btn_clear_backup_passphrase.clicked.connect(
            self._clear_backup_passphrase)
        pp_row.addWidget(self.btn_clear_backup_passphrase)
        pp_row.addStretch()
        backup_pp.addLayout(pp_row)
        self.backup_passphrase_label = QLabel("")
        self.backup_passphrase_label.setObjectName("cardHint")
        self.backup_passphrase_label.setWordWrap(True)
        backup_pp.addWidget(self.backup_passphrase_label)
        self._refresh_backup_passphrase_status()
        # S12 (S3): the real shortcuts, in one place. Only
        # what the code actually binds is listed here.
        shortcuts = self._settings_card(
            more_layout, "Keyboard shortcuts",
            "The keys VYUK listens for.")
        # v5.57 (redo, A9.2): the one list (SHORTCUT_ROWS) — the
        # "?" overlay renders from the same rows, so the two can
        # never drift apart.
        for _keys, _what in SHORTCUT_ROWS:
            _line = QLabel(f"<b>{_keys}</b> — {_what}")
            _line.setObjectName("cardHint")
            _line.setWordWrap(True)
            shortcuts.addWidget(_line)

        # Conversion Wave 9 (PF-8 + PF-9): shortcuts (the one
        # SHORTCUT_ROWS list) and Help cards in Advanced, per the
        # PF placement table. No sidebar button. UI rethink Build 3
        # (§3.4): the "What each setting does" card is GONE — its
        # content (SETTING_EXPLANATIONS) now lives in the Settings
        # search empty-state (43 → 42 cards); the class stays in
        # pf_settings_help.py as the content's home.
        try:
            from voice_assistant.gui import pf_surfaces as _pf
            # S4 (settings audit): the ShortcutsWidget is NOT added
            # here anymore — it rendered the same SHORTCUT_ROWS
            # under the same "Keyboard shortcuts" sectionTitle as
            # the native card above, so the palette listed the
            # row twice and show_settings_card had two matches.
            # The native card + the "?" sheet are the one list
            # now; the class stays in pf_surfaces for reuse.
            self._w9_help_widget = _pf.HelpWidget()
            more_layout.addWidget(self._w9_help_widget)
        except Exception:  # noqa: BLE001 — Settings must still build
            pass

        # Conversion Wave 9: theme packs (load -> validate -> apply).
        # An invalid pack fails validation and changes nothing;
        # a valid pack's accent becomes the live Custom accent and
        # its full tokens are stored under theme_pack/*.
        try:
            from voice_assistant.core.gui_facade import w9_wiring as _w9
            from .w9_wiring_theme import searchable_combo
            _pack_card = self._settings_card(
                _g_appearance, "Theme packs",
                "Paste a theme-pack JSON (schema version 1, "
                "tokens only). Invalid packs are rejected whole — "
                "nothing changes. A valid pack applies its accent "
                "live; its full tokens are stored, not reduced.")
            self._w9_pack_combo = searchable_combo(
                _w9.saved_theme_pack_names(self.settings))
            _pack_card.addWidget(self._w9_pack_combo)
            self._w9_pack_input = QPlainTextEdit()
            # E-F201: the app sheet styles the QLineEdit family
            # only; a bare QPlainTextEdit would render as a white
            # box on the dark theme, so it carries the same well
            # by hand — byte-identical to the Voice card's
            # pronunciations box (gui/window_settings_voice.py),
            # values mirror the sheet's input rule.
            self._w9_pack_input.setStyleSheet(
                f"QPlainTextEdit {{ background-color: {INPUT_BG};"
                f" border: 1px solid {LINE_SUBTLE};"
                f" border-radius: {RADIUS_BTN}px; padding: 8px 12px; color: {TEXT};"
                f" selection-background-color: {SELECTION_BG};"
                f" selection-color: {TEXT}; }}"
                f"QPlainTextEdit:focus {{ border: {FOCUS_BORDER};"
                f" background-color: {INPUT_BG_FOCUS}; }}")
            self._w9_pack_input.setPlaceholderText(
                'Paste theme-pack JSON here…')
            self._w9_pack_input.setMaximumHeight(90)
            _pack_card.addWidget(self._w9_pack_input)
            self._w9_pack_status = QLabel("")
            self._w9_pack_status.setObjectName("cardHint")
            self._w9_pack_status.setWordWrap(True)
            _pack_card.addWidget(self._w9_pack_status)

            def _w9_apply_pack(_self=self):
                _text = _self._w9_pack_input.toPlainText().strip()
                if not _text:
                    _self._w9_pack_status.setText(
                        "Paste a theme-pack JSON first.")
                    return
                try:
                    _applied = _w9.apply_theme_pack_text(
                        _text, _self.settings)
                except Exception as _exc:  # noqa: BLE001 — pack refused
                    _self._w9_pack_status.setText(
                        f"Pack rejected — nothing changed: {_exc}")
                    return
                _self._w9_pack_status.setText(
                    f"Applied pack “{_applied['name']}” — accent "
                    f"{_applied['accent']} is live as Custom.")
                _self._w9_pack_combo.clear()
                _self._w9_pack_combo.addItems(
                    _w9.saved_theme_pack_names(_self.settings))
                try:
                    _self._apply_theme_to_orb()
                    _self._apply_appearance()
                except Exception:  # noqa: BLE001
                    pass

            self._w9_pack_apply_btn = QPushButton("Apply theme pack")
            # NB: clicked passes a bool; the lambda keeps it from
            # overwriting _w9_apply_pack's _self default (a real
            # bug caught by the W9 suite's card-click test).
            self._w9_pack_apply_btn.clicked.connect(
                lambda _checked=False: _w9_apply_pack())
            _pack_card.addWidget(
                self._w9_pack_apply_btn,
                alignment=Qt.AlignmentFlag.AlignLeft)
        except Exception:  # noqa: BLE001 — Settings must still build
            pass

        # Conversion Wave 9: shared settings model (SettingsStore)
        # behind the live settings path, with migration from the
        # existing shape (appearance/density -> density) and
        # per-row reset. Non-QSettings-backed rows show their
        # model default and offer no reset (their owner path
        # persists them, not this store).
        try:
            from voice_assistant.core.gui_facade import w9_wiring as _w9s
            _store = _w9s.settings_store(self.settings)
            _migrated = _w9s.migrate_settings_store(_store)
            _model_card = self._settings_card(
                more_layout, "Settings store",
                "The shared settings model, live: one row per "
                "setting, its current value, and a reset where "
                "the store owns the key. Reset removes the key "
                "so the default returns.")
            if _migrated:
                _mig = QLabel(
                    "Migrated from the existing shape: "
                    + ", ".join(_migrated) + ".")
                _mig.setObjectName("cardHint")
                _mig.setWordWrap(True)
                _model_card.addWidget(_mig)
            self._w9_model_rows = {}
            for _row in _w9s.settings_schema_rows(_store):
                _line = QLabel(
                    f"{_row.label}: {_row.value!r}"
                    + (" (changed)" if _row.modified else ""))
                _line.setObjectName("cardHint")
                _line.setWordWrap(True)
                _model_card.addWidget(_line)
                self._w9_model_rows[_row.name] = _line
                if _row.modified and _row.name in (
                        "theme", "orb_style", "sounds_enabled",
                        "suggestions_enabled", "density"):
                    _btn = QPushButton(f"Reset {_row.label}")
                    _btn.setObjectName("chipBtn")

                    def _w9_reset(_checked=False, _name=_row.name,
                                  _lbl=_line, _self=self):
                        try:
                            _w9s.reset_settings_row(
                                _w9s.settings_store(_self.settings),
                                _name)
                            _lbl.setText(
                                f"{_row.label}: "
                                f"{_w9s.settings_store(_self.settings).read(_name)!r}")
                            _self._apply_saved_settings()
                            _self._apply_appearance()
                        except Exception:  # noqa: BLE001
                            pass

                    _btn.clicked.connect(_w9_reset)
                    _model_card.addWidget(
                        _btn, alignment=Qt.AlignmentFlag.AlignLeft)
        except Exception:  # noqa: BLE001 — Settings must still build
            pass

    def _refresh_backup_passphrase_status(self):
        """Loophole batch 3 (#10): the backup-passphrase card's
        status line — set or not set, never the passphrase itself."""
        try:
            from voice_assistant.core.gui_facade import backup as _backup
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            has = _backup.has_backup_passphrase(str(_cfg_mod.DATA_DIR))
            self.backup_passphrase_label.setText(
                "A backup passphrase is set — backups are encrypted "
                "with it." if has else
                "No backup passphrase is set — backups are refused "
                "until you set one.")
        except Exception:  # noqa: BLE001 — a status line never
            pass  # breaks Settings

    def _set_backup_passphrase(self):
        """Loophole batch 3 (#10): ask for a passphrase (typed
        twice, masked), store it protected, refresh the status."""
        try:
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            from voice_assistant.core.gui_facade import backup as _backup
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            first, ok = QInputDialog.getText(
                self, "Set backup passphrase",
                "Backups will be encrypted with this passphrase.\n"
                "Keep it somewhere safe — a backup cannot be "
                "restored without it.",
                QLineEdit.EchoMode.Password)
            if not ok or not (first or "").strip():
                return
            second, ok2 = QInputDialog.getText(
                self, "Set backup passphrase",
                "Type the passphrase again to confirm.",
                QLineEdit.EchoMode.Password)
            if not ok2 or second != first:
                self.backup_passphrase_label.setText(
                    "The two passphrases did not match — nothing "
                    "was changed.")
                return
            _backup.set_backup_passphrase(str(_cfg_mod.DATA_DIR), first)
            self._refresh_backup_passphrase_status()
        except Exception:  # noqa: BLE001 — Settings must still work
            pass

    def _clear_backup_passphrase(self):
        """Loophole batch 3 (#10): clear the stored passphrase.
        Existing bundles keep working only with the passphrase
        they were written under; new backups refuse until a new
        one is set."""
        try:
            from voice_assistant.core.gui_facade import backup as _backup
            from voice_assistant.core.gui_facade import config_module as _cfg_mod
            _backup.set_backup_passphrase(str(_cfg_mod.DATA_DIR), "")
            self._refresh_backup_passphrase_status()
        except Exception:  # noqa: BLE001 — Settings must still work
            pass

    def _build_adv_cards2(self, more_layout):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # v5.56 (Wave 2, Stream F — PF-7 / PF-10 / PF-12):
        # three cards wiring the trust surfaces. The About card
        # below is untouched.
        trust = self._settings_card(
            more_layout, "System status",
            "What is down, what still works, what to do — and "
            "how power and network are throttling VYUK right now.")
        self.trust_status_label = QLabel("")
        self.trust_status_label.setObjectName("cardHint")
        self.trust_status_label.setWordWrap(True)
        trust.addWidget(self.trust_status_label)
        try:
            from voice_assistant.gui.window_trust import (
                degraded_mode_summary as _trust_summary)
            _summary = _trust_summary()
            self.trust_status_label.setText(
                f"{_summary['headline']} {_summary['power']}")
        except Exception:  # noqa: BLE001 — the card must build
            self.trust_status_label.setText(
                "System status is unavailable right now.")
        trust_row = QHBoxLayout()
        self.btn_trust_status = QPushButton("View system status…")
        self.btn_trust_status.clicked.connect(self._open_trust_status)
        trust_row.addWidget(self.btn_trust_status)
        self.btn_activity_feed = QPushButton("Activity feed…")
        self.btn_activity_feed.clicked.connect(self._open_activity_feed)
        trust_row.addWidget(self.btn_activity_feed)
        self.btn_egress_ledger = QPushButton("Egress ledger…")
        self.btn_egress_ledger.clicked.connect(self._open_egress_ledger)
        trust_row.addWidget(self.btn_egress_ledger)
        trust.addLayout(trust_row)
        # GAPS-27 (M-V4/M-V6/M-V9): the unified activity view,
        # the cost ledger and the memory panel sit beside the
        # Activity feed — same read-only dialogs, same lazy-open
        # discipline. A second row: six buttons in one row would
        # crowd the card.
        # E-F205: this row wraps (FlowLayout) instead of forcing
        # five buttons onto one line — at 908 the row's 666 px
        # minimum drove the System status card (698) past the
        # 624 px viewport, so opening Advanced put a horizontal
        # scrollbar under the Settings page. Single-row
        # equivalent at wide widths (same 6 px spacing, same
        # order, same buttons).
        trust_row2 = FlowLayout(spacing=6)
        self.btn_unified_activity = QPushButton("All activity…")
        self.btn_unified_activity.clicked.connect(
            self._open_unified_activity)
        trust_row2.addWidget(self.btn_unified_activity)
        self.btn_cost_ledger = QPushButton("Cost ledger…")
        self.btn_cost_ledger.clicked.connect(self._open_cost_ledger)
        trust_row2.addWidget(self.btn_cost_ledger)
        self.btn_today = QPushButton("Today…")
        self.btn_today.clicked.connect(self._open_today)
        trust_row2.addWidget(self.btn_today)
        self.btn_templates = QPushButton("Routine templates…")
        self.btn_templates.clicked.connect(self._open_templates)
        trust_row2.addWidget(self.btn_templates)
        self.btn_memory_panel = QPushButton("What I remember…")
        self.btn_memory_panel.clicked.connect(self._open_memory_panel)
        trust_row2.addWidget(self.btn_memory_panel)
        trust.addLayout(trust_row2)

        # v5.57 owner override (2026-10-24 18:20 IST): Doctor
        # moved out of the sidebar into Advanced, beside
        # the health/trust cluster (System status above, Report
        # a problem below). The full view is unchanged — this
        # card only opens it (page 7, still in the stack, just
        # without a sidebar entry).
        doctor = self._settings_card(
            more_layout, "Doctor",
            "Her health, at a glance — what passed, what needs a "
            "look, and what to do next.")
        self.btn_doctor_card = QPushButton("Open Doctor")
        self.btn_doctor_card.clicked.connect(lambda: self._show_page(7))
        doctor.addWidget(self.btn_doctor_card)

        report = self._settings_card(
            more_layout, "Report a problem",
            "Bundle diagnostics, preview exactly what is inside, "
            "and export it only with your consent. Nothing is "
            "sent anywhere automatically — there is no support "
            "backend; the export is a file you can attach.")
        self.btn_report_problem = QPushButton("Report a problem…")
        self.btn_report_problem.clicked.connect(self._open_report_problem)
        report.addWidget(self.btn_report_problem)

        # Loophole batch 7 (#6): feedback & crash reporting —
        # opt-in, off by default, separate from sign-in. The
        # toggle writes the config field + FEEDBACK_REPORTING_ENABLED
        # in .env (the same persist shape as the Privacy card's
        # private-mode toggle). The status line says what is
        # recorded right now, and how many crash signatures sit
        # locally.
        feedback = self._settings_card(
            more_layout, "Feedback & crash reports",
            "Sends only the feedback label and a hash of the turn "
            "— never the words. Crash reports carry only the crash "
            "signature — never what you said or did. Off unless "
            "you turn it on; separate from sign-in.")
        self.chk_feedback_reporting = HudSwitch(text="Send feedback & crash reports")
        try:
            from voice_assistant.core.gui_facade import config as _cfg

            self.chk_feedback_reporting.setChecked(
                bool(getattr(_cfg, "feedback_reporting_enabled", False)))
        except Exception:  # noqa: BLE001 — a broken read reads off
            self.chk_feedback_reporting.setChecked(False)
        self.chk_feedback_reporting.toggled.connect(
            self._on_feedback_reporting_toggled)
        feedback.addWidget(toggle_row(self.chk_feedback_reporting))
        self._feedback_reporting_status = QLabel("")
        self._feedback_reporting_status.setObjectName("cardHint")
        self._feedback_reporting_status.setWordWrap(True)
        feedback.addWidget(self._feedback_reporting_status)
        self._refresh_feedback_reporting_status()

        # v5.65 (Wave 10 — PF-4 / PF-13 / PF-6): distribution cards.
        # Data portability: export a complete bundle (settings +
        # memory + milestones) or import one; a mismatch is
        # reported, never hidden. Models & storage: installed
        # models + sizes, removable with a clean fallback.
        # What's new: the full changelog, always retrievable.
        port = self._settings_card(
            more_layout, "Data portability",
            "Take your data with you: export settings, memory and "
            "milestones as one bundle, or import a bundle into "
            "this install. Nothing is dropped silently — the "
            "import report lists what applied and what didn't.")
        self._w10_port_status = QLabel("")
        self._w10_port_status.setObjectName("cardHint")
        self._w10_port_status.setWordWrap(True)
        port.addWidget(self._w10_port_status)
        _port_row = QHBoxLayout()
        self.btn_w10_export = QPushButton("Export my data…")
        self.btn_w10_export.clicked.connect(self._w10_export_data)
        _port_row.addWidget(self.btn_w10_export)
        self.btn_w10_import = QPushButton("Import a bundle…")
        self.btn_w10_import.clicked.connect(self._w10_import_data)
        _port_row.addWidget(self.btn_w10_import)
        port.addLayout(_port_row)

        models = self._settings_card(
            more_layout, "Models & storage",
            "Installed models and how much space they use. "
            "Removing a model reclaims its space and the app "
            "falls back cleanly to the next available tier — "
            "removal never breaks it.")
        self._w10_models_label = QLabel("")
        self._w10_models_label.setObjectName("cardHint")
        self._w10_models_label.setWordWrap(True)
        models.addWidget(self._w10_models_label)
        _models_row = QHBoxLayout()
        self.btn_w10_models_refresh = QPushButton("Refresh list")
        self.btn_w10_models_refresh.clicked.connect(
            self._w10_refresh_models)
        _models_row.addWidget(self.btn_w10_models_refresh)
        self._w10_model_combo = QComboBox()
        self._w10_model_combo.setAccessibleName("Local model")
        _models_row.addWidget(self._w10_model_combo)
        self.btn_w10_model_remove = QPushButton("Remove selected")
        self.btn_w10_model_remove.clicked.connect(
            self._w10_remove_model)
        _models_row.addWidget(self.btn_w10_model_remove)
        models.addLayout(_models_row)
        try:
            self._w10_refresh_models()
        except Exception:  # noqa: BLE001 — the card must build
            pass

        whatsnew = self._settings_card(
            more_layout, "What's new",
            "Every shipped version's changes, in plain language. "
            "The startup dialog shows only what's new since your "
            "last version; this list always shows everything.")
        self._w10_whatsnew_label = QLabel("")
        self._w10_whatsnew_label.setObjectName("cardHint")
        self._w10_whatsnew_label.setWordWrap(True)
        whatsnew.addWidget(self._w10_whatsnew_label)
        try:
            from voice_assistant.gui.changelog_widget import (
                format_entries as _fmt)
            from voice_assistant.core.gui_facade import changelog as _cl
            self._w10_whatsnew_label.setText(
                _fmt(_cl.pending_entries(None)) or
                "You're up to date — nothing new to show.")
        except Exception:  # noqa: BLE001 — the card must build
            self._w10_whatsnew_label.setText(
                "Changelog is unavailable right now.")

        power = self._settings_card(
            more_layout, "Power & network",
            "On battery, background checks slow down and local "
            "inference throttles; on a metered connection, model "
            "downloads and updates pause. Everything resumes on "
            "AC power and an unmetered connection.")
        self.power_status_label = QLabel("")
        self.power_status_label.setObjectName("cardHint")
        self.power_status_label.setWordWrap(True)
        power.addWidget(self.power_status_label)
        try:
            from voice_assistant.core.gui_facade import power_policy as _pp
            self.power_status_label.setText(
                _pp.policy_summary(_pp.current_policy()))
        except Exception:  # noqa: BLE001 — the card must build
            self.power_status_label.setText(
                "Power state is unavailable right now.")

        about = self._settings_card(more_layout, "About")
        about.addWidget(QLabel(f"VYUK — version {VERSION} · Premium"))
        # UX-safe batch: the skill count and the brain phrase are
        # LIVE, never hard-coded. The count comes from the same
        # registry source the Skills page uses; the brain phrase
        # follows the selected engine (BrainMixin._brain_engine,
        # same MainWindow — no new settings channel). A failed
        # count read shows no count rather than a wrong one (the
        # Skills page's rule). The world-view name left the label
        # with the feature (removed 2026-11-05). NOTE:
        # this label reflects the engine at settings-build time;
        # switching engines in this same page does not rewrite it
        # until the page is rebuilt.
        try:
            from voice_assistant.core.gui_facade import tools as _count_registry  # Phase 4 S1b INTENTIONAL: registry split to funnel
            _skill_count = len(_count_registry.get_declarations())
        except Exception:  # noqa: BLE001 — no count, not a wrong count
            _skill_count = None
        about_hint = QLabel(
            _about_hint_text(_skill_count, self._brain_engine()))
        about_hint.setObjectName("cardHint")
        about_hint.setWordWrap(True)
        about.addWidget(about_hint)
        # Phase 4 (D-P4-26): what Undo covers, stated where the
        # app is described — recorded: settings changes, file
        # writes, extension state; not recorded: a message sent,
        # a payment completed.
        undo_hint = QLabel(
            "Undo covers settings changes, file writes, and "
            "extension state. It can't undo a message sent or a "
            "payment completed.")
        undo_hint.setObjectName("cardHint")
        undo_hint.setWordWrap(True)
        about.addWidget(undo_hint)
        self.btn_updates = QPushButton("Check for updates")
        self.btn_updates.clicked.connect(self._on_updates_clicked)
        about.addWidget(self.btn_updates)
        self.update_label = QLabel("")
        self.update_label.setObjectName("cardHint")
        self.update_label.setWordWrap(True)
        about.addWidget(self.update_label)
        # S12 (S1+S2): reach the files behind the app — her
        # data folder, and what she has been doing lately.
        about_row = QHBoxLayout()
        self.btn_open_data = QPushButton("Open data folder")
        self.btn_open_data.clicked.connect(self._open_data_folder)
        about_row.addWidget(self.btn_open_data)
        self.btn_activity = QPushButton("Recent activity")
        self.btn_activity.clicked.connect(self._show_recent_activity)
        about_row.addWidget(self.btn_activity)
        about_row.addStretch()
        about.addLayout(about_row)
        self._update_info: dict | None = None
        self._update_busy = False
        try:
            from voice_assistant.core.gui_facade import updater as _updater  # Phase 3 H1: moved to core/health/
            _pending = _updater.pending_update()
        except Exception:  # noqa: BLE001 — the About card must build
            _pending = None
        if _pending:
            self.update_label.setText(
                f"VYUK {_pending.get('version', '')} is downloaded — "
                "restart VYUK to install it.")
            self.btn_updates.setEnabled(False)
            self.btn_updates.setText("Ready to install on restart")
        # Loophole batch 6 (#9): the update's two undo seams.
        # Roll back to the previous build (only when an update
        # backup exists) and discard a downloaded update (only
        # when one is staged). Both are plain buttons on this
        # card — never voice-triggered, never automatic. Their
        # visibility is refreshed by _refresh_update_buttons()
        # after any update action.
        update_actions = QHBoxLayout()
        self.btn_rollback = QPushButton("Roll back to previous build")
        self.btn_rollback.clicked.connect(self._on_rollback_clicked)
        update_actions.addWidget(self.btn_rollback)
        self.btn_discard_update = QPushButton("Discard downloaded update")
        self.btn_discard_update.clicked.connect(
            self._on_discard_update_clicked)
        update_actions.addWidget(self.btn_discard_update)
        update_actions.addStretch()
        about.addLayout(update_actions)
        self._refresh_update_buttons()

    # ------------------------------------------------- UI Phase 5 Home hub
    # (D-P5-15): the ONE place a hub is configured. Host in .env,
    # token in the secrets store (never plaintext, never shown),
    # allowlist in .env beside the host. ONE button tests the
    # connection and saves — a failed test saves NOTHING (S-P5-03);
    # there is no separate Save button to drift from the test.
    def _build_ha_card(self, _g_connect):
        from voice_assistant.core.gui_facade import (
            home_assistant_config as _hac)
        card = self._settings_card(
            _g_connect, "Home hub",
            "Connect a Home Assistant instance on your network. "
            "Local network only — a hub on the public internet is "
            "refused. The token is stored protected, never in "
            "plaintext, and never shown again.")
        self._ha_config_mod = _hac
        host_row = QHBoxLayout()
        host_label = QLabel("Hub address")
        host_label.setObjectName("cardHint")
        host_row.addWidget(host_label)
        self._ha_host_edit = QLineEdit()
        self._ha_host_edit.setPlaceholderText(
            "192.168.1.10 or homeassistant.local")
        self._ha_host_edit.setAccessibleName(
            "Home Assistant hub address")
        host_row.addWidget(self._ha_host_edit, stretch=1)
        card.addLayout(host_row)
        token_row = QHBoxLayout()
        token_label = QLabel("Long-lived access token")
        token_label.setObjectName("cardHint")
        token_row.addWidget(token_label)
        self._ha_token_edit = QLineEdit()
        self._ha_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._ha_token_edit.setPlaceholderText(
            "Paste a token from Home Assistant → Profile → "
            "Long-lived access tokens")
        self._ha_token_edit.setAccessibleName(
            "Home Assistant long-lived access token")
        token_row.addWidget(self._ha_token_edit, stretch=1)
        card.addLayout(token_row)
        btn_row = QHBoxLayout()
        self._ha_test_btn = QPushButton("Test connection and save")
        self._ha_test_btn.setObjectName("chipBtn")
        self._ha_test_btn.clicked.connect(self._ha_test_and_save)
        btn_row.addWidget(self._ha_test_btn)
        self._ha_discover_btn = QPushButton("Discover entities")
        self._ha_discover_btn.setObjectName("chipBtn")
        self._ha_discover_btn.clicked.connect(self._ha_discover)
        btn_row.addWidget(self._ha_discover_btn)
        self._ha_remove_btn = QPushButton("Remove hub")
        self._ha_remove_btn.setObjectName("chipBtn")
        self._ha_remove_btn.clicked.connect(self._ha_remove_hub)
        btn_row.addWidget(self._ha_remove_btn)
        btn_row.addStretch()
        card.addLayout(btn_row)
        self._ha_status = QLabel("")
        self._ha_status.setObjectName("cardHint")
        self._ha_status.setWordWrap(True)
        card.addWidget(self._ha_status)
        # The allowlist editor: one row per discovered entity —
        # Allow checkbox, name + entity id, room field, and the
        # Assist-exposed checkbox. The Assist tick is user-asserted
        # (no REST source exists for it — the Devices page gates
        # controls on it, so only tick it for entities actually
        # exposed to Assist in Home Assistant). Ticks are staged
        # and saved by the next Test & save.
        self._ha_assist_hint = QLabel(
            "Only tick “Exposed to Assist” for entities actually "
            "exposed to Assist in Home Assistant — the Devices "
            "page will only offer controls for ticked entities.")
        self._ha_assist_hint.setObjectName("cardHint")
        self._ha_assist_hint.setWordWrap(True)
        card.addWidget(self._ha_assist_hint)
        self._ha_entities_box = QVBoxLayout()
        self._ha_entities_box.setSpacing(6)
        card.addLayout(self._ha_entities_box)
        self._ha_unsaved_hint = QLabel(
            "Changes not yet saved — Test connection and save to "
            "save them.")
        self._ha_unsaved_hint.setObjectName("muted")
        self._ha_unsaved_hint.setWordWrap(True)
        self._ha_unsaved_hint.setVisible(False)
        card.addWidget(self._ha_unsaved_hint)
        # Configuring a hub and granting the permission are two
        # different steps — say so, with the door to Permissions.
        grant_row = QHBoxLayout()
        grant_hint = QLabel(
            "Setting up a hub does not grant VYUK permission to "
            "use your home devices — grant that in Permissions.")
        grant_hint.setObjectName("cardHint")
        grant_hint.setWordWrap(True)
        grant_row.addWidget(grant_hint, stretch=1)
        self._ha_perm_btn = QPushButton("Open Permissions")
        self._ha_perm_btn.setObjectName("chipBtn")
        self._ha_perm_btn.clicked.connect(
            lambda: self.show_settings_card("Permissions"))
        grant_row.addWidget(self._ha_perm_btn)
        card.addLayout(grant_row)
        self._ha_staged = {}
        self._ha_discovered = {}
        self._ha_refresh_card()

    def _ha_staged_allowlist(self):
        """{entity_id: room} from the staged ticks (allowed only)."""
        return {eid: str(s.get("room") or "").strip()
                for eid, s in self._ha_staged.items()
                if s.get("allow") and str(s.get("room") or "").strip()}

    def _ha_staged_exposed(self):
        """Assist-exposed set from the staged ticks — allowed
        entities only (an exposed-but-unallowed entity can never
        be controlled, so it is not stored as exposed)."""
        return {eid for eid, s in self._ha_staged.items()
                if s.get("allow") and s.get("assist")}

    def _ha_entered_token(self):
        """The token to test with: the field when filled, else the
        stored token (an empty field reuses it — the token is
        never shown, so it can never be re-typed from here)."""
        entered = self._ha_token_edit.text().strip()
        if entered:
            return entered
        token, _status = self._ha_config_mod.load_ha_token()
        return token or ""

    def _ha_refresh_card(self):
        """Fill the card from the stored config (on build, and
        after every save/remove)."""
        cfg = self._ha_config_mod.load_ha_config()
        self._ha_host_edit.setText(cfg.get("host") or "")
        if cfg.get("has_token"):
            self._ha_token_edit.setPlaceholderText(
                "Token saved — enter a new one to replace it")
        else:
            self._ha_token_edit.setPlaceholderText(
                "Paste a token from Home Assistant → Profile → "
                "Long-lived access tokens")
        self._ha_token_edit.clear()
        allowlist = dict(cfg.get("allowlist") or {})
        exposed = set(cfg.get("assist_exposed") or set())
        self._ha_staged = {}
        for eid in sorted(set(self._ha_discovered)
                          | set(allowlist)):
            self._ha_staged[eid] = {
                "allow": eid in allowlist,
                "room": allowlist.get(eid, ""),
                "assist": eid in exposed,
            }
        self._ha_rebuild_entities()
        self._ha_update_unsaved_hint()
        if cfg.get("configured"):
            self._ha_status.setText(
                f"Configured — hub at {cfg.get('host')}, "
                f"{len(allowlist)} entit"
                f"{'y' if len(allowlist) == 1 else 'ies'} allowed.")
        elif cfg.get("host"):
            self._ha_status.setText(
                "Hub address saved, but no token is stored — "
                "paste a token and test the connection.")
        else:
            self._ha_status.setText("No hub configured yet.")

    def _ha_rebuild_entities(self):
        """Rebuild the allowlist editor rows from staged + discovered."""
        # The _mem_clear_layout pattern: setParent(None) FIRST, not
        # just deleteLater — until the deferred delete lands, an
        # orphaned widget keeps answering findChildren (the orphan
        # trap from legs 51/54).
        while self._ha_entities_box.count():
            item = self._ha_entities_box.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)
                wdg.deleteLater()
                continue
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().setParent(None)
                        sub.widget().deleteLater()
        if not self._ha_discovered and not self._ha_staged:
            return
        for eid in sorted(set(self._ha_discovered)
                          | set(self._ha_staged)):
            staged = self._ha_staged.get(eid) or {
                "allow": False, "room": "", "assist": False}
            self._ha_staged[eid] = staged
            row = QHBoxLayout()
            allow = HudSwitch(text="Allow")
            allow.setChecked(bool(staged.get("allow")))
            allow.toggled.connect(
                lambda _c=False, _e=eid, _cb=allow:
                    self._ha_stage_toggle(_e, "allow", _cb.isChecked()))
            row.addWidget(toggle_row(allow))
            name = self._ha_discovered.get(eid) or eid
            label = QLabel(f"{name}\n{eid}")
            label.setObjectName("cardHint")
            label.setWordWrap(True)
            row.addWidget(label, stretch=1)
            room = QLineEdit(str(staged.get("room") or ""))
            room.setPlaceholderText("Room")
            room.setAccessibleName(f"Room for {eid}")
            room.textChanged.connect(
                lambda _t="", _e=eid, _f=room:
                    self._ha_stage_toggle(_e, "room", _f.text()))
            row.addWidget(room)
            assist = HudSwitch(text="Exposed to Assist")
            assist.setChecked(bool(staged.get("assist")))
            assist.toggled.connect(
                lambda _c=False, _e=eid, _cb=assist:
                    self._ha_stage_toggle(_e, "assist", _cb.isChecked()))
            row.addWidget(toggle_row(assist))
            self._ha_entities_box.addLayout(row)

    def _ha_stage_toggle(self, entity_id, key, value):
        staged = self._ha_staged.setdefault(
            entity_id, {"allow": False, "room": "", "assist": False})
        staged[key] = value
        self._ha_update_unsaved_hint()

    def _ha_update_unsaved_hint(self):
        try:
            stored_allow, stored_exposed = \
                self._ha_config_mod.load_ha_allowlist()
        except Exception:  # noqa: BLE001 — a failed read reads as empty
            stored_allow, stored_exposed = {}, set()
        staged_allow = self._ha_staged_allowlist()
        staged_exposed = self._ha_staged_exposed()
        self._ha_unsaved_hint.setVisible(
            staged_allow != stored_allow
            or staged_exposed != stored_exposed)

    def _ha_test_and_save(self):
        """ONE button: validate pre-network, test live, then save
        host + token + allowlist together. A failed test saves
        NOTHING (S-P5-03) — there is no separate Save button."""
        hac = self._ha_config_mod
        host = self._ha_host_edit.text().strip()
        if not host:
            self._ha_status.setText("That needs the hub's address.")
            return
        token = self._ha_entered_token()
        if not token:
            self._ha_status.setText(
                "A token is needed — create a long-lived access "
                "token in Home Assistant first.")
            return
        client, reason = hac.build_ha_client_for(
            host, token, self._ha_staged_allowlist(),
            self._ha_staged_exposed())
        if client is None:
            self._ha_status.setText(reason or
                                    "The hub couldn't be reached.")
            return
        try:
            client.health()
        except Exception as err:  # noqa: BLE001 — failed test:
            # nothing is saved.
            self._ha_status.setText(hac.ha_error_line(err))
            return
        try:
            states = client.get_states()
        except Exception:  # noqa: BLE001 — discovery failing does
            states = []  # not fail the save
        for state in states or []:
            eid = str((state or {}).get("entity_id") or "")
            if not eid:
                continue
            attrs = (state or {}).get("attributes") or {}
            friendly = str(attrs.get("friendly_name") or eid)
            self._ha_discovered[eid] = friendly
            self._ha_staged.setdefault(
                eid, {"allow": False, "room": "", "assist": False})
        ok_token, token_msg = hac.save_ha_token(token)
        if not ok_token:
            self._ha_status.setText(token_msg)
            return
        if not hac.save_ha_host(host):
            self._ha_status.setText(
                "The token is saved, but the hub address couldn't "
                "be saved — check the settings can be written, "
                "then try again.")
            return
        if not hac.save_ha_allowlist(
                self._ha_staged_allowlist(),
                self._ha_staged_exposed()):
            self._ha_status.setText(
                "The hub is saved, but the allowlist couldn't be "
                "saved — check the settings can be written, then "
                "try again.")
            return
        self._ha_token_edit.clear()
        self._ha_refresh_card()
        self._ha_status.setText(
            f"Saved — connected to {host}. "
            f"{len(self._ha_discovered)} entit"
            f"{'y' if len(self._ha_discovered) == 1 else 'ies'} "
            "found on the hub.")
        try:
            self._refresh_settings_card_values()
        except Exception:  # noqa: BLE001 — the value line is a nicety
            pass

    def _ha_discover(self):
        """Refresh the entity list from the hub without saving
        anything (the list is staged until the next Test & save)."""
        hac = self._ha_config_mod
        host = self._ha_host_edit.text().strip()
        if not host:
            self._ha_status.setText("That needs the hub's address.")
            return
        token = self._ha_entered_token()
        if not token:
            self._ha_status.setText(
                "A token is needed — create a long-lived access "
                "token in Home Assistant first.")
            return
        client, reason = hac.build_ha_client_for(
            host, token, self._ha_staged_allowlist(),
            self._ha_staged_exposed())
        if client is None:
            self._ha_status.setText(reason or
                                    "The hub couldn't be reached.")
            return
        try:
            states = client.get_states()
        except Exception as err:  # noqa: BLE001
            self._ha_status.setText(hac.ha_error_line(err))
            return
        found = 0
        for state in states or []:
            eid = str((state or {}).get("entity_id") or "")
            if not eid:
                continue
            attrs = (state or {}).get("attributes") or {}
            self._ha_discovered[eid] = str(
                attrs.get("friendly_name") or eid)
            self._ha_staged.setdefault(
                eid, {"allow": False, "room": "", "assist": False})
            found += 1
        self._ha_rebuild_entities()
        self._ha_update_unsaved_hint()
        self._ha_status.setText(
            f"Found {found} entit{'y' if found == 1 else 'ies'} on "
            "the hub — tick Allow and a room, then Test connection "
            "and save.")

    def _ha_remove_hub(self):
        """Clear the host, delete the token envelope, clear the
        allowlist. The Devices page reads not-configured after
        this, immediately."""
        hac = self._ha_config_mod
        hac.save_ha_host("")
        hac.clear_ha_token_file()
        hac.save_ha_allowlist({}, set())
        self._ha_discovered = {}
        self._ha_host_edit.clear()
        self._ha_refresh_card()
        self._ha_status.setText("Hub removed.")
        try:
            self._refresh_settings_card_values()
        except Exception:  # noqa: BLE001 — the value line is a nicety
            pass


    # ------------------------------------------------- v5.56 trust surfaces
    # (Stream F: PF-7 / PF-2 / PF-23 / PF-10 dialogs, opened
    # lazily so a missing trust module never breaks Settings.)
    def _open_trust_status(self):
        """PF-7 degraded-mode status dialog."""
        try:
            from voice_assistant.gui.window_trust import (
                show_degraded_mode_dialog)
            show_degraded_mode_dialog(self)
        except Exception:  # noqa: BLE001 — a dialog must not crash
            pass

    def _open_activity_feed(self):
        """PF-2 activity feed dialog."""
        try:
            from voice_assistant.gui.window_activity import (
                build_activity_dialog)
            dlg, _widgets = build_activity_dialog(self)
            dlg.exec()
        except Exception:  # noqa: BLE001
            pass

    def _open_egress_ledger(self):
        """PF-23 egress ledger dialog."""
        try:
            from voice_assistant.gui.window_activity import (
                build_ledger_dialog)
            dlg, _widgets = build_ledger_dialog(self)
            dlg.exec()
        except Exception:  # noqa: BLE001
            pass

    def _open_unified_activity(self):
        """GAPS-27 (M-V4): the unified activity dialog — chain
        human lines + per-tool ledgers + cost turns +
        data/activity.log, newest first."""
        try:
            from voice_assistant.gui.window_activity import (
                build_unified_dialog)
            dlg, _widgets = build_unified_dialog(self)
            dlg.exec()
        except Exception:  # noqa: BLE001
            pass

    def _open_cost_ledger(self):
        """GAPS-27 (M-V9): the cost ledger dialog."""
        try:
            from voice_assistant.gui.window_cost import (
                build_cost_dialog)
            dlg, _widgets = build_cost_dialog(self)
            dlg.exec()
        except Exception:  # noqa: BLE001
            pass

    def _open_today(self):
        """RET-3: the Today safety surface — today's actions,
        spend, confirmations, kill-chain state, honest undo."""
        try:
            from voice_assistant.gui.window_today import (
                build_today_dialog)
            dlg, _widgets = build_today_dialog(self)
            dlg.exec()
        except Exception:  # noqa: BLE001
            pass

    def _open_templates(self):
        """RET-5: the routine-template gallery — bundled
        templates to import as routines, and the user's routines
        to export as templates. Import goes through the funnel's
        confirm-gated save routine action; export shows the exact
        JSON first and only proceeds on yes."""
        try:
            from voice_assistant.gui.window_templates import (
                build_templates_dialog)
            dlg, _widgets = build_templates_dialog(self)
            dlg.exec()
        except Exception:  # noqa: BLE001
            pass

    def _open_memory_panel(self):
        """GAPS-27 (M-V6): the "What I remember about you"
        panel — facts with their source, recent episodes,
        per-item delete."""
        try:
            from voice_assistant.gui.window_memory_panel import (
                build_memory_dialog)
            dlg, _widgets = build_memory_dialog(self)
            dlg.exec()
        except Exception:  # noqa: BLE001
            pass

    def _open_report_problem(self):
        """PF-10 report-a-problem dialog. Loophole batch 7 (#6):
        the dialog is given the last assistant turn, so it can
        offer the answer-feedback row (label + turn hash, never
        the words); without a turn the row stays hidden."""
        try:
            from voice_assistant.gui.window_trust import (
                last_assistant_turn,
                show_report_problem_dialog,
            )

            turn = ""
            try:
                _thread_get = getattr(self, "_chat_thread", None)
                if callable(_thread_get):
                    turn = last_assistant_turn(_thread_get())
            except Exception:  # noqa: BLE001 — no turn, no row
                turn = ""
            show_report_problem_dialog(self, turn_text=turn)
        except Exception:  # noqa: BLE001
            pass

    def _on_feedback_reporting_toggled(self, on: bool):
        # Loophole batch 7 (#6): persists like the Privacy
        # card's private-mode toggle (config field +
        # FEEDBACK_REPORTING_ENABLED in .env). The crash hook
        # reads the config field live at crash time, so flipping
        # it on mid-session arms the hook at once. Lives HERE,
        # beside the card that connects it — a bare
        # SettingsAdvancedCardsMixin builds this card, and a
        # handler on another mixin leaves the build raising
        # AttributeError (loophole batch 5's lesson).
        try:
            from voice_assistant.core.gui_facade import config

            config.feedback_reporting_enabled = bool(on)
        except Exception:  # noqa: BLE001 — the toggle still shows
            pass
        try:
            self._persist_config_setting(
                "FEEDBACK_REPORTING_ENABLED", "true" if on else "false")
        except Exception:  # noqa: BLE001 — the in-memory field
            pass  # already carries the choice
        self._refresh_feedback_reporting_status()

    def _refresh_feedback_reporting_status(self):
        """Loophole batch 7 (#6): the feedback card's status line
        — plain words for what is recorded right now. Never
        raises (a broken read reads as off)."""
        label = getattr(self, "_feedback_reporting_status", None)
        if label is None:
            return
        try:
            from voice_assistant.core.gui_facade import config

            on = bool(getattr(config, "feedback_reporting_enabled",
                              False))
        except Exception:  # noqa: BLE001 — a broken read reads off
            on = False
        try:
            from voice_assistant.core.gui_facade import (
                feedback_report as _fr)

            crash_count = _fr.count_crash_signatures()
        except Exception:  # noqa: BLE001 — an unreadable store
            crash_count = 0  # reads as empty
        if on:
            text = ("On — feedback labels and crash signatures may "
                    "be recorded locally.")
        else:
            text = ("Off — nothing is recorded. Turn it on to send "
                    "feedback on an answer or record a crash.")
        if crash_count:
            text += (f" {crash_count} crash signature"
                     f"{'s' if crash_count != 1 else ''} recorded "
                     "locally.")
        try:
            label.setText(text)
        except Exception:  # noqa: BLE001 — a status line never
            pass  # breaks Settings

def _about_hint_text(skill_count, engine: str) -> str:
    """About-page hint, pure (no Qt): the count is live-or-absent,
    never hard-coded, and the brain phrase follows the selected
    engine. A failed count read shows no count rather than a wrong
    one (the Skills page's rule). An unknown engine falls back to
    the cloud phrase. UX-safe batch."""
    brain_phrase = {
        "gemini": "Google's cloud brain",
        "local": "the brain on this PC",
    }.get(engine, "Google's cloud brain")
    if skill_count is None:
        return f"Thinks with {brain_phrase}, remembers, and sees."
    return (f"Thinks with {brain_phrase}, remembers, sees, "
            f"and has {skill_count} skills.")
