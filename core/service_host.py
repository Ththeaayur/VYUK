"""Service layer (GAPS-28, G-V8) — background services owned
independent of the window.

The main window used to start every background service itself
(``gui/window_services_trust.py::_start_background_services``)
and stop them in its ``closeEvent`` — kill the window, kill
every service, with no lifecycle of their own. ``ServiceHost``
owns that lifecycle instead: ``start()`` / ``stop()`` /
``status()``, idempotent, headless (no window, no Qt — the gap
prover starts and stops it with fake services). ``main.py``
constructs one host before the window and passes it in
(``service_host=``); the window attaches its GUI callbacks
(hotkey actions, clipboard bridge, confirm/plan handlers,
failsafe trip callback, wake callback) and then calls
``host.start()``. A window built without a host (tests, the
old call sites) builds its own lazily.

Services, in start order (the window's old order, preserved):
scheduler (ProactiveScheduler), the scheduled-agents engine
(``scheduled_agents.ensure_engine_running`` — registers the
operative-agent handler, then starts the job scheduler),
hotkey daemon, clipboard monitor, failsafe watchdog, wake-word
detector (needs the audio manager).

Deliberately NOT host-owned:
* the failsafe heartbeat QTimer — it beats ``failsafe.beat``
  every 2 s from the GUI thread, which is exactly what the
  watchdog exists to prove (the GUI thread is alive); a
  host-side beat would prove nothing. It stays window-side.
* the confirm / plan-approval handler registrations and the
  signal connections — they point at window methods and
  dialogs; the window registers them before ``host.start()``.
* the audio manager — it is not started by the boot path (it
  starts with the voice session); only its ``stop()`` runs at
  close, and that stays in the window's ``closeEvent``.
* the routine scheduler / watcher engine — window-owned
  watch-mode machinery, stopped by the window itself.

``stop()`` pauses the engine (``job_scheduler.stop()``), it does
NOT shut it down: a shutdown scheduler cannot be started again
in the same process, and stop-then-start is the restart path.
Each service's start/stop failure is reported per service and
never raises out of ``start()``/``stop()`` — a service that
cannot start must not break boot, exactly as before.
"""

from __future__ import annotations

#: Start order — the window's old order, preserved.
_SERVICE_ORDER = (
    "scheduler",
    "engine",
    "hotkey",
    "clipboard",
    "failsafe",
    "wake",
)


class ServiceHost:
    """Owns the lifecycle of the app's background services."""

    def __init__(self, scheduler=None, engine=None, hotkey=None,
                 clipboard=None, failsafe=None, wake=None,
                 audio_manager=None, job_scheduler=None):
        """Each argument is a service object, or None to resolve
        the real singleton lazily on first use. ``engine`` is the
        scheduled-agents module (``ensure_engine_running``);
        ``job_scheduler`` is the engine's stop side (its
        ``stop()`` pauses the engine — the start and stop live
        on two objects, so both are injectable). ``audio_manager``
        is only needed for the wake-word detector's
        ``start(audio_manager)``. Tests pass fakes for all of
        them.
        """
        self._scheduler = scheduler
        self._engine = engine
        self._hotkey = hotkey
        self._clipboard = clipboard
        self._failsafe = failsafe
        self._wake = wake
        self._audio_manager = audio_manager
        self._job_scheduler = job_scheduler
        self._started = set()

    # ------------------------------------------------------------ resolve
    def _real_scheduler(self):
        if self._scheduler is None:
            from .scheduler import scheduler

            self._scheduler = scheduler
        return self._scheduler

    def _real_engine(self):
        if self._engine is None:
            from . import scheduled_agents

            self._engine = scheduled_agents
        return self._engine

    def _real_hotkey(self):
        if self._hotkey is None:
            from .desktop.hotkeys import hotkey_daemon

            self._hotkey = hotkey_daemon
        return self._hotkey

    def _real_clipboard(self):
        if self._clipboard is None:
            from .desktop.clipboard_monitor import clipboard_monitor

            self._clipboard = clipboard_monitor
        return self._clipboard

    def _real_failsafe(self):
        if self._failsafe is None:
            from . import failsafe

            self._failsafe = failsafe
        return self._failsafe

    def _real_wake(self):
        if self._wake is None:
            from .voice.wake_word import wake_word_detector

            self._wake = wake_word_detector
        return self._wake

    def _real_audio_manager(self):
        if self._audio_manager is None:
            from .voice.audio import audio_manager

            self._audio_manager = audio_manager
        return self._audio_manager

    def _real_job_scheduler(self):
        if self._job_scheduler is None:
            from .desktop import job_scheduler

            self._job_scheduler = job_scheduler
        return self._job_scheduler

    # ------------------------------------------------------------ lifecycle
    def start(self):
        """Start every service, in order. Idempotent: a second
        call starts only what is not already running. Returns
        ``{name: {"ok": bool, ...}}`` per service; a failure is
        reported, never raised."""
        results = {}
        results["scheduler"] = self._start_one(
            "scheduler", lambda: self._real_scheduler().start())
        results["engine"] = self._start_one(
            "engine", lambda: self._real_engine().ensure_engine_running())
        results["hotkey"] = self._start_one(
            "hotkey", lambda: self._real_hotkey().start())
        results["clipboard"] = self._start_one(
            "clipboard", lambda: self._real_clipboard().start())
        results["failsafe"] = self._start_one(
            "failsafe", lambda: self._real_failsafe().start())
        results["wake"] = self._start_one(
            "wake", lambda: self._real_wake().start(
                self._real_audio_manager()))
        return results

    def _start_one(self, name, fn):
        if name in self._started:
            return {"ok": True, "already_started": True}
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 — never break boot
            return {"ok": False, "reason": str(exc)}
        if isinstance(result, dict) and result.get("ok") is False:
            return result
        self._started.add(name)
        if isinstance(result, dict):
            return result
        return {"ok": True}

    def stop(self):
        """Stop every service this host started, in reverse
        order. Best-effort per service: a failure is reported,
        never raised. The engine is PAUSED (jobs survive, the
        engine thread stays idle) — a shutdown scheduler cannot
        be started again in this process. Returns
        ``{name: {"ok": bool, ...}}`` per service."""
        results = {}
        for name in reversed(_SERVICE_ORDER):
            if name not in self._started:
                continue
            results[name] = self._stop_one(name)
        return results

    def _stop_one(self, name):
        try:
            if name == "scheduler":
                self._real_scheduler().stop()
            elif name == "engine":
                self._real_job_scheduler().stop()
            elif name == "hotkey":
                self._real_hotkey().stop()
            elif name == "clipboard":
                self._real_clipboard().stop()
            elif name == "failsafe":
                self._real_failsafe().stop()
            elif name == "wake":
                self._real_wake().stop()
        except Exception as exc:  # noqa: BLE001 — best-effort stop
            return {"ok": False, "reason": str(exc)}
        self._started.discard(name)
        return {"ok": True}

    def status(self):
        """``{name: started-by-this-host}`` for every service."""
        return {name: name in self._started for name in _SERVICE_ORDER}
