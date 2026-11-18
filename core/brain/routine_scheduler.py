"""
Routine scheduler for VYUK (Presence pack, v2.1): fires scheduled
routines from core/brain/planner.py at their times.

A daemon thread ticks every 20 seconds. On each tick it re-reads
data/routines.json (via planner.get_routines(), so edits and new
routines are picked up live), and for every routine carrying a
"schedule" it asks planner.next_run() for the pending slot: if that
slot is at/before now, the routine fires — exactly once per slot.
Firing history lives in data/routine_state.json
({routine name: last_run ISO timestamp}), written atomically
(temp file + os.replace) BEFORE the callback runs, so a crash or a
restart can never re-fire a slot that already fired.

What "firing" does is the caller's business: the constructor takes
an on_fire(routine_dict) callback. The GUI wires it to "if a brain
session is connected, ask the brain to run it (so the user hears it
happen); otherwise run planner.run_routine() headlessly". Either
way the trust layer is untouched — scheduled routines run through
the same tools.execute() path and can never inject confirm=True.

All time logic delegates to planner.next_run(); this module only
adds the state file and the thread. `now_fn` is injectable so tests
can drive tick() deterministically without sleeping.

Standard library only; imports cleanly on any OS.
"""

import datetime
import json
import os
import threading
from pathlib import Path

from . import planner  # Phase 3 B2-remainder: planner now in core/brain/; B1b interim closed

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"  # Phase 3 B1b: one level deeper in core/brain/
_STATE_PATH = _DATA_DIR / "routine_state.json"

TICK_SECONDS = 20


def _load_state() -> dict:
    """{name: last_run_iso}. Missing/corrupt/wrong-type -> {}."""
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — corrupt state must never kill scheduling
        return {}


def _save_state(state: dict) -> None:
    """Write the state dict atomically-ish (temp file + os.replace)."""
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_name(_STATE_PATH.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _STATE_PATH)
    except Exception as e:  # noqa: BLE001
        print(f"[RoutineScheduler] could not write state file: {e}")


def _parse_iso(value) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


class RoutineScheduler:
    """Ticks on a daemon thread and fires due scheduled routines.

    on_fire(routine_dict) is called on the scheduler thread — it must
    be quick or hand off to another thread/queue itself. Exceptions
    inside on_fire are caught and printed; they never kill the loop.
    """

    def __init__(self, on_fire, now_fn=None, interval_seconds: int = TICK_SECONDS):
        self._on_fire = on_fire
        self._now_fn = now_fn or datetime.datetime.now
        self._interval = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Interval routines that have never run anchor their first
        # fire one interval after the scheduler starts (see
        # planner.next_run's docstring) — tick() passes this as the
        # fallback last_run for them.
        self.started_at = self._now_fn()

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.started_at = self._now_fn()
        self._thread = threading.Thread(
            target=self._loop, name="RoutineScheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 — the loop must outlive any tick bug
                print(f"[RoutineScheduler] tick failed: {e}")
            self._stop_event.wait(self._interval)

    # -- one scheduling pass ----------------------------------------------

    def tick(self) -> list[str]:
        """Check every scheduled routine once; fire the due ones.

        Returns the names fired (handy for tests and logging). Public
        so tests can drive the scheduler deterministically with an
        injected now_fn instead of waiting for the thread.
        """
        now = self._now_fn()
        state = _load_state()
        fired: list[str] = []
        for routine in planner.get_routines():
            schedule = routine.get("schedule")
            if not isinstance(schedule, dict):
                continue
            name = str(routine.get("name", "")).strip()
            if not name:
                continue
            last_run = _parse_iso(state.get(name))
            if schedule.get("kind") == "interval":
                anchor = last_run if last_run is not None else self.started_at
                due = planner.next_run(routine, now, anchor)
            else:
                due = planner.next_run(routine, now, last_run)
            if due is None or due > now:
                continue
            # Record BEFORE firing: if on_fire crashes the process,
            # this slot still counts as fired and won't repeat.
            state[name] = now.isoformat(timespec="seconds")
            _save_state(state)
            fired.append(name)
            try:
                self._on_fire(routine)
            except Exception as e:  # noqa: BLE001
                print(f"[RoutineScheduler] on_fire for routine '{name}' failed: {e}")
        # Conversion pass (Wave 7, PF-22): the daily-alarm tick
        # rides this live scheduler tick. The first tick in a
        # process runs the missed start-up pass (missed alarms
        # are REPORTED in w7_wiring.last_daily_tick_report(),
        # never dropped, never fired late); every tick fires due
        # alarms. With no daily store file present this is an
        # empty report — a default install pays one missing-file
        # read per tick and nothing else.
        try:
            try:
                from .. import w7_wiring as _w7  # Phase 3 B1b: moved to core/brain/
            except Exception:  # noqa: BLE001 — not a package import
                from voice_assistant.core import w7_wiring as _w7
            _w7.daily_tick(now)
        except Exception as e:  # noqa: BLE001 — an alarm tick must not kill routine scheduling
            # GAPS-26 (S-V3): a failed rider is REPORTED, never
            # swallowed — the tick carries on to the next rider
            # (and the routine firing above already ran).
            print(f"[RoutineScheduler] daily-alarm tick failed: {e}")
        # Conversion pass (Wave 6): the retention + overdue-promise
        # tick rides this live scheduler tick alongside the Wave 7
        # hook above (which is untouched). It surfaces stale
        # facts (flagged, never deleted/auto-confirmed) and overdue
        # promises in w6_wiring.last_w6_tick_report(). With no
        # file store present this is an empty report.
        try:
            try:
                from .. import w6_wiring as _w6  # Phase 3 B1b: moved to core/brain/
            except Exception:  # noqa: BLE001 — not a package import
                from voice_assistant.core import w6_wiring as _w6
            _w6.w6_tick(now)
        except Exception as e:  # noqa: BLE001 — a retention tick must not kill routine scheduling
            # GAPS-26 (S-V3): reported, never swallowed (see above).
            print(f"[RoutineScheduler] retention tick failed: {e}")
        # Wave 10 (PF-27): the scheduled-backup tick rides this
        # live scheduler tick alongside the hooks above (which are
        # untouched). Due -> a bundle lands in data/backups/ and
        # the schedule record advances; a failure is RECORDED on
        # the record (surfaced by backup_status), never swallowed.
        # With no data dir present this is a no-op report.
        try:
            try:
                from .. import w10_wiring as _w10  # Phase 3 B1b: moved to core/brain/
                from ..config import DATA_DIR as _DD  # Phase 3 B1b: moved to core/brain/
                from ..config import config as _cfg  # Phase 3 B1b: moved to core/brain/
            except Exception:  # noqa: BLE001 — not a package import
                from voice_assistant.core import w10_wiring as _w10
                from voice_assistant.core.config import DATA_DIR as _DD
                from voice_assistant.core.config import config as _cfg
            _settings = _w10.collect_portability_settings(_cfg)
            _w10.backup_tick(now, str(_DD), _settings)
        except Exception as e:  # noqa: BLE001 — a backup tick must not kill routine scheduling
            # GAPS-26 (S-V3): reported, never swallowed (see above).
            print(f"[RoutineScheduler] scheduled-backup tick failed: {e}")
        return fired
