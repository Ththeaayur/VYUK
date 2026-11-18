"""WatcherEngine lifecycle-loop-emit mixin, split from core/watchers.py (B45).

First mixin of the composed WatcherEngine: construction, start/stop, the
polling loop, probing, emission, and the meeting/anticipation/presence
watchers. Cross-mixin self-calls run watchers -> lifecycle only (measured
self-call graph); the composed class MRO resolves them.
"""

# INTENTIONAL (skills split B45): stdlib names the moved code resolves
# at module level, found by the import-completeness prover.
import threading
from datetime import datetime

# INTENTIONAL (skills split B45): lifecycle methods resolve probe/writer
# names by bare name; imported here so they resolve locally (§3.5 P3).
from .watchers_network_system import POLL_SECONDS, DISK_SCAN_TOP_COUNT
from .watchers_foreground_mic_camera import (
    _DEFAULT_PROBES,
    _set_live_engine,
    _clear_live_engine,
    _is_probe_success,
)
from .watchers_health_digest import (
    _default_event_writer,
    _default_timeline_events,
)
from .watchers_file_privacy import _record_structured_value

FAST_POLL_SECONDS = 5  # N10 (v5.45): while any watcher is confirming

SUPPRESS_SECONDS = 60  # same kind never re-fires within this window

