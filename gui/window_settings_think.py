# Refactor Phase 1 step 5: SettingsMixin card-group mixin, moved verbatim from gui/window_settings.py.
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
from voice_assistant.core.gui_facade import config



class SettingsThinkMixin:

    def _build_think_cards(self, _g_connect):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- How VYUK thinks: [On this PC] [Cloud] segmented choice.
        # The segmented buttons only drive the engine combo (which now
        # lives in Advanced as the mechanism): setting its index fires
        # the existing currentIndexChanged handler, so env saving and
        # every dependent behaviour stay exactly as they were.
        think = self._settings_card(_g_connect, "How VYUK thinks", "Where VYUK does her thinking. The choice is used the next time you connect.")
        seg_row = QHBoxLayout()
        seg_row.setSpacing(6)
        self.btn_engine_pc = QPushButton("On this PC · recommended")
        self.btn_engine_pc.setObjectName("chipBtn")
        self.btn_engine_pc.setCheckable(True)
        self.btn_engine_pc.clicked.connect(lambda _=False: self._on_engine_segment_clicked("local"))
        seg_row.addWidget(self.btn_engine_pc, stretch=1)
        self.btn_engine_cloud = QPushButton("Cloud · best voice")
        self.btn_engine_cloud.setObjectName("chipBtn")
        self.btn_engine_cloud.setCheckable(True)
        self.btn_engine_cloud.clicked.connect(lambda _=False: self._on_engine_segment_clicked("gemini"))
        seg_row.addWidget(self.btn_engine_cloud, stretch=1)
        think.addLayout(seg_row)
        # v5.57.1 (item 3): brain truth, display-only. The saved
        # pick (.env BRAIN_ENGINE) beside the effective engine,
        # so a fallback can never again read as "the button
        # lied": when they differ, the line says why, and that
        # the saved pick is unchanged. Refreshed by
        # _sync_think_card and by the FALLBACK status branch.
        self.brain_truth_lbl = QLabel("")
        self.brain_truth_lbl.setObjectName("muted")
        self.brain_truth_lbl.setWordWrap(True)
        think.addWidget(self.brain_truth_lbl)
        # v5.25: the full three-option comparison (incl. Both) lives
        # in the choice dialog — the segmented control above only
        # switches the engine, so give it a permanent door here.
        self.btn_brain_choice = QPushButton(
            "Compare all three brain options…")
        self.btn_brain_choice.setObjectName("chipBtn")
        self.btn_brain_choice.clicked.connect(
            lambda _=False: self._run_onboarding(choice_only=True))
        think.addWidget(self.btn_brain_choice)

        # Cloud face of the card: a provider picker (v5.15), then
        # one key box per provider — Gemini Live (moved from the old
        # Gemini API card, which is dissolved) and Groq.
        self.think_cloud_box = QWidget()
        cloud_layout = QVBoxLayout(self.think_cloud_box)
        cloud_layout.setContentsMargins(0, 0, 0, 0)
        cloud_layout.setSpacing(8)
        provider_row = QHBoxLayout()
        provider_row.setSpacing(6)
        provider_label = QLabel("Cloud service")
        provider_label.setObjectName("cardHint")
        provider_row.addWidget(provider_label)
        self.combo_cloud_provider = QComboBox()
        self.combo_cloud_provider.setAccessibleName("Cloud brain provider")
        self.combo_cloud_provider.addItem(
            "Gemini Live — best voice", "gemini")
        self.combo_cloud_provider.addItem(
            "Groq — Llama 3.3 70B, fast text brain", "groq")
        _provider = str(getattr(config, "cloud_provider", "gemini")
                        or "gemini").strip().lower()
        self.combo_cloud_provider.setCurrentIndex(
            1 if _provider == "groq" else 0)
        self.combo_cloud_provider.currentIndexChanged.connect(
            lambda *_: self._on_cloud_provider_changed())
        provider_row.addWidget(self.combo_cloud_provider, stretch=1)
        cloud_layout.addLayout(provider_row)

        self.think_gemini_box = QWidget()
        gemini_layout = QVBoxLayout(self.think_gemini_box)
        gemini_layout.setContentsMargins(0, 0, 0, 0)
        gemini_layout.setSpacing(8)
        cloud_hint = QLabel("Only needed for the cloud brain. Get a free key from Google AI Studio. Stored only on this device, never shared.")
        cloud_hint.setObjectName("cardHint")
        cloud_hint.setWordWrap(True)
        gemini_layout.addWidget(cloud_hint)
        self.input_api_key = QLineEdit(config.api_key)
        self.input_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_api_key.setAccessibleName("Gemini API key")
        self.input_api_key.setPlaceholderText("Paste your key…")
        self.input_api_key.textChanged.connect(
            lambda _t="": self._sync_test_key_button())
        gemini_layout.addWidget(self.input_api_key)
        key_btn_row = QHBoxLayout()
        key_btn_row.setSpacing(6)
        btn_save_key = QPushButton("Save key")
        btn_save_key.setObjectName("primaryBtn")
        btn_save_key.setMinimumHeight(34)  # v5.54 (DPI rule ⑤)
        btn_save_key.clicked.connect(self._save_api_key)
        key_btn_row.addWidget(btn_save_key, stretch=1)
        # v3.2: one cheap Google metadata call proves the key AND the
        # voice-model name before the user ever presses Connect, so a
        # bad key / dead model / dead network stops being a guessing
        # game. Runs on a worker thread (see _test_api_key).
        self.btn_test_key = QPushButton("Test key")
        self.btn_test_key.setMinimumHeight(34)  # v5.54 (DPI rule ⑤)
        self.btn_test_key.clicked.connect(self._test_api_key)
        key_btn_row.addWidget(self.btn_test_key)
        gemini_layout.addLayout(key_btn_row)
        self.key_test_label = QLabel("")
        self.key_test_label.setObjectName("cardHint")
        self.key_test_label.setWordWrap(True)
        gemini_layout.addWidget(self.key_test_label)
        self._sync_test_key_button()
        cloud_layout.addWidget(self.think_gemini_box)

        self.think_groq_box = QWidget()
        groq_layout = QVBoxLayout(self.think_groq_box)
        groq_layout.setContentsMargins(0, 0, 0, 0)
        groq_layout.setSpacing(8)
        groq_hint = QLabel("Groq thinks on its own computers — your laptop just sends and receives words, so it stays cool and fast. Get a free key from console.groq.com. Stored only on this device, never shared.")
        groq_hint.setObjectName("cardHint")
        groq_hint.setWordWrap(True)
        groq_layout.addWidget(groq_hint)
        self.input_groq_key = QLineEdit(
            str(getattr(config, "groq_api_key", "") or ""))
        self.input_groq_key.setAccessibleName("Groq API key")
        self.input_groq_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_groq_key.setPlaceholderText("Paste your Groq key…")
        self.input_groq_key.textChanged.connect(
            lambda _t="": self._sync_test_groq_button())
        groq_layout.addWidget(self.input_groq_key)
        groq_btn_row = QHBoxLayout()
        groq_btn_row.setSpacing(6)
        btn_save_groq_key = QPushButton("Save Groq key")
        btn_save_groq_key.setObjectName("primaryBtn")
        btn_save_groq_key.setMinimumHeight(34)  # v5.54 (DPI rule ⑤)
        btn_save_groq_key.clicked.connect(self._save_groq_key)
        groq_btn_row.addWidget(btn_save_groq_key, stretch=1)
        self.btn_test_groq_key = QPushButton("Test key")
        self.btn_test_groq_key.setMinimumHeight(34)  # v5.54 (DPI rule ⑤)
        self.btn_test_groq_key.clicked.connect(self._test_groq_key)
        groq_btn_row.addWidget(self.btn_test_groq_key)
        groq_layout.addLayout(groq_btn_row)
        self.groq_key_test_label = QLabel("")
        self.groq_key_test_label.setObjectName("cardHint")
        self.groq_key_test_label.setWordWrap(True)
        groq_layout.addWidget(self.groq_key_test_label)
        self._sync_test_groq_button()
        cloud_layout.addWidget(self.think_groq_box)
        self._sync_cloud_provider()
        think.addWidget(self.think_cloud_box)

        # On-this-PC face of the card: one plain line + the offline
        # pack row (status, download/pause, remove — moved widgets).
        # The pack controls hide when the Advanced backend is Ollama
        # (the pack belongs to the built-in backend), exactly as the
        # old builtin_group visibility did — see
        # _update_backend_visibility.
        self.think_local_box = QWidget()
        local_layout = QVBoxLayout(self.think_local_box)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(8)
        local_hint = QLabel("Works fully offline. Her brain downloads itself in the background the first time you connect.")
        local_hint.setObjectName("cardHint")
        local_hint.setWordWrap(True)
        local_layout.addWidget(local_hint)
        self.think_pack_box = QWidget()
        pack_layout = QVBoxLayout(self.think_pack_box)
        pack_layout.setContentsMargins(0, 0, 0, 0)
        pack_layout.setSpacing(8)
        self.pack_storage_label = QLabel("Her on-PC brain files: —")
        self.pack_storage_label.setObjectName("cardHint")
        self.pack_storage_label.setWordWrap(True)
        pack_layout.addWidget(self.pack_storage_label)
        download_row = QHBoxLayout()
        download_row.setSpacing(6)
        self.btn_setup_local = QPushButton("Download her on-PC brain")
        self.btn_setup_local.clicked.connect(self._setup_local_brain)
        download_row.addWidget(self.btn_setup_local, stretch=1)
        self.btn_pause_download = QPushButton("Pause download")
        self.btn_pause_download.clicked.connect(self._pause_offline_download)
        self.btn_pause_download.setVisible(False)
        download_row.addWidget(self.btn_pause_download)
        pack_layout.addLayout(download_row)
        self.btn_remove_pack = QPushButton("Remove her on-PC brain files")
        self.btn_remove_pack.setObjectName("chipBtn")
        self.btn_remove_pack.clicked.connect(self._remove_offline_pack)
        pack_layout.addWidget(self.btn_remove_pack)
        # v5.17: the last download error, shown where the user
        # actually looks (it used to live only in the doctor's
        # output), plus one-tap network diagnosis.
        self.pack_error_label = QLabel("")
        self.pack_error_label.setObjectName("cardHint")
        self.pack_error_label.setWordWrap(True)
        self.pack_error_label.setVisible(False)
        pack_layout.addWidget(self.pack_error_label)
        diag_row = QHBoxLayout()
        self.btn_diagnose_downloads = QPushButton("Check download problems")
        self.btn_diagnose_downloads.setObjectName("chipBtn")
        self.btn_diagnose_downloads.setToolTip(
            "Check whether this PC can reach the places her brain, "
            "voice and ears download from")
        self.btn_diagnose_downloads.clicked.connect(self._diagnose_downloads)
        diag_row.addWidget(self.btn_diagnose_downloads)
        diag_row.addStretch()
        pack_layout.addLayout(diag_row)
        self.pack_diag_label = QLabel("")
        self.pack_diag_label.setObjectName("cardHint")
        self.pack_diag_label.setWordWrap(True)
        self.pack_diag_label.setVisible(False)
        pack_layout.addWidget(self.pack_diag_label)
        local_layout.addWidget(self.think_pack_box)
        think.addWidget(self.think_local_box)

        self._build_world_card(_g_connect)

    def _on_engine_segment_clicked(self, engine: str):
        """v2.9 segmented 'How VYUK thinks' choice. The buttons only
        drive the engine combo (kept in Advanced as the mechanism):
        setting its index fires the pre-existing engine handler, which
        persists BRAIN_ENGINE — one source of truth, no second path."""
        combo = getattr(self, "combo_brain_engine", None)
        if combo is not None:
            idx = combo.findData(engine)
            if idx >= 0 and idx != combo.currentIndex():
                combo.setCurrentIndex(idx)
        self._sync_think_card()

    def _sync_think_card(self):
        """Reflect the engine combo's current value onto the segmented
        buttons and the cloud/local faces of the 'How VYUK thinks'
        card, then refresh the summary header line."""
        combo = getattr(self, "combo_brain_engine", None)
        engine = None
        if combo is not None and combo.currentIndex() >= 0:
            engine = combo.itemData(combo.currentIndex())
        if engine not in ("gemini", "local"):
            engine = self._brain_engine()
        for attr, value in (("btn_engine_pc", "local"),
                            ("btn_engine_cloud", "gemini")):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.blockSignals(True)
                btn.setChecked(engine == value)
                btn.blockSignals(False)
        cloud_box = getattr(self, "think_cloud_box", None)
        if cloud_box is not None:
            cloud_box.setVisible(engine == "gemini")
        if engine == "gemini":
            self._sync_cloud_provider()
        local_box = getattr(self, "think_local_box", None)
        if local_box is not None:
            local_box.setVisible(engine == "local")
        self._refresh_brain_truth()
        self._refresh_settings_summary()

    def _refresh_brain_truth(self):
        """v5.57.1 (item 3): display-only brain truth for the
        'How VYUK thinks' card. Stored = the .env file's own
        BRAIN_ENGINE line (never os.environ, never the live
        config value — those are the session, not the save).
        Effective = the picked engine, except while a cloud→local
        fallback is active this session, when it is on-device.
        When the two differ the line says why in plain words;
        it never writes anything and never touches persistence
        or fallback logic."""
        lbl = getattr(self, "brain_truth_lbl", None)
        if lbl is None:
            return
        try:
            from voice_assistant.core.gui_facade import (
                _stored_env_file_value as _stored)
            stored = _stored("BRAIN_ENGINE")
        except Exception:  # noqa: BLE001 — truth display degrades
            stored = None
        if stored not in ("gemini", "local"):
            stored = None
        combo = getattr(self, "combo_brain_engine", None)
        picked = None
        if combo is not None and combo.currentIndex() >= 0:
            picked = combo.itemData(combo.currentIndex())
        if picked not in ("gemini", "local"):
            picked = self._brain_engine()
        fallback = bool(getattr(self, "_brain_fallback_active", False))
        effective = "local" if (fallback and picked == "gemini") else picked
        names = {"gemini": "Cloud", "local": "On this PC"}
        if stored is None:
            line = ("Saved brain: not saved yet — this session's "
                    f"pick ({names[effective]}) won't survive a restart.")
        else:
            line = f"Saved brain: {names[stored]}"
            if effective != stored:
                if fallback and stored == "gemini":
                    line += (" · Running on-device right now because "
                             "the cloud connection failed — your saved "
                             "pick is unchanged.")
                else:
                    line += (f" · Running on {names[effective]} this "
                             f"session; saved as {names[stored]}.")
        # Phase 1 (C03): a frozen brain forcing local is a
        # requested-vs-effective gap the old line never named
        # (it only knew about the cloud→local fallback flag).
        # Display-only and guarded — a truth line must never
        # break Settings if the session object is unavailable.
        try:
            from voice_assistant.core.gui_facade import (
                live_session as _live_session)  # Phase 3 B2h: brain hub moved to core/brain/brain.py
            _status = _live_session.provider_status()
        except Exception:  # noqa: BLE001 — truth display degrades
            _status = None
        if _status and _status.get("frozen_override"):
            line += (" · Running on-device because the brain is "
                     "frozen — your saved pick is unchanged.")
        lbl.setText(line)
