"""MainWindow memory mixin: routine due/results, briefing, calendar
heads-up, routines/plans refresh.

Split from gui/window_memory.py (GUI split #12 batch 2, move only).
"""
import asyncio
import datetime
import json
import os
import threading
from pathlib import Path
from PySide6.QtWidgets import QLabel
from voice_assistant.core.gui_facade import live_session
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set


def _polish_row_labels(row_widget):
    """Polish a freshly built row's labels synchronously.

    R3F1: a row added to a layout is not polished by the event
    loop until the next turn, so its labels read the default
    9pt font until then — a reader measuring font sizes right
    after a refresh sees a fifth size (the scifi_activity Qt
    flake). ensurePolished() applies the stylesheet now, so a
    new row reads its styled sizes the moment it exists.
    QListWidget rows do not need this: setItemWidget shows
    (and polishes) them synchronously.
    """
    for _lbl in row_widget.findChildren(QLabel):
        _lbl.ensurePolished()


def _routine_row_name(row_widget):
    """The routine name on a routine row's face, or None for a
    non-routine widget (e.g. the 'No routines yet' label)."""
    name_lbl = row_widget.findChild(QLabel, "activityRowValue")
    if name_lbl is None:
        return None
    return name_lbl.text() or None


def _routine_editor_is_open(row_widget):
    """True when the row's schedule editor is open — the user
    opened it and has not closed it. Uses the editor's own
    hidden flag, not isHidden()/isVisible(): those also read
    the ancestors, so an open editor would read closed while
    the window is hidden (e.g. a refresh during startup).
    The editor is the row's second child widget, as the
    routine-row tests assume."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget as _QWidget

    children = [c for c in row_widget.children()
                if isinstance(c, _QWidget)]
    if len(children) < 2:
        return False
    editor = children[1]
    return not editor.testAttribute(Qt.WidgetAttribute.WA_WState_Hidden)


class MemoryRoutinesMixin:
    """Part-mixin (GUI split #12): method bodies moved verbatim
    from gui/window_memory.py; behaviour is unchanged.
    """

    def _on_routine_run_now(self, routine):
        """The Routines tab's "Run now" tap (RET-2): the run goes
        through the scheduled_agents run_routine action, not the
        coordinator path — a tap is a dispatch, and the gated steps
        inside still ask at run time through the funnel. Scheduled
        firings keep _on_routine_due (the coordinator path with
        plan approval / verification / pause-resume) — this handler
        is only for the explicit tap."""
        try:
            name = str((routine or {}).get("name", "") or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
        if not name:
            return
        threading.Thread(target=self._run_routine_via_action,
                         args=(name,), daemon=True).start()

    def _run_routine_via_action(self, name: str):
        """Run one routine through the scheduled_agents run_routine
        action (RET-2) — the same OperativeAgent/funnel path a voice
        run takes, so a gated step still asks at run time. The
        report comes back on screen via the routine_result signal."""
        try:
            from voice_assistant.core.gui_facade import (
                scheduled_agents as _sa)
            report = _sa.scheduled_agents(action="run_routine", name=name)
        except Exception as exc:  # noqa: BLE001
            report = {"ok": False, "reason": str(exc)}
        self.signals.routine_result.emit(
            self._format_routine_report(name, report))

    @staticmethod
    def _format_routine_report(name: str, report) -> str:
        """The run report as screen text: the speakable summary
        first, then one line per step with its real outcome (done /
        refused / failed / not_run) and the funnel's reason."""
        if not isinstance(report, dict):
            return f"Routine '{name}': {report}"
        lines = [str(report.get("summary") or report.get("reason")
                     or "Routine %r: no report" % name)]
        for index, step in enumerate(report.get("steps") or []):
            line = "%d. %s — %s" % (index + 1, step.get("tool"),
                                    step.get("outcome"))
            if step.get("reason"):
                line += ": %s" % step["reason"]
            duration = step.get("duration_ms")
            if isinstance(duration, (int, float)):
                line += " (%d ms)" % duration
            lines.append(line)
        return "\n".join(lines)

    def _on_routine_due(self, routine):
        """A scheduled routine fired (scheduler thread → Qt signal).
        Connected: ask the live brain to run it. Otherwise: run it
        headless through planner.run_routine, the same registry path."""
        try:
            name = str((routine or {}).get("name", "") or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
        if not name:
            return
        if self.worker and self.worker.loop and live_session.is_connected:
            injected = f"Run my routine '{name}' now — use the run_routine tool."
            self._note_self_sent(injected)
            try:
                future = asyncio.run_coroutine_threadsafe(
                    live_session.send_text(injected),
                    self.worker.loop)
            except Exception as exc:  # noqa: BLE001 — loop closed, etc.
                # Voice-control batch (2026-11-16, C5): the send
                # never even started — fall back at once instead
                # of leaving the "asking" line as the last word.
                self._add_system(
                    f"⏰ Routine '{name}' — the live send failed "
                    f"({exc}); running it directly instead.")
                threading.Thread(
                    target=self._run_routine_headless, args=(name,),
                    daemon=True).start()
                return
            # Voice-control batch (2026-11-16, C5): the future
            # used to be dropped — a send that failed after the
            # is_connected check (session dropped mid-flight, or
            # send_text silently no-opping while disconnected)
            # left the routine unrun with only the "asking" line
            # on screen and no error anywhere. A failed send now
            # falls back to the headless run and says so; a
            # delivered send needs nothing more (the live turn
            # reports back itself). The residual race — a send
            # that no-ops a blink before a reconnect — is noted,
            # not hidden: the callback judges the session state
            # it can see, which is the state the send ran in
            # except for that blink.
            def _on_routine_sent(done_future, _name=name):
                failed = None
                try:
                    done_future.result()
                except Exception as exc:  # noqa: BLE001
                    failed = str(exc)
                if failed is None:
                    try:
                        if not live_session.is_connected:
                            failed = "the live session dropped"
                    except Exception:  # noqa: BLE001
                        failed = "the live session state is unreadable"
                if failed is not None:
                    # The callback runs on the worker loop thread,
                    # so the notice goes through the routine_result
                    # signal (Qt thread-safe) — _add_system builds
                    # widgets and is GUI-thread only. _on_routine_
                    # result renders it and refreshes the rows.
                    self.signals.routine_result.emit(
                        f"⏰ Routine '{name}' — the live send failed "
                        f"({failed}); running it directly instead.")
                    threading.Thread(
                        target=self._run_routine_headless, args=(_name,),
                        daemon=True).start()

            try:
                future.add_done_callback(_on_routine_sent)
            except Exception:  # noqa: BLE001 — a future that cannot
                pass           # take a callback is already done
            self._add_system(f"⏰ Routine '{name}' — asking VYUK to run it.")
        else:
            threading.Thread(
                target=self._run_routine_headless, args=(name,), daemon=True
            ).start()

    def _run_routine_headless(self, name: str):
        try:
            from voice_assistant.core.gui_facade import run_routine  # Phase 3 B2-remainder: planner moved to core/brain/

            summary = str(run_routine(name) or "")
        except Exception as exc:  # noqa: BLE001
            summary = f"Could not run routine '{name}': {exc}"
        lines = summary.splitlines()
        first = lines[0] if lines else summary
        self.signals.routine_result.emit(
            f"⏰ Routine '{name}' ran on schedule: {first}")

    def _on_routine_result(self, text: str):
        self._add_system(text)
        self._refresh_routines()
        # v5.49 (programme item 32): a finished headless plan run
        # reports back through this same signal — refresh its rows.
        self._refresh_plans()

    def _check_briefing(self, now=None):
        """Speak-first morning briefing: at the configured time, once
        per day, and only when VYUK is already connected (she never
        connects just for this). `now` is injectable for tests."""
        try:
            if not bool(qsettings_get("presence/briefing_enabled", settings=self.settings)):
                return
            now = now or datetime.datetime.now()
            target = str(qsettings_get("presence/briefing_time", settings=self.settings) or "07:30")
            if now.strftime("%H:%M") != target:
                return
            today = now.date().isoformat()
            if getattr(self, "_briefing_fired_date", None) == today:
                return
            if str(qsettings_get("presence/briefing_last_date", settings=self.settings) or "") == today:
                return
            if not live_session.is_connected:
                return  # skip silently — try again next minute/day
            self._briefing_fired_date = today
            qsettings_set("presence/briefing_last_date", today, settings=self.settings)
            self._play_briefing()
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------- calendar heads-ups (v2.5)
    def _check_calendar_heads_up(self, now=None):
        """Fifteen minutes before something on the calendar starts,
        mention it — in chat, and out loud (through the watcher
        speech path) while she's connected. Each event is announced
        once, ever: the seen-set persists across restarts in
        data/heads_up_seen.json. `now` is injectable for tests."""
        try:
            if not bool(qsettings_get("watchers/enabled", settings=self.settings)):
                return
            if not bool(qsettings_get("watchers/calendar", settings=self.settings)):
                return
            module = self._google_services()
            if module is None or not self._google_connected():
                return
            now = now or datetime.datetime.now().astimezone()
            try:
                events = module.next_events(within_hours=2, limit=10)
            except Exception:  # noqa: BLE001
                return
            seen = self._load_heads_up_seen()
            changed = False
            for event in events or []:
                if not isinstance(event, dict) or event.get("all_day"):
                    continue
                start = event.get("start")
                if not isinstance(start, datetime.datetime):
                    continue
                if start.tzinfo is None:
                    start = start.replace(tzinfo=now.tzinfo)
                minutes = (start - now).total_seconds() / 60.0
                if not 0 < minutes <= 15:
                    continue
                key = f"{event.get('id')}|{start.isoformat()}"
                if key in seen:
                    continue
                seen.add(key)
                changed = True
                title = str(event.get("title") or "").strip() or "Event"
                unit = "minute" if round(minutes) == 1 else "minutes"
                message = (
                    f"Coming up: {title} at {start.strftime('%H:%M')} — "
                    f"in {int(round(minutes))} {unit}"
                )
                location = str(event.get("location") or "").strip()
                if location:
                    message += f" ({location})"
                message += "."
                self._add_system(message)
                self._maybe_speak_watcher_notice("calendar_heads_up", message)
                # N4 (v5.44): an event entering its heads-up
                # window is a material ambient change.
                try:
                    _refresh = getattr(self, "_refresh_ambient_context", None)
                    if _refresh is not None:
                        _refresh("calendar_heads_up")
                except Exception:  # noqa: BLE001
                    pass
            if changed:
                self._save_heads_up_seen()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _heads_up_seen_path():
        from voice_assistant.core.gui_facade import DATA_DIR

        return Path(DATA_DIR) / "heads_up_seen.json"

    def _load_heads_up_seen(self) -> set:
        """The persisted announced-event keys, loaded once. A missing
        or corrupt file is simply an empty set — heads-ups degrade
        to at-worst repeating themselves, never to crashing."""
        if self._heads_up_seen is None:
            keys = set()
            try:
                data = json.loads(self._heads_up_seen_path().read_text(encoding="utf-8"))
                if isinstance(data, list):
                    keys = {str(k) for k in data}
            except Exception:  # noqa: BLE001
                keys = set()
            self._heads_up_seen = self._prune_heads_up_seen(keys)
        return self._heads_up_seen

    def _save_heads_up_seen(self):
        if self._heads_up_seen is None:
            return
        try:
            self._heads_up_seen = self._prune_heads_up_seen(self._heads_up_seen)
            path = self._heads_up_seen_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(sorted(self._heads_up_seen), indent=1),
                encoding="utf-8")
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _prune_heads_up_seen(keys) -> set:
        """Drop keys whose event started more than two days ago —
        the set stays tiny and current."""
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=2)
        kept = set()
        for key in keys or ():
            try:
                start = datetime.datetime.fromisoformat(str(key).rsplit("|", 1)[-1])
            except ValueError:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=cutoff.tzinfo)
            if start >= cutoff:
                kept.add(key)
        return kept

    def _refresh_routines(self):
        """Rebuild the Reminders page's routine rows from
        planner.get_routines(). No-op until the page has been built."""
        layout = getattr(self, "routines_layout", None)
        if layout is None:
            return
        # R3F1: a row whose schedule editor is OPEN is the user's
        # in-progress edit — a refresh must never delete it (the
        # typed schedule would vanish with the widget). Keep such
        # rows, keyed by routine name, and re-add them in routine
        # order below; rebuild only the rest.
        kept = {}
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            name = _routine_row_name(widget)
            if name is not None and _routine_editor_is_open(widget):
                kept[name] = widget
                continue
            # Hide NOW, delete later: a deleteLater-only row
            # keeps painting at its old geometry until the
            # deferred deletion lands, overlapping the row
            # that replaces it — the same stale-label bug
            # _refresh_plans already fixed (the stale "No
            # routines yet" label over the first routine row,
            # caught by the Screen 3 render check).
            widget.hide()
            widget.deleteLater()
        routines = []
        try:
            from voice_assistant.core.gui_facade import planner  # Phase 3 B2-remainder: planner moved to core/brain/

            getter = getattr(planner, "get_routines", None)
            if getter is not None:
                routines = list(getter() or [])
        except Exception:  # noqa: BLE001
            routines = []
        if not routines:
            for _widget in kept.values():
                layout.addWidget(_widget)
            if not kept:
                empty = QLabel("No routines yet — ask VYUK: “save a routine…”")
                empty.setObjectName("muted")
                empty.setWordWrap(True)
                layout.addWidget(empty)
            return
        live_names = set()
        for routine in routines:
            if isinstance(routine, dict):
                name = str(routine.get("name", "") or "Untitled")
                live_names.add(name)
                if name in kept:
                    layout.addWidget(kept[name])
                else:
                    row = self._routine_row(routine)
                    layout.addWidget(row)
                    _polish_row_labels(row)
        # A kept row whose routine is gone (deleted elsewhere
        # while its editor was open) goes now that nothing
        # references it.
        for _name, _widget in kept.items():
            if _name not in live_names:
                _widget.hide()
                _widget.deleteLater()

    # --------------------------------- plans (v5.49, item 32)
    def _refresh_plans(self):
        """Rebuild the Reminders page's Plan rows from
        planner.plan_progress() (v5.49, programme item 32).
        No-op until the page has been built. The plan ids come
        from planner's own store loader (the GUI already reads
        planner storage helpers directly for routines); every
        row's numbers come from the public plan_progress()."""
        layout = getattr(self, "plans_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hide NOW, delete later: a deleteLater-only row
                # keeps painting at its old geometry until the
                # deferred deletion lands, overlapping the row
                # that replaces it (caught by the item-32 render
                # check — the stale "No plans yet" label over the
                # first plan row).
                widget.hide()
                widget.deleteLater()
        self._plan_resume_buttons = {}
        progresses = []
        try:
            from voice_assistant.core.gui_facade import planner  # Phase 3 B2-remainder: planner moved to core/brain/

            plans, _damaged = planner._load_plans()
            for plan in plans or []:
                if isinstance(plan, dict) and plan.get("id"):
                    prog = planner.plan_progress(str(plan["id"]))
                    if prog.get("found"):
                        progresses.append(prog)
        except Exception:  # noqa: BLE001 — the page still builds
            progresses = []
        if not progresses:
            empty = QLabel("No plans yet — ask VYUK: “make a plan…”")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            return
        for prog in progresses:
            row = self._plan_row(prog)
            layout.addWidget(row)
            _polish_row_labels(row)