# Refactor Phase 1 step 5: SettingsMixin card-group mixin, moved verbatim from gui/window_settings.py.
from voice_assistant.core.gui_facade import (
    LANGUAGES,
    TRANSCRIPTION_PROFILES,
)
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
from .design_system.switches import HudSwitch, toggle_row
from voice_assistant.core.gui_facade import config
from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QSettings,
    Qt,
    QTime,
    QTimer,
)
from voice_assistant.core.gui_facade import qsettings_get



class SettingsAdvBrainMixin:

    def _build_adv_brain_cards(self, more_layout):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- S4 (settings audit): the old "Advanced" card is now
        # "Local brain" — the Advanced disclosure above holds it,
        # so the card takes the name of what it actually holds
        # (every technical knob for the on-PC brain). The fold
        # inside (QSettings "advanced/open", default shut) is
        # unchanged; the engine combo lives here as the mechanism
        # — the segmented control above drives it.
        advanced = self._settings_card(more_layout, "Local brain", "For tinkerers — how the local brain runs, and the knobs most people never need.")
        self.btn_brain_advanced = QPushButton("Show advanced options ▸")
        self.btn_brain_advanced.setObjectName("chipBtn")
        self.btn_brain_advanced.setCheckable(True)
        advanced_open = bool(qsettings_get("advanced/open", settings=self.settings))
        self.btn_brain_advanced.setChecked(advanced_open)
        self.btn_brain_advanced.setText(
            "Hide advanced options ▾" if advanced_open else "Show advanced options ▸")
        advanced.addWidget(self.btn_brain_advanced)
        self.brain_advanced_box = QWidget()
        adv_layout = QVBoxLayout(self.brain_advanced_box)
        adv_layout.setContentsMargins(0, 0, 0, 0)
        adv_layout.setSpacing(6)

        brain_engine_lbl = QLabel("Brain engine (drives 'How VYUK thinks' above)")
        brain_engine_lbl.setObjectName("muted")
        adv_layout.addWidget(brain_engine_lbl)
        self.combo_brain_engine = QComboBox()
        self.combo_brain_engine.addItem("Cloud — Google's best voice", "gemini")
        self.combo_brain_engine.addItem("On this PC — offline, private, no key needed", "local")
        engine_index = self.combo_brain_engine.findData(self._brain_engine())
        self.combo_brain_engine.setCurrentIndex(engine_index if engine_index >= 0 else 0)
        self.combo_brain_engine.currentIndexChanged.connect(self._on_brain_engine_changed)
        self.combo_brain_engine.currentIndexChanged.connect(lambda *_: self._sync_think_card())
        adv_layout.addWidget(self.combo_brain_engine)

        # v5.45 (HANDS H13): the tool-profile selector — an
        # additive block in the Advanced fold, directly under
        # "How she thinks". It persists TOOL_PROFILE through the
        # same one-field pattern as the transcription-profile
        # combo below; core/funnel/tool_profiles owns what each id
        # means. `activated` fires only for user picks (the
        # combo_brain_size rule), so building the combo can
        # never overwrite a saved profile on its own.
        tool_profile_lbl = QLabel("Tools she keeps at hand")
        tool_profile_lbl.setObjectName("muted")
        adv_layout.addWidget(tool_profile_lbl)
        self.combo_tool_profile = QComboBox()
        self.combo_tool_profile.addItem(
            "Assistant — everyday tools, everything else one ask away",
            "assistant")
        self.combo_tool_profile.addItem(
            "Desktop — computer-use tools, in full detail", "desktop")
        self.combo_tool_profile.addItem(
            "Full — every tool loaded (uses the most context)", "full")
        _saved_tool_profile = str(
            getattr(config, "tool_profile", "assistant")
            or "assistant").strip().lower()
        if _saved_tool_profile not in ("assistant", "desktop", "full"):
            _saved_tool_profile = "assistant"
        _tool_profile_index = self.combo_tool_profile.findData(
            _saved_tool_profile)
        self.combo_tool_profile.setCurrentIndex(
            _tool_profile_index if _tool_profile_index >= 0 else 0)
        self.combo_tool_profile.activated.connect(
            self._on_tool_profile_picked)
        adv_layout.addWidget(self.combo_tool_profile)
        tool_profile_hint = QLabel("Which tools are loaded directly when she connects. "
                                   "Tools that aren't loaded stay reachable — she fetches them through her tool router — except in Full, where everything is loaded at once.")
        tool_profile_hint.setObjectName("cardHint")
        tool_profile_hint.setWordWrap(True)
        adv_layout.addWidget(tool_profile_hint)

        backend_lbl = QLabel("How the local brain runs")
        backend_lbl.setObjectName("muted")
        adv_layout.addWidget(backend_lbl)
        self.combo_local_backend = QComboBox()
        self.combo_local_backend.addItem("Built-in — zero setup, downloads once (~3.3 GB)", "builtin")
        self.combo_local_backend.addItem("Ollama — for experts who run their own AI models", "ollama")
        adv_layout.addWidget(self.combo_local_backend)

        # Ollama group: own model + reachability check.
        self.ollama_group = QWidget()
        ollama_layout = QVBoxLayout(self.ollama_group)
        ollama_layout.setContentsMargins(0, 0, 0, 0)
        ollama_layout.setSpacing(6)
        ollama_lbl = QLabel("Ollama model name")
        ollama_lbl.setObjectName("muted")
        ollama_layout.addWidget(ollama_lbl)
        self.input_ollama_model = QLineEdit(str(getattr(config, "ollama_model", "qwen2.5:7b") or ""))
        self.input_ollama_model.setAccessibleName("Ollama model")
        self.input_ollama_model.setPlaceholderText("the model's name in Ollama, e.g. qwen2.5:7b")
        self.input_ollama_model.editingFinished.connect(self._save_ollama_model)
        ollama_layout.addWidget(self.input_ollama_model)
        self.btn_check_brain = QPushButton("Check local brain")
        self.btn_check_brain.clicked.connect(self._check_local_brain)
        ollama_layout.addWidget(self.btn_check_brain)
        adv_layout.addWidget(self.ollama_group)

        # Built-in group: brain size + where the pack lives. (The
        # download row itself moved to "How VYUK thinks".)
        self.builtin_group = QWidget()
        builtin_layout = QVBoxLayout(self.builtin_group)
        builtin_layout.setContentsMargins(0, 0, 0, 0)
        builtin_layout.setSpacing(6)
        brain_size_lbl = QLabel("Brain size")
        brain_size_lbl.setObjectName("muted")
        builtin_layout.addWidget(brain_size_lbl)
        self.combo_brain_size = QComboBox()
        self.combo_brain_size.addItem(
            "Small — fastest (≈1.9 GB on this PC)",
            "Qwen3-1.7B-Q4_K_M.gguf",
        )
        self.combo_brain_size.addItem(
            "Balanced — recommended (≈3.3 GB on this PC)",
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        )
        self.combo_brain_size.addItem(
            "Large — smartest (≈6 GB on this PC)",
            "Qwen3-8B-Instruct-2507-Q4_K_M.gguf",
        )
        brain_size_index = self.combo_brain_size.findData(self._local_model_file())
        if brain_size_index < 0:
            # A custom LOCAL_MODEL_FILE matches no tier: display Balanced
            # (never write anything — `activated` below is user-picks only).
            brain_size_index = self.combo_brain_size.findData(
                "Qwen3-4B-Instruct-2507-Q4_K_M.gguf")
        self.combo_brain_size.setCurrentIndex(brain_size_index if brain_size_index >= 0 else 0)
        # `activated` fires only for user picks — initialising the combo (or a
        # custom LOCAL_MODEL_FILE filename) must never overwrite the setting.
        for _bi in range(self.combo_brain_size.count()):
            self.combo_brain_size.setItemData(
                _bi, self.combo_brain_size.itemData(_bi),
                Qt.ItemDataRole.ToolTipRole)
        self.combo_brain_size.setToolTip(
            "A bigger brain gives smarter answers — and uses more "
            "space and memory.")
        self.combo_brain_size.activated.connect(self._on_brain_size_changed)
        builtin_layout.addWidget(self.combo_brain_size)
        builtin_storage = QLabel("Her brain, ears and voice are kept on this PC — downloaded once, then fully offline.")
        builtin_storage.setObjectName("cardHint")
        builtin_storage.setWordWrap(True)
        builtin_layout.addWidget(builtin_storage)
        adv_layout.addWidget(self.builtin_group)

        whisper_lbl = QLabel("Listening quality (her ears)")
        whisper_lbl.setObjectName("muted")
        adv_layout.addWidget(whisper_lbl)
        self.combo_whisper_model = QComboBox()
        self.combo_whisper_model.addItem("Fastest — lightest on memory", "tiny")
        self.combo_whisper_model.addItem("Balanced", "base")
        self.combo_whisper_model.addItem("Accurate", "small")
        self.combo_whisper_model.addItem("Most accurate — heaviest", "medium")
        whisper_model = str(getattr(config, "whisper_model", "base") or "base")
        whisper_index = self.combo_whisper_model.findData(whisper_model)
        self.combo_whisper_model.setCurrentIndex(whisper_index if whisper_index >= 0 else 1)
        self.combo_whisper_model.currentIndexChanged.connect(
            lambda _i=0: self._on_whisper_model_changed(
                str(self.combo_whisper_model.currentData() or "base")))
        adv_layout.addWidget(self.combo_whisper_model)

        # v5.43 (EARS A7): the transcription profile sits with the
        # listening-quality pick it tunes — Auto follows her home
        # language, an explicit pick (Standard / Indian English /
        # Hinglish) always wins. The profile table + resolution
        # live in core/languages; this combo only persists the id.
        profile_lbl = QLabel("Transcription profile")
        profile_lbl.setObjectName("muted")
        adv_layout.addWidget(profile_lbl)
        self.combo_transcription_profile = QComboBox()
        for _profile in TRANSCRIPTION_PROFILES:
            self.combo_transcription_profile.addItem(
                _profile.label, _profile.id)
        _saved_profile = str(
            getattr(config, "transcription_profile", "auto")
            or "auto")
        _profile_index = self.combo_transcription_profile.findData(
            _saved_profile)
        if _profile_index < 0:
            _profile_index = self.combo_transcription_profile.findData(
                "auto")
        self.combo_transcription_profile.setCurrentIndex(_profile_index)
        self.combo_transcription_profile.activated.connect(
            self._on_transcription_profile_picked)
        adv_layout.addWidget(self.combo_transcription_profile)
        profile_hint = QLabel("How she hears you: Auto follows your preferred language below; "
                              "Indian English / Hinglish locks her ears to Hindi-English speech patterns.")
        profile_hint.setObjectName("cardHint")
        profile_hint.setWordWrap(True)
        adv_layout.addWidget(profile_hint)

        # v5.41: her home language sits directly above her voice —
        # the two are one decision seen from two sides. Picking a
        # language here sets the language AND her voice (to that
        # language's curated Kokoro voice, core/languages) through
        # the one write path; picking a voice below afterwards
        # changes the voice only — the language stays the home
        # until it is changed here again.
        lang_lbl = QLabel("Preferred language (her home)")
        lang_lbl.setObjectName("muted")
        adv_layout.addWidget(lang_lbl)
        self.combo_language = QComboBox()
        self.combo_language.setAccessibleName("Language")
        for _entry in LANGUAGES:
            self.combo_language.addItem(_entry.label, _entry.code)
        _saved_language = str(getattr(config, "preferred_language", "en-gb")
                              or "en-gb")
        _lang_index = self.combo_language.findData(_saved_language)
        if _lang_index < 0:
            _lang_index = self.combo_language.findData("en-gb")
        self.combo_language.setCurrentIndex(_lang_index)
        self.combo_language.activated.connect(self._on_language_picked)
        adv_layout.addWidget(self.combo_language)
        lang_hint = QLabel("Her home: she answers in this language by default, in its curated voice. "
                           "You can still pick any voice below — that overrides the voice only, not the language.")
        lang_hint.setObjectName("cardHint")
        lang_hint.setWordWrap(True)
        adv_layout.addWidget(lang_hint)

        kokoro_lbl = QLabel("Kokoro voice (her voice)")
        kokoro_lbl.setObjectName("muted")
        adv_layout.addWidget(kokoro_lbl)
        # Mouth track (v5.1): a curated roster combo instead of a bare
        # text field. `activated` fires only for user picks (the
        # combo_brain_size rule), so building/syncing the combo can
        # never overwrite a saved voice on its own.
        saved_kokoro_voice = str(getattr(config, "kokoro_voice", "bm_george") or "bm_george")
        self.combo_kokoro_voice = QComboBox()
        self.combo_kokoro_voice.setAccessibleName("Kokoro voice")
        for _voice_id, _voice_label in _KOKORO_VOICE_ROSTER:
            self.combo_kokoro_voice.addItem(_voice_label, _voice_id)
        _kokoro_index = self.combo_kokoro_voice.findData(saved_kokoro_voice)
        if _kokoro_index < 0:
            # A voice typed by a power user stays valid: it joins the
            # roster as a Custom entry rather than being reset.
            self.combo_kokoro_voice.addItem(f"Custom: {saved_kokoro_voice}", saved_kokoro_voice)
            _kokoro_index = self.combo_kokoro_voice.count() - 1
        self.combo_kokoro_voice.setCurrentIndex(_kokoro_index)
        self.combo_kokoro_voice.activated.connect(self._on_kokoro_voice_picked)
        self.btn_voice_preview = QPushButton("▶ Preview")
        self.btn_voice_preview.setToolTip("Hear this voice")
        self.btn_voice_preview.clicked.connect(self._play_voice_preview)
        kokoro_row = QHBoxLayout()
        kokoro_row.addWidget(self.combo_kokoro_voice, 1)
        kokoro_row.addWidget(self.btn_voice_preview)
        adv_layout.addLayout(kokoro_row)
        # v5.17 (Mouth M17): three preview scripts, so a voice can be
        # judged on more than one kind of sentence.
        script_row = QHBoxLayout()
        script_row.setSpacing(6)
        script_lbl = QLabel("Preview script")
        script_lbl.setObjectName("muted")
        script_row.addWidget(script_lbl)
        self._preview_script = "a"
        self.preview_script_buttons: dict[str, QPushButton] = {}
        for _sid, _label in (("a", "Everyday"), ("b", "Numbers & dates"),
                             ("c", "Tongue-twister")):
            _btn = QPushButton(_label)
            _btn.setObjectName("chipBtn")
            _btn.setCheckable(True)
            _btn.setChecked(_sid == "a")
            _btn.clicked.connect(
                lambda _=False, s=_sid: self._on_preview_script_selected(s))
            self.preview_script_buttons[_sid] = _btn
            script_row.addWidget(_btn)
        script_row.addStretch()
        adv_layout.addLayout(script_row)
        self._voice_preview_effect = None
        self._refresh_voice_preview_button()
        # Advanced escape: any Kokoro voice id can still be typed.
        self.input_kokoro_voice = QLineEdit(saved_kokoro_voice)
        self.input_kokoro_voice.setAccessibleName("Custom Kokoro voice")
        self.input_kokoro_voice.setPlaceholderText("or type any Kokoro voice id, e.g. am_michael")
        self.input_kokoro_voice.editingFinished.connect(self._save_kokoro_voice)
        adv_layout.addWidget(self.input_kokoro_voice)
        kokoro_hint = QLabel("Her built-in voice — pick one and preview it, or type any Kokoro voice id below.")
        kokoro_hint.setObjectName("cardHint")
        kokoro_hint.setWordWrap(True)
        adv_layout.addWidget(kokoro_hint)
        # v5.41: automatic voice switching (Kokoro path only).
        self.chk_voice_auto_switch = HudSwitch(text="Switch voice to match language")
        self.chk_voice_auto_switch.setChecked(
            bool(getattr(config, "voice_auto_switch", True)))
        self.chk_voice_auto_switch.toggled.connect(
            self._on_voice_auto_switch_toggled)
        adv_layout.addWidget(toggle_row(self.chk_voice_auto_switch))
        auto_switch_hint = QLabel("When a reply comes out in another language, she speaks it in that "
                                  "language's voice, then returns home. Her emergency fallback voices never switch.")
        auto_switch_hint.setObjectName("cardHint")
        auto_switch_hint.setWordWrap(True)
        adv_layout.addWidget(auto_switch_hint)
        # v5.43 (MOUTH M4): voice blend — a second Kokoro voice mixed
        # into her voice's style (blend machinery in
        # core/voice/local_client.py). Blank partner or 0 amount = no
        # blend, her voice exactly as picked above.
        blend_lbl = QLabel("Voice blend partner (optional)")
        blend_lbl.setObjectName("muted")
        adv_layout.addWidget(blend_lbl)
        self.input_kokoro_blend_voice = QLineEdit(
            str(getattr(config, "kokoro_blend_voice", "") or ""))
        self.input_kokoro_blend_voice.setAccessibleName("Kokoro blend voice")
        self.input_kokoro_blend_voice.setPlaceholderText(
            "second voice id to mix in, e.g. bm_lewis — blank = no blend")
        self.input_kokoro_blend_voice.editingFinished.connect(
            self._save_kokoro_blend)
        adv_layout.addWidget(self.input_kokoro_blend_voice)
        blend_ratio_lbl = QLabel("Blend amount (partner's share, 0–1)")
        blend_ratio_lbl.setObjectName("muted")
        adv_layout.addWidget(blend_ratio_lbl)
        try:
            _blend_ratio = float(
                getattr(config, "kokoro_blend_ratio", 0.0) or 0.0)
        except (TypeError, ValueError):
            _blend_ratio = 0.0
        self.input_kokoro_blend_ratio = QLineEdit(f"{_blend_ratio:.2f}")
        self.input_kokoro_blend_ratio.setAccessibleName("Kokoro blend ratio")
        self.input_kokoro_blend_ratio.setPlaceholderText(
            "0.00 = off; 0.30 = her voice with 30% of the partner's")
        self.input_kokoro_blend_ratio.editingFinished.connect(
            self._save_kokoro_blend)
        adv_layout.addWidget(self.input_kokoro_blend_ratio)
        blend_hint = QLabel("Her voice stays the base; the partner colours it. "
                            "Applies to the on-device Kokoro voice only.")
        blend_hint.setObjectName("cardHint")
        blend_hint.setWordWrap(True)
        adv_layout.addWidget(blend_hint)

        pace_lbl = QLabel("Voice pace")
        pace_lbl.setObjectName("muted")
        adv_layout.addWidget(pace_lbl)
        try:
            _pace = float(getattr(config, "kokoro_speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            _pace = 1.0
        _pace_pct = int(round(min(1.2, max(0.8, _pace)) * 100))
        self.slider_kokoro_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_kokoro_speed.setAccessibleName("Kokoro speech speed")
        self.slider_kokoro_speed.setRange(80, 120)
        self.slider_kokoro_speed.setFixedWidth(180)
        self.slider_kokoro_speed.setValue(_pace_pct)
        self.lbl_kokoro_speed = QLabel(f"{_pace_pct}%")
        self.lbl_kokoro_speed.setObjectName("muted")
        pace_row = QHBoxLayout()
        pace_row.addWidget(self.slider_kokoro_speed)
        pace_row.addWidget(self.lbl_kokoro_speed)
        pace_row.addStretch()
        adv_layout.addLayout(pace_row)
        self.slider_kokoro_speed.valueChanged.connect(self._on_kokoro_speed_changed)
        pace_hint = QLabel("How fast she speaks — 100% is her natural pace.")
        pace_hint.setObjectName("cardHint")
        pace_hint.setWordWrap(True)
        adv_layout.addWidget(pace_hint)

        tts_lbl = QLabel("Backup voice file (only for Ollama)")
        tts_lbl.setObjectName("muted")
        adv_layout.addWidget(tts_lbl)
        self.input_local_tts_voice = QLineEdit(str(getattr(config, "local_tts_voice", "") or ""))
        self.input_local_tts_voice.setAccessibleName("Local text-to-speech voice")
        self.input_local_tts_voice.setPlaceholderText("e.g. en_US-lessac-medium.onnx")
        self.input_local_tts_voice.editingFinished.connect(self._save_local_tts_voice)
        adv_layout.addWidget(self.input_local_tts_voice)

        self.brain_status_label = QLabel("Local brain not checked yet.")
        self.brain_status_label.setObjectName("cardHint")
        self.brain_status_label.setWordWrap(True)
        adv_layout.addWidget(self.brain_status_label)
        brain_hint = QLabel("With the built-in brain, VYUK downloads what she needs once, then thinks, listens and speaks entirely on this PC — no account, no internet. Ollama is a separate app for experts who run their own AI models.")
        brain_hint.setObjectName("cardHint")
        brain_hint.setWordWrap(True)
        adv_layout.addWidget(brain_hint)

        self.brain_advanced_box.setVisible(advanced_open)
        advanced.addWidget(self.brain_advanced_box)
        self.btn_brain_advanced.toggled.connect(self._on_brain_advanced_toggled)

        backend_index = self.combo_local_backend.findData(self._local_backend())
        self.combo_local_backend.setCurrentIndex(backend_index if backend_index >= 0 else 0)
        self.combo_local_backend.currentIndexChanged.connect(self._on_local_backend_changed)
        self._update_backend_visibility()
        self._refresh_pack_storage()
