"""MainWindow services mixin: timers, plugin trust, failsafe (part 2 of 4).

Split from gui/window_services.py (GUI split #10 batch 1, move only).
Timers/services start + plugin-trust loop + failsafe. Bodies moved
verbatim; behaviour is unchanged.
"""

import threading
from pathlib import Path
from PySide6.QtCore import QTimer
from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import clipboard_monitor
from voice_assistant.core.gui_facade import confirm_gate, failsafe, plan_approval
from voice_assistant.core.gui_facade import hotkey_daemon
from voice_assistant.core.gui_facade import live_session
from voice_assistant.core.gui_facade import scheduler
from voice_assistant.core.gui_facade import wake_word_detector
from .window_session_status import _LIVENESS_TICK_S
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set

class ServicesTrustMixin:
    """Timers / plugin-trust / failsafe mixin (GUI split #10 batch 1).

    Timers/services start, plugin-trust loop, failsafe.
    """
    # ------------------------------------------------------------ services
    def _setup_timers(self):
        # GAPS-30: single-shot, re-armed in _on_audio_timer
        # (see gui/window_services_speech.py). The 25 ms timer
        # used to repeat: a tick slower than the interval
        # (tracing, a loaded machine) queued timeout events
        # faster than they were consumed, so the event queue
        # never drained and processEvents() never returned
        # (test_journey_gui_path stuck under tracing; GAPS-28
        # r1 hung on the same call site untraced).
        self.audio_timer = QTimer(self)
        self.audio_timer.setSingleShot(True)
        self.audio_timer.timeout.connect(self._on_audio_timer)
        self.audio_timer.start(25)
        # Deaf-session watch (v5.37): created here, but only runs
        # while a backend session is live (started in
        # _start_connection, self-stops when the worker dies).
        self._mic_watch_timer = QTimer(self)
        self._mic_watch_timer.setInterval(2000)
        self._mic_watch_timer.timeout.connect(self._mic_watch_tick)
        # Session-liveness watch (R2, 2026-11-06): created here,
        # but only runs while a backend session is live or
        # starting (started in _start_connection, self-stops
        # when no session is expected). Same ownership shape
        # as the mic watch above: one timer, one owner.
        self._session_liveness_timer = QTimer(self)
        self._session_liveness_timer.setInterval(
            int(_LIVENESS_TICK_S * 1000))
        self._session_liveness_timer.timeout.connect(
            self._liveness_tick)
        self._refresh_mic_health()
        self._refresh_memories()
        self._refresh_reminders()

    def _start_background_services(self):
        """Attach the window's service callbacks, then start the
        services through the service host (GAPS-28, G-V8).

        The host (core/service_host.py, constructed by main.py
        and passed in as ``service_host=``) owns the services'
        lifecycle — start order is the old order (scheduler,
        scheduled-agents engine, hotkey daemon, clipboard
        monitor, failsafe watchdog, wake-word detector). A
        service that cannot start is reported, never fatal:
        the app must boot without any one of them. The failsafe
        heartbeat QTimer stays window-side (it beats
        ``failsafe.beat`` from the GUI thread, which is exactly
        what the watchdog exists to prove)."""
        # GAPS-26 (S-V4): migrate any retired scheduler-side briefing
        # record to the single presence briefing path BEFORE the
        # scheduler loads its store — the migration preserves the
        # hour the user had chosen. It only applies when the presence
        # briefing is not already enabled: a newer explicit choice
        # wins over the migrated record. The import is LAZY, inside
        # this guarded block, not module-level: the migration
        # resolves through the scheduler module, which the GUI
        # journey/review suites stub (voice_assistant.core.scheduler
        # carries only .scheduler there) — a module-level facade
        # import of it broke MainWindow construction in those
        # suites (neighbour r1: journey live_qt error + two
        # setUpClass skips, 15 tests lost). Boot-only and guarded
        # either way; lazy keeps the import failure inside the
        # "migration must not break boot" guard where it belongs.
        try:
            from voice_assistant.core.gui_facade import \
                migrate_legacy_briefing_records
            _migrated = migrate_legacy_briefing_records()
            if _migrated is not None and not bool(
                    qsettings_get("presence/briefing_enabled",
                                  settings=self.settings)):
                _hour, _minute = _migrated
                qsettings_set("presence/briefing_enabled", True,
                              settings=self.settings)
                qsettings_set("presence/briefing_time",
                              f"{_hour:02d}:{_minute:02d}",
                              settings=self.settings)
        except Exception as exc:  # noqa: BLE001 — migration must not break boot
            print(f"[BackgroundServices] briefing migration skipped: {exc}")
        # GAPS-28 (G-V3/G-V4): the window subscribes to the typed
        # state bus — a chat-set appearance key applies live.
        # The bus singleton is created on this (GUI) thread, so
        # the drain runs here too. Guarded: appearance must never
        # break boot.
        try:
            from voice_assistant.core.gui_facade import state_bus as _bus_mod
            _bus_mod.get_state_bus().subscribe(
                _bus_mod.SettingsChanged, self._on_settings_changed)
        except Exception as exc:  # noqa: BLE001 — appearance never breaks boot
            print(f"[BackgroundServices] state-bus subscribe skipped: {exc}")
        # Callbacks first: every handler points at this window
        # and must be attached before any service starts.
        scheduler.on_trigger = lambda label, msg: self.signals.scheduler_trigger.emit(label, msg)

        hotkey_daemon.on_ptt_start = self._on_ptt_pressed
        hotkey_daemon.on_ptt_stop = self._on_ptt_released
        hotkey_daemon.on_screen_capture = self._send_screen_capture
        hotkey_daemon.on_toggle_mute = self._toggle_mute
        if bool(qsettings_get("presence/summon_hotkey", settings=self.settings)):
            hotkey_daemon.on_summon = lambda: self.signals.summon_requested.emit()
        self.signals.summon_requested.connect(self._toggle_summon)
        # v5.42 (H14): the panic combo (Ctrl+Alt+Break) reaches the
        # window through the same signal bridge as summon — the
        # daemon thread never touches widgets or the session itself.
        hotkey_daemon.on_panic = lambda: self.signals.panic_requested.emit()
        self.signals.panic_requested.connect(self._on_panic_hotkey)

        clipboard_monitor.on_new_content = lambda text: self.signals.clipboard_content.emit(text)

        # Hardening (v4.3): executor-level human confirmation for
        # critical tools + the input-control failsafe watchdog. The
        # confirm handler is called from brain threads; it marshals
        # the popup onto the GUI thread via confirm_requested and
        # blocks on an Event (confirm_gate adds its own 60 s
        # default-deny timeout around it).
        self._confirm_lock = threading.Lock()
        self._confirm_event = None
        self._confirm_result = False
        self.signals.confirm_requested.connect(self._on_confirm_requested)
        confirm_gate.register_handler(self._confirm_gate_handler)
        # Fix 9 (voice plan approval): the voice routine's plan
        # ask marshals to the GUI thread the same way as the
        # confirm gate — one handler, serialized, dialog on
        # the GUI thread, 55 s handler budget / 50 s dialog.
        self._plan_approval_lock = threading.Lock()
        self._plan_approval_event = None
        self._plan_approval_result = []
        self._plan_approval_outcome = "ask_each_time"
        self.signals.plan_approval_requested.connect(
            self._on_plan_approval_requested)
        plan_approval.register_plan_handler(self._plan_approval_handler)
        self.signals.failsafe_tripped.connect(self._on_failsafe_tripped)
        failsafe.set_trip_callback(
            lambda reason: self.signals.failsafe_tripped.emit(str(reason)))

        wake_word_detector.on_wake = lambda: self.signals.wake_word_triggered.emit()

        # GAPS-28 (G-V8): start through the host. A window built
        # without one (tests, old call sites) builds its own.
        host = getattr(self, "service_host", None)
        if host is None:
            from voice_assistant.core.gui_facade import (
                service_host as _ServiceHost)
            host = _ServiceHost()
            self.service_host = host
        _display = {"engine": "scheduled-agents engine"}
        for _name, _result in host.start().items():
            if not _result.get("ok"):
                print("[BackgroundServices] %s did not start: %s"
                      % (_display.get(_name, _name),
                         _result.get("reason")))

        # The watchdog gets a heartbeat every 2 s from a dedicated
        # timer; beats arriving means the GUI thread is alive,
        # which is exactly what the watchdog exists to prove —
        # so this timer stays window-side, never in the host.
        self._failsafe_timer = QTimer(self)
        self._failsafe_timer.timeout.connect(failsafe.beat)
        self._failsafe_timer.start(2000)

        self._reload_plugins()

    def _on_settings_changed(self, event):
        """State-bus SettingsChanged -> re-apply the appearance
        (GAPS-28, G-V3/G-V4). Only appearance keys arrive here
        (the bus only emits for those); the apply reads the saved
        values, so the event's value is not needed. Never raises
        — a failed apply must not break the bus."""
        try:
            self._apply_appearance()
        except Exception:  # noqa: BLE001 — appearance never breaks the bus
            pass

    # ---------------------------------- plugin trust (v5.48, item 19b)
    def _enqueue_plugin_trust(self, filename, sha256, capabilities):
        """Queue one untrusted plugin for the deferred ask.
        Deduped by (filename, sha256): a changed file is a new
        question, the same file asked twice is not."""
        entry = (str(filename), str(sha256),
                 tuple(capabilities) if capabilities else None)
        lock = getattr(self, "_plugin_trust_lock", None)
        if lock is None:
            lock = self._plugin_trust_lock = threading.Lock()
        queue = getattr(self, "_plugin_trust_queue", None)
        if queue is None:
            queue = self._plugin_trust_queue = []
        with lock:
            if (entry[0], entry[1]) not in [
                    (name, sha) for (name, sha, _caps) in queue]:
                queue.append(entry)

    def _plugin_trust_prompt_adapter(self, filename, sha256,
                                     capabilities=None) -> bool:
        """The trust-prompt handler registered with
        core/plugin_loader (v5.48, programme item 19b).

        NON-BLOCKING by design: plugin discovery runs during
        startup (the Skills page build, _reload_plugins), where a
        synchronous prompt could stall that thread for the
        confirm gate's whole budget. The adapter NEVER waits: it
        enqueues the plugin for the deferred ask
        (_run_deferred_plugin_trust_asks, after the window is up)
        and returns False — refused for now, the safe default.
        The plugin loads only after the deferred human approval
        pins it (see _plugin_trust_ask_loop)."""
        try:
            self._enqueue_plugin_trust(filename, sha256, capabilities)
        except Exception:  # noqa: BLE001 — a broken queue still denies
            pass
        return False

    def _scan_untrusted_plugins(self):
        """One deferred scan for plugin files lacking trust, using
        the loader's own helpers: files the signed shipped
        manifest does not list and the user store has not pinned
        for these exact bytes get queued for the ask."""
        try:
            from voice_assistant.core.gui_facade import plugin_loader

            manifest = plugin_loader.load_hash_manifest()
            if manifest is None:
                return  # no list to govern by: everything refuses
            trusted = plugin_loader.load_user_trusted_plugins()
            for path in sorted(plugin_loader.PLUGINS_DIR.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                if path.name in manifest:
                    continue  # shipped store governs; never queued
                try:
                    sha = plugin_loader._sha256_file(path)
                except Exception:  # noqa: BLE001
                    continue
                if trusted.get(path.name) == sha:
                    continue  # already pinned for these bytes
                try:
                    caps = plugin_loader.parse_capabilities(
                        path.read_text(encoding="utf-8",
                                       errors="replace"))
                except Exception:  # noqa: BLE001
                    caps = None
                self._enqueue_plugin_trust(path.name, sha, caps)
        except Exception:  # noqa: BLE001 — the scan is an extra
            pass

    def _run_deferred_plugin_trust_asks(self):
        """The deferred trust pass (v5.48, programme item 19b):
        scheduled by QTimer.singleShot after the window is shown,
        so the event loop (and the confirm dialog's GUI thread) is
        free before anyone is asked anything. Scans once for
        untrusted plugin files, then kicks the ask worker."""
        self._scan_untrusted_plugins()
        self._kick_plugin_trust_asks()

    def _kick_plugin_trust_asks(self):
        """Start the ask worker if the queue is non-empty and no
        worker is already draining it."""
        lock = getattr(self, "_plugin_trust_lock", None)
        if lock is None:
            return
        with lock:
            if self._plugin_trust_asking or not self._plugin_trust_queue:
                return
            self._plugin_trust_asking = True
        thread = threading.Thread(
            target=self._plugin_trust_ask_loop, daemon=True,
            name="vyuk-plugin-trust")
        self._plugin_trust_thread = thread
        thread.start()

    def _plugin_trust_ask_loop(self):
        """Ask worker (daemon thread, never the GUI thread): one
        plugin at a time through confirm_gate.request_confirmation
        — safe from here because the ask's handler runs on its own
        daemon thread and the dialog lives on the free GUI thread.
        Approval pins the plugin through the loader's user-trust
        API (_pin_user_trusted_plugin) and hands the filename to
        the GUI thread (plugin_trust_pinned) to load it now;
        denial captions that it stays disabled."""
        try:
            while True:
                with self._plugin_trust_lock:
                    if not self._plugin_trust_queue:
                        return
                    filename, sha256, caps = \
                        self._plugin_trust_queue.pop(0)
                try:
                    from voice_assistant.core.gui_facade import plugin_loader

                    if plugin_loader.load_user_trusted_plugins().get(
                            filename) == sha256:
                        continue  # pinned meanwhile; nothing to ask
                except Exception:  # noqa: BLE001
                    plugin_loader = None
                approved = False
                try:
                    approved = bool(confirm_gate.request_confirmation(
                        f"Trust plugin {filename}",
                        {"file": filename,
                         "capabilities": (
                             ", ".join(caps) if caps
                             else "not declared"),
                         "sha256": sha256}))
                except Exception:  # noqa: BLE001 — a broken gate denies
                    approved = False
                if not approved:
                    self.signals.plugin_trust_notice.emit(
                        f"Plugin {filename} stays disabled — "
                        f"it wasn't approved.")
                    continue
                pinned = False
                if plugin_loader is not None:
                    try:
                        pinned = bool(
                            plugin_loader._pin_user_trusted_plugin(
                                filename, sha256))
                    except Exception:  # noqa: BLE001
                        pinned = False
                if pinned:
                    # GUI thread reloads through the loader (the
                    # Rescan path) and captions loaded / next start.
                    self.signals.plugin_trust_pinned.emit(filename)
                else:
                    self.signals.plugin_trust_notice.emit(
                        f"Plugin {filename} was approved, but its "
                        f"trust pin couldn't be saved — it will "
                        f"ask again next start.")
        finally:
            with self._plugin_trust_lock:
                self._plugin_trust_asking = False

    def _on_plugin_trust_pinned(self, filename: str):
        """GUI-thread follow-up to a pin (v5.48, programme item
        19b): reload through the window's own plugin path (the
        loader's discover pass behind Rescan — a pinned plugin
        now loads without prompting), caption the honest outcome,
        and re-kick the ask worker in case the reload's discovery
        queued another untrusted plugin."""
        loaded = False
        try:
            self._reload_plugins()
            loaded = any(
                Path(str(p.get("_source", ""))).name == filename
                for p in (getattr(self, "_plugins", None) or []))
        except Exception:  # noqa: BLE001
            loaded = False
        self._add_system(
            f"Plugin {filename} is trusted and loaded." if loaded
            else f"Plugin {filename} is trusted — it will load "
                 f"next start.")
        self._kick_plugin_trust_asks()

    def _on_failsafe_tripped(self, reason: str):
        """GUI-thread slot: the watchdog tripped — tell the human and
        offer the re-arm (tray menu). Her input tools stay stopped
        until a human deliberately re-arms them."""
        try:
            action = getattr(self, "tray_rearm_action", None)
            if action is not None:
                action.setEnabled(True)
        except Exception:  # noqa: BLE001
            pass
        self._notify(
            "Failsafe tripped — VYUK's hands are stopped and any held "
            "keys/buttons were released. Re-arm from the tray menu "
            "when you're ready.")

    def _on_panic_hotkey(self):
        """GUI-thread follow-up slot (v5.42, H14; v5.46 programme
        item 5, A3): the panic key was pressed.

        The TRIP already happened on the hotkey daemon's thread:
        core/desktop/hotkeys._fire trips the failsafe BEFORE invoking the
        callback that reaches this slot (v5.46, A3), so a frozen
        GUI can no longer delay the stop — held input is released
        and running processes are killed by the trip listeners
        even if this slot is queued behind a stuck event loop.
        This slot is follow-up only: its own trip_now call below
        stays as an idempotent backstop (harmless when the trip
        already landed), and the slot itself does the mute /
        interrupt / caption work — mute the session through the
        window's own mute path, interrupt whatever is in flight
        (playback cut at the audio manager, plus the inner
        session's barge-in interrupt and its pending turn tasks
        where it exposes them — the session itself is NOT
        disconnected), and say so on the caption line. Every step
        is individually guarded: a panic path that can throw is a
        panic path that doesn't exist."""
        try:
            failsafe.trip_now("panic hotkey")
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "btn_mute") and not self.btn_mute.isChecked():
                self.btn_mute.setChecked(True)
            self._toggle_mute()
        except Exception:  # noqa: BLE001
            pass
        try:
            audio_manager.interrupt_playback()
        except Exception:  # noqa: BLE001
            pass
        try:
            # GAPS-28 (G-V5, parent fix): the interrupt goes
            # through the session's public seam — no private reads.
            live_session.interrupt_in_flight()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._set_caption(
                "Stopped by the panic key (Ctrl+Alt+Break) — VYUK is "
                "muted and her hands stay stopped until you re-arm "
                "them from the tray menu.")
        except Exception:  # noqa: BLE001
            pass

    def _rearm_failsafe(self):
        """Tray action: a human deliberately re-arms input control."""
        try:
            failsafe.reset()
            action = getattr(self, "tray_rearm_action", None)
            if action is not None:
                action.setEnabled(False)
        except Exception:  # noqa: BLE001
            pass
        self._notify("Failsafe re-armed — VYUK can use her hands again.")
