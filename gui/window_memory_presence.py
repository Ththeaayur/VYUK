"""Presence card, pref toggles, mini-orb, routine-scheduler start.

Split from gui/window_memory.py (GUI split #12 batch 1, move only).
"""

import os
from core.gui_facade import config_spec
import threading
from PySide6.QtCore import (
    QMetaObject,
    QThread,
    Qt,
    QTime,
    QTimer,
    Slot,
)
from PySide6.QtWidgets import QApplication
from voice_assistant.core.gui_facade import hotkey_daemon  # Phase 3 D3: moved to core/desktop/
from voice_assistant.core.gui_facade import live_session  # Phase 3 B2h: brain hub moved to core/brain/brain.py
from .mini_orb import MiniOrb
from .status_strip import StatusStrip
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set


class MemoryPresenceMixin:
    def _init_presence_card(self):
        # States first, connections after: initialising the card must
        # never fire a handler (in particular, never rewrite the
        # Windows autostart entry just because Settings was built).
        state = self._autostart_enabled()
        if state is None:
            self.chk_autostart.setEnabled(False)
            self.presence_status_label.setText("Auto-start is available on Windows.")
        else:
            self.chk_autostart.setChecked(bool(state))
        self.chk_autostart.toggled.connect(self._on_autostart_toggled)
        self.chk_connect_launch.setChecked(
            bool(qsettings_get("presence/connect_on_launch", settings=self.settings)))
        self.chk_connect_launch.toggled.connect(self._on_connect_launch_toggled)
        self.chk_auto_reconnect.setChecked(
            bool(qsettings_get("presence/auto_reconnect", settings=self.settings)))
        self.chk_auto_reconnect.toggled.connect(self._on_auto_reconnect_toggled)
        self.chk_mini_orb.setChecked(
            bool(qsettings_get("presence/mini_orb", settings=self.settings)))
        self.chk_mini_orb.toggled.connect(self._on_mini_orb_pref_toggled)
        self.chk_status_strip.setChecked(
            bool(qsettings_get("presence/status_strip", settings=self.settings)))
        self.chk_status_strip.toggled.connect(self._on_status_strip_pref_toggled)
        self.chk_summon_hotkey.setChecked(
            bool(qsettings_get("presence/summon_hotkey", settings=self.settings)))
        self.chk_summon_hotkey.toggled.connect(self._on_summon_hotkey_pref_toggled)
        self.chk_briefing.setChecked(
            bool(qsettings_get("presence/briefing_enabled", settings=self.settings)))
        self.chk_briefing.toggled.connect(self._on_briefing_pref_toggled)
        saved_time = QTime.fromString(
            str(qsettings_get("presence/briefing_time", settings=self.settings) or "07:30"), "HH:mm")
        self.time_briefing.setTime(saved_time if saved_time.isValid() else QTime(7, 30))
        self.time_briefing.timeChanged.connect(self._on_briefing_time_changed)
        # RET-1: daily nudge opt-in (state lives in
        # data/daily_count.json, not QSettings — the chosen time is
        # a retention preference, not a config field). The toggle is
        # the on-demand opt-in; the time applies from the next day.
        try:
            from core.gui_facade import daily_count as _dc

            _nudge_state = _dc.DailyCountStore().read()
        except Exception:  # noqa: BLE001 — a dead store reads as opted out
            _nudge_state = {"opted_in": False, "nudge_time": "07:00"}
        self.chk_nudge.setChecked(bool(_nudge_state.get("opted_in")))
        self.chk_nudge.toggled.connect(self._on_nudge_pref_toggled)
        saved_nudge_time = QTime.fromString(
            str(_nudge_state.get("nudge_time") or "07:00"), "HH:mm")
        self.time_nudge.setTime(
            saved_nudge_time if saved_nudge_time.isValid() else QTime(7, 0))
        self.time_nudge.timeChanged.connect(self._on_nudge_time_changed)
        # Watchers pack (v2.3): Notices block — same states-then-
        # connections discipline as the rest of this card.
        self.chk_watch_master.setChecked(
            bool(qsettings_get("watchers/enabled", settings=self.settings)))
        self.chk_watch_master.toggled.connect(self._on_watchers_master_toggled)
        for attr, key in (("chk_watch_battery", "battery"),
                          ("chk_watch_downloads", "downloads"),
                          ("chk_watch_network", "network"),
                          ("chk_watch_welcome", "welcome_back"),
                          ("chk_watch_disk", "disk"),
                          ("chk_watch_calendar", "calendar"),
                          ("chk_watch_clipboard", "clipboard"),
                          ("chk_watch_privacy", "privacy"),
                          ("chk_watch_fullscreen", "fullscreen"),
                          ("chk_watch_load", "load")):
            chk = getattr(self, attr)
            chk.setChecked(
                bool(qsettings_get(f"watchers/{key}", settings=self.settings)))
            chk.toggled.connect(
                lambda enabled, k=key: self._on_watcher_kind_toggled(k, enabled))
        # NOSE Wave 3 (v5.45): ambient light defaults OFF (opt-in,
        # N12); foreground awareness defaults on like the other
        # watchers (N13). Separate wiring because their defaults
        # differ from the loop above.
        self.chk_watch_ambient.setChecked(
            bool(qsettings_get("watchers/ambient_light", settings=self.settings)))
        self.chk_watch_ambient.toggled.connect(
            lambda enabled: self._on_watcher_kind_toggled("ambient_light", enabled))
        self.chk_watch_foreground.setChecked(
            bool(qsettings_get("watchers/foreground_app", settings=self.settings)))
        self.chk_watch_foreground.toggled.connect(
            lambda enabled: self._on_watcher_kind_toggled("foreground_app", enabled))
        self._sync_watcher_sub_enabled()

    def _sync_watcher_sub_enabled(self):
        """The per-kind notice checkboxes only mean anything while the
        master 'VYUK notices things' switch is on."""
        master = bool(qsettings_get("watchers/enabled", settings=self.settings))
        for attr in ("chk_watch_battery", "chk_watch_downloads",
                     "chk_watch_network", "chk_watch_welcome",
                     "chk_watch_clipboard", "chk_watch_privacy",
                     "chk_watch_fullscreen", "chk_watch_load",
                     "chk_watch_disk", "chk_watch_calendar",
                     "chk_watch_ambient", "chk_watch_foreground"):
            chk = getattr(self, attr, None)
            if chk is not None:
                chk.setEnabled(master)

    def _on_watchers_master_toggled(self, enabled: bool):
        qsettings_set("watchers/enabled", bool(enabled), settings=self.settings)
        self._sync_watcher_sub_enabled()
        engine = getattr(self, "_watcher_engine", None)
        if engine is not None:
            try:
                if enabled:
                    engine.start()
                else:
                    engine.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"[Watchers] toggle failed: {exc}")
        self._set_presence_status(
            "VYUK will quietly keep an eye on things for you." if enabled
            else "Notices off — VYUK won't watch in the background.")

    def _on_watcher_kind_toggled(self, key: str, enabled: bool):
        qsettings_set(f"watchers/{key}", bool(enabled), settings=self.settings)
        # UI settings slice: the kind is read live at each
        # watcher event (window_memory.py _on_watcher_event), so
        # the change applies to the next notice — and it had
        # no feedback.
        self._note_settings_feedback(
            f"“{key}” notices on — applies to the next notice."
            if enabled else
            f"“{key}” notices off — applies to the next notice.")

    def _set_presence_status(self, text: str):
        if hasattr(self, "presence_status_label"):
            self.presence_status_label.setText(text)

    def _on_autostart_toggled(self, enabled: bool):
        try:
            from voice_assistant.core.gui_facade import presence

            self._set_presence_status(str(presence.set_autostart(bool(enabled))))
        except Exception as exc:  # noqa: BLE001
            self._set_presence_status(f"ERROR: could not change auto-start — {exc}")
            self.chk_autostart.blockSignals(True)
            self.chk_autostart.setChecked(not enabled)
            self.chk_autostart.blockSignals(False)

    def _on_connect_launch_toggled(self, enabled: bool):
        qsettings_set("presence/connect_on_launch", bool(enabled), settings=self.settings)
        self._set_presence_status(
            "VYUK will connect by itself at launch." if enabled
            else "VYUK will wait for you to press Connect.")

    def _on_auto_reconnect_toggled(self, enabled: bool):
        qsettings_set("presence/auto_reconnect", bool(enabled), settings=self.settings)
        if not enabled:
            # Switching it off mid-episode stops the retrying at once.
            self._cancel_reconnect()
        self._set_presence_status(
            "VYUK will quietly reconnect on her own if the connection drops."
            if enabled else
            "Auto-reconnect off — a dropped connection stays dropped.")

    def _on_mini_orb_pref_toggled(self, enabled: bool):
        self._set_mini_orb_visible(bool(enabled))

    def _on_summon_hotkey_pref_toggled(self, enabled: bool):
        qsettings_set("presence/summon_hotkey", bool(enabled), settings=self.settings)
        hotkey_daemon.on_summon = (
            (lambda: self.signals.summon_requested.emit()) if enabled else None)
        # UI settings slice: the daemon hook swaps live, but
        # nothing visible said so.
        self._note_settings_feedback(
            "Summon hotkey on — applied." if enabled else
            "Summon hotkey off — applied.")

    def _on_briefing_pref_toggled(self, enabled: bool):
        qsettings_set("presence/briefing_enabled", bool(enabled), settings=self.settings)
        when = self.time_briefing.time().toString("HH:mm")
        self._set_presence_status(
            f"Morning briefing on — {when}, whenever VYUK is connected." if enabled
            else "Morning briefing off.")

    def _on_briefing_time_changed(self, qtime: QTime):
        qsettings_set("presence/briefing_time", qtime.toString("HH:mm"), settings=self.settings)
        # UI settings slice: the toggle already reports through
        # the presence status label, but a time change reported
        # nothing — yet _check_briefing reads this time live on
        # every timer tick, so the change applies at the next
        # check.
        self._note_settings_feedback(
            f"Morning briefing time set to "
            f"{qtime.toString('HH:mm')} — plays at that time on "
            "days VYUK is connected.")

    def _on_nudge_pref_toggled(self, enabled: bool):
        """RET-1: one-tap nudge opt-in / opt-out.

        Opt-in schedules the daily job at the chosen time; opt-out
        is durable and cancels the job. A failure is reported in
        the presence status and the checkbox is put back — never
        silent, never a toggle that claims a state it did not reach.
        """
        try:
            from core.gui_facade import daily_count as _dc

            store = _dc.DailyCountStore()
            if enabled:
                when = self.time_nudge.time().toString("HH:mm")
                _dc.set_opt_in(store, when)
                result = _dc.ensure_nudge_job(store)
                if result.get("ok") or "already set up" in str(
                        result.get("reason", "")):
                    self._set_presence_status(
                        f"Daily nudge on — one spoken nudge at {when}.")
                    return
                raise RuntimeError(result.get("reason", "unknown error"))
            _dc.set_opt_out(store)
            self._set_presence_status(
                "Daily nudge off — it will not ask again.")
        except Exception as exc:  # noqa: BLE001 — report, then put the toggle back
            self._set_presence_status(
                f"Daily nudge change failed: {exc}")
            try:
                self.chk_nudge.blockSignals(True)
                self.chk_nudge.setChecked(not enabled)
            finally:
                self.chk_nudge.blockSignals(False)

    def _on_nudge_time_changed(self, qtime: QTime):
        """RET-1: the chosen nudge time lives in the store. While
        opted in, a change re-schedules the job (cancel + ensure);
        while opted out it only updates the stored preference."""
        try:
            from core.gui_facade import daily_count as _dc

            store = _dc.DailyCountStore()
            when = qtime.toString("HH:mm")
            if not store.read().get("opted_in"):
                store.update(nudge_time=when)
                self._note_settings_feedback(
                    f"Daily nudge time set to {when} — applies when "
                    f"you turn the nudge on.")
                return
            _dc.set_opt_in(store, when)
            try:
                _dc.cancel_nudge_job()
            except Exception:  # noqa: BLE001 — no job yet is fine
                pass
            result = _dc.ensure_nudge_job(store)
            if result.get("ok") or "already set up" in str(
                    result.get("reason", "")):
                self._note_settings_feedback(
                    f"Daily nudge time set to {when}.")
            else:
                raise RuntimeError(result.get("reason", "unknown error"))
        except Exception as exc:  # noqa: BLE001 — report, never silent
            self._set_presence_status(
                f"Daily nudge time change failed: {exc}")

    def _init_presence(self):
        """Build the mini orb, start the routine scheduler + briefing
        timer, and apply the launch behaviours. Called once, at the
        end of __init__ (settings and the main orb already exist)."""
        try:
            mini = MiniOrb()
            custom = getattr(self.visualizer, "_custom_theme", None)
            if custom:
                mini.set_custom_theme(*custom)
            else:
                mini.set_theme(self.visualizer.theme_name)
            mini.set_orb_style(self.visualizer.orb_style)
            mini.on_click = self._mini_orb_activated
            mini.on_open_main = self._mini_orb_activated
            mini.on_toggle_connect = self._toggle_connection
            mini.on_hide = lambda: self._set_mini_orb_visible(False)
            self.mini_orb = mini
            mini.set_state(self._status_raw or "DISCONNECTED")
        except Exception as exc:  # noqa: BLE001
            print(f"[Presence] mini orb unavailable: {exc}")
            self.mini_orb = None

        self._start_routine_scheduler()
        self._init_watchers()

        self._briefing_timer = QTimer(self)
        self._briefing_timer.setInterval(60_000)
        self._briefing_timer.timeout.connect(lambda: self._check_briefing())
        self._briefing_timer.start()
        # Feels-alive pack (v2.5): calendar heads-ups poll on the
        # same minute beat as the briefing check.
        self._calendar_timer = QTimer(self)
        self._calendar_timer.setInterval(60_000)
        self._calendar_timer.timeout.connect(lambda: self._check_calendar_heads_up())
        self._calendar_timer.start()
        # NOSE N1 (v5.44): held watcher notices get their digest
        # when a call ends even if no new notice arrives to notice
        # the fact — poll the (cached, probeless) call state.
        self._call_digest_timer = QTimer(self)
        self._call_digest_timer.setInterval(30_000)
        self._call_digest_timer.timeout.connect(
            lambda: self._flush_held_call_notices())
        self._call_digest_timer.start()
        # One early check so a launch right at briefing time isn't
        # missed waiting for the first 60 s tick.
        QTimer.singleShot(5000, self._check_briefing)

        if config_spec.explicit("NOVA_START_MINIMIZED") == "1":
            # main.py shows the window right after __init__ returns;
            # hiding on the next event-loop turn avoids fighting that
            # show() — and the tray already exists by then.
            QTimer.singleShot(0, self._apply_minimized_start)
        elif bool(qsettings_get("onboarded", settings=self.settings)):
            # First-run users meet the mini orb after onboarding
            # (see _run_onboarding).
            self._maybe_show_mini_orb()
            # Rethink Build 3: the strip is the docked bottom row,
            # on by default — this restores the owner's choice
            # (hidden only when they hid it).
            self._maybe_show_status_strip()

        if bool(qsettings_get("presence/connect_on_launch", settings=self.settings)):
            QTimer.singleShot(800, self._connect_on_launch_once)

    def _maybe_show_mini_orb(self):
        mini = getattr(self, "mini_orb", None)
        if mini is None:
            return
        if bool(qsettings_get("presence/mini_orb", settings=self.settings)):
            mini.show()

    def _build_docked_status_strip(self):
        """Build the docked status strip (rethink Build 3, §6.11):
        one dot + one word as the window's bottom row. The strip is
        built ONCE, here, as a child of the shell, and owned under
        ``self.vitals_strip`` — never ``self.status_strip``, which
        is the Home page's header cluster (the floating strip's old
        name collided with it and could never appear). Called from
        the shell's ``_build_ui`` after the page column."""
        strip = StatusStrip(parent=self.shell)
        self.vitals_strip = strip
        return strip

    def _maybe_show_status_strip(self):
        # Rethink Build 3: the strip is the docked bottom row, ON
        # by default (presence/status_strip default True). This
        # restores the owner's explicit choice at boot: the row is
        # hidden only when they hid it (checkbox / tray / tool).
        self._set_status_strip_visible(
            bool(qsettings_get("presence/status_strip", settings=self.settings)))

    def _ensure_status_strip(self):
        """Return the docked strip, building it lazily only when the
        shell never built it (headless / test windows). Returns None
        when it cannot be built — the caller reports that honestly
        instead of claiming a strip that does not exist."""
        strip = getattr(self, "vitals_strip", None)
        if strip is not None:
            return strip
        try:
            strip = StatusStrip()
        except Exception as exc:  # noqa: BLE001
            print(f"[Presence] status strip unavailable: {exc}")
            self.vitals_strip = None
            return None
        self.vitals_strip = strip
        return strip

    def _set_status_strip_visible(self, visible: bool):
        """Single funnel for status-strip visibility: persists the
        pref, shows/hides the docked row, and keeps the tray item +
        Settings checkbox in sync (the mini-orb funnel's shape)."""
        visible = bool(visible)
        qsettings_set("presence/status_strip", visible, settings=self.settings)
        strip = self._ensure_status_strip() if visible else getattr(
            self, "vitals_strip", None
        )
        if strip is not None:
            strip.show() if visible else strip.hide()
        action = getattr(self, "tray_strip_action", None)
        if action is not None:
            action.setChecked(visible)  # setChecked never emits triggered
        chk = getattr(self, "chk_status_strip", None)
        if chk is not None and chk.isChecked() != visible:
            chk.blockSignals(True)
            chk.setChecked(visible)
            chk.blockSignals(False)

    def _on_status_strip_pref_toggled(self, enabled: bool):
        self._set_status_strip_visible(bool(enabled))

    def _status_strip_sink(self, action: str) -> dict:
        """Sink the funnel's status_strip tool calls (R8-6, yasb
        port) — the frame-sink seam's shape: the window owns the
        strip, so the window applies the action and VERIFIES it.
        Tool calls arrive off the GUI thread, so the apply hops to
        the event loop and this waits, bounded (2 s), for the
        outcome — a show that never made the strip visible is
        reported as a failure, never claimed."""
        app = QApplication.instance()
        gui_thread = app.thread() if app is not None else None
        if gui_thread is None or QThread.currentThread() is gui_thread:
            return self._apply_status_strip_sink(action)
        done = threading.Event()
        self._strip_sink_request = action
        self._strip_sink_result = None
        self._strip_sink_done = done
        QMetaObject.invokeMethod(
            self, "_drain_status_strip_sink", Qt.QueuedConnection
        )
        if not done.wait(timeout=2.0):
            return {
                "ok": False,
                "visible": False,
                "text": "",
                "error": "The status strip didn't respond in time.",
            }
        result = getattr(self, "_strip_sink_result", None)
        if not isinstance(result, dict):
            return {
                "ok": False,
                "visible": False,
                "text": "",
                "error": "The status strip didn't answer clearly.",
            }
        return result

    @Slot()
    def _drain_status_strip_sink(self) -> None:
        """Apply one queued strip action on the GUI thread."""
        action = getattr(self, "_strip_sink_request", "status")
        try:
            self._strip_sink_result = self._apply_status_strip_sink(action)
        except Exception as exc:  # noqa: BLE001 — a broken apply reads honest
            self._strip_sink_result = {
                "ok": False,
                "visible": False,
                "text": "",
                "error": f"The status strip failed ({exc}).",
            }
        done = getattr(self, "_strip_sink_done", None)
        if done is not None:
            done.set()

    def _apply_status_strip_sink(self, action: str) -> dict:
        """Apply one strip action on the GUI thread and verify it —
        the window IS the record (A1-store): visibility is read back
        after the apply, never assumed from the request."""
        if action == "show":
            self._set_status_strip_visible(True)
        elif action == "hide":
            self._set_status_strip_visible(False)
        strip = getattr(self, "vitals_strip", None)
        visible = bool(strip is not None and strip.isVisible())
        if action == "show" and not visible:
            return {
                "ok": False,
                "visible": False,
                "text": "",
                "error": "The status strip didn't appear.",
            }
        if action == "hide" and visible:
            return {
                "ok": False,
                "visible": True,
                "text": "",
                "error": "The status strip is still showing.",
            }
        text = strip.state_word() if (strip is not None and visible) else ""
        return {"ok": True, "visible": visible, "text": text, "error": None}

    def _set_mini_orb_visible(self, visible: bool):
        """Single funnel for mini-orb visibility: persists the pref,
        shows/hides the orb, and keeps the tray item + Settings
        checkbox in sync."""
        visible = bool(visible)
        qsettings_set("presence/mini_orb", visible, settings=self.settings)
        mini = getattr(self, "mini_orb", None)
        if mini is not None:
            mini.show() if visible else mini.hide()
        action = getattr(self, "tray_mini_action", None)
        if action is not None:
            action.setChecked(visible)  # setChecked never emits triggered
        chk = getattr(self, "chk_mini_orb", None)
        if chk is not None and chk.isChecked() != visible:
            chk.blockSignals(True)
            chk.setChecked(visible)
            chk.blockSignals(False)

    def _mini_orb_activated(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _apply_minimized_start(self):
        # Autostart launch (--minimized): tray + mini orb only.
        if getattr(self, "tray", None) is None:
            # No tray to minimise to — keep the window reachable.
            self._maybe_show_mini_orb()
            self._maybe_show_status_strip()
            return
        self.hide()
        self._maybe_show_mini_orb()
        self._maybe_show_status_strip()

    def _connect_on_launch_once(self):
        if live_session.is_connected:
            return
        if self.worker is not None and self.worker.is_alive():
            return
        # SPEAKER-LOCK (v5.45): connect-on-launch is an AUTO start,
        # not a user start — call _start_connection directly (its
        # guards cover the checks above) so provenance records
        # "auto" instead of riding the Connect button's user path.
        self._start_connection()

    def _start_routine_scheduler(self):
        try:
            from voice_assistant.core.gui_facade import RoutineScheduler  # Phase 3 B1b: moved to core/brain/
        except Exception:  # noqa: BLE001
            self._routine_scheduler = None
            return
        try:
            sched = RoutineScheduler(
                on_fire=lambda routine: self.signals.routine_due.emit(routine))
            sched.start()
            self._routine_scheduler = sched
        except Exception as exc:  # noqa: BLE001
            print(f"[Presence] routine scheduler unavailable: {exc}")
            self._routine_scheduler = None
