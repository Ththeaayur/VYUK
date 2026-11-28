"""MainWindow session mixin: connection lifecycle + mic/PTT/mute.

Split from gui/window_session.py (GUI split #11 batch 2, move only).
"""

import asyncio
import time

from PySide6.QtCore import QEvent

from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import live_session  # Phase 3 B2h: brain hub moved to core/brain/brain.py
from .icons import make_icon
from .style import TEXT_MUTED
from .window_widgets import BackendWorker
from voice_assistant.core.gui_facade import qsettings_get


# Shown under the orb when the microphone permission itself is off.
_MIC_OFF_HINT = ("The microphone is switched off in VYUK, so nothing "
                 "you say is heard \u2014 turn it on in Settings "
                 "\u2192 Permissions.")

# Mic remake (v6.3.x): shown when the session has been silent
# for 20 s because the mic is MUTED (not because the device
# is silent) — the generic "can't hear anything" hint used to
# send the user to Settings for a device that was fine.
_MIC_MUTED_HINT = ("The microphone is muted, so nothing you say "
                   "is heard \u2014 press the mic button to unmute.")

# Voice-control batch (2026-11-16, §10 P1): shown at once when
# the microphone failed to OPEN and Windows has it off for
# desktop apps. The doctor's verdict names the same cause, but
# the doctor only runs when asked — without this, the live
# session sat deaf with a generic open error in the log.

# Field-check fix #16 (T8 #26): shown when mic_status() itself
# keeps failing — the watch can no longer tell whether the mic
# is hearing anything, so the session must not keep claiming
# LISTENING in silence. _MIC_STATUS_FAIL_THRESHOLD is the
# number of consecutive failed ticks (2 s apart) before the
# hint appears: one transient failure never alarms, but the
# watch gives up loudly well inside the five silent ticks the
# field check observed.
_MIC_STATUS_FAIL_THRESHOLD = 3
_MIC_STATUS_HINT = ("I can't check whether the microphone is "
                    "hearing you right now — the mic status keeps "
                    "failing. If I seem deaf, reconnect the session.")
_MIC_OS_DENIED_HINT = ("Windows has the microphone off for desktop "
                       "apps, so nothing you say is heard \u2014 open "
                       "Settings \u2192 Privacy \u2192 Microphone and "
                       "turn on \u2018Let desktop apps access your "
                       "microphone\u2019.")


