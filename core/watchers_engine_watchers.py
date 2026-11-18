"""WatcherEngine watchers mixin, split from core/watchers.py (skills split B45).

Second mixin of the composed WatcherEngine: the per-domain watch methods
(load, battery, downloads, network, sleeptime, idle, disk, drives,
clipboard, privacy, foreground, ambient light, fullscreen).
"""

# INTENTIONAL (skills split B45): stdlib names the moved code resolves
# at module level, found by the import-completeness prover.
from pathlib import Path

# INTENTIONAL (skills split B45): watch methods resolve probe/helper names
# by bare name; imported here so they resolve locally (§3.5 P3 trap).
# NOTE: current_privacy_state (the method below) calls the G2 FUNCTION of
# the same name by bare name — the bare name resolves to this module
# global, not to the method; verbatim from the old module.
from .watchers_network_system import (
    _human_bytes,
    diagnose_network_verdict,
    _folder_phrase,
)
from .watchers_foreground_mic_camera import (
    estimate_minutes_to_empty,
    _os_estimate_is_wild,
    _normalise_process_name,
    _set_cached_foreground,
    _MEDIA_APP_NAMES,
)
from .watchers_health_digest import compose_welcome_digest
from .watchers_file_privacy import (
    current_privacy_state,
    _set_cached_privacy_state,
)

# N6 (v5.45): a download counts as finished only when its size is
# stable across polls, no live sibling partial remains, AND it has
# been around at least this long (a just-appeared stable file is a
# stalled download until it proves otherwise by surviving).
DOWNLOAD_MIN_AGE_SECONDS = 10

# Extensions whose finished downloads earn an automatic sniff_file
# verdict (executables + archives) in the completion notice.
_SNIFF_EXTS = frozenset({
    ".exe", ".msi", ".com", ".scr", ".bat", ".cmd", ".ps1",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".jar", ".apk", ".dmg", ".pkg", ".deb", ".rpm", ".iso",
})

# N9 (v5.45): battery foresight — observed drain-rate estimates.
BATTERY_FORECAST_PERCENT = 30      # heads-up only at/below this…

BATTERY_FORECAST_WITHIN_MINUTES = 45  # …and only when empty is near

BATTERY_FORECAST_REARM_PERCENT = 35

_BATT_HISTORY_MAX = 30             # samples of (when, percent)

# N12 (v5.45): ambient light — at most one frame per hour.
AMBIENT_LIGHT_INTERVAL_SECONDS = 3600

BATTERY_LOW_PERCENT = 20

BATTERY_CRITICAL_PERCENT = 10

BATTERY_FULL_PERCENT = 100

REARM_RISE = 5  # percent points above a threshold that re-arm it

DISK_LOW_FRACTION = 0.10

DISK_REARM_FRACTION = 0.12

IDLE_AWAY_SECONDS = 20 * 60

IDLE_BACK_SECONDS = 10

NETWORK_FAILS_TO_ANNOUNCE = 2

NETWORK_FLAP_COUNT = 3          # drops inside the window = "keeps dropping"

NETWORK_FLAP_WINDOW_SECONDS = 60 * 60

LOAD_CPU_PERCENT = 90           # sustained-load watcher (S14)

LOAD_MEM_PERCENT = 92

LOAD_TICKS_TO_ANNOUNCE = 4      # 4 x 30 s polls = ~2 min of strain

LOAD_CLEAR_CPU = 70             # re-arm only once well below the line

LOAD_CLEAR_MEM = 80

CLIPBOARD_LONG_TEXT_CHARS = 200  # shorter copies are too chatty to note

FULLSCREEN_SUGGEST_SECONDS = 600  # 10 minutes in fullscreen → one nudge

_TEMP_SUFFIXES = (".crdownload", ".part", ".tmp", ".download",
                  ".opdownload", ".aria2")

