"""MainWindow memory mixin: watcher engine, call notices, ambient
context, and the sole closeEvent.

Split from gui/window_memory.py (GUI split #12 batch 2, move only).
"""
import asyncio
import threading
import time
from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import confirm_gate
from voice_assistant.core.gui_facade import live_session
from voice_assistant.core.gui_facade import (
    generate_session_summary,
    save_session_summary,
)
from .window_widgets import (
    _WATCHER_KIND_TO_SETTING,
    _CALL_APP_NAMES,
    _OWN_PROCESS_NAMES,
    _AMBIENT_MATERIAL_KINDS,
    _normalise_process_name,
    _foreground_process_name,
)
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set


# closeEvent (GUI split #12 batch 2): the sole closeEvent among the
# window mixins; it stops the watcher + routine engines. It lives
# in the watchers mixin per the plan and is defined exactly once.

class MemoryWatchersMixin:
    """Part-mixin (GUI split #12): method bodies moved verbatim
    from gui/window_memory.py; behaviour is unchanged.
    """

    def _init_watchers(self):
        """Build the watcher engine (core.watchers, imported lazily
        and guarded like the routine scheduler) and start it when
        notices are on. If the module is unavailable the feature
        simply stays off — the window never crashes over a watcher."""
        self._watcher_engine = None
        # v5.65 (Wave 10 — PF-11): safe mode runs no watchers.
        try:
            from voice_assistant.core.gui_facade import w10_wiring as _w10
            from voice_assistant.core.gui_facade import DATA_DIR as _DD
            if _w10.is_safe_mode_active(data_dir=str(_DD)):
                print("[Watchers] safe mode — watchers off")
                return
        except Exception:  # noqa: BLE001 — guard failure never starts
            pass
        try:
            from voice_assistant.core.gui_facade import WatcherEngine
        except Exception:  # noqa: BLE001
            print("[Watchers] core.watchers unavailable — notices off")
            return
        try:
            from voice_assistant.core.gui_facade import w8_wiring as _w8
            _sleeptime_hook = _w8.maybe_sleeptime_on_idle
        except Exception:  # noqa: BLE001 — sleeptime off, watchers still run
            _sleeptime_hook = None
        try:
            engine = WatcherEngine(
                on_event=lambda kind, message: self.signals.watcher_event.emit(
                    kind, message),
                # Sleeptime idle trigger (2026-11-03): the
                # watcher's away episode arms one sleeptime pass
                # through the existing idle gate. The hook is
                # fail-closed: if w8_wiring cannot be imported
                # the engine still runs, sleeptime just stays
                # off. Safe mode / watchers-off already gate the
                # whole engine above, so the hook can only fire
                # when the user has watchers enabled.
                sleeptime_hook=_sleeptime_hook,
                # v5.45 (N12/N13 wiring): the Settings toggles the
                # NOSE cluster persisted now reach the engine as its
                # gate probes, read live from QSettings on every use.
                # Ambient light stays fail-closed (default False);
                # foreground awareness defaults on, as its Settings
                # checkbox does.
                probes={
                    "ambient_light_enabled": lambda: bool(
                        qsettings_get("watchers/ambient_light", settings=self.settings)),
                    "foreground_enabled": lambda: bool(
                        qsettings_get("watchers/foreground_app", settings=self.settings)),
                })
            self._watcher_engine = engine
            if bool(qsettings_get("watchers/enabled", settings=self.settings)):
                engine.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[Watchers] engine unavailable: {exc}")
            self._watcher_engine = None

    def _on_watcher_event(self, kind: str, message: str):
        """A watcher noticed something (engine thread → Qt signal).
        Chat always gets the message as one calm system line — the
        messages arrive as finished sentences in VYUK's voice. Speech
        only while she's connected, and throttled (see below). A
        watcher must never crash the window: everything is guarded.

        N11 (v5.43): the event is written to the senses timeline
        BEFORE any Settings gate is consulted — the toggles govern
        only the telling (chat + speech), never the record, so a
        switched-off kind still lands in history."""
        try:
            kind = str(kind or "")
            message = str(message or "").strip()
            if not message:
                return
            try:
                from voice_assistant.core.gui_facade import senses_log
                senses_log.append_event(kind, message)
            except Exception:  # noqa: BLE001 — the timeline is a diary, not a dependency
                pass
            # N4 (v5.44): a material watcher transition refreshes
            # the brain's ambient context — the telling gates below
            # do not apply (context is not a mention). Guarded like
            # everything here; a missing seam degrades silently.
            try:
                _refresh = getattr(self, "_refresh_ambient_context", None)
                if _refresh is not None:
                    _refresh(kind)
            except Exception:  # noqa: BLE001
                pass
            if not bool(qsettings_get("watchers/enabled", settings=self.settings)):
                return
            suffix = _WATCHER_KIND_TO_SETTING.get(kind)
            if suffix is not None and not bool(
                    qsettings_get(f"watchers/{suffix}", settings=self.settings)):
                return  # this kind is muted — no chat, no speech (already logged above)
            self._add_system(message)
            self._maybe_speak_watcher_notice(kind, message)
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------- N1 (v5.44): call-aware gate

    def _foreground_call_app(self):
        """The foreground process name when it is a known call
        app (see _CALL_APP_NAMES), else None. A per-instance
        _foreground_name_override (tests) wins over the real
        foreground probe."""
        try:
            override = getattr(self, "_foreground_name_override", None)
            name = (_normalise_process_name(override)
                    if override is not None
                    else _foreground_process_name())
            if name in _CALL_APP_NAMES:
                return name
        except Exception:  # noqa: BLE001
            pass
        # N13 (v5.45): additional signal — the watcher engine's
        # per-tick foreground reading (core.watchers' cache,
        # names only). Either source naming a call app is
        # enough; reading the cache runs no probe.
        try:
            from voice_assistant.core.gui_facade import watchers

            return watchers.current_foreground_call_app()
        except Exception:  # noqa: BLE001
            return None

    def _other_mic_holders(self) -> list:
        """Apps OTHER than VYUK currently holding the microphone,
        from the watchers' CACHED privacy state (core.watchers
        current_privacy_state — the engine's last probe). Reading
        it runs no probe and no registry access."""
        try:
            override = getattr(self, "_mic_holders_override", None)
            if override is not None:
                holders = [str(h) for h in override]
            else:
                from voice_assistant.core.gui_facade import watchers

                holders = list(watchers.current_mic_users())
            return [h for h in holders
                    if _normalise_process_name(h) not in _OWN_PROCESS_NAMES]
        except Exception:  # noqa: BLE001
            return []

    def _in_call(self) -> bool:
        """True while the user looks to be in a call: a call app
        is in the foreground, OR another app currently holds the
        microphone (the privacy watcher's cached state). Either
        signal alone is enough — a call minimised to the tray
        still holds the mic; a call app in front is in a call
        even before its mic flag lands."""
        try:
            if self._foreground_call_app() is not None:
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            return bool(self._other_mic_holders())
        except Exception:  # noqa: BLE001
            return False

    def _held_call_notices(self) -> list:
        """The held-notice queue, created lazily so a partially
        built window (or a stub) still gates safely."""
        queue = getattr(self, "_call_held_notices", None)
        if queue is None:
            queue = self._call_held_notices = []
        return queue

    def _queue_call_notice(self, kind: str, message: str) -> None:
        """Hold one notice for the post-call digest. Exact repeats
        already queued are not held twice, and the queue is capped
        (oldest non-critical drops first) so a long call cannot
        build an endless digest."""
        queue = self._held_call_notices()
        entry = (str(kind or ""), str(message or ""))
        if entry in queue:
            return
        queue.append(entry)
        while len(queue) > 10:
            drop = next((i for i, (k, _m) in enumerate(queue)
                         if k != "battery_critical"), 0)
            queue.pop(drop)

    @staticmethod
    def _compose_call_digest(queue) -> str:
        """The held notices as ONE speakable digest sentence,
        composed like the welcome-back digest (N3): the held
        sentences, in arrival order, joined into a single
        "While you were in your call: …" line."""
        parts = []
        for _kind, message in queue or []:
            text = str(message or "").strip()
            if text:
                parts.append(text.rstrip("."))
        if not parts:
            return ""
        if len(parts) == 1:
            body = parts[0]
        elif len(parts) == 2:
            body = f"{parts[0]}, and {parts[1]}"
        else:
            body = ", ".join(parts[:-1]) + f", and {parts[-1]}"
        return f"While you were in your call: {body}."

    def _deliver_call_digest(self, now=None) -> bool:
        """Inject the held notices as ONE digest through the live
        session. Caller has already established: connected, not
        in a call, queue non-empty, throttle satisfied (or a
        critical notice is part of the delivery). Returns True
        iff the digest was sent (queue cleared)."""
        try:
            queue = self._held_call_notices()
            if not queue:
                return False
            digest = self._compose_call_digest(queue)
            if not digest:
                queue.clear()
                return False
            self._last_watcher_spoken = (
                time.monotonic() if now is None else now)
            self.earcons.alert()
            notice = (
                f"[Notice, not from the user: {digest} Briefly tell the "
                "user this in your own words, warmly and shortly.]")
            self._note_self_sent(notice)
            asyncio.run_coroutine_threadsafe(
                live_session.send_text(notice),
                self.worker.loop)
            queue.clear()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _flush_held_call_notices(self):
        """Deliver the held digest once the call has cleared —
        called by the 30 s digest timer and by the speech path.
        Respects the 90 s speech throttle for the digest itself,
        unless a held battery_critical is waiting (it already
        earcon'd during the call; its words should not wait out
        a throttle window opened by an unrelated notice)."""
        try:
            if not self._held_call_notices():
                return
            connected = bool(getattr(self, "_connected", False)) or bool(
                getattr(live_session, "is_connected", False))
            if not connected:
                return
            if not (self.worker and self.worker.loop):
                return
            if self._in_call():
                return
            now = time.monotonic()
            has_critical = any(
                k == "battery_critical" for k, _m in self._held_call_notices())
            if not has_critical and now - self._last_watcher_spoken < 90.0:
                return
            self._deliver_call_digest(now)
        except Exception:  # noqa: BLE001
            pass

    def _refresh_ambient_context(self, kind=None):
        """N4 (v5.44): tell the brain its ambient context changed.
        Only MATERIAL transitions call through (see
        _AMBIENT_MATERIAL_KINDS); kind=None means the caller has
        already decided the change is material. The seam —
        refresh_ambient_context() on the brain session — is
        called defensively: a brain without it (or a refresh
        that fails) degrades silently."""
        try:
            if kind is not None and str(kind) not in _AMBIENT_MATERIAL_KINDS:
                return
            brain = getattr(self, "_brain", None)
            if brain is None:
                brain = live_session
            refresh = getattr(brain, "refresh_ambient_context", None)
            if callable(refresh):
                refresh()
        except Exception:  # noqa: BLE001
            pass

    def _maybe_speak_watcher_notice(self, kind: str, message: str):
        """Speak a notice through the live session — the same
        worker-loop send _on_routine_due uses — at most once per
        90 s, so a flaky network can't make her nag. A critical
        battery always speaks, throttle or not. Disconnected: the
        chat line above is all there is.

        N1 (v5.44): while the user is in a call (see _in_call)
        she does NOT talk over it — non-critical notices are
        held and delivered afterwards as one digest. A critical
        battery during a call still lands in chat immediately
        (the caller posted it before this runs), earns exactly
        one alert earcon, and its words join the digest — no
        speech crosses the call."""
        try:
            connected = bool(getattr(self, "_connected", False)) or bool(
                getattr(live_session, "is_connected", False))
            if not connected:
                return
            if not (self.worker and self.worker.loop):
                return
            now = time.monotonic()
            if self._in_call():
                if kind == "battery_critical":
                    # Chat already has the line (the caller posted
                    # it); one short earcon, no speech, and the
                    # words wait for the post-call digest.
                    try:
                        self.earcons.alert()
                    except Exception:  # noqa: BLE001
                        pass
                    self._queue_call_notice(kind, message)
                else:
                    self._queue_call_notice(kind, message)
                return
            if self._held_call_notices():
                # The call has cleared and held notices are
                # waiting: this notice joins them and the whole
                # queue goes out as ONE digest — or keeps waiting
                # while the digest's own throttle window is shut
                # (a critical notice forces the delivery).
                self._queue_call_notice(kind, message)
                has_critical = any(
                    k == "battery_critical"
                    for k, _m in self._held_call_notices())
                if has_critical or now - self._last_watcher_spoken >= 90.0:
                    self._deliver_call_digest(now)
                return
            if kind != "battery_critical" and now - self._last_watcher_spoken < 90.0:
                return
            self._last_watcher_spoken = now
            self.earcons.alert()
            notice = (
                f"[Notice, not from the user: {message} Briefly tell the "
                "user this in your own words, warmly and shortly.]")
            self._note_self_sent(notice)
            asyncio.run_coroutine_threadsafe(
                live_session.send_text(notice),
                self.worker.loop)
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event):
        # Field-check fix #3 (U7): the Doctor checks worker is a
        # QThread that outlives the window — closing while it runs
        # used to abort the process (SIGABRT, "QThread: Destroyed
        # while thread '' is still running"). Stop it first, before
        # any other teardown touches the page; stop_checks() never
        # raises, and this whole call is guarded anyway so closing
        # can never break on it.
        try:
            page = getattr(self, "doctor_page", None)
            if page is not None:
                page.stop_checks()
        except Exception:  # noqa: BLE001 — closing must never break
            pass
        # UI Phase 6 (D-02): the mic level arc is a child widget
        # with its own native window once shown — a window merely
        # closed (not deleted) leaves the arc's native window to
        # outlive the app teardown, and the process segfaults at
        # interpreter shutdown (repro: leg35 solo, rc=139; the
        # probe deleting the arc while the app is alive exits
        # clean, rc=0). Destroy it here, while the app is alive,
        # like the other sinks below — the window is closing, so
        # the ring is never needed again.
        try:
            arc = getattr(self, "mic_level_arc", None)
            if arc is not None:
                arc.deleteLater()
                self.mic_level_arc = None
        except Exception:  # noqa: BLE001 — closing must never break
            pass
        # EYES E6 (v5.45): watch mode never outlives the window —
        # stopping the engine also drops its in-memory baseline.
        self._disable_watch_mode("window closing")
        # v5.48 (programme item 21): closing ends the capture
        # session too (approvals cleared) and drops the capture
        # indicator listener, so nothing outlives the window.
        try:
            from voice_assistant.core.gui_facade import capture_guard

            capture_guard.reset_session()
            capture_guard.clear_indicator_listener()
        except Exception:  # noqa: BLE001
            pass
        # Feels-alive pack (v2.5): closing is the most intentional
        # disconnect there is — no auto-reconnect may outlive it.
        self._intentional_disconnect = True
        self._cancel_reconnect()
        self._stop_voice_preview()
        qsettings_set("geometry", self.saveGeometry(), settings=self.settings)
        if self.tray is not None:
            self.tray.hide()
        audio_manager.stop()
        # GAPS-28 (G-V8): the background services stop through
        # the host that started them. The audio manager is not
        # a host service (it starts with the voice session), so
        # it stops here, directly, as before.
        _host = getattr(self, "service_host", None)
        if _host is not None:
            _host.stop()
        # Hardening (v4.3): stop the failsafe heartbeat timer
        # and drop the confirm handler so nothing outlives the
        # window. (The watchdog itself stops through the host.)
        try:
            timer = getattr(self, "_failsafe_timer", None)
            if timer is not None:
                timer.stop()
            confirm_gate.clear_handler()
            # v5.42 (Truth, E1): drop the frame sink with the
            # session it pointed at — no window, no viewer.
            try:
                from voice_assistant.core.gui_facade import set_frame_sink  # Phase 4 S1b INTENTIONAL: handlers split to funnel

                set_frame_sink(None)
            except Exception:  # noqa: BLE001
                pass
            # R8-6 (yasb port): drop the strip sink with the window
            # it pointed at — no window, no strip.
            try:
                from voice_assistant.core.gui_facade import set_status_strip_sink

                set_status_strip_sink(None)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        routine_scheduler = getattr(self, "_routine_scheduler", None)
        if routine_scheduler is not None:
            try:
                routine_scheduler.stop()
            except Exception:  # noqa: BLE001
                pass
        watcher_engine = getattr(self, "_watcher_engine", None)
        if watcher_engine is not None:
            try:
                watcher_engine.stop()
            except Exception:  # noqa: BLE001
                pass

        if len(self._transcript_history) >= 4:
            history_copy = list(self._transcript_history)
            client_ref = live_session.client
            model_ref = config.model_name
            # B5 (v5.45): summarise with the ACTIVE brain, not only
            # Gemini — the proxy builds a summariser for whichever
            # engine ran (Groq / local / Gemini); None (e.g. the
            # lite session) keeps the old no-summary behaviour.
            summariser_ref = None
            try:
                maker = getattr(live_session, "make_summariser", None)
                summariser_ref = maker() if callable(maker) else None
            except Exception:  # noqa: BLE001
                summariser_ref = None

            def _summarize_and_save():
                try:
                    summary = asyncio.run(generate_session_summary(history_copy, client_ref, model_ref, summariser=summariser_ref))
                    if summary:
                        save_session_summary(summary)
                except Exception as e:  # noqa: BLE001
                    print(f"[Session Summary Error]: {e}")

            threading.Thread(target=_summarize_and_save, daemon=True).start()

        if self.worker:
            self.worker.stop()
        event.accept()