class SessionConnectionMixin:
    """Tool/scheduler/clipboard hooks + connection lifecycle +
    mic/PTT/mute (GUI split #11 batch 2)."""
    def _on_scheduler_trigger(self, label: str, message: str):
        self._add_system(f"Reminder: {message}")
        self.earcons.done()
        self._refresh_reminders()
        if self.worker and self.worker.loop and live_session.is_connected:
            injected = f"[REMINDER TRIGGERED]: {message}"
            self._note_self_sent(injected)
            asyncio.run_coroutine_threadsafe(
                live_session.send_text(injected), self.worker.loop
            )

    def _on_clipboard_content(self, text: str):
        # v5.48 (programme item 23): the notice is COUNT-ONLY —
        # clipboard_notice(text) is the entire system line, and no
        # substring of what was copied ever reaches the display
        # (secret-shaped copies get the "content hidden" variant).
        # The old text[:120] preview leaked the copy itself.
        from voice_assistant.core.gui_facade import (
            clipboard_notice,
        )  # Phase 3 D3: moved to core/desktop/

        self._add_system(clipboard_notice(text))

    def _connection_in_flight(self) -> bool:
        """True while a connect attempt is still in flight (brain
        audit round 2, defect 1): a BackendWorker exists and is
        alive, but the session has not reached connected — the
        window's own bookkeeping for "connected" is self._connected
        (set by LISTENING, cleared by DISCONNECTED/drops) backed by
        live_session.is_connected, the same pair the disconnect
        branch below already trusts. CONNECTING, the SETUP download
        lines and a slow first warm-up are all "in flight": there is
        a worker to cancel, and starting another one would stack
        two workers on the shared session singleton."""
        if not (self.worker and self.worker.is_alive()):
            return False
        return not (self._connected or live_session.is_connected)

    def _toggle_connection(self):
        # A manual press always cancels any pending auto-reconnect:
        # the human outranks the backoff schedule.
        self._cancel_reconnect()
        if self.worker and self.worker.is_alive() and live_session.is_connected:
            # Mark the intent BEFORE stopping the session, so the
            # DISCONNECTED it emits while dying reads as deliberate
            # and never triggers an auto-reconnect.
            self._intentional_disconnect = True
            self.worker.stop()
            self._on_status("DISCONNECTED")
            # SPEAKER-LOCK (v5.45): session over — provenance resets
            # to unknown (fail-closed) until the next start marks it.
            self._mark_session_provenance("unknown")
        elif self._connection_in_flight():
            # Brain audit round 2, defect 1: a press while a start
            # is in flight CANCELS the attempt — it never starts a
            # second worker. (The old code fell through to
            # _start_connection, overwriting self.worker; the
            # orphaned worker shared this same session singleton
            # and could never be stopped.) Latch the intent first,
            # stop the worker, and let the normal DISCONNECTED
            # path present the Ready state. The reference is
            # KEPT (S13): since the loop-stop fix a stop can
            # take a few hundred ms — 5 s worst case — to
            # unwind, and every consumer of self.worker guards
            # on is_alive(), so the slot frees itself when the
            # thread actually dies. Dropping the ref here was
            # the last hole in the defect-1 fix: a fast
            # re-Connect could stack a second worker on the
            # shared session singleton while the first was
            # still unwinding.
            self._intentional_disconnect = True
            self.worker.stop()
            self._on_status("DISCONNECTED")
            # SPEAKER-LOCK (v5.45): cancelled start — no session,
            # provenance back to unknown (fail-closed).
            self._mark_session_provenance("unknown")
        else:
            # SPEAKER-LOCK (v5.45): this branch is a human pressing
            # Connect — the one user-started session origin.
            self._start_connection(user_started=True)

    @staticmethod
    def _mark_session_provenance(provenance: str):
        """v5.45 (PROGRAMME SPEAKER-LOCK): tells core/funnel/tools_gate.py (set_session_provenance) how
        the current session started, for the speaker-lock gate.
        Guarded: a provenance failure must never break connecting."""
        try:
            from voice_assistant.core.gui_facade import set_session_provenance  # Phase 4 S1b INTENTIONAL: gate split to funnel

            set_session_provenance(provenance)
        except Exception:  # noqa: BLE001
            pass

    def _maybe_mic_first_use_ask(self, user_started: bool):
        """First-use mic ask (D3, 2026-11-05) at the first human Connect.

        Returns the choice ("always" | "allow_once" | "settings" |
        "not_now") when the ask fired, else None. Only on a human Connect press
        (user_started) — auto-reconnect / connect-on-launch never
        pop a dialog. The ask itself fires only when
        permission_grants.mic_first_use_ask_needed() holds (mic
        denied, never decided — no store file yet, or a store with no
        microphone record — no session grant, not declined this
        session). The dialog is GUI-only by construction (see
        _ask_mic_first_use — it never touches confirm_gate, so no
        voice reply can answer it). The choice itself is applied
        by pf_permissions.apply_mic_first_use_choice — the one
        implementation shared with the first-run wizard's mic page,
        so the two asks can never drift apart. "Open Settings"
        opens the Permissions page (stack 4) and the caller must
        NOT connect. Never raises: any failure reads as "not_now"
        (the ask never breaks connecting)."""
        if not user_started:
            return None
        try:
            from voice_assistant.core.gui_facade import permission_grants as _pg
            if not _pg.mic_first_use_ask_needed():
                return None
        except Exception:  # noqa: BLE001 — the ask never breaks connecting
            return None
        try:
            choice = self._ask_mic_first_use()
        except Exception:  # noqa: BLE001 — a broken popup denies
            choice = "not_now"
        if choice == "settings":
            try:
                self._show_page(4)
            except Exception:  # noqa: BLE001 — the page open never breaks the ask
                pass
            return "settings"
        try:
            from .pf_permissions import (
                apply_mic_first_use_choice as _apply_mic_choice)

            # The note goes to the system line when the caller
            # has one; the ConnectTriggerTests' fake window has
            # none, and the shared choice tolerates notify=None.
            return _apply_mic_choice(
                choice, notify=getattr(self, "_add_system", None))
        except Exception:  # noqa: BLE001 — a broken apply denies
            return "not_now"

    def _reset_mute_state_for_connect(self) -> None:
        """Mic remake (v6.3.x): at (re)connect, transient mute
        state from the previous session must not leak into the
        new one — a session that ended muted (wake timeout,
        a PTT release that never landed, a stale toggle)
        used to reconnect deaf while the UI showed LISTENING.
        The mute BUTTON is the source of truth: is_muted is
        synced from it, the PTT toggle resets, and the mic
        button's active property + label follow. A
        deliberately-checked mute button still mutes (that is
        the user's explicit choice) — only the leaked
        transient state is cleared. Never raises."""
        try:
            self._ptt_toggled_on = False
        except Exception:  # noqa: BLE001
            pass
        # Voice-control batch (2026-11-16, W6): a hold that
        # started before this connect must not survive it — the
        # hold flag is transient state like the toggle above.
        try:
            self._ptt_hold_active = False
        except Exception:  # noqa: BLE001
            pass
        try:
            btn = getattr(self, "btn_mute", None)
            muted = (bool(btn.isChecked()) if btn is not None
                     else bool(audio_manager.is_muted))
            audio_manager.is_muted = muted
        except Exception:  # noqa: BLE001
            pass
        try:
            self._sync_muted_label()
        except Exception:  # noqa: BLE001
            pass
        try:
            mic_btn = getattr(self, "btn_mic", None)
            if mic_btn is not None:
                mic_btn.setProperty(
                    "active", "false" if audio_manager.is_muted
                    else "true")
                mic_btn.setStyle(mic_btn.style())
        except Exception:  # noqa: BLE001
            pass

    def _start_connection(self, user_started: bool = False) -> bool:
        """The connect half of _toggle_connection — also exactly what
        an auto-reconnect attempt invokes. Returns True when a worker
        was actually started (False when the API-key gate stepped
        in instead, or when a worker is already alive — below).

        SPEAKER-LOCK provenance (v5.45): `user_started` is True ONLY
        when a human explicitly pressed Connect (the _toggle_connection
        start path passes True). Every other caller — connect-on-
        launch, auto-reconnect, watchers — leaves the default False,
        so those sessions record "auto" (fail-closed by default)."""
        if self.worker and self.worker.is_alive():
            # Brain audit round 2, defect 1: hard guard. Every
            # caller (the Connect button, auto-reconnect, connect-
            # on-launch) funnels through here, so no path may stack
            # a second BackendWorker on the shared session
            # singleton. A caller that owns the slot clears the old
            # worker first (the toggle's cancel path does);
            # auto-reconnect treats this refusal as a consumed
            # attempt and backs off (see _attempt_reconnect).
            return False
        self._intentional_disconnect = False
        if user_started:
            # Fix 4: a HUMAN Connect is the only thing that clears
            # a terminal give-up (the latch) and the
            # cross-episode budget. Auto-reconnect (user_started
            # False) must never clear them — that would defeat
            # the budget it is supposed to respect.
            self._reconnect_exhausted = False
            self._reconnect_episodes_failed = 0
            self._last_episode_end_at = None
        # A fresh session hasn't proven itself yet; _was_connected is
        # earned back by its first live status.
        self._was_connected = False
        if not config.selected_provider_key_present():
            # Phase 0 (C01): the credential checked is the SELECTED
            # provider's — a Groq-only setup must not be refused for
            # lacking a Gemini key (and vice versa). Local needs no
            # key. Auto-reconnect funnels through here too, so this
            # is the one gate for every connect path.
            provider = config.selected_provider()
            display = {"groq": "Groq", "gemini": "Gemini"}.get(
                provider, "cloud")
            self._show_page(4)
            self.state_label.setText(
                f"Add your {display} key to use the {display} cloud brain")
            self._set_caption(config.missing_provider_key_message())
            self._set_pill("Setup needed")
            return False
        # First-use mic ask (D3, 2026-11-05): a human Connect on a
        # fresh install (mic denied, no store yet) asks once — the
        # mic is the thing that's off, so the ask is a GUI dialog,
        # never voice. "Open Settings" stops the connect here (the
        # user flips the mic on there and presses Connect again);
        # the other choices proceed to connect below.
        try:
            _mic_choice = self._maybe_mic_first_use_ask(user_started)
        except Exception:  # noqa: BLE001 — the ask never breaks connecting
            _mic_choice = None
        if _mic_choice == "settings":
            return False
        # The mic stream opens (or re-opens) here when it may:
        # start() is idempotent (input opens only when no stream
        # is open and the mic is not denied; output only when no
        # stream is open), and this call is what makes a Settings
        # flip to granted — or an "Allow once" above — actually
        # take effect on the next Connect instead of waiting for
        # an app restart.
        try:
            audio_manager.start()
        except Exception:  # noqa: BLE001 — never break connecting
            pass
        # Mic remake (v6.3.x): reset transient mute state so a
        # session that ended muted does not reconnect deaf.
        try:
            self._reset_mute_state_for_connect()
        except Exception:  # noqa: BLE001 — never break connecting
            pass
        # Voice-control batch (2026-11-16, W4): a session
        # starting voids the idle wake window — a 6 s timer
        # armed by an earlier wake must never fire into this
        # session's lifetime and mute the state after it.
        try:
            _cancel_wake = getattr(self, "_cancel_wake_timeout", None)
            if callable(_cancel_wake):
                _cancel_wake()
        except Exception:  # noqa: BLE001 — never break connecting
            pass
        self.worker = BackendWorker(self.signals)
        self.worker.start()
        # Deaf-session watch (v5.37): poll the mic while this
        # session is live (the tick self-stops when the worker
        # dies). A fresh session starts with a clean slate — any
        # hint left over from a previous episode is cleared.
        self._deaf_since = None
        self._clear_deaf_hint()
        # Field-check fix #16 (T8 #26): a fresh session also
        # clears any mic-status failure count left over from the
        # previous episode.
        self._mic_status_failures = 0
        # Fix 4: a fresh session also clears any mic-stall note
        # left over from the previous episode.
        self._mic_stall_noted = False
        try:
            self._mic_watch_timer.start()
        except Exception:  # noqa: BLE001
            pass
        # R2 (2026-11-06): the session-liveness watch starts
        # with the session — a fresh worker gets a fresh
        # probe slate, and a declaration from a previous
        # session can never fire into this one. It also
        # opens the "awaiting first status" window: until
        # this worker's first status arrives, a death with
        # no status at all is still a death worth declaring.
        self._liveness_probe_sent_at = None
        self._liveness_last_ack = None
        self._silent_death_declared = False
        self._liveness_awaiting_first_status = True
        try:
            self._session_liveness_timer.start()
        except Exception:  # noqa: BLE001
            pass
        # SPEAKER-LOCK (v5.45): the worker is actually starting —
        # record how this session began before returning success.
        self._mark_session_provenance("user" if user_started else "auto")
        # v5.48 (programme item 21): a fresh Connect is a fresh
        # capture session — approvals are per session by design.
        try:
            from voice_assistant.core.gui_facade import capture_guard

            capture_guard.reset_session()
        except Exception:  # noqa: BLE001 — never break connecting
            pass
        return True

    # ------------------------------------------- deaf-session watch (v5.37)
    def _mic_watch_tick(self):
        """2 s poll while a session is live: is the microphone
        actually delivering voice? A stream that opened fine but
        stays silent (Windows mic privacy, an OS mute, the wrong
        device) looks exactly like a user who never speaks —
        after 20 s of it, say so on the caption label, once per
        episode, and restore the caption when sound returns."""
        try:
            live = bool(self.worker and self.worker.is_alive())
        except Exception:  # noqa: BLE001
            live = False
        if not live:
            try:
                self._mic_watch_timer.stop()
            except Exception:  # noqa: BLE001
                pass
            self._deaf_since = None
            self._clear_deaf_hint()
            return
        try:
            status = audio_manager.mic_status()
        except Exception as exc:  # noqa: BLE001 — the watch must never break the GUI
            # Field-check fix #16 (T8 #26): a mic_status() that
            # keeps raising used to end the tick silently — the
            # deaf watch died while the session claimed LISTENING,
            # with no hint and no log line. Count consecutive
            # failures; log the first and the threshold crossing
            # to the sendable log (the stall note's channel), and
            # show a hint once the threshold is reached. The hint
            # re-asserts on later ticks, like the silence hint.
            failures = getattr(self, "_mic_status_failures", 0) + 1
            self._mic_status_failures = failures
            if failures == 1 or failures == _MIC_STATUS_FAIL_THRESHOLD:
                try:
                    self._log_drop_line(
                        "[GUI] DROP mic_status_failed: mic_status() "
                        f"failed {failures} time(s) in a row "
                        f"({exc!r}); the deaf watch cannot tell "
                        "whether the mic is hearing you.")
                except Exception:  # noqa: BLE001 — logging must
                    pass  # never break the watch
            if failures >= _MIC_STATUS_FAIL_THRESHOLD:
                try:
                    self._show_deaf_hint(None, text=_MIC_STATUS_HINT)
                except Exception:  # noqa: BLE001 — the hint must
                    pass  # never break the watch
            return
        # A mic_status() that failed before is healthy again —
        # the failure episode is over, so its hint goes with it
        # and the counter resets; the normal silence logic below
        # takes over from here (a still-silent mic re-fires its
        # own hint after 20 s, as usual).
        if getattr(self, "_mic_status_failures", 0) >= _MIC_STATUS_FAIL_THRESHOLD:
            try:
                self._clear_deaf_hint()
            except Exception:  # noqa: BLE001
                pass
        self._mic_status_failures = 0
        # Fix 4 (L6): a stalled input stream is a drop cause the
        # session never hears about — the mic watchdog reopens
        # it locally and tells nobody. Note it once per stall
        # episode in the sendable log; the note clears when the
        # callbacks flow again.
        try:
            if status.get("callback_stalled"):
                self._note_mic_stall(status)
            else:
                self._mic_stall_noted = False
        except Exception:  # noqa: BLE001 — the note must never break the watch
            pass
        # A microphone that is switched off in VYUK's own Permissions
        # opens NO input stream (state "no_stream"), which is not the
        # "silent" case below — so before this the session sat there
        # connected and deaf with nothing on screen explaining why
        # (only a console print). Say so right away: it is a fixed
        # fact, not something to wait 20 s to infer.
        if status.get("state") == "no_stream" \
                and self._mic_permission_blocked():
            self._show_deaf_hint(None, text=_MIC_OFF_HINT)
            return
        # Voice-control batch (2026-11-16, §10 P1): the mic
        # FAILED TO OPEN (open_error is set) and VYUK's own
        # permission is not the cause — that branch returned
        # above. Consult the OS privacy probe: when Windows has
        # the microphone off for desktop apps, say so on screen
        # at once instead of leaving the session deaf with a
        # generic open error. The probe is read-only and guarded
        # (a failed probe reads as "not denied"); the hint
        # clears through the normal else-branch when sound
        # returns, like every other deaf hint.
        if status.get("open_error"):
            try:
                from voice_assistant.core.gui_facade import platform_permission_probe as _ppp
            except Exception:  # noqa: BLE001
                try:
                    from core.gui_facade import platform_permission_probe as _ppp
                except Exception:  # noqa: BLE001
                    _ppp = None
            if _ppp is not None:
                try:
                    _states = _ppp.probe_platform_permissions()
                    _mic = (_states or {}).get("microphone")
                    _os = getattr(_mic, "state", None) if _mic is not None else None
                    if str(_os or "").strip().lower() == "denied":
                        self._show_deaf_hint(None, text=_MIC_OS_DENIED_HINT)
                        return
                except Exception:  # noqa: BLE001 — the watch must
                    pass           # never break the GUI
        # Mic fix (2026-11-16, issue-hunt #5): the send loop's
        # starvation signal now rides mic_status() — no mic audio
        # has reached the session for 20 s. A starving session
        # must not look connected: show the deaf hint at once
        # (the 20 s wait is already inside the signal). Gated on
        # the session being connected, so a stale value left over
        # from a previous session can never fire during a fresh
        # connect. The two specific branches above (switched off
        # in VYUK, Windows privacy denial) already returned with
        # their own wording when they apply; this is the
        # catch-all — it also covers the muted case (state reads
        # "ok" on energy while the queue is gated) and the
        # never-opened case (state "no_stream", unknown open
        # failure), which the silent branch below never sees.
        try:
            _connected = bool(live_session.is_connected)
        except Exception:  # noqa: BLE001 — a failed consult
            _connected = False  # reads as "not connected"
        if _connected and status.get("mic_starved_seconds", 0.0) > 0:
            if status.get("muted"):
                self._show_deaf_hint(None, text=_MIC_MUTED_HINT)
            else:
                self._show_deaf_hint(status.get("device"))
            return
        if status.get("state") == "silent":
            now = time.monotonic()
            if self._deaf_since is None:
                self._deaf_since = now
            if now - self._deaf_since >= 20.0:
                # Mic remake: when the silence is a MUTE, say
                # so — the generic device-silence wording used
                # to send the user to Settings for a device
                # that was fine. (In PTT-hold mode the generic
                # hint already fired here after 20 s of not
                # holding, so the muted wording is no noisier
                # than what it replaces.)
                if status.get("muted"):
                    self._show_deaf_hint(None, text=_MIC_MUTED_HINT)
                else:
                    self._show_deaf_hint(status.get("device"))
        else:
            self._deaf_since = None
            self._clear_deaf_hint()

    @staticmethod
    def _mic_permission_blocked() -> bool:
        """True when VYUK's own microphone permission is what keeps
        the input stream closed (denied and no session grant).
        Never raises: a failed consult reads as "not blocked", so
        the hint can only ever be a missed hint, never a false one."""
        try:
            from voice_assistant.core.gui_facade import (
                permission_grants as _pg,
            )
            return bool(_pg.is_mic_denied()
                        and not _pg.has_session_grant("microphone"))
        except Exception:  # noqa: BLE001
            return False

    def _show_deaf_hint(self, device_name, text=None):
        """Put the can't-hear-you hint on the caption label, once
        per silent episode. The label's current text is kept and
        restored by _clear_deaf_hint(); the hint is re-asserted
        if a status line rewrote the caption meanwhile. `text`
        overrides the default device-silence wording (used for the
        microphone-switched-off case)."""
        name = device_name or "your microphone"
        if text is None:
            text = (f"I can't hear anything from \"{name}\" — "
                    "check the mic, or pick another in Settings.")
        if self._deaf_hint_active:
            if self.caption_label.text() != text:
                self.caption_label.setText(text)
                self._deaf_hint_text = text
            return
        self._deaf_saved_caption = self.caption_label.text()
        self._deaf_hint_text = text
        self._deaf_hint_active = True
        self.caption_label.setText(text)

    def _clear_deaf_hint(self):
        """End a deaf episode: restore the caption's prior text —
        but only when the hint is still what's on the label, so a
        caption a status line set in the meantime (Disconnect's
        own line, say) is never clobbered by a stale restore."""
        if not self._deaf_hint_active:
            return
        self._deaf_hint_active = False
        try:
            if self.caption_label.text() == self._deaf_hint_text:
                self.caption_label.setText(self._deaf_saved_caption or "")
        except Exception:  # noqa: BLE001
            pass
        self._deaf_hint_text = ""
        self._deaf_saved_caption = None

    def _ptt_set_active(self, active: bool):
        audio_manager.is_muted = not active
        self.btn_mic.setProperty("active", "true" if active else "false")
        self.btn_mic.setStyle(self.btn_mic.style())

    def _on_ptt_pressed(self):
        # SPEAKER-LOCK (v5.45): push-to-talk is the user physically
        # driving from the app — the live session counts as
        # user-started from this press on.
        self._mark_session_provenance("user")
        # v5.17 (Ears A10): in "toggle" mode a press flips the mic
        # instead of starting a hold; the matching release is a
        # no-op (see _on_ptt_released).
        if str(qsettings_get("ears/ptt_mode", settings=self.settings)) == "toggle":
            self._ptt_toggled_on = not getattr(self, "_ptt_toggled_on", False)
            self._ptt_hold_active = False
            self._ptt_set_active(self._ptt_toggled_on)
            return
        # Voice-control batch (2026-11-16, W6): a hold is
        # tracked explicitly — the wake timeout used to read
        # btn_mic.isDown(), which is False for Space and
        # global-hotkey holds, so the timer muted the user
        # mid-hold. The flag is cleared on release and on
        # connect (see _reset_mute_state_for_connect).
        self._ptt_hold_active = True
        self._ptt_set_active(True)

    def _on_ptt_released(self):
        if str(qsettings_get("ears/ptt_mode", settings=self.settings)) == "toggle":
            return
        self._ptt_hold_active = False
        self._ptt_set_active(False)

    def changeEvent(self, event):  # noqa: N802 (Qt naming)
        # Field-check fix #5 (M9): a Space-key PTT hold outlives
        # window deactivation — the OS delivers the Space release
        # to the window the user switched to, so VYUK never sees
        # it and the mic keeps streaming indefinitely. Release the
        # hold on ANY activation change, through the existing
        # release path, guarded on the hold flag. Never raises —
        # activation must never break.
        #
        # Why ActivationChange, not WindowDeactivate: QWidget::
        # event handles WindowDeactivate without calling
        # changeEvent (probed on a plain QWidget) — the event
        # never reaches this override. ActivationChange is what
        # the OS delivers on every real Alt-Tab, in both
        # directions (probed: WindowDeactivate + ActivationChange
        # together, isActiveWindow() False).
        #
        # Why BOTH directions: the deactivation direction is the
        # primary fix — the window-level Space hold (keyPressEvent
        # in MemoryLearnedMixin) never touches the mic button, so
        # no button release can save it. The re-activation
        # direction catches holds that started while the window
        # was ALREADY inactive (the field-check m9b sequence: the
        # other window stole activation before the press, so no
        # deactivation ever fires during the hold — only the
        # return can release it). A hold still active at ANY
        # activation change is stale by definition: a legitimate
        # hold cannot span a deactivation (the button releases
        # itself, or this handler releases it on the way out).
        #
        # The one carve-out is the global hotkey: it holds PTT
        # from anywhere BY DESIGN, and its daemon tracks the
        # physical key and releases on its own — the window must
        # not cut a hotkey hold mid-press. See
        # _global_hotkey_holds_ptt.
        if event.type() == QEvent.Type.ActivationChange:
            try:
                if (getattr(self, "_ptt_hold_active", False)
                        and not self._global_hotkey_holds_ptt()):
                    self._on_ptt_released()
            except Exception:  # noqa: BLE001
                pass
        super().changeEvent(event)

    def _global_hotkey_holds_ptt(self) -> bool:
        """True while the OS-level global-hotkey daemon physically
        holds PTT (Ctrl+Shift+N). The daemon owns that hold — it
        fires on_ptt_stop on the physical release — so a window
        activation change must not cut it. The `is True` check
        matters: stub daemons answer any attribute access with a
        truthy stand-in, and only a real True counts."""
        try:
            from voice_assistant.core.gui_facade import hotkey_daemon
            return getattr(hotkey_daemon, "_ptt_active", False) is True
        except Exception:  # noqa: BLE001
            return False

    def _tray_toggle_mute(self, checked=False):
        """Tray Mute: flip the window's mute button so it stays
        the one source of truth, then let _toggle_mute apply it."""
        self.btn_mute.setChecked(bool(checked))
        self._toggle_mute()

    def _toggle_mute(self):
        # The facade re-resolves audio_manager per access (no
        # cache), but this module's top-level import bound the
        # value at first import — a test harness that replaces
        # the audio stub per test (WindowBase._install_shim)
        # would leave _toggle_mute writing to a stale fake
        # while the leg reads the current one. Resolve fresh
        # here so the mute write lands on the live manager.
        import voice_assistant.core.gui_facade as _facade
        audio_manager = _facade.audio_manager
        audio_manager.is_muted = self.btn_mute.isChecked()
        muted = audio_manager.is_muted
        self.btn_mute.setText("Unmute" if muted else "Mute")
        # Look-fix 2026-11-24: the icon follows the resolved
        # theme (the house TEXT_MUTED is near-white — invisible
        # on Light); the theme pass re-makes it on appearance
        # change, this toggle re-makes it on mute change.
        _resolved = getattr(self, "_resolved_theme", None) or {}
        _muted = (_resolved.get("text_muted", TEXT_MUTED)
                  if isinstance(_resolved, dict) else TEXT_MUTED)
        self.btn_mute.setIcon(make_icon(
            "volume_off" if muted else "volume", _muted, 16))
        tray_mute = getattr(self, "tray_mute_action", None)
        if tray_mute is not None and tray_mute.isChecked() != muted:
            tray_mute.setChecked(muted)
        # v5.43 F2: the orb carries the same mute — dimmed, stilled,
        # badged — and the state label reads "Muted", whatever the
        # session state. Unmuting clears both, exactly.
        try:
            self.visualizer.set_muted(muted)
        except Exception:  # noqa: BLE001
            pass
        self._sync_muted_label()
        # UI Phase 1: the header mute pill mirrors btn_mute.
        try:
            pill = getattr(self, "home_mute_pill", None)
            if pill is not None:
                pill.setText("Muted" if muted else "Mute")
        except Exception:  # noqa: BLE001
            pass

    def _sync_muted_label(self):
        """v5.43 F2: keep the state label honest about the mute.

        While the mute button is checked the label reads "Muted".
        The text it replaces is remembered — and refreshed whenever
        a status line rewrites the label underneath the mute, so
        unmuting restores the CURRENT presentation, not a stale
        one. The mute button is the source of truth here (it is
        what _toggle_mute applies to audio_manager): push-to-talk's
        direct writes to audio_manager.is_muted are a different,
        momentary mechanism and never take the label.
        """
        label = getattr(self, "state_label", None)
        if label is None:
            return
        btn = getattr(self, "btn_mute", None)
        try:
            muted = bool(btn.isChecked()) if btn is not None \
                else bool(audio_manager.is_muted)
        except Exception:  # noqa: BLE001
            muted = False
        try:
            if muted:
                if not getattr(self, "_muted_label_active", False):
                    self._muted_saved_label = label.text()
                    self._muted_label_active = True
                elif label.text() != "Muted":
                    # A status line rewrote the label under the
                    # mute — that newer text is what unmute must
                    # restore.
                    self._muted_saved_label = label.text()
                if label.text() != "Muted":
                    label.setText("Muted")
            elif getattr(self, "_muted_label_active", False):
                self._muted_label_active = False
                saved = getattr(self, "_muted_saved_label", "")
                self._muted_saved_label = ""
                if label.text() == "Muted":
                    label.setText(saved)
        except Exception:  # noqa: BLE001
            pass
