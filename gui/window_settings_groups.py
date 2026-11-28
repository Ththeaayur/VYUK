# Refactor Phase 1 step 5: SettingsMixin card-group mixin, moved verbatim from gui/window_settings.py.
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
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import qsettings_get
from .design_system.flowlayout import FlowLayout
from .design_system.switches import HudSwitch, toggle_row




class SettingsGroupsMixin:

    def _build_notices_cards(self, _g_notices):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- Notices gets its own card now (it lived inside Presence
        # before v2.9). Same widgets, same QSettings keys, same
        # handlers — _init_presence_card below wires both cards.
        notices = self._settings_card(_g_notices, "Notices", "What VYUK keeps an eye on — and mentions only when it matters.")
        self.chk_watch_master = HudSwitch(text="VYUK notices things")
        notices.addWidget(toggle_row(self.chk_watch_master))
        self.chk_watch_battery = HudSwitch(text="Battery")
        self.chk_watch_downloads = HudSwitch(text="Downloads && drives")
        self.chk_watch_network = HudSwitch(text="Internet")
        self.chk_watch_welcome = HudSwitch(text="Welcome back")
        self.chk_watch_disk = HudSwitch(text="Disk space")
        self.chk_watch_calendar = HudSwitch(text="Calendar heads-ups")
        self.chk_watch_clipboard = HudSwitch(text="Clipboard watcher")
        self.chk_watch_privacy = HudSwitch(text="Camera & microphone")
        self.chk_watch_fullscreen = HudSwitch(text="Fullscreen breaks")
        self.chk_watch_load = HudSwitch(text="Heavy load")
        # NOSE Wave 3 (v5.45, N12/N13): ambient light is OPT-IN
        # (off unless ticked) and says plainly what it does;
        # foreground awareness shares only app NAMES, never
        # window titles, and nothing leaves this PC.
        self.chk_watch_ambient = HudSwitch(text="Ambient light (opt-in)")
        self.chk_watch_foreground = HudSwitch(text="Foreground app awareness")
        for chk in (self.chk_watch_battery, self.chk_watch_downloads,
                    self.chk_watch_network, self.chk_watch_welcome,
                    self.chk_watch_disk, self.chk_watch_calendar,
                    self.chk_watch_clipboard, self.chk_watch_privacy,
                    self.chk_watch_fullscreen, self.chk_watch_load,
                    self.chk_watch_ambient, self.chk_watch_foreground):
            notices.addWidget(toggle_row(chk))
        notices_hint = QLabel("VYUK quietly keeps an eye on your battery, downloads and connection — and mentions what matters. She only speaks up out loud while she's connected. Turning a notice off means she won't mention it — it still goes in the timeline.")
        notices_hint.setObjectName("cardHint")
        notices_hint.setWordWrap(True)
        notices.addWidget(notices_hint)
        ambient_hint = QLabel("Ambient light is off unless you switch it on: once an hour the camera opens briefly, VYUK notes only how bright the room is as a number, and the picture itself is never stored or sent anywhere. Foreground app awareness uses app names only — never window titles — so she can hold notices during calls and skip break nudges while a film plays; it stays on this PC.")
        ambient_hint.setObjectName("cardHint")
        ambient_hint.setWordWrap(True)
        notices.addWidget(ambient_hint)
        self._init_presence_card()

    def _build_suggestions_cards(self, _g_notices, c_layout):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- Suggestions (v5.49, programme item 35): the
        # proactivity engine's controls — a master switch, the
        # auto-act tier (OFF by default), and the per-class
        # auto-act whitelist. Wired through core.brain.suggestions'
        # configure_suggestions accessors (see
        # _apply_suggestion_settings); persisted in QSettings
        # like the Notices toggles above, per the suggestions
        # module's own settings-seam design. Acting on a card
        # always runs through execute()'s gates — these controls
        # govern SHOWING and auto-acting, never privilege.
        # S4 (settings audit): "Advanced" disclosure — everything
        # not in the five everyday groups lives here, collapsed
        # by default (the old "More settings" disclosure, renamed
        # to the doc's vocabulary; internal attribute names kept).
        # Built here so the cards below can join more_layout in
        # order; the disclosure button + container join c_layout
        # right after the everyday groups.
        self._more_expanded = False
        self.more_toggle_btn = QPushButton("Advanced ▸")
        self.more_toggle_btn.setStyleSheet(
            f"text-align: left; font-weight: 600; color: {TEXT};"
            " background: transparent; border: none; padding: 8px 2px;")
        self.more_toggle_btn.clicked.connect(self._on_more_toggled)
        c_layout.addWidget(self.more_toggle_btn)
        self.more_container = QWidget()
        more_layout = QVBoxLayout(self.more_container)
        more_layout.setContentsMargins(0, 0, 0, 0)
        more_layout.setSpacing(12)
        self.more_container.setVisible(False)
        c_layout.addWidget(self.more_container)
        suggestions_card = self._settings_card(
            _g_notices, "Suggestions",
            "VYUK can offer to act — open a meeting link, dim the screen — as a card you accept or dismiss. Accepting runs the action through the same checks as anything she does.")
        self.chk_suggestions_enabled = HudSwitch(text="Show suggestions")
        self.chk_suggestions_enabled.setChecked(bool(
            qsettings_get("suggestions/enabled", settings=self.settings)))
        self.chk_suggestions_enabled.toggled.connect(
            self._on_suggestions_enabled_toggled)
        suggestions_card.addWidget(toggle_row(self.chk_suggestions_enabled))
        self.chk_suggestions_auto_act = HudSwitch(text="Act automatically — only for the classes ticked below")
        self.chk_suggestions_auto_act.setChecked(bool(
            qsettings_get("suggestions/auto_act", settings=self.settings)))
        self.chk_suggestions_auto_act.toggled.connect(
            self._on_suggestions_auto_act_toggled)
        suggestions_card.addWidget(toggle_row(self.chk_suggestions_auto_act))
        auto_act_lbl = QLabel("Kinds she may act on without asking")  # v5.51 (X-1): plain words for "auto-act classes"
        auto_act_lbl.setObjectName("muted")
        suggestions_card.addWidget(auto_act_lbl)
        self.suggestion_class_checks: dict[str, HudSwitch] = {}
        for _cls_name, _cls_label in self._SUGGESTION_CLASSES:
            _chk = HudSwitch(text=_cls_label)
            _chk.setChecked(bool(qsettings_get(f"suggestions/auto_act_{_cls_name}", settings=self.settings)))
            _chk.toggled.connect(
                lambda on, n=_cls_name:
                self._on_suggestion_class_toggled(n, on))
            self.suggestion_class_checks[_cls_name] = _chk
            suggestions_card.addWidget(toggle_row(_chk))
        suggestions_hint = QLabel("Auto-act is off unless you turn it on, and even then it only ever fires for a ticked class. Everything else waits for your tap on the card.")
        suggestions_hint.setObjectName("cardHint")
        suggestions_hint.setWordWrap(True)
        suggestions_card.addWidget(suggestions_hint)
        return more_layout

    def _build_privacy_cards(self, _g_privacy):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- Privacy & security (v5.45, PROGRAMME SPEAKER-LOCK):
        # additive region — the speaker-lock toggle only. Other
        # Wave-3 builders add their own Settings controls in their
        # own regions; nothing here touches their widgets.
        privacy = self._settings_card(_g_privacy, "Privacy & security", "Who VYUK takes orders from, and what needs your OK first.")
        # v5.48 (programme item 24): the toggle binds the renamed
        # config field directly — config.session_provenance_lock,
        # not the deprecated speaker_lock alias — and the card's
        # label is the honest name (the hint keeps saying why:
        # session provenance, not voiceprint).
        self.chk_session_provenance_lock = HudSwitch(text="Session provenance lock")
        self.chk_session_provenance_lock.setChecked(
            bool(getattr(config, "session_provenance_lock", False)))
        self.chk_session_provenance_lock.toggled.connect(
            self._on_session_provenance_lock_toggled)
        privacy.addWidget(toggle_row(self.chk_session_provenance_lock))
        session_provenance_lock_hint = QLabel("On by default — a voice session starts unproven, and a spoken yes cannot approve anything until you prove the session: start it from the app, unlock it on screen below, or say your voice PIN. While unproven it can still answer and read — it just cannot approve anything or grant anything. This is a session lock, not voiceprint verification: it does not recognise who is speaking.")
        session_provenance_lock_hint.setObjectName("cardHint")
        session_provenance_lock_hint.setWordWrap(True)
        privacy.addWidget(session_provenance_lock_hint)
        # Loophole batch 4 (#1, #20, L-27, #19): the on-screen
        # unlock + the voice-PIN set/clear, beside the lock. The
        # unlock proves THIS session only (a new session starts
        # unproven again); the PIN is opt-in and never shown.
        _proof_row = QHBoxLayout()
        self.btn_unlock_voice_session = QPushButton(
            "Unlock voice for this session")
        self.btn_unlock_voice_session.setObjectName("chipBtn")
        self.btn_unlock_voice_session.setToolTip(
            "Prove this session is yours, so a spoken yes can "
            "approve things again. Lasts until the session ends.")
        self.btn_unlock_voice_session.clicked.connect(
            self._unlock_voice_for_session)
        _proof_row.addWidget(self.btn_unlock_voice_session)
        self.btn_set_voice_pin = QPushButton("Set voice PIN")
        self.btn_set_voice_pin.setObjectName("chipBtn")
        self.btn_set_voice_pin.setToolTip(
            "A 4–8 digit PIN you can say aloud (\"voice PIN "
            "1234\") to unlock a voice session when you are away "
            "from the screen.")
        self.btn_set_voice_pin.clicked.connect(self._set_voice_pin)
        _proof_row.addWidget(self.btn_set_voice_pin)
        self.btn_clear_voice_pin = QPushButton("Clear voice PIN")
        self.btn_clear_voice_pin.setObjectName("chipBtn")
        self.btn_clear_voice_pin.clicked.connect(
            self._clear_voice_pin)
        _proof_row.addWidget(self.btn_clear_voice_pin)
        _proof_row.addStretch()
        privacy.addLayout(_proof_row)
        self.voice_proof_label = QLabel("")
        self.voice_proof_label.setObjectName("cardHint")
        self.voice_proof_label.setWordWrap(True)
        privacy.addWidget(self.voice_proof_label)
        self.voice_pin_label = QLabel("")
        self.voice_pin_label.setObjectName("cardHint")
        self.voice_pin_label.setWordWrap(True)
        privacy.addWidget(self.voice_pin_label)
        self._refresh_voice_proof_status()
        # Loophole batch 5 (#5): private mode — the toggle + the
        # status line, beside the lock. While ON, a turn writes
        # NOTHING anywhere: no transcript, no receipt, no audit
        # record, no Activity line, no cost-ledger row, no
        # learning. The mode says so plainly, on this card and
        # in the status line below. Off by default.
        self.chk_private_mode = HudSwitch(text="Private mode")
        self.chk_private_mode.setChecked(
            bool(getattr(config, "private_mode", False)))
        self.chk_private_mode.toggled.connect(
            self._on_private_mode_toggled)
        privacy.addWidget(toggle_row(self.chk_private_mode))
        private_mode_hint = QLabel("While on, a turn writes nothing anywhere — no transcript, no receipt, no audit record, no Activity line, no cost-ledger row, nothing learned. The turn still runs and you still see it; it is simply never stored. Turn it off again to record turns normally.")
        private_mode_hint.setObjectName("cardHint")
        private_mode_hint.setWordWrap(True)
        privacy.addWidget(private_mode_hint)
        self.private_mode_label = QLabel("")
        self.private_mode_label.setObjectName("cardHint")
        self.private_mode_label.setWordWrap(True)
        privacy.addWidget(self.private_mode_label)
        self._refresh_private_mode_status()
        # Sci-fi Home: the promise the sidebar card used to carry
        # lives here now, beside the standing switches — the card
        # states the guarantee the switches enforce.
        # L-31 (loophole batch 8): the sentence lives in
        # core/privacy_copy.py (PRIVACY_PROMISE), shared with the
        # sidebar card so the two cannot drift apart. "Memory
        # stays on this device" hid that a cloud turn sends the
        # facts she needs to answer to the cloud brain.
        from voice_assistant.core.gui_facade import privacy_copy
        privacy_promise = QLabel(
            "Private by design — " + privacy_copy.PRIVACY_PROMISE)
        privacy_promise.setObjectName("cardHint")
        privacy_promise.setWordWrap(True)
        privacy.addWidget(privacy_promise)
        # S4 (settings audit): safety findability — the automatic
        # suggestion actions live on the Suggestions card (in
        # Notifications), so Privacy & permissions links to them
        # rather than leaving a safety-adjacent control to be
        # found only by browsing. One jump, no second writer.
        self.btn_privacy_auto_act = QPushButton(
            "Automatic suggestion actions…")
        self.btn_privacy_auto_act.setObjectName("chipBtn")
        self.btn_privacy_auto_act.setToolTip(
            "Suggestions she may act on without asking — see the "
            "Suggestions card.")
        self.btn_privacy_auto_act.clicked.connect(
            lambda _=False: self.show_settings_card("Suggestions"))
        privacy.addWidget(self.btn_privacy_auto_act)

        # Conversion Wave 9 (PF-18 + PF-3): Permissions card and the
        # Your-stored-facts dashboard beside Privacy & security, per
        # the PF placement table. The dashboard runs over the Wave 6
        # privacy API (default_privacy_api); delete-everywhere is
        # still an open decision (see w9_wiring docstring). No
        # sidebar button.
        try:
            from voice_assistant.gui import pf_surfaces as _pf
            self._w9_permissions_widget = _pf.PermissionsWidget()
            _g_privacy.addWidget(self._w9_permissions_widget)
            self._w9_privacy_widget = _pf.PrivacyDashboardWidget()
            _g_privacy.addWidget(self._w9_privacy_widget)
        except Exception:  # noqa: BLE001 — Settings must still build
            pass

    def _refresh_voice_proof_status(self):
        """Loophole batch 4: the Privacy card's voice-proof status
        lines — whether this session is proven (and how), and
        whether a voice PIN is set. The PIN is never shown."""
        try:
            from voice_assistant.core.gui_facade import (
                session_owner_proof_status as _status)
            from voice_assistant.core.gui_facade import (
                voice_pin as _vp)
            from voice_assistant.core.gui_facade import (
                config_module as _cfg_mod)
            status = _status()
            if status.get("proven"):
                reason = status.get("reason") or "screen"
                how = {"user-started": "you started it from the app",
                       "screen": "you unlocked it on screen",
                       "voice-pin": "you unlocked it with the "
                       "voice PIN"}.get(
                           reason, "you unlocked it on screen")
                self.voice_proof_label.setText(
                    f"This voice session is unlocked — {how}.")
            else:
                self.voice_proof_label.setText(
                    "This voice session is locked — a spoken yes "
                    "cannot approve anything until you unlock it.")
            has = _vp.has_voice_pin(str(_cfg_mod.DATA_DIR))
            self.voice_pin_label.setText(
                "A voice PIN is set — say \"voice PIN\" followed "
                "by the digits to unlock a voice session." if has else
                "No voice PIN is set — there is no voice unlock "
                "until you set one.")
        except Exception:  # noqa: BLE001 — a status line never
            pass  # breaks Settings

    def _on_private_mode_toggled(self, on: bool):
        # Loophole batch 5 (#5): persists like the neighbouring
        # toggles (config field + PRIVATE_MODE in .env). The
        # stores consult core/private_mode.private_mode_on() at
        # write time, reading this config field live — a turn
        # that flips it on mid-turn suppresses its own stores.
        # Lives HERE, beside the card that connects it (and
        # _refresh_private_mode_status below) — not on the brain
        # mixin: a bare SettingsGroupsMixin builds this card, and
        # a handler on another mixin leaves the build raising
        # AttributeError (neighbour run, loophole batch 5).
        config.private_mode = bool(on)
        self._persist_config_setting(
            "PRIVATE_MODE", "true" if on else "false")
        self._refresh_private_mode_status()

    def _refresh_private_mode_status(self):
        """Loophole batch 5 (#5): the Privacy card's private-mode
        status line — plain words for what private mode does
        right now. Never raises (a broken read reads as off)."""
        try:
            on = bool(getattr(config, "private_mode", False))
        except Exception:  # noqa: BLE001 — a broken read reads off
            on = False
        label = getattr(self, "private_mode_label", None)
        if label is None:
            return
        label.setText(
            "Private mode is ON — this turn writes nothing "
            "anywhere." if on else
            "Private mode is off — turns are recorded normally.")

    def _unlock_voice_for_session(self):
        """Loophole batch 4: the on-screen unlock — proves THIS
        session owner (lasts until the session ends)."""
        try:
            from voice_assistant.core.gui_facade import (
                mark_session_owner_proven as _mark)
            _mark("screen")
            self._refresh_voice_proof_status()
        except Exception:  # noqa: BLE001 — Settings must still work
            pass

    def _set_voice_pin(self):
        """Loophole batch 4: ask for a 4–8 digit PIN (typed
        twice, masked), store it protected, refresh the status.
        A spoken PIN can be overheard — the dialog says so."""
        try:
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            from voice_assistant.core.gui_facade import (
                voice_pin as _vp)
            from voice_assistant.core.gui_facade import (
                config_module as _cfg_mod)
            first, ok = QInputDialog.getText(
                self, "Set voice PIN",
                "A 4–8 digit PIN you can say aloud to unlock a "
                "voice session (\"voice PIN 1234\").\nAnyone who "
                "hears it can use it — pick digits you do not "
                "use anywhere else.",
                QLineEdit.EchoMode.Password)
            if not ok or not (first or "").strip():
                return
            second, ok2 = QInputDialog.getText(
                self, "Set voice PIN",
                "Type the PIN again to confirm.",
                QLineEdit.EchoMode.Password)
            if not ok2 or second != first:
                self.voice_pin_label.setText(
                    "The two PINs did not match — nothing was "
                    "changed.")
                return
            try:
                _vp.set_voice_pin(str(_cfg_mod.DATA_DIR), first)
            except ValueError:
                self.voice_pin_label.setText(
                    "A voice PIN is 4 to 8 digits — nothing was "
                    "changed.")
                return
            self._refresh_voice_proof_status()
        except Exception:  # noqa: BLE001 — Settings must still work
            pass

    def _clear_voice_pin(self):
        """Loophole batch 4: clear the stored voice PIN. With no
        PIN stored there is no voice unlock path at all."""
        try:
            from voice_assistant.core.gui_facade import (
                voice_pin as _vp)
            from voice_assistant.core.gui_facade import (
                config_module as _cfg_mod)
            _vp.set_voice_pin(str(_cfg_mod.DATA_DIR), "")
            self._refresh_voice_proof_status()
        except Exception:  # noqa: BLE001 — Settings must still work
            pass

    def _build_eyes_cards(self, _g_privacy):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- EYES E6 (v5.45): Screen watching — additive region
        # directly after the Privacy & security card above (its
        # own card; no other builder's widgets are touched). The
        # switch is SESSION-SCOPED by design: it reflects the live
        # mode on core/desktop/screen_watch's switchboard, is OFF at every
        # launch, and is deliberately NOT persisted — the mode
        # auto-disables at session end, so a stored "on" would be
        # a lie at next launch. Voice can flip the same switch
        # (see _maybe_watch_command); this checkbox follows via
        # _on_watch_control_changed.
        screen_watch_card = self._settings_card(
            _g_privacy, "Screen watching",
            "VYUK can keep an eye on your screen and mention what changes.")
        self.chk_watch_screen = HudSwitch(text="Watch my screen")
        self.chk_watch_screen.setChecked(False)
        self.chk_watch_screen.toggled.connect(self._on_watch_screen_toggled)
        screen_watch_card.addWidget(toggle_row(self.chk_watch_screen))
        watch_screen_hint = QLabel(
            "While this is on, VYUK looks at your screen every few "
            "seconds and speaks up only when something visibly "
            "changes (more than a small flicker). Frames are never "
            "saved to disk. It turns itself off when the session "
            "ends or you disconnect, and you can also just say "
            "“watch my screen” / “stop watching”. Off by default.")
        watch_screen_hint.setObjectName("cardHint")
        watch_screen_hint.setWordWrap(True)
        screen_watch_card.addWidget(watch_screen_hint)

    def _build_firstopen_cards(self, _g_privacy):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- First-open continuous choice (identity-gap
        # queue): the SAME choice the first-open prompt asks,
        # in the SAME words, available all the way and
        # reversible at any time. Unlike the Screen watching
        # card above — session-scoped by design, never
        # persisted — this choice IS persisted, through the
        # one canonical key pair in core/firstopen_surface
        # (seen flag + choice JSON), which the first-open
        # dialog writes too, so the two surfaces can never
        # drift into two choices. Default OFF: absent or
        # malformed stored choice gates to disabled. Nothing
        # here starts capture: the choice only opens or
        # closes the gate future continuous features must
        # consult (firstopen_surface.gate_from_stored).
        # Copy is taken from the descriptor itself, never
        # re-worded: title, the OFF + local-only promise as
        # the hint, the Yes choice as the checkbox, and the
        # sign + switch-off + deletion promise as the note.
        from voice_assistant.core.gui_facade import firstopen_surface as _fos
        from voice_assistant.core.gui_facade import (
            first_open_prompt as _first_open_prompt)
        _fo_prompt = _first_open_prompt()
        _fo_paras = _fo_prompt["paragraphs"]
        fo_card = self._settings_card(
            _g_privacy, _fo_prompt["title"],
            " ".join(_fo_paras[1:3]))
        self.chk_continuous_display = HudSwitch(text=_fo_prompt["choices"][0])
        try:
            _fo_stored = {
                _fos.PROMPT_SEEN_KEY: bool(self.settings.value(
                    _fos.PROMPT_SEEN_KEY, False, type=bool)),
                _fos.CHOICE_KEY: str(self.settings.value(
                    _fos.CHOICE_KEY, "", type=str) or ""),
            }
            _fo_enabled = bool(_fos.gate_from_stored(_fo_stored)["enabled"])
        except Exception:  # noqa: BLE001 — unreadable store reads as OFF
            _fo_enabled = False
        self.chk_continuous_display.setChecked(_fo_enabled)
        self.chk_continuous_display.toggled.connect(
            self._on_continuous_display_toggled)
        fo_card.addWidget(toggle_row(self.chk_continuous_display))
        fo_note = QLabel(" ".join(_fo_paras[3:6]))
        fo_note.setObjectName("cardHint")
        fo_note.setWordWrap(True)
        fo_card.addWidget(fo_note)

    def _build_browser_cards(self, _g_privacy):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- Browser control (v5.49, programme item 33
        # follow-up): the real-Chrome attach opt-in, bound to the
        # new core/config fields core/browser.py already reads
        # (browser_allow_real_chrome_attach / browser_debug_port).
        # Default OFF: attaching reaches the user's signed-in
        # Chrome, so it is an explicit, reversible opt-in here —
        # never a default, never a voice action.
        browser_card = self._settings_card(
            _g_privacy, "Browser control",
            "Which Chrome VYUK drives when she works in the browser.")
        self.chk_browser_real_chrome = HudSwitch(text="Allow attaching to my real Chrome")
        self.chk_browser_real_chrome.setChecked(bool(
            getattr(config, "browser_allow_real_chrome_attach", False)))
        self.chk_browser_real_chrome.toggled.connect(
            self._on_browser_real_chrome_toggled)
        browser_card.addWidget(toggle_row(self.chk_browser_real_chrome))
        browser_hint = QLabel("Off (the default): VYUK drives no browser at all — your real Chrome is the only browser she ever touches, and she won't touch it without this on. On: she may attach to your real Chrome — the one with your sign-ins — over the connection port below, but only when Chrome itself runs with that port open (see the help text in the app). Turn it off again any time; nothing is attached while it's off.")  # v5.70: real-Chrome-only — the separate-Chrome sentence was deleted with its launches; v5.51 (X-1): "debug port" is engineer-speak; the port is for connecting
        browser_hint.setObjectName("cardHint")
        browser_hint.setWordWrap(True)
        browser_card.addWidget(browser_hint)
        port_row = QHBoxLayout()
        port_row.setSpacing(6)
        port_lbl = QLabel("Chrome connection port")  # v5.51 (X-1): plain words, same setting
        port_lbl.setObjectName("muted")
        port_row.addWidget(port_lbl)
        self.input_browser_debug_port = QLineEdit(
            str(getattr(config, "browser_debug_port", 9222) or 9222))
        self.input_browser_debug_port.setAccessibleName("Browser debug port")
        self.input_browser_debug_port.setFixedWidth(90)
        self.input_browser_debug_port.editingFinished.connect(
            self._on_browser_debug_port_changed)
        port_row.addWidget(self.input_browser_debug_port)
        port_row.addStretch()
        browser_card.addLayout(port_row)
        # Batch 3 (2026-11-05): the one-click enable flow. The
        # checkbox above only flips the opt-in flag; these do the
        # whole job — flip it on, start/restart Chrome with the
        # connection port if needed, and verify the attach — and
        # say plainly what happened. The Chrome button stays
        # hidden until the enable result asks for a launch or a
        # restart; its label then names the action it will take.
        self.browser_enable_status_label = QLabel(
            "Browser control is off.")
        self.browser_enable_status_label.setObjectName("cardHint")
        self.browser_enable_status_label.setWordWrap(True)
        browser_card.addWidget(self.browser_enable_status_label)
        self.btn_browser_enable = QPushButton("Enable browser control")
        self.btn_browser_enable.setObjectName("primaryBtn")
        self.btn_browser_enable.setMinimumHeight(34)  # v5.54 (DPI rule ⑤)
        self.btn_browser_enable.clicked.connect(
            self._on_browser_enable_clicked)
        browser_card.addWidget(self.btn_browser_enable)
        # First-run onboarding (2026-11-16): the one-tap way in —
        # the wizard's Browser page as a standalone dialog. The
        # inline Enable flow above stays as the second way in.
        self.btn_browser_connect = QPushButton("Connect my browser")
        self.btn_browser_connect.setObjectName("chipBtn")
        self.btn_browser_connect.setAccessibleName("Connect my browser")
        self.btn_browser_connect.clicked.connect(
            self._open_browser_connect_dialog)
        browser_card.addWidget(self.btn_browser_connect)
        self.btn_browser_chrome_action = QPushButton(
            "Launch Chrome with browser control")
        self.btn_browser_chrome_action.setMinimumHeight(34)  # v5.54 (DPI rule ⑤)
        self.btn_browser_chrome_action.clicked.connect(
            self._on_browser_chrome_action_clicked)
        self.btn_browser_chrome_action.setVisible(False)
        browser_card.addWidget(self.btn_browser_chrome_action)
        self._browser_chrome_pending = None
        self._refresh_browser_enable_status()

    def _build_audit_cards(self, _g_privacy):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- Audit (v5.49, programme item 38): the recent
        # tool-call record, one human-readable line per record,
        # plus an export. Built against core.audit's contract —
        # read_recent(limit=50) -> list[dict] (newest first; keys
        # ts, tool, args, taint, gate, epoch, fg, kind, ms) and
        # export_to(path) -> int. While the module is absent or
        # empty the view says so plainly ("No audit records
        # yet") instead of failing.
        audit_card = self._settings_card(
            _g_privacy, "Audit",
            "What VYUK has done lately — every tool call, the gate it passed and how long it took. Stored only on this device.")
        self.audit_list = QListWidget()
        self.audit_list.setMaximumHeight(230)
        audit_card.addWidget(self.audit_list)
        audit_row = QHBoxLayout()
        audit_row.setSpacing(6)
        self.btn_audit_refresh = QPushButton("Refresh")
        self.btn_audit_refresh.setObjectName("chipBtn")
        self.btn_audit_refresh.clicked.connect(self._refresh_audit)
        audit_row.addWidget(self.btn_audit_refresh)
        self.btn_audit_export = QPushButton("Export…")
        self.btn_audit_export.setObjectName("chipBtn")
        self.btn_audit_export.clicked.connect(self._export_audit)
        audit_row.addWidget(self.btn_audit_export)
        audit_row.addStretch()
        audit_card.addLayout(audit_row)
        self.audit_status_label = QLabel("")
        self.audit_status_label.setObjectName("cardHint")
        self.audit_status_label.setWordWrap(True)
        self.audit_status_label.setVisible(False)
        audit_card.addWidget(self.audit_status_label)
        self._refresh_audit()

    def _build_approvals_cards(self, _g_privacy):
        # Refactor Phase 1 step 5: section moved verbatim from SettingsMixin._build_settings_page.

        # ---- Remembered approvals (v5.54.1): the per-kind
        # allowlist behind "Always allow this kind" on the
        # confirmation popup. Only navigation kinds can ever
        # appear here (core/confirm_gate.is_allowlistable);
        # destructive kinds never get the button, so they can
        # never be remembered. Removing a kind — or forgetting
        # them all — makes VYUK ask again for that kind.
        # UI rethink Build 3 (§4.4): this card is the ENTRY to
        # the Always allow screen (the screen itself lands in
        # Build 4) — the text names it. Until then, the list
        # below + Remove / Forget all stay the working way back.
        allow_card = self._settings_card(
            _g_privacy, "Remembered approvals",
            "Action kinds VYUK no longer asks about… See them "
            "all and take them back in Always allow. Only "
            "harmless navigation kinds (clicks, typing) can be "
            "remembered — deleting, sending, purchases and "
            "system changes always ask, every time.")
        self.allowlist_list = QListWidget()
        self.allowlist_list.setMaximumHeight(120)
        allow_card.addWidget(self.allowlist_list)
        # Stage 0 features batch 5 (narrow window, render-found
        # at 640 px): five chip buttons in one QHBoxLayout summed
        # to a 606 px minimum, so the card clipped 34 px past
        # the 576 px viewport — the row never wraps. FlowLayout
        # (the E-F205 wrapping-row precedent, already used for
        # Settings rows in window_settings_voice.py) keeps one
        # row at wide widths and wraps to two below 908 px.
        allow_row = FlowLayout(spacing=6)
        self.btn_allowlist_refresh = QPushButton("Refresh")
        self.btn_allowlist_refresh.setObjectName("chipBtn")
        self.btn_allowlist_refresh.clicked.connect(
            self._refresh_allowlist)
        allow_row.addWidget(self.btn_allowlist_refresh)
        self.btn_allowlist_remove = QPushButton("Remove selected")
        self.btn_allowlist_remove.setObjectName("chipBtn")
        self.btn_allowlist_remove.clicked.connect(
            self._on_allowlist_remove)
        allow_row.addWidget(self.btn_allowlist_remove)
        # F-F402 reversal (loophole #2): a paused kind is shown
        # with a " — paused" suffix in the list above, and this
        # button resumes it — the always-allow stands again.
        self.btn_allowlist_resume = QPushButton("Resume selected")
        self.btn_allowlist_resume.setObjectName("chipBtn")
        self.btn_allowlist_resume.clicked.connect(
            self._on_allowlist_resume)
        allow_row.addWidget(self.btn_allowlist_resume)
        self.btn_allowlist_forget = QPushButton("Forget all")
        self.btn_allowlist_forget.setObjectName("chipBtn")
        self.btn_allowlist_forget.clicked.connect(
            self._on_allowlist_forget_all)
        allow_row.addWidget(self.btn_allowlist_forget)
        # UI rethink Build 4 (§4.4): the card is the ENTRY to
        # the Always allow screen — the button opens it; the
        # list + Remove / Forget all above stay the working
        # way back for kinds, and the screen adds the
        # permission rows, the terms and the granted-when.
        self.btn_allowlist_open = QPushButton("Always allow…")
        self.btn_allowlist_open.setObjectName("chipBtn")
        self.btn_allowlist_open.setAccessibleName(
            "Open Always allow screen")
        self.btn_allowlist_open.clicked.connect(
            self._open_always_allow_screen)
        allow_row.addWidget(self.btn_allowlist_open)
        # No trailing stretch: a wrapping row left-aligns and
        # wraps on its own (the E-F205 FlowLayout precedent).
        allow_card.addLayout(allow_row)
        self.allowlist_status_label = QLabel("")
        self.allowlist_status_label.setObjectName("cardHint")
        self.allowlist_status_label.setWordWrap(True)
        self.allowlist_status_label.setVisible(False)
        allow_card.addWidget(self.allowlist_status_label)
        self._refresh_allowlist()
