"""MainWindow session mixin: watch mode + briefing playback.

Split from gui/window_session.py (GUI split #11 batch 2, move only).
"""

import asyncio
import time

from voice_assistant.core.gui_facade import live_session  # Phase 3 B2h: brain hub moved to core/brain/brain.py
from voice_assistant.core.gui_facade import vision_manager
from voice_assistant.core.gui_facade import qsettings_get


class SessionWatchMixin:
    """Watch mode + briefing playback (GUI split #11 batch 2)."""
    # ------------------------------------------------------------ #
    # EYES E6 (v5.45): watch-my-screen — window side. The mode's
    # state lives on core/desktop/screen_watch's switchboard (GUI-owned);
    # this window owns the engine, the timer, the pill and the
    # brain delivery. See core/desktop/screen_watch.py's header for the
    # design, including why voice control rides the user's own
    # transcript (no registry tool exists for it, and none was
    # invented — the registry stays at 477).
    # ------------------------------------------------------------ #

    def _on_watch_control_changed(self, enabled: bool):
        """Switchboard listener (core.desktop.screen_watch.subscribe_watch,
        registered in __init__): every surface that flips the mode
        — Settings, voice, session end — lands here so the pill,
        the checkbox and the engine can never disagree."""
        self._apply_watch_state(bool(enabled))

    def _set_watch_mode(self, enabled: bool, source: str = ""):
        """The one door into watch mode. Flips the switchboard
        (which notifies _on_watch_control_changed) and applies
        the state here too, so a no-change flip still re-syncs
        the UI. Announces the change in chat when it is one."""
        try:
            from voice_assistant.core.gui_facade import screen_watch as _sw  # Phase 3 D3: moved to core/desktop/

            changed = _sw.set_watch_enabled(bool(enabled), source)
        except Exception:  # noqa: BLE001 — watch mode is an extra
            changed = False
        self._apply_watch_state(bool(enabled))
        if changed:
            self._add_system(
                "Watching your screen — I'll mention what changes. "
                "Say “stop watching” any time."
                if enabled else
                "Stopped watching your screen.")

    def _disable_watch_mode(self, source: str):
        """Auto-off (session end / disconnect / window closing):
        silent when the mode is already off."""
        try:
            from voice_assistant.core.gui_facade import screen_watch as _sw  # Phase 3 D3: moved to core/desktop/

            if _sw.watch_enabled():
                self._set_watch_mode(False, source)
        except Exception:  # noqa: BLE001
            pass

    def _apply_watch_state(self, enabled: bool):
        """Renders + runs one watch state: pill, Settings
        checkbox, engine, timer. Idempotent and fully guarded —
        it is called from status handlers and closeEvent too."""
        try:
            self._sync_watch_pill(enabled)
            chk = getattr(self, "chk_watch_screen", None)
            if chk is not None and chk.isChecked() != bool(enabled):
                chk.blockSignals(True)
                chk.setChecked(bool(enabled))
                chk.blockSignals(False)
            if enabled:
                if self._screen_watcher is None:
                    from voice_assistant.core.gui_facade import (
                        ScreenWatcher,
                    )  # Phase 3 D3: moved to core/desktop/

                    self._screen_watcher = ScreenWatcher(
                        capture=self._watch_capture,
                        deliver=self._deliver_watch_frame)
                self._screen_watcher.start()
                self._screen_watch_timer.start()
                self._announce_watch_start()
            else:
                self._screen_watch_timer.stop()
                if self._screen_watcher is not None:
                    self._screen_watcher.stop()
        except Exception as exc:  # noqa: BLE001 — never break the window
            print(f"[WatchScreen] apply state failed: {exc}")

    def _sync_watch_pill(self, enabled: bool | None = None):
        """The pill renders core.desktop.screen_watch.pill_state: visible
        exactly while watching, always the same words.

        v5.48 (programme item 21): while the watcher's last tick
        was blocked by a sensitive foreground window, the pill
        says the watch is PAUSED (not silently "watching"), and
        the block reason rides the pill's tooltip and the status
        line's tooltip."""
        pill = getattr(self, "watch_pill", None)
        if pill is None:
            return
        try:
            from voice_assistant.core.gui_facade import screen_watch as _sw  # Phase 3 D3: moved to core/desktop/

            visible, text = _sw.pill_state(enabled)
        except Exception:  # noqa: BLE001
            visible, text = False, ""
        pill.setText(f"👁  {text}" if visible else "")
        pill.setVisible(visible)
        pill.setToolTip("")
        if visible:
            watcher = getattr(self, "_screen_watcher", None)
            if watcher is not None and getattr(
                    watcher, "last_event", None) == "blocked-sensitive":
                pill.setText(
                    "👁  Watching your screen — paused "
                    "(sensitive window in front)")
                reason = getattr(watcher, "last_block_reason", None)
                if reason:
                    pill.setToolTip(str(reason))
                    state_label = getattr(self, "state_label", None)
                    if state_label is not None:
                        state_label.setToolTip(str(reason))

    def _on_watch_screen_toggled(self, enabled: bool):
        """The Settings switch (Screen watching card)."""
        self._set_watch_mode(bool(enabled), "settings")

    def _maybe_watch_command(self, text: str) -> bool:
        """Voice control for watch mode: an explicit watch phrase
        in the user's OWN utterance (this runs on the _on_transcript
        user surface — every genuine utterance passes there exactly
        once). Bracketed VYUK injections and the window's own
        self-sent echoes are never the user asking, so they are
        skipped. Fully guarded: a recogniser hiccup must never
        break the transcript path.

        Returns True when the utterance was CONSUMED as a watch
        command (a disarm that landed, or the v5.48 item-21
        honest reply to an arm phrase) — the caller then does not
        pass it on as a command or learn from it."""
        try:
            if not text or text.lstrip().startswith("["):
                return False
            pending = getattr(self, "_self_sent_texts", []) or []
            now = time.monotonic()
            if any(t == text and exp > now for (t, exp) in pending):
                return False  # our own injected text echoing back
            from voice_assistant.core.gui_facade import screen_watch as _sw  # Phase 3 D3: moved to core/desktop/

            verdict = _sw.match_watch_command(text)
            if verdict is None:
                # v5.48 (programme item 21): voice can no longer
                # ARM watch mode (match_watch_command returns None
                # for arm phrases) — but the user asked out loud,
                # so answer honestly instead of letting the words
                # ride on to the brain as if they were a command
                # she could obey, when watch is off and the
                # utterance matches an arm phrase.
                if not _sw.watch_enabled():
                    lowered = " ".join(str(text).lower().split())
                    if any(phrase in lowered
                           for phrase in _sw._START_PHRASES):
                        self._add_system(
                            "I can't start watching your screen by voice — turn it on in Settings → Screen watching. You can always say 'stop watching' to turn it off.")
                        return True
                return False
            if verdict == _sw.watch_enabled():
                return False  # already in the asked-for state
            self._set_watch_mode(verdict, "voice")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _announce_watch_start(self):
        """One bracketed notice so the brain knows frames will
        arrive and what to do with them (the framing text brains
        can't take per-frame rides here instead): sent once per
        enable, only while connected."""
        worker = getattr(self, "worker", None)
        loop = getattr(worker, "loop", None) if worker is not None else None
        if not (loop and live_session.is_connected):
            return
        notice = (
            "[Notice, not from the user: the user has turned on "
            "watch-my-screen. Screen frames will now arrive from "
            "time to time. For each one, briefly say what changed "
            "on the screen since the last frame you saw; if "
            "nothing meaningful changed, say so in a few words.]")
        try:
            self._note_self_sent(notice)
            asyncio.run_coroutine_threadsafe(
                live_session.send_text(notice), loop)
        except Exception:  # noqa: BLE001 — the mode works without it
            pass

    def _watch_capture(self):
        """The engine's capture seam: vision_manager.capture_screen
        (E7-cropped by default), or None when the grab fails."""
        try:
            return vision_manager.capture_screen()
        except Exception:  # noqa: BLE001 — a failed grab skips a poll
            return None

    def _on_screen_watch_tick(self):
        """1 s timer: a dead session ends the mode (belt to the
        _on_status DISCONNECTED hook's braces); a live one ticks
        the engine, which throttles itself."""
        try:
            from voice_assistant.core.gui_facade import screen_watch as _sw  # Phase 3 D3: moved to core/desktop/

            if not _sw.watch_enabled():
                self._screen_watch_timer.stop()
                return
            worker = getattr(self, "worker", None)
            loop = getattr(worker, "loop", None) \
                if worker is not None else None
            if not (loop and live_session.is_connected):
                self._disable_watch_mode("session end")
                return
            if self._screen_watcher is not None:
                self._screen_watcher.tick()
                # v5.48 (programme item 21): a tick can change the
                # paused state (blocked-sensitive) — re-render the
                # pill so the pause / resume shows immediately.
                self._sync_watch_pill()
        except Exception as exc:  # noqa: BLE001
            print(f"[WatchScreen] tick failed: {exc}")

    def _deliver_watch_frame(self, image_bytes: bytes,
                             mime_type: str, framing: str):
        """The engine's deliver seam: one brain turn about a
        changed frame, split by brain the way _deliver_tool_frame
        splits it —
          * Gemini Live (native vision): the frame via
            send_image_frame, then the framing as a bracketed
            notice (its send_image_frame takes no text);
          * Groq: the inner session's send_image_frame with the
            framing as its question (E5a — vision turn, OCR
            degrade inside);
          * every other brain: the session's own send_image_frame
            (the local path describes the frame — OCR, or the VLM
            after E5b); the per-enable notice already gave it the
            standing "say what changed" instruction."""
        worker = getattr(self, "worker", None)
        loop = getattr(worker, "loop", None) if worker is not None else None
        if not (loop and live_session.is_connected):
            return
        # v5.46 (programme item 2, D-C1): a watch frame handed to a
        # brain is untrusted image content — note it exactly as the
        # sibling delivery paths do (_send_screen_capture and
        # _deliver_tool_frame both call note_image_frame). Before
        # this, watch frames reached native-vision brains with NO
        # note at all (text brains were covered only indirectly,
        # by the OCR wrap inside _describe_frame); now every watch
        # frame records the same provenance — and since v5.46
        # note_image_frame sets a SOFT taint, a watching session
        # can still navigate inside an approved screen scope
        # while high-impact tools keep asking every time.
        try:
            from voice_assistant.core.gui_facade import note_image_frame
            note_image_frame()
        except Exception:  # noqa: BLE001 — bookkeeping never breaks delivery
            pass
        # GAPS-28 (G-V5): the frame routing and the engine name
        # go through the session's public seams — no private reads.
        try:
            asyncio.run_coroutine_threadsafe(
                live_session.send_watch_frame(image_bytes, mime_type,
                                              framing=framing), loop)
            if live_session.engine_name == "gemini":
                notice = f"[Notice, not from the user: {framing}]"
                self._note_self_sent(notice)
                asyncio.run_coroutine_threadsafe(
                    live_session.send_text(notice), loop)
        except Exception as exc:  # noqa: BLE001 — a lost frame is a lost frame
            print(f"[WatchScreen] deliver failed: {exc}")

    def _play_briefing(self):
        if self.worker and self.worker.loop and live_session.is_connected:
            # v5.17 (Mouth M18): the briefing voice preset — while
            # it's on, the briefing is spoken at the preset pace.
            # The override lives on the session and clears on the
            # user's next genuine words (see _on_transcript).
            try:
                if bool(qsettings_get("voice/briefing_preset", settings=self.settings)):
                    pace = float(qsettings_get("voice/briefing_speed", settings=self.settings))
                else:
                    pace = None
                live_session.set_speech_speed_override(pace)
            except Exception:  # noqa: BLE001
                pass
            # GAPS-28 (G-V9): the button drives the real digest
            # pipeline (core/scheduled_agents.py run_digest) instead
            # of sending a literal prompt. The build is local and
            # fast — no summariser provider is registered in the
            # app today, so run_digest is extractive-only — and it
            # runs on the GUI thread; if a provider is ever
            # registered, this must move off the GUI thread. The
            # digest is STORED by the pipeline (the 7am job stores
            # the same shape), so a failed fresh build falls back
            # to the last stored digest, and only when there is
            # none does the button say so honestly. The old
            # Google addendum ("check my today's schedule and
            # unread email") is subsumed: the digest's own inputs
            # cover activity, and no calendar function is passed
            # by any caller of the pipeline today.
            try:
                from voice_assistant.core.gui_facade import (
                    scheduled_agents as _agents)
                result = _agents.run_digest(speak=False)
                record = (result.get("digest")
                          if isinstance(result, dict) else None)
                if not (isinstance(result, dict) and result.get("ok")
                        and record):
                    record = _agents.load_last_digest()
                text = str((record or {}).get("text") or "")
                if not text.strip():
                    self._notify("I couldn't build your briefing — "
                                 "there's nothing to summarise yet.")
                    return
            except Exception as exc:  # noqa: BLE001 — a failed build never breaks the button
                print(f"[Briefing] digest build failed: {exc}")
                self._notify("I couldn't build your briefing just now.")
                return
            self._note_self_sent(text)
            asyncio.run_coroutine_threadsafe(
                live_session.send_text(text), self.worker.loop)
            self.earcons.sent()
            self._add_system("Playing your morning briefing…")
        else:
            self._notify("Connect first, then I'll play your briefing.")