class WatcherEngineLifecycleMixin:
    def __init__(self, on_event, probes=None, poll_seconds=POLL_SECONDS,
                 now_fn=datetime.now, timeline_reader=None,
                 event_writer=None, network_changed_quiet=True,
                 power_policy=None, sleeptime_hook=None):
        self._on_event = on_event
        # Sleeptime idle trigger (2026-11-03): optional callable
        # taking idle_seconds (float) and returning a report dict
        # (or None). Called ONCE per away episode, at the moment
        # _watch_idle arms the episode (see _sleeptime_pass).
        # Default None = off: engines without a hook behave
        # exactly as before. The shipped hook is
        # w8_wiring.maybe_sleeptime_on_idle, wired in
        # gui/window_memory._init_watchers; the hook is injected
        # (never imported here) so this module stays decoupled
        # from the memory wiring and tests drive it with fakes.
        self._sleeptime_hook = sleeptime_hook
        self._probes = dict(_DEFAULT_PROBES)
        if probes:
            self._probes.update(probes)
        self._poll_seconds = poll_seconds
        # v5.56 (PF-12): optional power policy — a zero-arg
        # callable returning a core/power_policy.current_policy()
        # dict (see power_policy.watcher_policy_fn()). When None
        # (the default) cadence is byte-identical to before;
        # when set, current_poll_seconds() multiplies the STEADY
        # poll only — the N10 fast poll for confirming states
        # still wins, so throttling never hides a watcher that
        # is mid-confirmation. Production attaches it via
        # power_policy.apply_to_watcher(engine).
        self._power_policy = power_policy
        self._now_fn = now_fn
        # N3: where the welcome-back digest reads the timeline
        # from — a zero-arg callable returning senses-log event
        # dicts. Tests inject a fake; production uses the log.
        self._timeline_reader = (timeline_reader
                                 if timeline_reader is not None
                                 else _default_timeline_events)
        # v5.45: where timeline-ONLY writes go (quiet
        # network_changed lines, hourly light readings, held-back
        # break nudges). Tests inject a capture; production writes
        # to the senses log. Signature: (kind, message, value,
        # unit) — see _write_timeline.
        self._event_writer = (event_writer if event_writer is not None
                              else _default_event_writer)
        # N5: an SSID switch is a quiet timeline event by default
        # — it reaches on_event (chat/speech) only when the engine
        # is explicitly built non-quiet.
        self._network_changed_quiet = bool(network_changed_quiet)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._last_emitted: dict[tuple[str, str], datetime] = {}

        # Health tracking (v5.42) — never raises, never changes
        # event behaviour. _probe_last_success maps probe name ->
        # datetime of its last successful reading (see
        # _is_probe_success for the per-probe definition);
        # _last_tick_at/_tick_count are the engine heartbeat.
        self._probe_last_success: dict = {
            name: None for name in self._probes}
        self._probe_ever_succeeded: dict = {
            name: False for name in self._probes}
        self._last_tick_at = None
        self._tick_count = 0
        self._stopped = True  # not live until start() or tick()

        # Battery state
        self._batt_low_fired = False
        self._batt_crit_fired = False
        self._batt_full_fired = False
        # N9 (v5.45): observed drain history + forecast one-shot,
        # and the last reading (the N10 fast-poll check reads it).
        self._batt_history: list = []
        self._batt_forecast_fired = False
        self._last_batt_percent = None
        self._last_batt_charging = None
        # Downloads state (baseline seeded on the first tick — which
        # start() runs immediately, so "engine start" it is)
        self._dl_baseline: dict[str, int] | None = None
        self._dl_prev: dict[str, int] = {}
        self._dl_announced: set[str] = set()
        # N6 (v5.45): when each candidate was first seen (the
        # minimum-age rule) + whether any candidate changed size
        # on the latest tick (the N10 fast-poll signal).
        self._dl_first_seen: dict = {}
        self._dl_changed_last_tick = False
        # Network state
        self._net_fail_streak = 0
        self._net_down_announced = False
        # v5.52 (item 6): the N5 ladder verdict from the moment
        # the drop was announced — kept so core/ambient can render
        # the SAME hedged words to the brain instead of a bare
        # "offline" (see network_state_snapshot). Cleared when
        # the connection is seen back.
        self._net_last_verdict = None
        # N5 (v5.45): the SSID at the last tick (None = unknown /
        # not seeded yet — the first reading is the baseline).
        self._net_ssid = None
        self._net_ssid_seeded = False
        # N13 (v5.45): the foreground process name at the last
        # tick (normalised; "" = could not be determined).
        self._fg_name = ""
        # N12 (v5.45): the last ambient-light capture ATTEMPT (the
        # hourly cap applies to attempts too — a busy camera must
        # not be retried every poll for an hour).
        self._light_last_attempt = None
        # Idle state (N3: _away_since is the moment the away
        # episode armed — the digest's window opens there)
        self._away = False
        self._away_since: datetime | None = None
        self._away_peak_seconds = 0.0
        # Disk state
        self._disk_low_fired = False
        # Drives state (baseline seeded on the first tick)
        self._known_drives: set[str] | None = None
        # Clipboard state (baseline seeded on the first tick; only
        # fingerprints are ever stored — never copied content)
        self._clip_prev: tuple | None = None
        self._clip_seeded = False
        # Privacy state (apps using camera/mic at the last tick)
        self._priv_prev: frozenset | None = None
        # N1 (v5.44): the last successful privacy reading on THIS
        # engine, mirrored into the module cache by _watch_privacy
        # so current_privacy_state() can serve it without probing.
        self._last_privacy_state: frozenset | None = None
        # Fullscreen state (episode start + one-shot flag)
        self._fs_since: datetime | None = None
        self._fs_fired = False
        # Load state (S14: consecutive hot polls + one-shot flag)
        self._load_hot_streak = 0
        self._load_fired = False
        # Network flap state (S14: delivered drop timestamps)
        self._net_down_times: list[datetime] = []

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._stopped = False
        _set_live_engine(self)
        self._thread = threading.Thread(
            target=self._loop, name="WatcherEngine", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stopped = True
        _clear_live_engine(self)
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 — tick() shouldn't raise, but the loop must outlive anything
                print(f"[Watchers] tick failed: {e}")
            # N10 (v5.45): the wait is state-dependent — fast while
            # any watcher is mid-confirmation, steady when clear.
            self._stop_event.wait(self.current_poll_seconds())

    def current_poll_seconds(self) -> float:
        """N10 (v5.45): the interval the loop should wait NOW —
        FAST_POLL_SECONDS while any watcher is in a confirming /
        uncertain state (one network failure recorded and not yet
        resolved, battery under 25% while unplugged, a download
        changed size on the last tick), relaxing back to the
        configured steady poll when all clear. Never faster than
        the configured poll when that is already the faster one
        (tests drive sub-second polls). Never raises.
        v5.56 (PF-12): with a power policy attached, the STEADY
        poll is multiplied by the policy (x4 on battery, x8 at
        <=20%) — the fast-poll branches above still win, so a
        confirming watcher is never slowed by throttling."""
        try:
            steady = float(self._poll_seconds or POLL_SECONDS)
            try:
                policy_fn = getattr(self, "_power_policy", None)
                if callable(policy_fn):
                    policy = policy_fn()
                    if isinstance(policy, dict) \
                            and policy.get("throttle_watchers"):
                        mult = policy.get("watcher_multiplier", 4)
                        try:
                            mult = float(mult)
                        except (TypeError, ValueError):
                            mult = 4.0
                        if mult >= 1:
                            steady = steady * mult
            except Exception:  # noqa: BLE001 — policy down: unthrottled
                pass
            fast = min(steady, float(FAST_POLL_SECONDS))
            if self._net_fail_streak >= 1:
                return fast
            if self._last_batt_charging is False \
                    and self._last_batt_percent is not None \
                    and self._last_batt_percent < 25:
                return fast
            if self._dl_changed_last_tick:
                return fast
            return steady
        except Exception:  # noqa: BLE001
            return POLL_SECONDS

    def _write_timeline(self, kind, message, value=None, unit=None) -> None:
        """One timeline-only write through the event_writer seam.
        Never raises — a lost diary line must not break a watcher."""
        try:
            self._event_writer(kind, message, value, unit)
        except Exception as e:  # noqa: BLE001
            print(f"[Watchers] timeline write for '{kind}' failed: {e}")

    def _probe(self, name):
        """Run one probe; any exception becomes None (watcher skips)."""
        try:
            result = self._probes[name]()
        except Exception as e:  # noqa: BLE001
            print(f"[Watchers] probe '{name}' failed: {e}")
            return None
        # Health tracking (v5.42): record success per
        # _is_probe_success; tracking must never raise.
        try:
            if _is_probe_success(name, result):
                self._probe_last_success[name] = self._now_fn()
                self._probe_ever_succeeded[name] = True
        except Exception:  # noqa: BLE001
            pass
        return result

    @staticmethod
    def _valid_culprit(reading):
        """Validate a top-process probe reading (N2): a
        (name, value) pair with a non-empty name and a positive
        number, else None (caller degrades to the plain message)."""
        try:
            if not reading:
                return None
            name, value = reading[0], float(reading[1])
            name = str(name).strip()
            if not name or value <= 0:
                return None
            return (name, value)
        except Exception:  # noqa: BLE001 — a malformed reading is no culprit
            return None

    @staticmethod
    def _valid_folders(reading):
        """Validate a disk_top_folders probe reading (N2): a
        list of (name, positive-size) pairs, biggest first,
        capped at DISK_SCAN_TOP_COUNT — else None (the caller
        offers the scan in words instead)."""
        try:
            if not reading:
                return None
            cleaned = []
            for item in reading:
                name, size = str(item[0]).strip(), float(item[1])
                if name and size > 0:
                    cleaned.append((name, size))
            if not cleaned:
                return None
            cleaned.sort(key=lambda kv: kv[1], reverse=True)
            return cleaned[:DISK_SCAN_TOP_COUNT]
        except Exception:  # noqa: BLE001 — a malformed scan is no scan
            return None

    def _emit(self, kind: str, message: str, out: list,
              value=None, unit=None) -> bool:
        """Deliver one event; returns True iff it was delivered.

        Suppression dedupes REPEATS: the same notice — same kind AND
        same message — is not delivered twice within
        SUPPRESS_SECONDS of its last ACTUAL delivery. A different
        message under the same kind (battery low at a new
        percentage, a different finished download) is a new event,
        not a repeat, and is never eaten.

        Callers set their one-shot flags ONLY when this returns
        True. That contract is the whole point: if a notice is
        suppressed, the watcher's flag stays unset, so the event
        stays pending and is retried on later ticks (delivered once
        the window passes) instead of being silently lost for the
        rest of the episode — the flaky-charger case, where a
        re-armed battery_low inside the window used to vanish
        forever. Callback exceptions are caught and printed; the
        event still counts as delivered (it reached the listener).
        """
        # Conversion pass (Wave 7, PF-20): quiet hours HOLD
        # proactive notices — held, never dropped. Returning False
        # here leaves the caller's flag unset, so the event stays
        # pending for a later tick (the same contract as
        # suppression). Alarms are exempt and never travel this
        # path. With no quiet window set the gate allows, so a
        # default install's notices are unchanged.
        if kind != "alarm":
            try:
                try:
                    from . import w7_wiring as _w7
                except Exception:  # noqa: BLE001 — not a package import
                    from voice_assistant.core import w7_wiring as _w7
                _allowed, _reason = _w7.quiet_gate_for_watchers(
                    self._now_fn())
                if not _allowed:
                    return False
            except Exception:  # noqa: BLE001 — a gate failure must not silence notices
                pass
        now = self._now_fn()
        key = (kind, message)
        last = self._last_emitted.get(key)
        if last is not None and (now - last).total_seconds() < SUPPRESS_SECONDS:
            return False
        self._last_emitted[key] = now
        # N7 (v5.45): a delivered notice's structured measurement
        # is recorded for the timeline append that follows it.
        if value is not None:
            _record_structured_value(kind, message, value, unit)
        out.append((kind, message))
        try:
            self._on_event(kind, message)
        except Exception as e:  # noqa: BLE001 — a bad listener must not kill noticing
            print(f"[Watchers] on_event for '{kind}' failed: {e}")
        return True

    def tick(self) -> list:
        """ONE poll of all watchers. Never raises. Returns the list
        of (kind, message) events emitted during this poll."""
        # Heartbeat (v5.42): count + timestamp every tick, and
        # register as the live engine so watcher_health() can
        # surface this engine (covers manual tick() driving too).
        try:
            self._tick_count += 1
            self._last_tick_at = self._now_fn()
            self._stopped = False
            _set_live_engine(self)
        except Exception:  # noqa: BLE001 — tracking must never raise
            pass
        out: list = []
        for watcher in (self._watch_battery, self._watch_downloads,
                        self._watch_network, self._watch_idle,
                        self._watch_disk, self._watch_drives,
                        self._watch_clipboard, self._watch_privacy,
                        self._watch_foreground, self._watch_fullscreen,
                        self._watch_load, self._watch_ambient_light,
                        self._watch_meetings, self._watch_anticipation,
                        self._watch_presence):
            try:
                watcher(out)
            except Exception as e:  # noqa: BLE001 — one watcher must not kill the others
                print(f"[Watchers] {watcher.__name__} failed: {e}")
        # v5.49 (programme item 35): flush suggestions the
        # discipline held back (in-call holds, throttle deferrals)
        # — the post-call digest leaves through here.
        try:
            try:
                from .brain import suggestions as _suggestions  # Phase 3 B1b: moved to core/brain/
            except Exception:  # noqa: BLE001 — not a package import
                from voice_assistant.core.brain import suggestions \
                    as _suggestions  # Phase 3 B1b: suggestions moved to core/brain/
            _suggestions.poll_suggestions()
        except Exception:  # noqa: BLE001 — flushing must not kill a tick
            pass
        return out

    def _watch_meetings(self, out) -> None:
        """v5.49 (programme item 35): the meeting-imminent
        producer for the suggestions engine. Polls the next
        calendar events (the next_events probe) and hands them to
        core.brain.suggestions.poll_meeting_suggestions — an event
        starting within ~10 minutes raises ONE suggestion (the
        registry dedupes the 30 s re-polls). Emits nothing
        through on_event: suggestions travel their own delivery
        seam, not the notice path."""
        events = self._probe("next_events")
        if not events:
            return
        try:
            try:
                from .brain import suggestions as _suggestions  # Phase 3 B1b: moved to core/brain/
            except Exception:  # noqa: BLE001 — not a package import
                from voice_assistant.core.brain import suggestions \
                    as _suggestions  # Phase 3 B1b: suggestions moved to core/brain/
            _suggestions.poll_meeting_suggestions(
                events, now=self._now_fn())
        except Exception:  # noqa: BLE001 — a producer must not kill a tick
            pass

    def _watch_anticipation(self, out) -> None:
        """Conversion pass (Wave 7, AN-1): the live anticipation
        poll. Offers the engine's cached foreground NAME (names
        only, never a title) to the CA-1 capture session — which
        refuses it until a live opt-in + indicator are granted —
        then polls the predictor's suggestion: below the
        evidence floor this raises NOTHING (silence, never a
        hedged card, never proactive speech); above it, one
        suggestion card through the live suggestions registry.
        Emits nothing through on_event: suggestion cards travel
        their own delivery seam, not the notice path."""
        try:
            try:
                from . import w7_wiring as _w7
            except Exception:  # noqa: BLE001 — not a package import
                from voice_assistant.core import w7_wiring as _w7
            name = getattr(self, "_fg_name", None)
            if name:
                _w7.note_foreground_app(name)
            _w7.poll_anticipation_suggestion()
        except Exception:  # noqa: BLE001 — a producer must not kill a tick
            pass

    def _watch_presence(self, out) -> None:
        """Conversion pass (Wave 7, presence lock): one poll of
        the presence lock on the watcher tick. DISABLED by
        default — poll_presence_lock() returns "disabled" and
        evaluates nothing until enable_presence_lock() is
        called, so a default install never locks anything from
        this tick. When enabled, the idle-probe signal drives
        evaluate() (unknown presence holds, never locks)."""
        try:
            try:
                from . import w7_wiring as _w7
            except Exception:  # noqa: BLE001 — not a package import
                from voice_assistant.core import w7_wiring as _w7
            _w7.poll_presence_lock()
        except Exception:  # noqa: BLE001 — a poller must not kill a tick
            pass
