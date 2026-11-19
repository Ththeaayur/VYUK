"""MainWindow session mixin: status/caption + reconnect cluster.

Split from gui/window_session.py (GUI split #11 batch 1, move only).
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics

from voice_assistant.core.gui_facade import config
from .drop_cause import (
    classify_drop,
    drop_log_line,
    give_up_caption,
    reconnect_caption,
)
from .window_widgets import (
    _RECONNECT_DELAYS,
    _friendly_error,
    _friendly_fallback,
    _friendly_setup,
    _is_fatal_error,
)
from voice_assistant.core.gui_facade import qsettings_get


# v5.57.1 (item 6, second scope addition): after this many
# consecutive spoken turns die unanswered (each ending in a
# watchdog reconnect), she posts ONE diagnostic notice naming
# the likeliest cause class and the next step — phrased as the
# leading check, never a certainty (a quiet mic is the
# field's leading cause, not a proven one on any given box).
# Delivery is the chat line, always: there is no window-level
# spoken route to use here — the only voice route is the Live
# session the watchdog has just declared dead, and speaking
# through a fresh reconnect would itself be an unasked turn
# that can watchdog again. The 7 s watchdog threshold and the
# reconnect mechanics are untouched; only the notices change.
_WATCHDOG_DIAGNOSTIC_AFTER = 3
_WATCHDOG_DIAGNOSTIC_TEXT = (
    "Three spoken turns in a row got no reply from the live "
    "connection, so I won't keep reconnecting silently. The "
    "likeliest cause is that no speech is reaching the service "
    "— please check the microphone is selected and loud enough "
    "(Settings → Microphone → Test microphone), or type "
    "your message instead and I'll answer in text.")

# Fix 4 (2026-11-05): the cross-episode reconnect budget — the
# zombie-loop closer. _RECONNECT_DELAYS bounds attempts WITHIN
# one episode (4); this bounds consecutive episodes that end
# WITHOUT a real server response (SPEAKING/THINKING — the same
# bar the watchdog streak already uses; LISTENING alone never
# resets it, because a reconnected session reports LISTENING
# without having answered anything). At the budget, the next
# drop gives up instead of starting another episode.
# The window is one resumption-handle lifetime (600 s, the
# same window live_client_resume enforces): episodes further
# apart than that are a new incident, not the same flap, and
# reset the count — unrelated drops hours apart must never
# accumulate into a false give-up.
_RECONNECT_EPISODE_BUDGET = 3
_RECONNECT_EPISODE_WINDOW_S = 600.0

# R2 (2026-11-06): the session-liveness watch — the Fix 4
# residual (a status-less worker death). One GUI-thread timer
# probes the worker's event loop from OUTSIDE it: a trivial
# callback scheduled with call_soon_threadsafe, whose ack
# proves the loop is still processing work. The interval is
# 5 s; the grace is 30 s. The grace is the busy-vs-dead
# line: the loop's work is awaits (audio, network, tool
# queue), and a sync tool handler that blocks the loop for
# 30 s straight has already frozen the voice session for
# 30 s — declaring the drop and reconnecting is the
# recovery, and the task pauses resumably (Fix 9) instead
# of dying. 30 s also sits inside the 45 s connect budget
# (core/voice/live_client_helpers.py CONNECT_BUDGET_S), so
# a stalled connect attempt is declared before its budget
# even expires. Never retuned here: Fix 4's budgets
# (3 episodes / 600 s / 2-5-15-30 s) are untouched.
_LIVENESS_TICK_S = 5.0
_LIVENESS_GRACE_S = 30.0


class SessionStatusMixin:
    """Status/caption + reconnect cluster (GUI split #11 batch 1)."""
    def _on_status(self, status: str):
        upper = status.upper()
        self._status_raw = upper
        # R2: any status proves the worker loop is alive and
        # delivering — it clears an outstanding liveness probe,
        # so a status can never arrive alongside a wedged-loop
        # verdict for the same moment. It also ends the
        # "awaiting first status" window a fresh worker opens.
        self._liveness_probe_sent_at = None
        self._liveness_awaiting_first_status = False
        # EYES E6 (v5.45): watch-my-screen auto-disables at
        # session end — DISCONNECTED is the signal every session
        # end / disconnect path funnels through, so the hook
        # lives here, ahead of all presentation.
        if "DISCONNECTED" in upper:
            self._disable_watch_mode("session end")
            # v5.48 (programme item 21): session end is a capture
            # session boundary — per-kind capture approvals from
            # the dead session never carry into the next one.
            try:
                from voice_assistant.core.gui_facade import capture_guard

                capture_guard.reset_session()
            except Exception:  # noqa: BLE001 — never break status
                pass
        # First-run setup (built-in brain downloads/loads) reads as CONNECTING,
        # with the full setup line shown in the state label.
        is_setup = upper.startswith("SETUP:") or upper.startswith("DOWNLOADING")
        # v5.17: the brain proxy fell back from a cloud engine that
        # never connected to the on-device brain. A notice, not a
        # state: explain it once in plain words and let the fallback
        # session's own statuses drive the UI from here. This runs
        # BEFORE the drop machinery on purpose — the reason text can
        # itself contain the word "error" and must not be mistaken
        # for a session drop.
        if upper.startswith("FALLBACK"):
            reason = status.split(":", 1)[1].strip() if ":" in status else ""
            friendly = _friendly_fallback(reason)
            self._add_system(friendly)
            # v5.57.1 (item 3): the fallback is active this
            # session — the brain truth line on the Settings
            # card must say so until the user picks again.
            self._brain_fallback_active = True
            try:
                self._refresh_brain_truth()
            except Exception:  # noqa: BLE001 — the notice stands
                pass
            # Settings redesign §4c: the moment of need — the
            # Home strip offers the one-tap Connect repair right
            # now (its 1 s timer would show it anyway; this is
            # immediate, not a wait).
            try:
                self._refresh_settings_voice_strip()
            except Exception:  # noqa: BLE001 — the notice stands
                pass
            self.state_label.setText("Switched to the on-device brain")
            self.state_label.setToolTip(status)
            self._set_caption(friendly)
            self._set_pill("On-device brain")
            self.visualizer.set_state("CONNECTING")
            mini = getattr(self, "mini_orb", None)
            if mini is not None:
                try:
                    mini.set_state("CONNECTING")
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._update_tray_state("CONNECTING")
            except Exception:  # noqa: BLE001
                pass
            return
        # Fix 4 (2026-11-05): a server goAway is not a drop yet —
        # the session ends on the next status — but it IS the
        # cause of that end. Remember it so the DISCONNECTED
        # that follows classifies as server_ended instead of
        # unknown; _record_drop consumes the flag.
        if upper.startswith("LIVE_GOAWAY"):
            self._go_away_seen = True
        # v5.57.1 (item 6): the Live watchdog's notice, same shape
        # as the FALLBACK notice above — a notice, not a state:
        # the session went quiet after voice was sent, so it is
        # being ended for auto-reconnect. The old comment here
        # claimed this cannot spam because the watchdog fires
        # once per session — true per session, false across
        # auto-reconnects: on a box where every spoken turn
        # dies unanswered, every episode posted the same pair
        # of lines and the chat filled with them (field, 7
        # pairs). So episodes coalesce: the first episode posts
        # its line (and "Back online" on success, in
        # _note_session_live); later episodes in the same
        # streak stay silent in chat — the Reconnecting pill +
        # caption below are the persistent indicator — and at
        # the third consecutive unanswered turn ONE diagnostic
        # notice names the likeliest cause class and the next
        # step. The streak resets only on a real server
        # response (SPEAKING/THINKING below); a reconnected
        # session merely reporting LISTENING does NOT reset it,
        # because it has not answered anything yet.
        if upper.startswith("LIVE_WATCHDOG"):
            streak = int(getattr(self, "_watchdog_streak", 0) or 0) + 1
            self._watchdog_streak = streak
            if streak == 1:
                self._add_system(
                    "The live connection went quiet after you "
                    "spoke, so I'm reconnecting — one moment.")
            elif (streak == _WATCHDOG_DIAGNOSTIC_AFTER
                    and not getattr(
                        self, "_watchdog_diagnostic_posted", False)):
                self._watchdog_diagnostic_posted = True
                self._add_system(_WATCHDOG_DIAGNOSTIC_TEXT)
            # Fix 4: record the drop HERE, with its cause — the
            # session is about to end, and the DISCONNECTED that
            # follows must keep watchdog_silence, not overwrite
            # it with unknown. _record_drop's pending record is
            # exactly what carries it across.
            self._record_drop(status, watchdog=True)
            self.state_label.setText("Connection went quiet")
            self.state_label.setToolTip(status)
            self._set_caption(reconnect_caption("watchdog_silence"))
            self._set_pill("Reconnecting")
            self.visualizer.set_state("CONNECTING")
            mini = getattr(self, "mini_orb", None)
            if mini is not None:
                try:
                    mini.set_state("CONNECTING")
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._update_tray_state("CONNECTING")
            except Exception:  # noqa: BLE001
                pass
            return
        self.visualizer.set_state("CONNECTING" if is_setup else upper)
        # Presence pack: mirror the session state into the mini orb.
        mini = getattr(self, "mini_orb", None)
        if mini is not None:
            try:
                mini.set_state(status)
            except Exception:  # noqa: BLE001
                pass
        # SENSES v5.16: the tray icon carries a state dot + tooltip.
        try:
            self._update_tray_state("CONNECTING" if is_setup else upper)
        except Exception:  # noqa: BLE001
            pass
        # Phase 3: thinking-dots bubble lives only in THINKING state.
        try:
            if "THINKING" in upper:
                self._show_thinking_bubble()
            else:
                self._hide_thinking_bubble()
        except Exception:  # noqa: BLE001
            pass

        self.state_label.setToolTip("")
        self.state_label.setWordWrap(False)

        # Feels-alive pack (v2.5): liveness bookkeeping runs before
        # the presentation chain. A live state proves the session (and
        # ends any reconnect episode); a drop may turn into the quiet
        # reconnecting presentation instead of the usual one.
        # v5.57.1 (item 6): a real server response (SPEAKING /
        # THINKING) ends any no-response streak — LISTENING
        # deliberately does not, since a reconnected session
        # reports LISTENING without having answered anything.
        if not is_setup and ("SPEAKING" in upper or "THINKING" in upper):
            self._watchdog_streak = 0
            self._watchdog_diagnostic_posted = False
        if not is_setup and (
            "LISTENING" in upper or "SPEAKING" in upper or "THINKING" in upper
        ):
            self._note_session_live()
        drop_handled = False
        drop_exhausted = False
        if not is_setup:
            is_error_status = (
                "ERROR" in upper or "MISSING_API_KEY" in upper
            ) and not (
                "MISSING_API_KEY" in upper and self._brain_engine() == "local"
            )
            if is_error_status:
                fatal = _is_fatal_error(status)
                self._last_status_fatal = fatal
                # v5.29: every ERROR status is also written verbatim
                # to data/activity.log. Diagnosing the 2026-10-22
                # connect failures kept depending on a hover tooltip
                # nobody could send back; the log file is sendable,
                # and now it always holds the raw error.
                try:
                    from voice_assistant.core.gui_facade import DATA_DIR as _DD
                    _DD.mkdir(parents=True, exist_ok=True)
                    with open(_DD / "activity.log", "a", encoding="utf-8") as _fh:
                        _fh.write(f"[GUI] ERROR status: {status}\n")
                except Exception:  # noqa: BLE001 — logging must never break status handling
                    pass
                # Fix 4: classify the fall before the drop
                # machinery runs, so the reconnect caption and
                # the log carry the cause, not a bare error.
                self._record_drop(status, fatal=fatal)
                drop_handled, drop_exhausted = self._handle_session_drop(fatal)
            elif "DISCONNECTED" in upper:
                fatal = self._last_status_fatal
                self._last_status_fatal = False
                # Fix 4: a bare DISCONNECTED used to log nothing
                # — the cause died with the session. Record it
                # first (a pending watchdog/fatal record wins).
                self._record_drop(status, fatal=fatal)
                drop_handled, drop_exhausted = self._handle_session_drop(fatal)

        if is_setup:
            # v2.2: plain words on the label; the raw setup line
            # (filenames and all) stays one hover away in the tooltip.
            detail = _friendly_setup(status)
            metrics = QFontMetrics(self.state_label.font())
            width = self.state_label.width()
            if width > 40:
                detail = metrics.elidedText(detail, Qt.TextElideMode.ElideRight, max(120, width - 8))
            elif len(detail) > 110:
                detail = detail[:107] + "…"
            self.state_label.setText(detail)
            self.state_label.setToolTip(status)
            self._set_caption("First-run setup — this only happens once.")
            self._set_pill("Setting up")
        elif "LISTENING" in upper:
            if not self._connected:
                self.earcons.connect()
            self._connected = True
            self.state_label.setText("Listening…")
            self._set_caption("Speak now — I'm listening.")
            self._set_pill("Listening")
        elif "SPEAKING" in upper:
            self.state_label.setText(f"{config.assistant_name} is speaking")
            self._set_caption("")
            self._set_pill("Speaking")
        elif "THINKING" in upper:
            self.state_label.setText("Thinking…")
            # Keep your words on screen while VYUK thinks (live caption)
            if not self.caption_label.text().startswith("“"):
                self._set_caption("")
            self._set_pill("Thinking")
        elif upper.startswith("CONNECTING: RETRY"):
            # Round-3 fix #6 (#58): a connect retry names itself
            # on the state label — the user was watching
            # CONNECTING with no word that a retry was happening.
            # The pill stays "Connecting" and the orb keeps its
            # CONNECTING presentation (the status still contains
            # CONNECTING); only the label carries the retry words,
            # elided like the setup line above. The full line stays
            # one hover away in the tooltip.
            detail = status.split(":", 1)[1].strip() if ":" in status else status
            detail = detail[:1].upper() + detail[1:] if detail else detail
            metrics = QFontMetrics(self.state_label.font())
            width = self.state_label.width()
            if width > 40:
                detail = metrics.elidedText(detail, Qt.TextElideMode.ElideRight, max(120, width - 8))
            elif len(detail) > 110:
                detail = detail[:107] + "…"
            self.state_label.setText(detail)
            self.state_label.setToolTip(status)
            self._set_caption("Setting up your live session.")
            self._set_pill("Connecting")
        elif "CONNECTING" in upper:
            self.state_label.setText("Connecting…")
            self._set_caption("Setting up your live session.")
            self._set_pill("Connecting")
        elif ("ERROR" in upper or "MISSING_API_KEY" in upper) and not (
            "MISSING_API_KEY" in upper and self._brain_engine() == "local"
        ) and not drop_handled:
            # v2.2: plain words on the label; the raw technical error
            # stays one hover away in the tooltip — never shouted.
            # The friendly lines are full sentences, so let them wrap
            # instead of clipping off both edges of the window.
            self.state_label.setWordWrap(True)
            _friendly = _friendly_error(status)
            self.state_label.setText(_friendly)
            self.state_label.setToolTip(status)
            # Phase 2 (chatbox #16): the error also lands in the
            # chat itself, where the user is looking — once per
            # distinct error, so reconnect retries don't spam it.
            if getattr(self, "_last_mirrored_error", None) != _friendly:
                self._last_mirrored_error = _friendly
                self._add_system(_friendly)
            self._set_caption("")
            self._set_pill("Problem")
            now = time.monotonic()
            if now - self._last_error_sound > 5.0:
                self.earcons.error()
                self._last_error_sound = now
        elif "DISCONNECTED" in upper and not drop_handled:
            if self._connected:
                self.earcons.disconnect()
            self._connected = False
            # v5.53 (F1): the idle line is wake-aware — with the
            # wake word off it says she is not listening (and how
            # to talk to her anyway), never a bare Ready.
            self._present_idle_state_text()
            self._set_caption("Press Connect, then just talk.")
            self._set_pill("Offline")

        if drop_exhausted:
            # The last auto-reconnect attempt just failed — the normal
            # presentation stands, but says so honestly, naming the
            # cause (Fix 4: the give-up caption is per-cause, and
            # always keeps the pinned "Couldn't reconnect — press
            # Connect" shape).
            record = getattr(self, "_last_drop", None)
            cause = (record or {}).get("cause") \
                if isinstance(record, dict) else None
            self._set_caption(give_up_caption(cause or "unknown"))

        # v5.43 F2: while the mic is muted the state label says so,
        # whatever status just presented — a session event must
        # never leave it claiming "Listening…" over a muted mic.
        self._sync_muted_label()

        # Chat UI (2026-11-16): the voice-state line follows the
        # status (Listening / Thinking / Speaking / Dictating);
        # a LISTENING turn-end flushes the composer queue; a
        # disconnect mid-turn keeps the failed attempt as a
        # message with a Retry instead of dropping it silently;
        # and the send button re-derives (a landed reply or a
        # dropped session flips it back to Send). All guarded —
        # status presentation never breaks on chat state.
        try:
            self._chat_voice_state_update(upper)
        except Exception:  # noqa: BLE001
            pass
        try:
            if "LISTENING" in upper:
                self._chat_queue_flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            if "DISCONNECTED" in upper:
                _live = getattr(self, "_live_reply", None)
                if _live is not None and not _live.get("finalized"):
                    _msg_id = _live.get("msg_id")
                    _bubble = _live.get("bubble")
                    self._finalize_live_reply(
                        _live["reveal"].full, status="failed")
                    if _msg_id is not None:
                        _node = self._chat_thread().get(_msg_id)
                        if _node is not None:
                            _node.error = (
                                "That reply didn't arrive — you're "
                                "no longer connected.")
                            if _bubble is not None:
                                self._chat_apply_status_row(
                                    _bubble, _node)
                elif getattr(self, "_chat_turn_in_flight", False):
                    self._chat_turn_in_flight = False
                    self._chat_add_failed_reply(
                        "That reply didn't arrive — you're no longer "
                        "connected.")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._sync_send_button()
        except Exception:  # noqa: BLE001
            pass

        self._sync_connect_button()

    def _sync_connect_button(self):
        if self._connected:
            self.btn_connect.setText("Disconnect")
            self.btn_connect.setObjectName("dangerBtn")
        elif self._connection_in_flight():
            # Brain audit round 2: while a start is in flight
            # (CONNECTING / SETUP / warming up) the button is the
            # attempt's Cancel — pressing it cancels, per
            # _toggle_connection. _on_status re-syncs on every
            # status, so the label tracks the attempt's life.
            self.btn_connect.setText("Cancel")
            self.btn_connect.setObjectName("primaryBtn")
        else:
            self.btn_connect.setText("Connect")
            self.btn_connect.setObjectName("primaryBtn")
        self.btn_connect.setStyle(self.btn_connect.style())

    # ------------------------------------------- auto-reconnect (v2.5)
    def _note_session_live(self):
        """A LISTENING/SPEAKING/THINKING status: the session is (back)
        alive. Ends any reconnect episode — with a word in chat when
        the return was automatic. v5.57.1 (item 6): only the FIRST
        episode of a no-response streak posts "Back online" — later
        episodes stay silent in chat, or the pair-spam returns
        through this half alone.

        Fix 4: this is also where the cross-episode budget is
        kept. A real server response (SPEAKING/THINKING) proves
        the service is alive and resets the budget — a mere
        LISTENING does not, because a reconnected session
        reports LISTENING without having answered anything
        (the zombie that connects, claims to listen, then goes
        quiet again). An episode that ends on LISTENING alone
        is one strike against the budget of
        _RECONNECT_EPISODE_BUDGET; at the budget the next drop
        gives up instead of starting another episode. A live
        status also consumes any pending drop record and clears
        a stale goAway flag."""
        self._was_connected = True
        self._last_status_fatal = False
        self._go_away_seen = False
        upper = (getattr(self, "_status_raw", "") or "").upper()
        if "SPEAKING" in upper or "THINKING" in upper:
            # A real server response proves the service is alive
            # and resets the budget — unconditionally, not only
            # while an episode is pending: a zombie episode's
            # strikes must clear the moment the service finally
            # answers, even if that answer arrives after the
            # episode already ended on LISTENING.
            self._reconnect_episodes_failed = 0
            self._last_episode_end_at = None
        was_pending = bool(getattr(self, "_reconnect_pending", False))
        if was_pending and "LISTENING" in upper:
            self._reconnect_episodes_failed = int(
                getattr(self, "_reconnect_episodes_failed", 0) or 0) + 1
            self._last_episode_end_at = time.monotonic()
        record = getattr(self, "_last_drop", None)
        if isinstance(record, dict):
            record["pending"] = False
        if self._reconnect_pending:
            self._reconnect_timer.stop()
            self._reconnect_pending = False
            self._reconnect_attempts = 0
            if int(getattr(self, "_watchdog_streak", 0) or 0) <= 1:
                self._add_system("Back online — reconnected automatically.")
        # R2: the session is alive again — a new fall may be
        # declared from here on; the old one is closed.
        self._silent_death_declared = False

    def _auto_reconnect_enabled(self) -> bool:
        return bool(qsettings_get("presence/auto_reconnect", settings=self.settings))

    def _drop_cause(self) -> str:
        """The recorded cause of the current fall, or "unknown"
        when no drop has been recorded yet."""
        record = getattr(self, "_last_drop", None)
        if isinstance(record, dict):
            return record.get("cause") or "unknown"
        return "unknown"

    def _show_reconnecting(self, cause=None):
        """The reconnecting presentation: one line only. The state
        label says "Connection dropped" and the pill says
        "Reconnecting" — the caption names the cause, in the
        plain words drop_cause.py already defined for it. The
        state label never carries the cause, so this is not the
        same fact said twice, and it replaces the stale caption:
        a drop used to leave "Speak now — I'm listening." on
        screen while the connection was down. The cause stays
        recorded where it belongs too: the drop record
        (_last_drop) and the activity log. Fix 4's episode budgets
        are untouched — the caption was presentation, not the
        counting/delays, and those are pinned."""
        self._connected = False
        self.state_label.setText("Connection dropped")
        self._set_caption(
            reconnect_caption(cause or self._drop_cause()))
        self._set_pill("Reconnecting")

    def _begin_reconnect(self):
        """Schedule the next attempt (backoff: 2 s, 5 s, 15 s, 30 s)
        and hold the reconnecting presentation while we wait.

        Fix 4: the episode window lives here — an episode that
        starts more than one resumption-handle lifetime after
        the last one ended is a new incident, and the
        cross-episode budget resets instead of carrying stale
        strikes into it."""
        now = time.monotonic()
        last_end = getattr(self, "_last_episode_end_at", None)
        if last_end is not None and \
                now - last_end > _RECONNECT_EPISODE_WINDOW_S:
            self._reconnect_episodes_failed = 0
            self._last_episode_end_at = None
        delay = _RECONNECT_DELAYS[self._reconnect_attempts]
        self._reconnect_attempts += 1
        self._reconnect_pending = True
        self._show_reconnecting(self._drop_cause())
        self._reconnect_timer.start(int(delay * 1000))

    def _log_drop_line(self, line: str) -> None:
        """One drop line into data/activity.log — the sendable log.
        Same guarded pattern as the ERROR-status write above:
        logging must never break status handling."""
        try:
            from voice_assistant.core.gui_facade import DATA_DIR as _DD
            _DD.mkdir(parents=True, exist_ok=True)
            with open(_DD / "activity.log", "a", encoding="utf-8") as _fh:
                _fh.write(f"{line}\n")
        except Exception:  # noqa: BLE001 — logging must never break status handling
            pass

    def _record_drop(self, status: str, *, fatal: bool = False,
                     watchdog: bool = False, silent: bool = False) -> str:
        """Record this fall's drop cause — once. The first, most
        specific cause wins: a pending record (e.g. the
        LIVE_WATCHDOG record made moments before the
        DISCONNECTED that follows it) is never overwritten by
        the blander status that arrives with the fall's second
        half. Returns the cause. Also writes the DROP line to
        activity.log, so a bare DISCONNECTED — which used to
        log nothing — now leaves evidence. Never raises."""
        try:
            record = getattr(self, "_last_drop", None)
            if isinstance(record, dict) and record.get("pending"):
                return record.get("cause") or "unknown"
            go_away = bool(getattr(self, "_go_away_seen", False))
            self._go_away_seen = False
            cause = classify_drop(
                status, fatal=fatal, watchdog=watchdog, go_away=go_away,
                silent=silent)
            self._last_drop = {
                "cause": cause, "raw": status or "",
                "pending": True, "at": time.monotonic(),
            }
            self._log_drop_line(drop_log_line(cause, status))
            return cause
        except Exception:  # noqa: BLE001 — recording must never break status handling
            return "unknown"

    def _note_mic_stall(self, status: dict | None) -> None:
        """Mic-stall surfacing (Fix 4, L6). The mic watchdog
        already reopens a stalled stream locally and tells
        nobody — the session keeps claiming LISTENING while
        deaf. This notes the stall once per episode in the
        sendable log (device included); it changes no session
        state and ends no session — the local reopen is the
        recovery, this is only the visibility it lacked.
        Never raises."""
        try:
            if bool(getattr(self, "_mic_stall_noted", False)):
                return
            self._mic_stall_noted = True
            device = (status or {}).get("device") or "your microphone"
            self._log_drop_line(
                "[GUI] DROP mic_stall: no microphone callbacks "
                f"from {device}; the input stream is stalled.")
        except Exception:  # noqa: BLE001 — a note must never break the mic watch
            pass

    def _give_up(self) -> None:
        """The ONE terminal give-up (Fix 4). Latches, so no late
        status can restart an episode after the user was told
        to press Connect (L4); stops a wedged worker, so the
        Connect button offers a fresh start instead of Cancel
        (L5 — one-tap recovery); posts the give-up once in
        chat and once in the log, naming the cause — a
        caption-only give-up is invisible in the sendable log.
        Idempotent: a second call while latched is a no-op.
        Only a human Connect clears the latch (see
        _start_connection, user_started)."""
        if bool(getattr(self, "_reconnect_exhausted", False)):
            return
        self._reconnect_exhausted = True
        self._reconnect_pending = False
        self._reconnect_attempts = 0
        self._was_connected = False
        # R2: the session is over — the liveness watch stops
        # with it (it never fires after the session ended).
        try:
            timer = getattr(self, "_session_liveness_timer", None)
            if timer is not None:
                timer.stop()
        except Exception:  # noqa: BLE001 — the stop must never break give-up
            pass
        self._last_episode_end_at = time.monotonic()
        record = getattr(self, "_last_drop", None)
        if isinstance(record, dict):
            record["pending"] = False
        cause = (record or {}).get("cause") \
            if isinstance(record, dict) else None
        cause = cause or "unknown"
        try:
            worker = getattr(self, "worker", None)
            if worker is not None and worker.is_alive():
                worker.stop()
        except Exception:  # noqa: BLE001 — stopping must never break give-up
            pass
        text = give_up_caption(cause)
        try:
            self._add_system(text)
        except Exception:  # noqa: BLE001 — the chat line must never break give-up
            pass
        try:
            self._log_drop_line(f"[GUI] DROP give-up {cause}")
        except Exception:  # noqa: BLE001 — logging must never break give-up
            pass
        self._connected = False
        try:
            self.state_label.setText("Ready when you are")
        except Exception:  # noqa: BLE001 — presentation must never break give-up
            pass
        self._set_caption(text)
        self._set_pill("Offline")
        self._sync_connect_button()

    def _handle_session_drop(self, fatal: bool):
        """Decide what a dropped session becomes. Returns
        (handled, exhausted): handled — the reconnecting presentation
        replaces the normal error/disconnected one; exhausted — an
        episode just ran out of attempts, so the normal presentation
        runs but carries the 'couldn't reconnect' caption.

        Fix 4: a latched give-up never starts a new episode, and
        the cross-episode budget is checked alongside the
        within-episode attempt count — a zombie that keeps
        reaching LISTENING without ever answering exhausts by
        episodes, not just by attempts."""
        if bool(getattr(self, "_reconnect_exhausted", False)):
            return False, True
        if fatal or self._intentional_disconnect:
            if self._reconnect_pending:
                self._cancel_reconnect()
            return False, False
        if not self._auto_reconnect_enabled():
            if self._reconnect_pending:
                self._cancel_reconnect()
            return False, False
        if not (self._reconnect_pending or self._was_connected):
            return False, False
        if self._reconnect_attempts >= len(_RECONNECT_DELAYS) or \
                int(getattr(self, "_reconnect_episodes_failed", 0) or 0) \
                >= _RECONNECT_EPISODE_BUDGET:
            self._give_up()
            return False, True
        if self._reconnect_timer.isActive():
            # A second drop status for the same fall (an ERROR and a
            # DISCONNECTED often arrive as a pair) — an attempt is
            # already scheduled; just hold the presentation.
            self._show_reconnecting(self._drop_cause())
            return True, False
        self._begin_reconnect()
        return True, False

    def _attempt_reconnect(self):
        """One scheduled retry: start a session exactly the way the
        Connect button does. Its own statuses drive what happens
        next — success ends the episode in _note_session_live, a
        fresh drop schedules the following attempt."""
        if not self._reconnect_pending:
            return
        # The timer that brought us here has fired; make that
        # explicit so the drop handler never mistakes the coming
        # attempt's own failure statuses for a duplicate drop.
        self._reconnect_timer.stop()
        try:
            started = self._start_connection()
        except Exception:  # noqa: BLE001
            started = False
        if started:
            return
        # The start was refused before a new worker existed (e.g.
        # the API key vanished mid-episode, or the dropped session's
        # worker is still alive and wedged — _start_connection's
        # in-flight guard): count it as a failed attempt, never a
        # reason to spin.
        if self._reconnect_attempts >= len(_RECONNECT_DELAYS):
            self._reconnect_give_up()
        else:
            self._begin_reconnect()

    def _reconnect_give_up(self):
        """The attempt-refused path's give-up — one shared
        terminal state with the exhausted-episode path (Fix 4:
        see _give_up). The old body only reset flags and set a
        caption; the shared helper also latches, stops a
        wedged worker, and posts the give-up in chat and log."""
        self._give_up()

    def _cancel_reconnect(self):
        """Forget any reconnect episode: no timer, no attempts, no
        pending flag. Manual Connect/Disconnect and window close
        both land here — a human pressing buttons outranks backoff."""
        timer = getattr(self, "_reconnect_timer", None)
        if timer is not None:
            timer.stop()
        self._reconnect_pending = False
        self._reconnect_attempts = 0

    # ------------------------------------------- session liveness (R2)
    def _liveness_probe_ack(self):
        """Runs ON the worker loop thread: proves the loop is
        still processing callbacks. Only touches one float
        attribute (atomic under the GIL); never raises."""
        try:
            self._liveness_last_ack = time.monotonic()
        except Exception:  # noqa: BLE001 — an ack must never break the loop
            pass

    def _liveness_tick(self):
        """One 5 s tick of the session-liveness watch (R2, the
        Fix 4 residual: a status-less worker death). Runs on
        the GUI thread, never on the worker's own loop — a
        wedged loop cannot watch itself. Detects exactly two
        shapes, both invisible to every status-driven path:
        the worker thread ended with no status, and the
        worker loop stopped answering probes. A busy worker
        is never declared dead: any probe ack, or any status
        at all (which clears the outstanding probe in
        _on_status), proves the loop is alive. Never raises;
        never declares twice for one fall; never fires after
        the session ended."""
        try:
            timer = getattr(self, "_session_liveness_timer", None)
            if bool(getattr(self, "_intentional_disconnect", False)):
                if timer is not None:
                    timer.stop()
                return
            if bool(getattr(self, "_reconnect_exhausted", False)):
                if timer is not None:
                    timer.stop()
                return
            worker = getattr(self, "worker", None)
            # A session is expected while connected, while a
            # reconnect episode is pending, or while a started
            # worker has delivered NO status yet (a fresh
            # connect / a reconnect attempt in flight). It is
            # deliberately NOT _connection_in_flight(): after
            # a decided end (fatal ERROR + DISCONNECTED) the
            # old worker is still alive while it unwinds, and
            # that unwinding worker is not a session — probing
            # or declaring it would misname a decided death.
            session_expected = bool(
                getattr(self, "_connected", False)
                or getattr(self, "_reconnect_pending", False)
                or getattr(self, "_liveness_awaiting_first_status",
                           False))
            if worker is None:
                if not session_expected:
                    if timer is not None:
                        timer.stop()
                return
            try:
                alive = bool(worker.is_alive())
            except Exception:  # noqa: BLE001 — an unreadable worker is a dead one
                alive = False
            if not alive:
                if not session_expected:
                    if timer is not None:
                        timer.stop()
                    return
                self._declare_silent_worker_death(
                    "worker thread ended with no status")
                return
            if bool(getattr(self, "_silent_death_declared", False)):
                return
            if getattr(self, "_reconnect_timer", None) is not None \
                    and self._reconnect_timer.isActive():
                # An attempt is SCHEDULED, not in flight — it
                # has not had its chance yet; this tick is not
                # the judge of it. This check sits AFTER the
                # dead-worker declaration above ON PURPOSE — do
                # not move it before: a dead worker during
                # backoff is still a silent death (the episode
                # keeps its first cause and the death gets its
                # own log line; see test_voice_drop_silent's
                # inside-episode test, which a moved check
                # breaks). The declaration inside a pending
                # episode is budget-safe by design — first cause
                # wins, the duplicate-drop branch holds the
                # presentation, no second schedule — so there is
                # nothing for this check to protect there. Its
                # job is the probe path only: an ALIVE worker
                # whose attempt is still scheduled must not be
                # probed as wedged. (Voice-control batch
                # 2026-11-16, B9: investigated, reorder tried
                # and reverted — the neighbour run caught it.)
                return
            if not session_expected:
                # Alive worker, no session: unwinding after a
                # decided end. Leave it alone.
                return
            loop = getattr(worker, "loop", None)
            if loop is None:
                # The thread just started; its loop does not
                # exist yet. Not wedged — just young.
                return
            try:
                if loop.is_closed():
                    self._declare_silent_worker_death(
                        "worker event loop closed with no status")
                    return
            except Exception:  # noqa: BLE001 — an unreadable loop is probed below
                pass
            now = time.monotonic()
            sent_at = getattr(self, "_liveness_probe_sent_at", None)
            last_ack = getattr(self, "_liveness_last_ack", None)
            if sent_at is not None and last_ack is not None \
                    and last_ack >= sent_at:
                sent_at = None
                self._liveness_probe_sent_at = None
            if sent_at is not None:
                if now - sent_at > _LIVENESS_GRACE_S:
                    self._declare_silent_worker_death(
                        "worker event loop answered no probe for "
                        f"{now - sent_at:.0f}s")
                return
            try:
                loop.call_soon_threadsafe(self._liveness_probe_ack)
                self._liveness_probe_sent_at = now
            except RuntimeError:
                self._declare_silent_worker_death(
                    "worker event loop closed with no status")
            except Exception:  # noqa: BLE001 — a failed probe is retried next tick
                pass
        except Exception:  # noqa: BLE001 — the watch must never break the GUI
            pass

    def _declare_silent_worker_death(self, detail: str) -> None:
        """Declare a status-less worker death (R2) through the
        ONE Fix 4 drop path — same record, same budget, same
        terminal give-up; never a second reconnect path.
        Exactly once per fall (the _silent_death_declared
        flag; a live status or a fresh start clears it).
        The session epoch is bumped here, not by a session
        restart: the session DID end, so Fix 9's pause check
        (epoch moved since run_task entry) engages at the
        next step boundary even before any restart, and a
        stale funnel approval stamped before the death can
        no longer proceed. NEVER calls worker.stop(): the
        live session's stop() clears the saved resumption
        handle, and an unexpected drop must keep its handle
        for the resume-first reconnect — the watchdog
        death's own rule. A wedged-alive worker frees its
        slot through the existing refused-start accounting
        and the give-up path, not through a stop here.
        A death inside a pending episode keeps the
        episode's recorded cause (first cause wins) and the
        silent death is logged as its own line; a death on
        a fresh start that never lived presents an honest
        failure with NO episode and NO budget burn.
        Never raises."""
        try:
            if bool(getattr(self, "_silent_death_declared", False)):
                return
            self._silent_death_declared = True
            record = getattr(self, "_last_drop", None)
            if isinstance(record, dict) and record.get("pending"):
                cause = record.get("cause") or "unknown"
                self._log_drop_line(
                    "[GUI] DROP worker_died_silent: "
                    f"{detail} (attempt failed inside {cause} episode)")
            else:
                self._record_drop(detail, silent=True)
                rec = getattr(self, "_last_drop", None)
                if isinstance(rec, dict):
                    # No episode follows a fresh stall — the
                    # record must not linger as "pending" into
                    # the next connect and misname its failures.
                    rec["pending"] = False
            try:
                from voice_assistant.core.gui_facade import session_epoch as _epoch
                _epoch.bump_epoch("silent worker death")
            except Exception:  # noqa: BLE001 — the pause hook must never break the drop
                pass
            if getattr(self, "_reconnect_pending", False) \
                    or getattr(self, "_was_connected", False):
                self._handle_session_drop(False)
                return
            # A fresh start that never lived: honest failure,
            # no episode, no budget burn, no latch — the user
            # can press Connect immediately.
            timer = getattr(self, "_session_liveness_timer", None)
            if timer is not None:
                timer.stop()
            self._connected = False
            try:
                self.state_label.setText(
                    "The voice worker didn't respond")
            except Exception:  # noqa: BLE001 — presentation must never break the drop
                pass
            self._set_caption("Press Connect to try again.")
            self._set_pill("Offline")
            self._sync_connect_button()
        except Exception:  # noqa: BLE001 — the declaration must never break the GUI
            pass