class WatcherEngineWatchersMixin:
    def _watch_load(self, out) -> None:
        reading = self._probe("load")
        if not reading:
            return  # no psutil — watcher off
        cpu, mem = float(reading[0]), float(reading[1])
        if cpu >= LOAD_CPU_PERCENT or mem >= LOAD_MEM_PERCENT:
            self._load_hot_streak += 1
            if (self._load_hot_streak >= LOAD_TICKS_TO_ANNOUNCE
                    and not self._load_fired):
                # Name whichever resource is straining harder
                # (the bigger overshoot past its own threshold).
                # N2: then name the culprit — the top process by
                # that resource. A probe that finds nothing (or
                # fails — _probe never raises) degrades to the
                # plain pre-N2 sentence, byte for byte.
                if (mem - LOAD_MEM_PERCENT) >= (cpu - LOAD_CPU_PERCENT):
                    base = ("Your machine has been straining for "
                            f"a few minutes — memory is at "
                            f"{mem:.0f}% full")
                    message = base + "."
                    culprit = self._valid_culprit(
                        self._probe("top_memory_process"))
                    if culprit is not None:
                        name, rss = culprit
                        message = (f"{base} — {name} is using "
                                   f"{_human_bytes(rss)}.")
                    # N7: the straining resource's number rides
                    # the notice as its structured value.
                    _value, _unit = mem, "%"
                else:
                    base = ("Your machine has been straining for "
                            f"a few minutes — the processor is "
                            f"at {cpu:.0f}%")
                    message = base + "."
                    culprit = self._valid_culprit(
                        self._probe("top_cpu_process"))
                    if culprit is not None:
                        name, pct = culprit
                        message = (f"{base} — {name} is using "
                                   f"{pct:.0f}% of the processor.")
                    _value, _unit = cpu, "%"
                if self._emit("system_load", message, out,
                              value=_value, unit=_unit):
                    self._load_fired = True
            return
        self._load_hot_streak = 0
        if cpu < LOAD_CLEAR_CPU and mem < LOAD_CLEAR_MEM:
            self._load_fired = False

    def _batt_time_left_suffix(self, minutes_left, observed) -> str:
        """The time-left sentence for a battery notice (N9): the
        OS estimate when it is present and sane (the original
        wording, byte for byte); otherwise the observed
        drain-rate estimate, LABELLED as an estimate; otherwise
        nothing. `observed` is estimate_minutes_to_empty's result
        for the current discharge (or None)."""
        if minutes_left and not _os_estimate_is_wild(minutes_left,
                                                     observed):
            return f" About {minutes_left} minutes left."
        if observed:
            return (f" At this rate, about {int(round(observed))} "
                    "minutes left (my estimate).")
        return ""

    def _watch_battery(self, out) -> None:
        reading = self._probe("battery")
        if not reading:
            return  # no battery (desktop) or probe down — watcher off
        # Probes may be 2-tuples (older fakes) or the current
        # 3-tuple with the OS time-left estimate.
        percent, charging = reading[0], reading[1]
        minutes_left = reading[2] if len(reading) > 2 else None
        percent = int(percent)
        # N9/N10 bookkeeping: the last reading (fast-poll check)
        # and the observed drain history. Charging ends a
        # discharge episode — the history (and the forecast's
        # one-shot) reset, so the next discharge projects from
        # its own samples only.
        self._last_batt_percent = percent
        self._last_batt_charging = bool(charging)
        if charging:
            self._batt_history = []
            self._batt_forecast_fired = False
        else:
            self._batt_history.append((self._now_fn(), percent))
            self._batt_history = self._batt_history[-_BATT_HISTORY_MAX:]
            if percent >= BATTERY_FORECAST_REARM_PERCENT:
                self._batt_forecast_fired = False
        observed = (None if charging
                    else estimate_minutes_to_empty(self._batt_history))

        if charging:
            # Plugged in re-arms both discharge thresholds.
            self._batt_low_fired = False
            self._batt_crit_fired = False
            if percent >= BATTERY_FULL_PERCENT:
                if not self._batt_full_fired:
                    if self._emit("battery_full",
                                  "Battery's full — you're good to unplug.", out):
                        self._batt_full_fired = True
                return
        # Not (charging at 100%): a future charge cycle can fill again.
        if not (charging and percent >= BATTERY_FULL_PERCENT):
            self._batt_full_fired = False
        # Rising well above a threshold re-arms it too.
        if percent >= BATTERY_LOW_PERCENT + REARM_RISE:
            self._batt_low_fired = False
        if percent >= BATTERY_CRITICAL_PERCENT + REARM_RISE:
            self._batt_crit_fired = False
        if charging:
            return
        if percent <= BATTERY_CRITICAL_PERCENT:
            if not self._batt_crit_fired:
                # Flags are set only on actual delivery (see _emit):
                # a suppressed notice stays pending, never lost.
                _left = self._batt_time_left_suffix(minutes_left, observed)
                if self._emit(
                        "battery_critical",
                        f"Battery is at {percent}% — plug in soon, "
                        "or I'll have to say goodbye." + _left, out,
                        value=percent, unit="%"):
                    self._batt_crit_fired = True
                    self._batt_low_fired = True  # critical says it all
        elif percent <= BATTERY_LOW_PERCENT:
            if not self._batt_low_fired:
                _left = self._batt_time_left_suffix(minutes_left, observed)
                if self._emit(
                        "battery_low",
                        f"Your battery is at {percent}% and not "
                        f"charging." + _left, out,
                        value=percent, unit="%"):
                    self._batt_low_fired = True
        # N9 foresight: a SOFT heads-up at 30% — only when the
        # observed projection says empty is genuinely near
        # (~45 min). It rides its own kind so the GUI maps it to
        # the Battery toggle; speech-eligibility is the GUI's
        # business, like every notice. The number is labelled
        # an estimate in the text itself, and the structured
        # value is the projected minutes.
        if (not self._batt_forecast_fired
                and percent <= BATTERY_FORECAST_PERCENT
                and observed is not None
                and observed <= BATTERY_FORECAST_WITHIN_MINUTES):
            _mins = int(round(observed))
            if self._emit(
                    "battery_forecast",
                    f"Battery heads-up: you're at {percent}% and, "
                    f"at the current drain rate, it could be empty "
                    f"in about {_mins} minutes (estimate) — "
                    "worth plugging in soon.", out,
                    value=_mins, unit="min"):
                self._batt_forecast_fired = True
                # v5.49 (programme item 35): the notice says it;
                # the suggestion OFFERS the fix — dimming the
                # screen via set_brightness, through the
                # suggestions engine's own discipline.
                try:
                    try:
                        from .brain import suggestions as _suggestions  # Phase 3 B1b: moved to core/brain/
                    except Exception:  # noqa: BLE001
                        from voice_assistant.core.brain import suggestions \
                            as _suggestions  # Phase 3 B1b: suggestions moved to core/brain/
                    _suggestions.raise_battery_forecast_suggestion(
                        percent, _mins)
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _has_live_partial(name: str, snapshot: dict) -> bool:
        """N6: True when the snapshot still holds a live partial
        sibling for `name` — a temp-suffixed file whose stem IS
        the candidate ("setup.exe.crdownload" is "setup.exe"
        still arriving). A browser that renames in place leaves
        no sibling; Chrome/Firefox-style sidecar partials do."""
        for other in snapshot:
            if other == name:
                continue
            low = other.lower()
            for suffix in _TEMP_SUFFIXES:
                if low.endswith(suffix) \
                        and other[:len(other) - len(suffix)] == name:
                    return True
        return False

    def _download_done_message(self, name: str) -> str:
        """The completion notice (N6): the plain sentence, plus —
        for an executable or archive — the sniff_file verdict
        when the sniffer produced one, and an offer to open it
        (wording only; nothing is ever auto-opened). Other files
        keep the original sentence byte for byte."""
        base = f"Your download finished: {name}."
        ext = Path(name).suffix.lower()
        if ext not in _SNIFF_EXTS:
            return base
        verdict = None
        # The sniffer takes the file NAME as an argument, so it
        # cannot ride the no-arg _probe() seam — call the raw
        # probe with the same never-raise discipline instead.
        try:
            raw = self._probes.get("file_sniffer")
            if callable(raw):
                found = raw(name)
                if isinstance(found, dict):
                    verdict = found.get("verdict") or None
        except Exception:  # noqa: BLE001 — no verdict, still offer
            verdict = None
        if verdict:
            return (f"{base} Sniff check: {verdict} "
                    "Say the word and I'll open it.")
        return f"{base} Say the word and I'll open it."

    def _watch_downloads(self, out) -> None:
        current = self._probe("downloads")
        if current is None:
            return  # probe down this tick — keep prior state untouched
        current = dict(current)
        if self._dl_baseline is None:
            # First poll: everything already here is the baseline
            # and is NEVER announced.
            self._dl_baseline = dict(current)
            self._dl_prev = dict(current)
            self._dl_changed_last_tick = False
            return
        now = self._now_fn()
        changed = False
        for name in sorted(current):
            size = current[name]
            if name in self._dl_baseline or name in self._dl_announced:
                continue
            if size <= 0:
                continue
            if self._dl_prev.get(name) != size:
                # New or still growing — either way something in
                # Downloads changed on this tick (the N10
                # fast-poll signal). Partial files count here:
                # a download spends most of its life as one.
                changed = True
            if name.lower().endswith(_TEMP_SUFFIXES):
                continue
            if name not in self._dl_first_seen:
                self._dl_first_seen[name] = now
            if self._dl_prev.get(name) != size:
                continue
            # N6 completion rule — ALL three must hold:
            # size stable across polls (just established),
            # no live sibling partial, and a minimum file age.
            # The old rule (stable size alone) crowned stalled
            # downloads as finished.
            if self._has_live_partial(name, current):
                continue
            age = (now - self._dl_first_seen[name]).total_seconds()
            if age < DOWNLOAD_MIN_AGE_SECONDS:
                continue
            # Marked announced only when actually delivered.
            if self._emit("download_done",
                          self._download_done_message(name), out):
                self._dl_announced.add(name)
                self._dl_first_seen.pop(name, None)
        self._dl_changed_last_tick = changed
        # A file that vanished may be announced again if it truly
        # re-downloads later; baseline names stay announced-never.
        self._dl_announced = {n for n in self._dl_announced if n in current}
        self._dl_first_seen = {n: t for n, t in self._dl_first_seen.items()
                               if n in current}
        self._dl_prev = current

    def _watch_network_ssid(self) -> None:
        """N5: track the current SSID; a switch writes a QUIET
        network_changed timeline event (timeline-only by default
        — it reaches on_event only when the engine was built
        non-quiet, so speech for it is opt-in by construction).
        The first reading is the baseline and is silent; an
        unknown reading (wired / probe down) changes nothing."""
        ssid = self._probe("network_ssid")
        if ssid is None:
            return
        ssid = str(ssid).strip()
        if not ssid:
            return
        if not self._net_ssid_seeded:
            self._net_ssid = ssid
            self._net_ssid_seeded = True
            return
        if ssid == self._net_ssid:
            return
        previous, self._net_ssid = self._net_ssid, ssid
        message = (f"Wi-Fi network changed from {previous} "
                   f"to {ssid}.")
        self._write_timeline("network_changed", message)
        if not self._network_changed_quiet:
            # Opt-in telling: the event ALSO goes out through the
            # normal notice path (the GUI's per-kind gate still
            # applies there). The out list of the current tick is
            # not reachable from here, so deliver via the callback
            # contract directly through _emit's bookkeeping by
            # way of a throwaway list.
            delivered: list = []
            self._emit("network_changed", message, delivered)

    def _watch_network(self, out) -> None:
        self._watch_network_ssid()
        reachable = self._probe("network")
        if reachable is None:
            return  # probe itself failed — not evidence either way
        if reachable:
            self._net_fail_streak = 0
            if self._net_down_announced:
                # The "announced" flag clears only when the
                # back-online notice is actually delivered — state
                # flags and delivered notices must never disagree.
                if self._emit("network_up", "You're back online.", out):
                    self._net_down_announced = False
            if not self._net_down_announced:
                # v5.52 (item 6): the drop is over — its verdict
                # must not linger into a later, different outage.
                self._net_last_verdict = None
            return
        self._net_fail_streak += 1
        if (self._net_fail_streak >= NETWORK_FAILS_TO_ANNOUNCE
                and not self._net_down_announced):
            # N5: the cheap probe has failed twice — NOW the
            # diagnosis ladder runs (never before), and its
            # verdict joins the notice. A ladder that produced
            # nothing usable keeps the original sentence.
            verdict = diagnose_network_verdict(
                self._probe("network_diag"))
            # v5.52 (item 6): remember the verdict with the state
            # (set BEFORE the emit, so a delivered announcement
            # and the mirrored ambient state can never disagree
            # about which outage this was).
            self._net_last_verdict = verdict
            message = ("You've gone offline." if verdict is None
                       else f"You've gone offline — {verdict}.")
            if self._emit("network_down", message, out):
                self._net_down_announced = True
                # S14: count delivered drops; the third inside an
                # hour is a different fact from a single drop, and
                # earns one flap notice (list clears on delivery,
                # so a continuing storm re-earns it an hour on).
                _now = self._now_fn()
                self._net_down_times = [
                    t for t in self._net_down_times
                    if (_now - t).total_seconds()
                    < NETWORK_FLAP_WINDOW_SECONDS]
                self._net_down_times.append(_now)
                if len(self._net_down_times) >= NETWORK_FLAP_COUNT:
                    if self._emit(
                            "network_flap",
                            "Your connection keeps dropping — that's "
                            f"{len(self._net_down_times)} times in "
                            "the last hour.", out):
                        self._net_down_times = []

    def _sleeptime_pass(self, out, idle) -> None:
        """Fire one sleeptime pass at the arming of an away
        episode. Called ONLY from _watch_idle's False→True
        transition, so a pass fires at most once per episode:
        later ticks in the same episode never re-fire, and a
        new episode (away → welcome-back → away again) fires
        again. The hook is caller-supplied (see __init__); with
        no hook this is a no-op.

        Approval/visibility reasoning, stated so it can be
        overruled: the pass is NOT put through a per-pass
        approval dialog, because it fires only after 20 minutes
        of no input (nobody is present to approve) and its
        writes are bounded consolidation of the user's own
        already-stored facts (heat-eligible, byte-changed
        only, file-store seam only — no tools, no network, no
        model calls; see core/sleeptime.idle_pass). It is
        covered by the user's watchers-enabled setting (the
        engine does not run in safe mode or when watchers are
        off), it can never fire from a voice path (this hook
        runs on the watcher thread; run_sleeptime_pass still
        refuses from_voice_path), and it is NOT silent: a pass
        that writes reports through the watcher event channel
        (chat line + senses timeline, like every other watcher
        event). A pass that writes nothing emits nothing —
        no effectful work happened, and a per-episode
        zero-write line would be noise. Best-effort delivery:
        if _emit holds the notice (quiet hours, 60 s
        suppression window) this episode's notice does not
        retry; the writes themselves remain auditable in the
        file store. A hook failure never breaks the watcher.

        The pass itself writes 0 until a candidate/heat source
        exists — see SLEEPTIME_CANDIDATE_GATE in w8_wiring.
        """
        hook = self._sleeptime_hook
        if hook is None:
            return
        try:
            report = hook(float(idle))
        except Exception as e:  # noqa: BLE001 — sleeptime must never break the watcher
            print(f"[Watchers] sleeptime hook failed: {e}")
            return
        if not isinstance(report, dict):
            return
        try:
            writes = int(report.get("writes", 0))
        except (TypeError, ValueError):
            writes = 0
        if writes <= 0:
            return
        noun = "fact" if writes == 1 else "facts"
        self._emit("sleeptime",
                   f"Sleeptime pass while you were away: "
                   f"consolidated {writes} {noun} into "
                   f"long-term memory.",
                   out)

    def _watch_idle(self, out) -> None:
        idle = self._probe("idle_seconds")
        if idle is None:
            return  # off Windows / probe down — watcher off
        idle = float(idle)
        if idle >= IDLE_AWAY_SECONDS:
            if not self._away:
                # N3: the episode arms NOW — the digest window
                # opens at this timestamp.
                self._away_since = self._now_fn()
                # Sleeptime idle trigger (2026-11-03): this
                # transition — and only this transition — fires
                # one sleeptime pass. Later ticks in the same
                # episode do not re-fire (see _sleeptime_pass).
                self._sleeptime_pass(out, idle)
            self._away = True
            self._away_peak_seconds = max(self._away_peak_seconds, idle)
            return
        if self._away:
            self._away_peak_seconds = max(self._away_peak_seconds, idle)
            if idle < IDLE_BACK_SECONDS:
                minutes = int(round(self._away_peak_seconds / 60))
                message = (f"Welcome back — you were away for "
                           f"{minutes} minutes.")
                # N3: compose the while-you-were-out digest from
                # the timeline since the episode armed. Any
                # failure (reader down, log unreadable) or an
                # empty window keeps the greeting exactly as it
                # was before N3.
                digest = None
                try:
                    digest = compose_welcome_digest(
                        self._timeline_reader(), self._away_since)
                except Exception:  # noqa: BLE001 — never break the greeting
                    digest = None
                if digest:
                    message = f"{message} {digest}"
                # Away state clears only on delivery; a suppressed
                # welcome stays pending for the next tick.
                if self._emit("welcome_back", message, out):
                    self._away = False
                    self._away_since = None
                    self._away_peak_seconds = 0.0

    def _watch_disk(self, out) -> None:
        reading = self._probe("disk")
        if not reading:
            return
        total, free = reading
        if not total:
            return
        fraction_free = free / total
        if fraction_free > DISK_REARM_FRACTION:
            self._disk_low_fired = False
        elif fraction_free < DISK_LOW_FRACTION and not self._disk_low_fired:
            percent_left = int(round(fraction_free * 100))
            base = (f"Your main drive is nearly full — "
                    f"only {percent_left}% left")
            # N2: name the biggest top-level folders when the
            # budget-capped scan comes back with a usable list;
            # otherwise offer the scan in words (the scan blew
            # its budget, failed, or found nothing nameable).
            folders = self._valid_folders(
                self._probe("disk_top_folders"))
            if folders:
                message = (f"{base}. The biggest folders are "
                           f"{_folder_phrase(folders)}.")
            else:
                message = (f"{base} — say the word and I'll find "
                           f"what's eating it.")
            if self._emit("disk_low", message, out):
                self._disk_low_fired = True

    def _watch_drives(self, out) -> None:
        current = self._probe("drives")
        if not current:
            return  # empty = probe failure (a real system has >= 1)
        current = set(current)
        if self._known_drives is None:
            self._known_drives = set(current)  # baseline — silent
            return
        undelivered = set()
        for mount in sorted(current - self._known_drives):
            if not self._emit("drive_connected",
                              f"A drive was connected: {mount}.", out):
                # Suppressed: keep the mount "unknown" so the
                # notice is retried on a later tick, not lost.
                undelivered.add(mount)
        self._known_drives = set(current) - undelivered

    def _watch_clipboard(self, out) -> None:
        fp = self._probe("clipboard")
        if fp is None:
            return  # off Windows / nothing readable — watcher off
        if not self._clip_seeded:
            # First readable poll is the baseline and is silent.
            self._clip_prev = fp
            self._clip_seeded = True
            return
        if fp == self._clip_prev:
            return
        category, length = fp[0], fp[1]
        if category == "link":
            message = ("You copied a link — say 'sniff what I copied' "
                       "and I'll check where it leads.")
        elif category == "path":
            message = "You copied a file path."
        elif length >= CLIPBOARD_LONG_TEXT_CHARS:
            message = f"You copied a long passage ({length} characters)."
        else:
            # A short ordinary copy: noted in state, never announced.
            self._clip_prev = fp
            return
        # _clip_prev advances only on actual delivery, so a
        # suppressed notice stays pending instead of being lost.
        if self._emit("clipboard", message, out):
            self._clip_prev = fp

    def current_privacy_state(self) -> dict:
        """N1 (v5.44): the current privacy state (see the module
        accessor of the same name) — this engine's ticks keep the
        shared cache fresh, so reading it here never probes."""
        return current_privacy_state()

    def _watch_privacy(self, out) -> None:
        current = self._probe("privacy")
        if current is None:
            return  # off Windows / probe down — watcher off
        current = frozenset(current)
        # N1: publish the fresh reading — the speech gate reads
        # the cache, never a probe of its own.
        self._last_privacy_state = current
        _set_cached_privacy_state(current)
        if self._priv_prev is None:
            # Baseline (v5.42): seed silently when nothing is in
            # use, but if camera/mic are ALREADY in use on the
            # first poll, announce that fact exactly once — the
            # user should know something was already watching/
            # listening before VYUK started. Seeding is otherwise
            # unchanged: _priv_prev is set either way, so these
            # users are not re-announced on later ticks.
            self._priv_prev = current
            if current:
                _base = sorted(current)
                _cams = [e.split(":", 1)[1] for e in _base
                         if e.startswith("webcam:")]
                _mics = [e.split(":", 1)[1] for e in _base
                         if e.startswith("microphone:")]
                _parts = []
                if _cams:
                    _parts.append(
                        f"Your camera is already in use by {_cams[0]} "
                        f"(it was on before VYUK started watching).")
                if _mics:
                    _parts.append(
                        f"Your microphone is already in use by "
                        f"{_mics[0]} (it was on before VYUK started "
                        f"watching).")
                if _parts:
                    self._emit("privacy", " ".join(_parts), out)
            return
        new = sorted(current - self._priv_prev)
        if not new:
            # Apps stopping (or nothing changing) is silent, but
            # the state still tracks the present.
            self._priv_prev = current
            return
        cams = [e.split(":", 1)[1] for e in new
                if e.startswith("webcam:")]
        mics = [e.split(":", 1)[1] for e in new
                if e.startswith("microphone:")]
        parts = []
        if cams:
            parts.append(
                f"Your camera just turned on (used by {cams[0]}).")
        if mics:
            parts.append(
                f"Your microphone just turned on (used by {mics[0]}).")
        if not parts:
            self._priv_prev = current
            return
        # One message per tick even if camera and mic start
        # together; state advances only on delivery (see _emit).
        if self._emit("privacy", " ".join(parts), out):
            self._priv_prev = current

    def _watch_foreground(self, out) -> None:
        """N13: read the foreground process NAME each tick and
        cache it (module cache + this engine's copy). Names only,
        local only — the reading itself emits nothing; consumers
        are the N1 call gate (via the module cache) and the
        fullscreen watcher's media-player suppression (via the
        engine copy). Setting-gated by the foreground_enabled
        probe seam: an explicit False stands the watcher down
        entirely (the cache keeps its last value, honestly stale
        rather than fabricated)."""
        if self._probe("foreground_enabled") is False:
            return
        name = self._probe("foreground_app")
        if name is None:
            return  # probe down this tick — keep the last reading
        self._fg_name = _normalise_process_name(name)
        _set_cached_foreground(self._fg_name)

    def _watch_ambient_light(self, out) -> None:
        """N12: the opt-in ambient-light watcher. OFF by default
        (the ambient_light_enabled probe seam). When on: at most
        ONE webcam frame per hour through the senses brightness
        path; the frame is reduced to its brightness NUMBER and
        dropped — never stored, never transmitted — and the
        reading is logged as a structured "light" timeline event.
        Nothing is emitted through on_event: this watcher speaks
        only to the timeline."""
        if not self._probe("ambient_light_enabled"):
            return  # opt-in and fail-closed: anything but a clear
            # "on" (including a probe failure) means no camera
        now = self._now_fn()
        if self._light_last_attempt is not None and \
                (now - self._light_last_attempt).total_seconds() \
                < AMBIENT_LIGHT_INTERVAL_SECONDS:
            return
        self._light_last_attempt = now
        frame = self._probe("ambient_frame")
        if not frame:
            return  # no camera this hour — the attempt still counts
        image_bytes = frame[0] if isinstance(frame, tuple) else frame
        if not isinstance(image_bytes, (bytes, bytearray)):
            return
        try:
            try:
                from .senses import frame_brightness
            except Exception:  # noqa: BLE001 — not a package import
                from voice_assistant.core.senses import frame_brightness
            result = frame_brightness(bytes(image_bytes))
        except Exception:  # noqa: BLE001 — a failed reading logs nothing
            return
        # The frame ends its life here: only the number leaves.
        del image_bytes, frame
        try:
            pct = int(round(float(result.get("brightness", 0.0)) * 100))
        except Exception:  # noqa: BLE001
            return
        pct = max(0, min(100, pct))
        self._write_timeline(
            "light", f"Room brightness measured at about {pct}%.",
            value=pct, unit="%")

    def _watch_fullscreen(self, out) -> None:
        fs = self._probe("fullscreen")
        if fs is None:
            return  # off Windows / probe down — watcher off
        if not fs:
            # Leaving fullscreen ends the episode and re-arms.
            self._fs_since = None
            self._fs_fired = False
            return
        now = self._now_fn()
        if self._fs_since is None:
            self._fs_since = now
            return
        if self._fs_fired:
            return
        if (now - self._fs_since).total_seconds() \
                >= FULLSCREEN_SUGGEST_SECONDS:
            # N13 (b): fullscreen in a known media player is a
            # film, not a focus session — hold the break nudge
            # back, but keep the episode on the timeline.
            media = self._fg_name if self._fg_name \
                in _MEDIA_APP_NAMES else None
            if media is not None:
                self._write_timeline(
                    "fullscreen",
                    "Break suggestion held back — "
                    f"{media.capitalize()} has been playing in "
                    "fullscreen, so this looked like watching, "
                    "not working.")
                self._fs_fired = True
                return
            if self._emit(
                    "fullscreen",
                    "You've been in fullscreen for about 10 minutes "
                    "— a short break keeps the eyes honest. Say the "
                    "word and I'll start a focus session instead.",
                    out):
                self._fs_fired = True
