"""MainWindow session mixin: suggestions + wake word + idle text.

Split from gui/window_session.py (GUI split #11 batch 1, move only).
"""

import threading
import time

from PySide6.QtCore import (
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import live_session  # Phase 3 B2h: brain hub moved to core/brain/brain.py
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import wake_word_detector  # Phase 3 V1a-2: wake_word moved to core/voice/
from .icons import make_icon
from .style import ACCENT_2
from .window_widgets import _tool_friendly_name
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set


class SessionSuggestionsMixin:
    """Suggestions + wake word + idle text (GUI split #11 batch 1)."""

    # --------------------------- suggestions (v5.49, item 35)
    #: The auto-act classes, in Settings display order — the
    #: names are core.brain.suggestions' CLASS_* constants (the only
    #: classes its whitelist will ever honour).
    _SUGGESTION_CLASSES = (
        ("meeting_open", "Meetings — open the meeting link"),
        ("battery_saver", "Battery — dim the screen to save power"),
        ("download_open", "Downloads — open the finished download"),
    )

    def _apply_suggestion_settings(self):
        """v5.49 (programme item 35): push the Settings suggestion
        controls (persisted in QSettings, like the watchers'
        toggles — core.brain.suggestions' own settings seams live with
        the watchers, not in core/config) into the engine through
        its configure_suggestions accessor. Called at window
        start and after every suggestion toggle changes."""
        try:
            from voice_assistant.core.gui_facade import suggestions as _sug  # Phase 3 B1b: suggestions moved to core/brain/

            enabled = bool(qsettings_get("suggestions/enabled", settings=self.settings))
            auto_act = bool(qsettings_get("suggestions/auto_act", settings=self.settings))
            whitelist = [
                name for name, _label in self._SUGGESTION_CLASSES
                if bool(qsettings_get(f"suggestions/auto_act_{name}", settings=self.settings))]
            _sug.configure_suggestions(
                enabled=enabled, auto_act=auto_act,
                auto_act_whitelist=whitelist)
        except Exception:  # noqa: BLE001 — suggestions degrade off
            pass

    def _on_suggestions_enabled_toggled(self, on: bool):
        qsettings_set("suggestions/enabled", bool(on), settings=self.settings)
        self._apply_suggestion_settings()
        # UI settings slice: configure_suggestions applies live,
        # but nothing visible said so.
        self._note_settings_feedback(
            "Suggestions on — applied." if on else
            "Suggestions off — applied.")

    def _on_suggestions_auto_act_toggled(self, on: bool):
        qsettings_set("suggestions/auto_act", bool(on), settings=self.settings)
        self._apply_suggestion_settings()
        self._note_settings_feedback(
            "Suggestion auto-act on — applied." if on else
            "Suggestion auto-act off — applied.")

    def _on_suggestion_class_toggled(self, name: str, on: bool):
        qsettings_set(f"suggestions/auto_act_{name}", bool(on), settings=self.settings)
        self._apply_suggestion_settings()
        self._note_settings_feedback(
            f"Suggestions for “{name}” auto-act on — applied."
            if on else
            f"Suggestions for “{name}” auto-act off — applied.")

    def _on_suggestion_received(self, record):
        """v5.49 (programme item 35): a delivered suggestion
        renders as a chat card — its text, plus Act / Dismiss.
        The card adds NO privilege: Act runs the suggestion's
        ordinary {tool, args} through core.brain.suggestions.act(),
        which is core/funnel/tools_registry.py's execute() funnel with the full gate
        stack; Dismiss is core.brain.suggestions.dismiss()."""
        if not isinstance(record, dict):
            return
        sid = str(record.get("id") or "")
        text = str(record.get("text") or "").strip()
        if not sid or not text or sid in self._suggestion_cards:
            return
        # v5.51 (polish item N-1): ONE layout for every card,
        # whatever its class — [icon slot] title line, one body
        # line, then the action row with Act and Dismiss in fixed
        # positions (Act first, Dismiss second, both the same
        # fixed size on every card). Title + icon come from the
        # one template map in core.brain.suggestions; an unknown or
        # missing class renders the map's generic template.
        try:
            from voice_assistant.core.gui_facade import suggestions as _sug_tpl  # Phase 3 B1b: suggestions moved to core/brain/

            template = _sug_tpl.suggestion_template(
                str(record.get("class") or ""),
                kind=str(record.get("kind") or ""))
        except Exception:  # noqa: BLE001 — generic card, never none
            template = {"icon": "spark",
                        "title": "A suggestion for you"}
        self._group_break = True
        self._mark_chat_has_messages()
        row = QHBoxLayout()
        card = QFrame()
        card.setObjectName("resultCard")
        card.setMaximumWidth(560)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(6)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        icon_lbl = QLabel()
        icon_lbl.setObjectName("suggestionIcon")
        icon_lbl.setFixedSize(18, 18)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            icon_lbl.setPixmap(make_icon(
                str(template.get("icon") or "spark"),
                ACCENT_2, 14).pixmap(14, 14))
        except Exception:  # noqa: BLE001 — the slot stays, empty
            pass
        title_row.addWidget(icon_lbl)
        title = QLabel(str(template.get("title")
                           or "A suggestion for you"))
        title.setObjectName("resultTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        c_layout.addLayout(title_row)
        body = QLabel(text)
        body.setObjectName("resultBody")
        body.setWordWrap(True)
        c_layout.addWidget(body)
        status = QLabel("")
        status.setObjectName("cardHint")
        status.setWordWrap(True)
        status.setVisible(False)
        c_layout.addWidget(status)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_act = QPushButton("Act")
        btn_act.setObjectName("primaryBtn")
        btn_act.setMinimumSize(96, 32)  # v5.54 (DPI rule ⑤)
        btn_act.clicked.connect(
            lambda _=False, s=sid: self._on_suggestion_act(s))
        btn_row.addWidget(btn_act)
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setObjectName("chipBtn")
        btn_dismiss.setMinimumSize(96, 32)  # v5.54 (DPI rule ⑤)
        btn_dismiss.clicked.connect(
            lambda _=False, s=sid: self._on_suggestion_dismiss(s))
        btn_row.addWidget(btn_dismiss)
        btn_row.addStretch()
        c_layout.addLayout(btn_row)
        # Same width discipline as the result cards: the chat
        # panel's viewport cap, not the card's 560 px ceiling —
        # uncapped, the card outgrew the 400 px panel and its
        # text ran off the edge (item-35 render check).
        card.setProperty("content_w", 560)
        self._fit_chat_item(card)
        row.addWidget(card)
        row.addStretch()
        self.chat_layout.addLayout(row)
        self._suggestion_cards[sid] = {
            "card": card, "act": btn_act, "dismiss": btn_dismiss,
            "status": status, "title": title, "body": body,
            "icon": icon_lbl,
        }
        # No entrance slide for this card (animate=False): the
        # slide helper captures its base position before the
        # layout's first geometry pass over a just-added item,
        # and its finish-snap would park a terminal chat item at
        # the content's top until the next layout activation
        # (caught by the item-35 render check). An actionable
        # card arriving still is also the calmer behaviour.
        self._fade_in(card, animate=False)
        self._scroll_chat_to_bottom()

    def _resolve_suggestion_card(self, sid: str, note: str):
        entry = self._suggestion_cards.get(sid)
        if entry is None:
            return
        entry["act"].setEnabled(False)
        entry["dismiss"].setEnabled(False)
        entry["status"].setText(note)
        entry["status"].setVisible(True)

    def _on_suggestion_act(self, sid: str):
        # v5.57.1 (item 7): the funnel executes synchronously
        # in the calling thread, so calling act() here froze
        # the GUI for the tool's whole duration. Act runs on a
        # worker thread instead; the card resolves to a working
        # note now (both buttons disabled, so no double-Act
        # race) and to the outcome when the signal lands. The
        # confirm gate is unaffected: its handler already runs
        # on a worker thread and marshals its popup to the GUI.
        self._resolve_suggestion_card(sid, "Working on it…")

        def _work():
            try:
                from voice_assistant.core.gui_facade import suggestions as _sug  # Phase 3 B1b: suggestions moved to core/brain/

                outcome = _sug.act(sid)
            except Exception as exc:  # noqa: BLE001 — an outcome
                outcome = {"status": "error", "message": str(exc)}
            try:
                self.signals.suggestion_act_completed.emit(sid, outcome)
            except Exception:  # noqa: BLE001 — window gone; drop it
                pass

        threading.Thread(target=_work, daemon=True,
                         name="vyuk-suggestion-act").start()

    def _on_suggestion_act_completed(self, sid: str, outcome):
        """GUI-thread landing for a worker-thread suggestion Act."""
        if not isinstance(outcome, dict):
            outcome = {"status": "success", "message": str(outcome)}
        # Audit A5: contract statuses, not just legacy "error".
        from voice_assistant.core.gui_facade import (
            is_non_success as _is_non_success)

        if _is_non_success(outcome):
            note = ("That didn't go through: "
                    + str(outcome.get("message") or "refused."))
        else:
            note = str(outcome.get("message")
                       or outcome.get("result") or "Done.")
        self._resolve_suggestion_card(sid, note)

    def _on_suggestion_dismiss(self, sid: str):
        try:
            from voice_assistant.core.gui_facade import suggestions as _sug  # Phase 3 B1b: suggestions moved to core/brain/

            _sug.dismiss(sid)
        except Exception:  # noqa: BLE001
            pass
        self._resolve_suggestion_card(
            sid, "Dismissed — I won't offer this one again today.")

    def _on_wake_word_activated(self):
        # v5.17 (Ears A18): "stay quiet while media plays" — a wake
        # word heard over a film stays a no-op when the user opted
        # in. The probe answers None off Windows / on any failure,
        # and unknown never blocks a wake word.
        if bool(qsettings_get("ears/pause_while_media", settings=self.settings)):
            try:
                from voice_assistant.core.gui_facade import system_audio_active
                if system_audio_active() is True:
                    now = time.monotonic()
                    if now - getattr(self, "_last_media_gate_note", 0.0) > 60.0:
                        self._last_media_gate_note = now
                        self._add_system(
                            "Wake word heard, but media is playing — staying "
                            "quiet. (Settings → Microphone "
                            "turns this off.)")
                    return
            except Exception:  # noqa: BLE001 — the gate must never eat a wake
                pass
        # Voice-control batch (2026-11-16, W3): a live session
        # owns the mic — a wake word heard mid-conversation
        # (quoting the phrase, or the model saying it) must not
        # fire the idle ceremony: no earcon, no system line, no
        # orb flip, no timer. The timeout already exempts live
        # sessions; the activation itself did not.
        try:
            _worker = getattr(self, "worker", None)
            if _worker is not None and _worker.is_alive():
                return
        except Exception:  # noqa: BLE001 — the gate must never eat a wake
            pass
        # Voice-control batch (2026-11-16, W5): the
        # deliberate-mute check comes FIRST — the old code ran
        # the earcon + "listening" line + orb flip before asking
        # whether the mute button was checked, telling the user
        # she was listening while deliberately muted. A muted
        # wake gets one honest line and no timer.
        try:
            _mute_btn = getattr(self, "btn_mute", None)
            _muted_by_button = (bool(_mute_btn.isChecked())
                                if _mute_btn is not None else False)
        except Exception:  # noqa: BLE001
            _muted_by_button = False
        if _muted_by_button:
            self._add_system(
                "Wake word heard, but the microphone is muted — "
                "unmute to talk.")
            return
        self.earcons.wake()
        self._add_system("Wake word heard — listening for your command.")
        self.visualizer.set_state("LISTENING")
        audio_manager.is_muted = False
        self.btn_mic.setProperty("active", "true")
        self.btn_mic.setStyle(self.btn_mic.style())
        self._arm_wake_timeout()

    def _arm_wake_timeout(self):
        """(Re)arm the 6 s wake window on ONE cancellable timer
        (voice-control batch, 2026-11-16, W4): the old
        QTimer.singleShot was fire-and-forget — activations
        stacked timers, and a timer firing after its session
        ended could mute the idle state. One timer, replaced
        on each activation, cancelled on connect (see
        _cancel_wake_timeout, called from _start_connection)."""
        try:
            timer = getattr(self, "_wake_timer", None)
            if timer is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self._on_wake_timeout)
                self._wake_timer = timer
            timer.stop()
            timer.start(6000)
        except Exception:  # noqa: BLE001 — the window must never break a wake
            QTimer.singleShot(6000, self._on_wake_timeout)

    def _cancel_wake_timeout(self):
        """Cancel the armed wake window, if any. Called when a
        session starts — the idle window is void once the user
        has engaged (see W4 above). Never raises."""
        try:
            timer = getattr(self, "_wake_timer", None)
            if timer is not None:
                timer.stop()
        except Exception:  # noqa: BLE001
            pass

    def _on_wake_timeout(self):
        # Mic remake (v6.3.x): a LIVE session owns the mic —
        # the 6 s wake window is for the ambient/idle state,
        # not for an active conversation. The old code muted
        # unconditionally 6 s after every "Hey VYUK", so any
        # utterance starting later, or any pause over 6 s,
        # silently re-muted mid-session. Defensive getattr:
        # the micfix WakeTimeoutTests drive this with a
        # SimpleNamespace stub that has no worker at all.
        try:
            _worker = getattr(self, "worker", None)
            if _worker is not None and _worker.is_alive():
                return
        except Exception:  # noqa: BLE001
            pass
        # v5.95 (micfix): a PTT toggle holds the mic open
        # deliberately — toggle is a click, not a hold, so
        # isDown() is False even when toggled on. The wake
        # window must not clobber it (recovery then took two
        # presses while the toggle still read on).
        if getattr(self, "_ptt_toggled_on", False):
            return
        # Voice-control batch (2026-11-16, W6): a PTT HOLD via
        # Space or the global hotkey never presses btn_mic, so
        # isDown() was False mid-hold and the timer muted the
        # user while still holding the key down. The hold flag
        # (set in _on_ptt_pressed, cleared on release/connect)
        # is the hold test now; isDown() stays as the mouse
        # case below it.
        if getattr(self, "_ptt_hold_active", False):
            return
        if not self.btn_mic.isDown():
            audio_manager.is_muted = True
            self.btn_mic.setProperty("active", "false")
            self.btn_mic.setStyle(self.btn_mic.style())


    # v5.53 (F1): the idle status line while her ear is off.
    # Field evidence (2026-10-23 evening, the user's own
    # screenshot): wake word OFF — a small sidebar chip, and
    # NOTHING else in the idle UI admitted she could not hear
    # "Hey VYUK"; the state label still said "Ready when you
    # are", inviting him to talk at a switched-off ear. The
    # idle line now tells the truth, driven by the same state
    # changes that drive every other status (the DISCONNECTED
    # presentation in _on_status, the wake toggle itself, and
    # first paint), so it can never contradict reality.
    # Command Deck Phase E (copy pass): the old sentence said
    # "Not listening" without qualification, which contradicts
    # the LISTENING pill whenever the mic is live via the mic
    # button — wake-off means she is not listening FOR THE WAKE
    # WORD, not that she cannot hear at all. The scoped wording
    # keeps the v5.53 admission (she will not hear "Hey VYUK")
    # while agreeing with every pill state. Production
    # _on_status always sets label + pill together for live
    # states, so a live pill never coexists with this idle line
    # outside a render script that sets the pill directly.
    _WAKE_OFF_IDLE_TEXT = (
        "Not listening for “Hey VYUK” — wake word is off "
        "(click the Wake toggle, or use the mic button)")

    def _wake_is_off(self) -> bool:
        try:
            return not wake_word_detector.enabled
        except Exception:  # noqa: BLE001 — a broken probe must
            return False  # never invent a "not listening" claim

    def _idle_state_text(self) -> str:
        """The honest idle line for the current wake state."""
        if self._wake_is_off():
            return self._WAKE_OFF_IDLE_TEXT
        return "Ready when you are"

    def _present_idle_state_text(self) -> None:
        """Put the idle line on the state label (wrapping only
        in the wake-off case — the sentence is long)."""
        text = self._idle_state_text()
        self.state_label.setWordWrap(
            text != "Ready when you are")
        self.state_label.setText(text)

    def _apply_idle_listening_text(self) -> None:
        """Re-derive the idle line after a wake toggle / at
        first paint. Only ever touches the label while NO
        session owns it: a live session's Listening/Speaking/
        Thinking lines and an in-flight Connect keep the label
        until their own statuses land."""
        try:
            if self._connected or live_session.is_connected:
                return
            if self._connection_in_flight():
                return
            self._present_idle_state_text()
        except Exception:  # noqa: BLE001 — cosmetics, guarded
            pass

    def _toggle_wake_word(self):
        enabled = self.btn_wake.isChecked()
        wake_word_detector.enabled = enabled
        if enabled:
            self.btn_wake.setText("On — say “Hey VYUK”")
            self._wake_full_text = "On — say “Hey VYUK”"
            self._add_system(f'Wake word on. Say "Hey {config.assistant_name}" to start talking.')
        else:
            self.btn_wake.setText("Off — click to enable")
            self._wake_full_text = "Off — click to enable"
            self._add_system("Wake word off.")
        # Stage 0 features batch 5: a toggle while the sidebar
        # is a compact rail must not resurrect the text in the
        # 64 px column — re-apply the rail (it reads the new
        # state text into the tooltip).
        if getattr(self, "_sidebar_compact", False):
            self._apply_sidebar_compact(True)
        # v5.53 (F1): the idle line follows the toggle the
        # moment it flips (no-op while a session owns the label).
        self._apply_idle_listening_text()

    def _on_tool(self, name: str, result: str):
        pretty = _tool_friendly_name(name)
        # Chat UI (2026-11-16): the answer first — the first
        # line of the result, in her voice, at the top of the
        # card, before the detail. A failure reads as itself,
        # never as "Done".
        _first = (str(result or "").strip().split("\n")[0]
                  if (result or "").strip() else "")
        _answer = None
        if _first:
            if len(_first) > 100:
                _first = _first[:97] + "…"
            _low = _first.lower()
            if _low.startswith(("error", "failed", "couldn't",
                                "could not", "unable", "denied")):
                _answer = _first
            else:
                _answer = f"Done — {_first}"
        self._add_result_card(pretty, result, tool_name=name,
                              answer=_answer)
        self.earcons.done()
