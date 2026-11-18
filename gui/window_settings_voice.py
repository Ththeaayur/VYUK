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
from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    Qt,
    QTime,
    QTimer,
)
from voice_assistant.core.gui_facade import config
from .style import THEMES as STYLE_THEMES
from .style import (
    build_stylesheet,
    derive_theme,
    TYPE_CARD_TITLE,
    # Command Deck Phase E: Settings carries no colour literals of
    # its own — every value below is a style token.
    ACCENT,
    TEXT,
    INPUT_BG,
    INPUT_BG_FOCUS,
    LINE_SUBTLE,
    FOCUS_BORDER,
    SELECTION_BG,
    RADIUS_BTN,
)
from voice_assistant.core.gui_facade import qsettings_contains, qsettings_get, qsettings_remove, qsettings_set



class SettingsVoiceMixin:

    def _build_voice_cards(self, _g_voice, _g_appearance, _g_connect):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- Voice cards (v5.57.1, item 10): the old Voice & sound
        # card split into focused cards in the same pattern —
        # same controls, same handlers, same order within each
        # group; only the grouping changes.
        voice = self._settings_card(
            _g_voice, "Voice",
            "For the cloud brain only — the local brain has its own "
            "voice, under Advanced below.")
        self.combo_voice = QComboBox()
        self.combo_voice.setAccessibleName("Voice")
        self.combo_voice.addItems(config.available_voices)
        self.combo_voice.setCurrentText(config.voice_name)
        self.combo_voice.currentTextChanged.connect(self._on_voice_changed)
        voice.addWidget(self.combo_voice)
        # v5.17 (Mouth): how the on-device voice says things.
        pron_lbl = QLabel("Pronunciations — one per line: word = sounds-like")
        pron_lbl.setObjectName("muted")
        voice.addWidget(pron_lbl)
        self.input_pronunciations = QPlainTextEdit()
        # The app sheet styles the QLineEdit family only; a bare
        # QPlainTextEdit would render as a white box on the dark
        # theme, so it carries the same well by hand (values mirror
        # the sheet's input rule).
        self.input_pronunciations.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {INPUT_BG};"
            f" border: 1px solid {LINE_SUBTLE};"
            f" border-radius: {RADIUS_BTN}px; padding: 8px 12px; color: {TEXT};"
            f" selection-background-color: {SELECTION_BG};"
            f" selection-color: {TEXT}; }}"
            f"QPlainTextEdit:focus {{ border: {FOCUS_BORDER};"
            f" background-color: {INPUT_BG_FOCUS}; }}")
        self.input_pronunciations.setPlaceholderText("e.g.  visor = vy-zor")
        self.input_pronunciations.setMaximumHeight(74)
        self.input_pronunciations.setPlainText(self._pronunciations_as_lines())
        voice.addWidget(self.input_pronunciations)
        pron_save_row = QHBoxLayout()
        self.btn_save_pronunciations = QPushButton("Save pronunciations")
        self.btn_save_pronunciations.setObjectName("chipBtn")
        self.btn_save_pronunciations.clicked.connect(self._on_pronunciations_saved)
        pron_save_row.addWidget(self.btn_save_pronunciations)
        self.lbl_pronunciations = QLabel("")
        self.lbl_pronunciations.setObjectName("cardHint")
        pron_save_row.addWidget(self.lbl_pronunciations)
        pron_save_row.addStretch()
        voice.addLayout(pron_save_row)
        whisper = self._settings_card(_g_voice, "Whisper hours", "She speaks softer between these hours.")
        self.chk_whisper_hours = QCheckBox("Whisper hours — speak softer at night")
        self.chk_whisper_hours.setChecked(bool(getattr(config, "whisper_hours_enabled", False)))
        self.chk_whisper_hours.toggled.connect(self._on_whisper_hours_toggled)
        whisper.addWidget(self.chk_whisper_hours)
        whisper_row = QHBoxLayout()
        whisper_row.setSpacing(6)
        from_lbl = QLabel("From")
        from_lbl.setObjectName("muted")
        whisper_row.addWidget(from_lbl)
        # Command Deck Phase E: the whisper time edits carry no
        # inline sheet — the app sheet's input-well rule covers
        # QTimeEdit (well, cyan focus ring, cyan selection).
        self.time_whisper_start = QTimeEdit()
        self.time_whisper_start.setAccessibleName("Whisper hours start time")
        self.time_whisper_start.setDisplayFormat("HH:mm")
        self.time_whisper_start.setTime(QTime.fromString(
            str(getattr(config, "whisper_start", "22:00") or "22:00"), "HH:mm"))
        self.time_whisper_start.timeChanged.connect(self._on_whisper_times_changed)
        whisper_row.addWidget(self.time_whisper_start)
        to_lbl = QLabel("to")
        to_lbl.setObjectName("muted")
        whisper_row.addWidget(to_lbl)
        self.time_whisper_end = QTimeEdit()
        self.time_whisper_end.setAccessibleName("Whisper hours end time")
        self.time_whisper_end.setDisplayFormat("HH:mm")
        self.time_whisper_end.setTime(QTime.fromString(
            str(getattr(config, "whisper_end", "07:00") or "07:00"), "HH:mm"))
        self.time_whisper_end.timeChanged.connect(self._on_whisper_times_changed)
        whisper_row.addWidget(self.time_whisper_end)
        whisper_row.addStretch()
        whisper.addLayout(whisper_row)
        self.slider_whisper_factor, _ = self._slider_row(
            whisper, "Night volume", 30, 80,
            int(round(float(getattr(config, "whisper_factor", 0.5) or 0.5) * 100)),
            lambda v: f"{v}%", self._on_whisper_factor_changed)
        briefing = self._settings_card(_g_voice, "Briefing voice", "While the preset is on, the morning briefing is spoken at the pace below; turn it off and the briefing uses her normal pace.")
        self.chk_briefing_preset = QCheckBox("Briefing voice preset")
        self.chk_briefing_preset.setChecked(
            bool(qsettings_get("voice/briefing_preset", settings=self.settings)))
        self.chk_briefing_preset.toggled.connect(
            lambda on: qsettings_set("voice/briefing_preset", bool(on), settings=self.settings))
        briefing.addWidget(self.chk_briefing_preset)
        self.slider_briefing_speed, _ = self._slider_row(
            briefing, "Briefing pace", 80, 120,
            int(round(float(qsettings_get("voice/briefing_speed", settings=self.settings)) * 100)),
            lambda v: f"{v}%", self._on_briefing_speed_changed)
        mic = self._settings_card(_g_voice, "Microphone", "Which microphone she listens with, and how she hears you.")
        self.combo_in_dev = QComboBox()
        self.combo_in_dev.setAccessibleName("Microphone")
        mic.addWidget(self.combo_in_dev)
        # Field-check fix #12 (M11a): the speaker chooser — the
        # output mirror of combo_in_dev above. Populated by
        # _populate_audio_devices (same call) and persisted the
        # same way (audio/output_device[_name]); the label below
        # it names the effective speaker, mirroring lbl_input_dev.
        self.combo_out_dev = QComboBox()
        self.combo_out_dev.setAccessibleName("Speaker")
        mic.addWidget(self.combo_out_dev)
        self.combo_in_dev.currentIndexChanged.connect(self._on_input_device_changed)
        self.combo_out_dev.currentIndexChanged.connect(
            self._on_output_device_changed)
        # One populate call fills both combos (each combo's
        # signals are blocked while it is refilled, so building
        # the page never fires a handler mid-build — the same
        # guard the input combo already relied on).
        self._populate_audio_devices()
        # v5.52 (item 3): the effective microphone, in words —
        # "Automatic — using this PC's default: …" or "Your pick: …".
        self.lbl_input_dev = QLabel("Microphone: —")
        self.lbl_input_dev.setObjectName("cardHint")
        self.lbl_input_dev.setWordWrap(True)
        mic.addWidget(self.lbl_input_dev)
        self._refresh_input_device_label()
        # v5.17 (Ears): the microphone, tuned. Every control applies
        # live and persists through the config/env pattern.
        self.lbl_output_dev = QLabel("Output: —")
        self.lbl_output_dev.setObjectName("cardHint")
        self.lbl_output_dev.setWordWrap(True)
        mic.addWidget(self.lbl_output_dev)
        self._refresh_output_device_label()
        self.slider_mic_gain, _ = self._slider_row(
            mic, "Microphone gain", 50, 200,
            int(round(float(getattr(config, "mic_gain", 1.0) or 1.0) * 100)),
            lambda v: f"{v}%", self._on_mic_gain_changed)
        self.slider_noise_gate, _ = self._slider_row(
            mic, "Noise gate (how loud counts as speech)", 150, 1200,
            int(getattr(config, "vad_rms_threshold", 400) or 400),
            lambda v: f"{v}", self._on_noise_gate_changed)
        self.slider_barge_in, _ = self._slider_row(
            mic, "Barge-in firmness (talking over her)", 80, 200,
            int(round(float(getattr(config, "barge_in_factor", 1.6) or 1.6) * 100)),
            lambda v: f"{v}%", self._on_barge_in_changed)
        # v5.44 (EARS A4): the other two echo-discipline numbers,
        # same slider pattern — post-playback guard and barge-in
        # sustain, both in milliseconds (0 disables each).
        self.slider_barge_guard, _ = self._slider_row(
            mic, "Echo guard after she speaks", 0, 1000,
            int(getattr(config, "barge_in_guard_ms", 250) or 0),
            lambda v: f"{v} ms", self._on_barge_guard_changed)
        self.slider_barge_sustain, _ = self._slider_row(
            mic, "Barge-in sustain (hold to interrupt)", 0, 500,
            int(getattr(config, "barge_in_sustain_ms", 150) or 0),
            lambda v: f"{v} ms", self._on_barge_sustain_changed)
        self.slider_wake_sens, _ = self._slider_row(
            mic, "Wake word sensitivity", 50, 200,
            int(round(float(getattr(config, "wake_sensitivity", 1.0) or 1.0) * 100)),
            lambda v: f"{v}%", self._on_wake_sens_changed)
        wake_phrase_lbl = QLabel("Extra wake phrase (optional)")
        wake_phrase_lbl.setObjectName("muted")
        mic.addWidget(wake_phrase_lbl)
        self.input_wake_phrase = QLineEdit(str(getattr(config, "wake_phrase_alt", "") or ""))
        self.input_wake_phrase.setAccessibleName("Wake phrase")
        self.input_wake_phrase.setPlaceholderText("e.g. hey computer — her ears learn it at next start")
        self.input_wake_phrase.editingFinished.connect(self._on_wake_phrase_changed)
        mic.addWidget(self.input_wake_phrase)
        # UI settings slice: the ONLY restart-required setting
        # found in the audit — WakeWordDetector is a module-level
        # singleton (core/voice/wake_word.py) that reads the alt phrase
        # once, in __init__. The placeholder disappears while
        # typing, so this persistent line carries the restart
        # requirement instead of the placeholder alone.
        wake_restart_lbl = QLabel(
            "Takes effect when VYUK next starts — her wake-word "
            "listener reads this phrase once, at startup.")
        wake_restart_lbl.setObjectName("muted")
        wake_restart_lbl.setWordWrap(True)
        mic.addWidget(wake_restart_lbl)
        timeout_row = QHBoxLayout()
        timeout_row.setSpacing(6)
        timeout_lbl = QLabel("Stop listening after silence")
        timeout_lbl.setObjectName("muted")
        timeout_row.addWidget(timeout_lbl)
        self.listen_timeout_buttons: dict[float, QPushButton] = {}
        for _secs, _label in ((0.0, "Never"), (30.0, "30s"), (60.0, "60s"), (120.0, "2 min")):
            _btn = QPushButton(_label)
            _btn.setObjectName("chipBtn")
            _btn.setCheckable(True)
            _btn.clicked.connect(lambda _=False, s=_secs: self._on_listen_timeout_selected(s))
            self.listen_timeout_buttons[_secs] = _btn
            timeout_row.addWidget(_btn)
        timeout_row.addStretch()
        mic.addLayout(timeout_row)
        self._sync_listen_timeout_buttons()
        ptt_row = QHBoxLayout()
        ptt_row.setSpacing(6)
        ptt_lbl = QLabel("Push-to-talk")
        ptt_lbl.setObjectName("muted")
        ptt_row.addWidget(ptt_lbl)
        self.ptt_mode_buttons: dict[str, QPushButton] = {}
        for _mode, _label in (("hold", "Hold"), ("toggle", "Click to toggle")):
            _btn = QPushButton(_label)
            _btn.setObjectName("chipBtn")
            _btn.setCheckable(True)
            _btn.clicked.connect(lambda _=False, m=_mode: self._on_ptt_mode_selected(m))
            self.ptt_mode_buttons[_mode] = _btn
            ptt_row.addWidget(_btn)
        ptt_row.addStretch()
        mic.addLayout(ptt_row)
        self._sync_ptt_mode_buttons()
        self.chk_media_pause = QCheckBox("Stay quiet while media is playing (Windows)")
        self.chk_media_pause.setChecked(
            bool(qsettings_get("ears/pause_while_media", settings=self.settings)))
        self.chk_media_pause.toggled.connect(self._on_media_pause_toggled)
        mic.addWidget(self.chk_media_pause)
        test_row = QHBoxLayout()
        test_row.setSpacing(6)
        self.btn_mic_test = QPushButton("Test microphone")
        self.btn_mic_test.clicked.connect(lambda: self._start_mic_sample("test"))
        test_row.addWidget(self.btn_mic_test)
        self.btn_mic_calibrate = QPushButton("Calibrate noise gate")
        self.btn_mic_calibrate.setToolTip(
            "Stay quiet for 3 seconds — she sets the gate just above your room's silence")
        self.btn_mic_calibrate.clicked.connect(lambda: self._start_mic_sample("calibrate"))
        test_row.addWidget(self.btn_mic_calibrate)
        test_row.addStretch()
        mic.addLayout(test_row)
        self.lbl_mic_test = QLabel(
            "Test: talk at your normal volume for a few seconds. "
            "Calibrate: stay quiet and let her measure the room.")
        self.lbl_mic_test.setObjectName("cardHint")
        self.lbl_mic_test.setWordWrap(True)
        mic.addWidget(self.lbl_mic_test)
        self._mic_sample_timer = QTimer(self)
        self._mic_sample_timer.setInterval(100)
        self._mic_sample_timer.timeout.connect(self._mic_sample_tick)
        self._mic_sample_mode: str | None = None
        self._mic_samples: list[float] = []
        # Wiring Build 1: voice bridge toggle, copying the wake
        # toggle's pattern (checkable control + handler that flips
        # a session flag and updates its own label). Session state
        # ONLY, exactly like wake — wake has no persistence (only
        # `wake_word_detector.enabled` is assigned, nothing is
        # saved), so this toggle likewise resets to OFF on every
        # launch and the user must opt in again. No auto-enable,
        # no new sidebar entry, no stack renumbering. When ON, a
        # free-form voice goal hands to the text loop through
        # core/voice/voice_text_bridge.py; every loop action still passes
        # planner.run_goal's gate stack (C-14 approve-first), the
        # bridge itself takes no consequential action, and
        # narration stays transcript-seam (no new audio path).
        # The toggle also attaches the app session's existing
        # inner text brain when one is running (Groq / on-PC);
        # with the Gemini voice engine there is no separate text
        # brain to attach, so voice goals refuse honestly until a
        # Groq or on-PC brain session is running.
        bridge = self._settings_card(
            _g_voice, "Voice bridge",
            "Let free-form voice goals run on the text brain. Off by default.")
        from voice_assistant.core.gui_facade import voice_text_bridge as _bridge_mod
        self.chk_voice_bridge = QCheckBox(
            "Voice bridge — voice goals run on the text loop")
        self.chk_voice_bridge.setChecked(_bridge_mod.is_voice_bridge_enabled())
        self.chk_voice_bridge.toggled.connect(self._on_voice_bridge_toggled)
        bridge.addWidget(self.chk_voice_bridge)
        self.lbl_voice_bridge = QLabel("Voice bridge off.")
        self.lbl_voice_bridge.setObjectName("cardHint")
        self.lbl_voice_bridge.setWordWrap(True)
        bridge.addWidget(self.lbl_voice_bridge)
        self._sync_voice_bridge_label()
        sounds = self._settings_card(
            _g_voice, "Sounds",
            "Short, quiet sounds for waking, sending, finishing and errors.")
        self.chk_sounds = QCheckBox("Interface sounds")
        self.chk_sounds.toggled.connect(self._on_sounds_toggled)
        sounds.addWidget(self.chk_sounds)
        vol_row = QHBoxLayout()
        vol_lbl = QLabel("Volume")
        vol_lbl.setObjectName("muted")
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setAccessibleName("Interface sounds volume")
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setFixedWidth(180)
        self.slider_volume.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(vol_lbl)
        vol_row.addWidget(self.slider_volume)
        # UI settings slice: the volume slider is a raw QSlider
        # with no value readout (unlike the _slider_row sliders),
        # so its changes were invisible — the readout is its
        # feedback, updated by _on_volume_changed.
        self.lbl_volume = QLabel("")
        self.lbl_volume.setObjectName("muted")
        vol_row.addWidget(self.lbl_volume)
        vol_row.addStretch()
        sounds.addLayout(vol_row)
        # v5.17 (Ears A11/A12): the second earcon family + per-cue
        # switches. The family and toggles persist in QSettings and
        # are applied to the Earcons object in _apply_saved_settings.
        fam_row = QHBoxLayout()
        fam_row.setSpacing(6)
        fam_lbl = QLabel("Sound style")  # v5.51 (X-1): "earcon" is our jargon, not the user's
        fam_lbl.setObjectName("muted")
        fam_row.addWidget(fam_lbl)
        self.earcon_family_buttons: dict[str, QPushButton] = {}
        for _fam, _label in (("classic", "Classic"), ("soft", "Soft")):
            _btn = QPushButton(_label)
            _btn.setObjectName("chipBtn")
            _btn.setCheckable(True)
            _btn.clicked.connect(lambda _=False, f=_fam: self._on_earcon_family_selected(f))
            self.earcon_family_buttons[_fam] = _btn
            fam_row.addWidget(_btn)
        fam_row.addStretch()
        sounds.addLayout(fam_row)
        cue_lbl = QLabel("Play a sound for…")
        cue_lbl.setObjectName("muted")
        sounds.addWidget(cue_lbl)
        cue_grid = QGridLayout()
        cue_grid.setSpacing(4)
        self.cue_checks: dict[str, QCheckBox] = {}
        from voice_assistant.gui.sounds import CUE_NAMES as _CUE_NAMES
        for _i, _cue in enumerate(_CUE_NAMES):
            _chk = QCheckBox(_cue.capitalize())
            _chk.setChecked(bool(qsettings_get(f"sounds/cue_{_cue}", settings=self.settings)))
            _chk.toggled.connect(
                lambda on, n=_cue: self._on_cue_toggled(n, on))
            self.cue_checks[_cue] = _chk
            cue_grid.addWidget(_chk, _i // 3, _i % 3)
        sounds.addLayout(cue_grid)
        # B-R1-9: Kenney audition row — explicit-demand A/B of the
        # six CC0 candidates in gui/assets/audition/kenney/.
        # Audition only: these sounds are never played by the
        # assistant on its own, and the earcon veto still forbids
        # layering them into product audio (see gui/audition.py).
        # No inline style calls here (audit2s1 pins the total)
        # and no font-size literals.
        aud_lbl = QLabel("Try a candidate sound (audition only — the assistant never plays these on its own)")
        aud_lbl.setObjectName("muted")
        aud_lbl.setWordWrap(True)
        sounds.addWidget(aud_lbl)
        aud_row = QHBoxLayout()
        aud_row.setSpacing(4)
        self.audition_buttons: dict[str, QPushButton] = {}
        from voice_assistant.gui.audition import AUDITION_SOUNDS as _AUDITION_SOUNDS
        for _name in _AUDITION_SOUNDS:
            _btn = QPushButton(_name)
            _btn.setObjectName("chipBtn")
            _btn.setToolTip(f"Audition the Kenney {_name} candidate")
            _btn.clicked.connect(lambda _=False, n=_name: self._on_audition_kenney(n))
            self.audition_buttons[_name] = _btn
            aud_row.addWidget(_btn)
        aud_row.addStretch()
        sounds.addLayout(aud_row)
        self.lbl_audition = QLabel("")
        self.lbl_audition.setObjectName("muted")
        self.lbl_audition.setWordWrap(True)
        sounds.addWidget(self.lbl_audition)

        appear = self._settings_card(_g_appearance, "Appearance", "Theme, text size, density, and motion. Applied instantly.")
        swatch_row = QHBoxLayout()
        self.theme_buttons: dict[str, QPushButton] = {}
        for theme_name, tdict in STYLE_THEMES.items():
            btn = QPushButton()
            btn.setObjectName("themeSwatch")
            btn.setCheckable(True)
            btn.setFixedSize(46, 30)
            btn.setToolTip(theme_name)
            btn.setStyleSheet(
                f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {tdict['accent']}, stop:1 {tdict['accent_2']});"
                f" border-radius: {RADIUS_BTN}px;")
            btn.clicked.connect(lambda _=False, n=theme_name: self._on_theme_selected(n))
            self.theme_buttons[theme_name] = btn
            swatch_row.addWidget(btn)
        custom_btn = QPushButton("Custom…")
        custom_btn.setObjectName("chipBtn")
        custom_btn.setCheckable(True)
        custom_btn.setToolTip("Pick any accent colour")
        custom_btn.clicked.connect(lambda _=False: self._on_custom_accent())
        self.theme_buttons["Custom"] = custom_btn
        swatch_row.addWidget(custom_btn)
        swatch_row.addStretch()
        appear.addLayout(swatch_row)
        self.theme_name_label = QLabel("Cyan")
        self.theme_name_label.setObjectName("muted")
        appear.addWidget(self.theme_name_label)
        # Aura-only (owner decision 2026-11-01): a single style needs
        # no picker. A static label keeps the card's layout intact.
        orb_label = QLabel("Orb: Aura")
        orb_label.setObjectName("muted")
        appear.addWidget(orb_label)
        # v5.18 (Eyes E5): the ambient particle field behind the
        # orb — on by default, always subordinate to reduced motion.
        amb_row = QHBoxLayout()
        amb_lbl = QLabel("Ambience")
        amb_lbl.setObjectName("muted")
        amb_row.addWidget(amb_lbl)
        self.btn_particles = QPushButton("Ambient particles")
        self.btn_particles.setObjectName("chipBtn")
        self.btn_particles.setCheckable(True)
        self.btn_particles.setChecked(True)
        self.btn_particles.setToolTip("Slow drifting particles behind the orb on the Assistant page")
        self.btn_particles.clicked.connect(self._on_particles_toggled)
        amb_row.addWidget(self.btn_particles)
        amb_row.addStretch()
        appear.addLayout(amb_row)
        font_row = QHBoxLayout()
        font_lbl = QLabel("Text size")
        font_lbl.setObjectName("muted")
        font_row.addWidget(font_lbl)
        self.font_scale_buttons: dict[float, QPushButton] = {}
        for scale, label in ((0.9, "90%"), (1.0, "100%"), (1.1, "110%"), (1.2, "120%")):
            btn = QPushButton(label)
            btn.setObjectName("chipBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, s=scale: self._on_font_scale_selected(s))
            self.font_scale_buttons[scale] = btn
            font_row.addWidget(btn)
        font_row.addStretch()
        appear.addLayout(font_row)
        density_row = QHBoxLayout()
        density_lbl = QLabel("Density")
        density_lbl.setObjectName("muted")
        density_row.addWidget(density_lbl)
        self.density_buttons: dict[str, QPushButton] = {}
        for density, label in (("comfortable", "Comfortable"), ("compact", "Compact")):
            btn = QPushButton(label)
            btn.setObjectName("chipBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, d=density: self._on_density_selected(d))
            self.density_buttons[density] = btn
            density_row.addWidget(btn)
        density_row.addStretch()
        appear.addLayout(density_row)
        # v5.57 (redo, A9.3): motion intensity refines the old
        # reduced-motion bool into Full / Subtle / Off (see
        # _motion_level). Full moves everywhere; Subtle calms the
        # orb and particles while panels still glide; Off calms
        # the orb and stills the panels too.
        motion_row = QHBoxLayout()
        motion_lbl = QLabel("Motion")
        motion_lbl.setObjectName("muted")
        motion_row.addWidget(motion_lbl)
        self.motion_buttons: dict[str, QPushButton] = {}
        for _level, _label, _tip in (
                ("full", "Full", "Everything moves."),
                ("subtle", "Subtle",
                 "The orb calms down; panels still glide."),
                ("off", "Off",
                 "Panels snap instead of gliding; the orb stays calm.")):
            _btn = QPushButton(_label)
            _btn.setObjectName("chipBtn")
            _btn.setCheckable(True)
            _btn.setToolTip(_tip)
            _btn.clicked.connect(
                lambda _=False, lv=_level: self._on_motion_selected(lv))
            self.motion_buttons[_level] = _btn
            motion_row.addWidget(_btn)
        motion_row.addStretch()
        appear.addLayout(motion_row)
        # v5.24 (Eyes E32): the translucent shell — the window is
        # already translucent-frameless, so this softens the
        # shell's own fill and lets the desktop ghost through.
        glass_row = QHBoxLayout()
        self.chk_glass = QCheckBox("Translucent shell")
        self.chk_glass.setChecked(bool(qsettings_get("appearance/glass", settings=self.settings)))
        self.chk_glass.toggled.connect(self._on_glass_toggled)
        glass_row.addWidget(self.chk_glass)
        glass_row.addStretch()
        appear.addLayout(glass_row)
        glass_hint = QLabel("The shell goes softly see-through, so your desktop glows through it. Applied instantly.")
        glass_hint.setObjectName("cardHint")
        glass_hint.setWordWrap(True)
        appear.addWidget(glass_hint)
        self._sync_appearance_controls()

        presence = self._settings_card(_g_connect, "Presence", "How VYUK lives on this PC — starting by itself, the floating orb, and speaking first.")
        self.chk_autostart = QCheckBox("Launch VYUK when Windows starts")
        autostart_hint = QLabel("Starts quietly in your tray with the mini orb — no window, no noise.")
        autostart_hint.setObjectName("cardHint")
        autostart_hint.setWordWrap(True)
        presence.addWidget(self.chk_autostart)
        presence.addWidget(autostart_hint)
        # v5.65 (Wave 10 — PF-24): the toggle was created but never
        # wired — checking it did nothing. It now works both ways
        # through the lifecycle consent record + the frozen Run-key
        # mechanism (core/presence.py, value "Nova").
        self._syncing_autostart = False
        self.chk_connect_launch = QCheckBox("Connect automatically on launch")
        presence.addWidget(self.chk_connect_launch)
        self.chk_auto_reconnect = QCheckBox("Reconnect automatically")
        reconnect_hint = QLabel("If the connection drops, VYUK quietly tries to come back on her own.")
        reconnect_hint.setObjectName("cardHint")
        reconnect_hint.setWordWrap(True)
        presence.addWidget(self.chk_auto_reconnect)
        presence.addWidget(reconnect_hint)
        self.chk_mini_orb = QCheckBox("Floating mini orb")
        mini_hint = QLabel("A small always-on-top orb that mirrors VYUK's state. Drag it anywhere; right-click it for options.")
        mini_hint.setObjectName("cardHint")
        mini_hint.setWordWrap(True)
        presence.addWidget(self.chk_mini_orb)
        presence.addWidget(mini_hint)
        # R8-6 (yasb port): the status strip toggle, beside the mini
        # orb — off by default; it shows live CPU, memory, disk,
        # network and battery at the top of the screen.
        # UI rethink Build 3 (§6.11): the strip is one dot + one word
        # at the bottom of the window — the numbers live behind a tap
        # on it (and on the System Pulse page). The checkbox hides the
        # row; it never deletes it.
        self.chk_status_strip = QCheckBox(
            "Status strip (system status at the bottom of the window)")
        strip_hint = QLabel("A thin always-on-top bar with live CPU, memory, disk, network and battery. Drag it anywhere; it only samples while visible.")
        strip_hint.setObjectName("cardHint")
        strip_hint.setWordWrap(True)
        presence.addWidget(self.chk_status_strip)
        presence.addWidget(strip_hint)
        self.chk_summon_hotkey = QCheckBox("Summon hotkey (Ctrl+Shift+Space)")
        summon_hint = QLabel("Press it anywhere to bring VYUK forward — or tuck her away.")
        summon_hint.setObjectName("cardHint")
        summon_hint.setWordWrap(True)
        presence.addWidget(self.chk_summon_hotkey)
        presence.addWidget(summon_hint)
        briefing_row = QHBoxLayout()
        self.chk_briefing = QCheckBox("Morning briefing")
        self.time_briefing = QTimeEdit()
        self.time_briefing.setAccessibleName("Morning briefing time")
        self.time_briefing.setDisplayFormat("HH:mm")
        self.time_briefing.setFixedWidth(90)
        briefing_row.addWidget(self.chk_briefing)
        briefing_row.addWidget(self.time_briefing)
        briefing_row.addStretch()
        presence.addLayout(briefing_row)
        briefing_hint = QLabel("Plays your briefing at this time, on days VYUK is connected.")
        briefing_hint.setObjectName("cardHint")
        briefing_hint.setWordWrap(True)
        presence.addWidget(briefing_hint)
        # RET-1: daily nudge opt-in, beside the briefing — one spoken
        # nudge each morning. State lives in data/daily_count.json
        # (not QSettings): the toggle is the on-demand opt-in, the
        # time is the chosen nudge time.
        nudge_row = QHBoxLayout()
        self.chk_nudge = QCheckBox("Daily nudge")
        self.time_nudge = QTimeEdit()
        self.time_nudge.setAccessibleName("Daily nudge time")
        self.time_nudge.setDisplayFormat("HH:mm")
        self.time_nudge.setFixedWidth(90)
        nudge_row.addWidget(self.chk_nudge)
        nudge_row.addWidget(self.time_nudge)
        nudge_row.addStretch()
        presence.addLayout(nudge_row)
        nudge_hint = QLabel("One spoken nudge each morning — how many things need you today. Offered after your first voice task; turn it off any time and it never asks again.")
        nudge_hint.setObjectName("cardHint")
        nudge_hint.setWordWrap(True)
        presence.addWidget(nudge_hint)
        self.presence_status_label = QLabel("Presence ready.")
        self.presence_status_label.setObjectName("cardHint")
        self.presence_status_label.setWordWrap(True)
        presence.addWidget(self.presence_status_label)

        # Conversion Wave 9 (PF-21): Desktop citizenship card beside
        # Presence, per the PF placement table. No sidebar button.
        try:
            from voice_assistant.gui import pf_surfaces as _pf
            self._w9_citizenship_widget = _pf.CitizenshipWidget(
                on_autostart_jump=lambda: self.show_settings_card(
                    "Presence"))
            _g_connect.addWidget(self._w9_citizenship_widget)
        except Exception:  # noqa: BLE001 — Settings must still build
            pass

    def _sync_voice_bridge_label(self):
        """Wiring Build 1: the bridge status line under the toggle."""
        from voice_assistant.core.gui_facade import voice_text_bridge as _bridge_mod
        if _bridge_mod.is_voice_bridge_enabled():
            if _bridge_mod.get_text_brain() is not None:
                text = ("Voice bridge on — free-form voice goals run on "
                        "the text loop. Every action still needs its "
                        "normal approval first.")
            else:
                text = ("Voice bridge on, but no text brain is attached "
                        "yet — voice goals will still refuse honestly "
                        "until a Groq or on-PC brain session is running.")
        else:
            text = ("Voice bridge off — free-form voice goals refuse "
                    "honestly, as before.")
        label = getattr(self, "lbl_voice_bridge", None)
        if label is not None:
            label.setText(text)

    def _on_voice_bridge_toggled(self, checked: bool):
        """Wiring Build 1: the bridge toggle, wake pattern.

        Session state only (no persistence, like wake). ON flips
        the bridge flag and attaches the app session's existing
        inner text brain when one is running and is not the
        Gemini voice session itself; OFF flips the flag back and
        detaches. A failed attach never enables anything silently
        — the label says so, and voice goals refuse honestly.
        """
        from voice_assistant.core.gui_facade import voice_text_bridge as _bridge_mod
        if checked:
            _bridge_mod.enable_voice_bridge()
            try:
                from voice_assistant.core.gui_facade import (
                    live_session as _app_session)  # Phase 3 B2h: brain hub moved to core/brain/brain.py
                from voice_assistant.core.gui_facade import (
                    voice_live_session as _gemini_session)  # Phase 3 V1b: moved to core/voice/
                inner = getattr(_app_session, "_inner", None)
                if inner is not None and inner is not _gemini_session:
                    _bridge_mod.attach_text_brain_candidate(inner)
            except Exception:  # noqa: BLE001 — attach is best-effort
                pass
        else:
            _bridge_mod.disable_voice_bridge()
            _bridge_mod.set_text_brain(None)
        self._sync_voice_bridge_label()

    def _on_sounds_toggled(self, enabled: bool):
        # Code-Perfection Build 1 — settings undo producer: the
        # sounds toggle is a real settings change through the
        # app, so it goes through the shared undo producer when
        # an undo store is configured (the Doctor page configures
        # one). The adapter below is the mapping the producer
        # needs: setting the key writes QSettings AND the live
        # earcons state, deleting it restores the True default
        # for both, so an undo restores the whole change, not
        # just the stored value. When no undo store is configured
        # yet, or recording fails, the toggle applies exactly as
        # today, unrecorded — recording must never break it.
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
                result = _prod.apply_setting_change(
                    _SoundsEnabledStore(self), "sounds_enabled",
                    bool(enabled))
                if result.get("ok"):
                    return
            except Exception:  # noqa: BLE001 — fall through, apply directly
                pass
        self.earcons.set_enabled(enabled)
        qsettings_set("sounds_enabled", enabled, settings=self.settings)
        self._note_settings_feedback(
            "Interface sounds on — applied."
            if enabled else "Interface sounds off — applied.")

    def _on_continuous_display_toggled(self, enabled: bool):
        """The Settings switch for the first-open continuous
        choice (Privacy & permissions card). Writes through
        the one canonical key pair in core/firstopen_surface
        — the same keys the first-open dialog writes — so
        toggling here changes the gate, reversibly, at any
        time. No undo-producer wiring: the choice is a
        privacy grant, not a value the undo stack should
        silently restore. Guarded throughout: a settings
        failure must never break the Settings page. Nothing
        here starts capture."""
        try:
            from datetime import datetime

            from voice_assistant.core.gui_facade import firstopen_surface as _fos
        except Exception:  # noqa: BLE001
            try:
                from datetime import datetime

                from core.gui_facade import firstopen_surface as _fos
            except Exception:  # noqa: BLE001
                return
        try:
            stored = {
                _fos.PROMPT_SEEN_KEY: bool(qsettings_get(
                    _fos.PROMPT_SEEN_KEY, settings=self.settings)),
                _fos.CHOICE_KEY: str(qsettings_get(
                    _fos.CHOICE_KEY, settings=self.settings) or ""),
            }
            now_iso = datetime.now().isoformat(timespec="seconds")
            updated = _fos.apply_choice(stored, bool(enabled), now_iso)
            for key, value in updated.items():
                qsettings_set(key, value, settings=self.settings)
            # UI settings slice: the choice had no visible
            # feedback. The wording must not claim capture
            # starts — this only changes the gate (see the
            # handler docstring: "Nothing here starts capture").
            self._note_settings_feedback(
                "Continuous display allowed — the gate is open, "
                "but nothing starts capturing on its own."
                if enabled else
                "Continuous display off — saved.")
        except Exception:  # noqa: BLE001 — never break the page
            pass

    def _on_volume_changed(self, value: int):
        self.earcons.set_volume(value / 100.0)
        qsettings_set("sounds_volume", value / 100.0, settings=self.settings)
        # UI settings slice: the readout IS this slider's
        # feedback (no status-line spam per tick).
        label = getattr(self, "lbl_volume", None)
        if label is not None:
            label.setText(f"{value}%")

    def _on_audition_kenney(self, name: str):
        """B-R1-9: play one Kenney audition candidate on explicit
        demand. The player is built lazily and re-seeded from the
        live Earcons volume/enabled at click time, so undo/redo of
        the sounds toggle and volume moves stay in sync with no
        extra wiring. The outcome (played / refused + reason) is
        shown in lbl_audition; nothing is ever substituted."""
        try:
            from voice_assistant.gui.audition import KenneyAudition as _Aud
        except Exception:  # noqa: BLE001
            try:
                from gui.audition import KenneyAudition as _Aud
            except Exception:  # noqa: BLE001
                _Aud = None
        label = getattr(self, "lbl_audition", None)
        if _Aud is None:
            if label is not None:
                label.setText("Audition unavailable — could not load the audition player.")
            return
        audition = getattr(self, "_kenney_audition", None)
        if audition is None:
            try:
                audition = _Aud(parent=self, volume=self.earcons.volume,
                                enabled=self.earcons.enabled)
            except Exception:  # noqa: BLE001
                audition = _Aud(parent=self)
            self._kenney_audition = audition
        try:
            audition.set_volume(self.earcons.volume)
            audition.set_enabled(self.earcons.enabled)
        except Exception:  # noqa: BLE001
            pass
        record = audition.play(name)
        if label is not None:
            if record.get("played"):
                label.setText(
                    f"Played {name} (audition only, gain {record.get('gain_db')} dB to match the shipped -20 dBFS standard).")
            else:
                label.setText(f"Not played: {record.get('reason')}")

class _SoundsEnabledStore:
    """Mapping adapter for the sounds toggle, for the shared
    undo producer (Code-Perfection Build 1). It scopes the
    mapping protocol to the one key it owns, "sounds_enabled":
    getting reads the stored bool (default True, matching the
    shell and the settings schema), setting writes QSettings
    AND the live earcons state, and deleting removes the stored
    key and restores the True default live. Any other key is a
    KeyError — this store owns one setting, nothing else."""

    def __init__(self, page):
        self._page = page

    def __contains__(self, key):
        if key != "sounds_enabled":
            return False
        try:
            return bool(qsettings_contains(key, settings=self._page.settings))
        except Exception:  # noqa: BLE001
            return False

    def __getitem__(self, key):
        if key != "sounds_enabled":
            raise KeyError(key)
        return bool(qsettings_get(key, settings=self._page.settings))

    def __setitem__(self, key, value):
        if key != "sounds_enabled":
            raise KeyError(key)
        self._page.earcons.set_enabled(bool(value))
        qsettings_set(key, bool(value), settings=self._page.settings)

    def __delitem__(self, key):
        if key != "sounds_enabled":
            raise KeyError(key)
        try:
            qsettings_remove(key, settings=self._page.settings)
        except Exception:  # noqa: BLE001
            pass
        self._page.earcons.set_enabled(True)
