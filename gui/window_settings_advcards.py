# Refactor Phase 1 step 5: SettingsMixin card-group mixin, moved verbatim from gui/window_settings.py.
from .window_widgets import (
    SHORTCUT_ROWS,
    _KOKORO_VOICE_ROSTER,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QCheckBox,
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
        trust_row2 = QHBoxLayout()
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
        trust_row2.addStretch(1)
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
        """PF-10 report-a-problem dialog."""
        try:
            from voice_assistant.gui.window_trust import (
                show_report_problem_dialog)
            show_report_problem_dialog(self)
        except Exception:  # noqa: BLE001
            pass

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
