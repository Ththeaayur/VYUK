"""
SENSES-4 skills for VYUK — the hindsight pack for her
senses: read the Senses timeline back and say, in plain words,
how the room has been trending and what shape the day took.

These tools SENSE NOTHING themselves. Everything they report is
read out of core/senses_log's timeline (senses_timeline.jsonl in
the app data dir), and both handlers are honest about what that
timeline actually holds, because it is thinner than it sounds:

- A line is {"ts": <ISO local time, seconds>, "kind": <str>,
  "message": <sentence>}. Since N7 (v5.45) a line MAY also carry
  structured measurement fields {"value": <number>, "unit":
  <str>} — the sound watcher, the ambient-light watcher and the
  battery/load watchers write them. _event_value() reads the
  structured field when present and falls back to the older
  prose parsing (the first "N%" figure in the message — the
  sound watcher's lines carry their peak level that way) for
  lines written before N7. "light" lines come only from the
  opt-in ambient-light watcher (off by default), so a light
  trend usually still reports too little data — honestly,
  rather than inventing one.
- The GUI also appends every watcher notice (battery, downloads,
  network, disk, drives, clipboard, privacy, fullscreen — and
  "welcome_back", fired when the user returns after 20+ idle
  minutes). Nobody continuously samples activity, so the
  presence report is a sketch built from those notices, and it
  says exactly that.

All log reading sits behind one module-level helper, _events(),
so tests can fake the timeline. Handlers never raise — a
failure becomes an honest "I couldn't…" line.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import re
from datetime import datetime, timedelta

from . import senses_log
from . import watchers as _watchers

_MIN_TREND_EVENTS = 4       # below this, a "trend" would be a guess
_BURST_GAP_MINUTES = 30     # notices closer than this = one burst
_TREND_DEADBAND = 0.10      # ±10% between halves reads as "steady"

# sense_trends kind -> the timeline kinds it reads. "noise" is
# the sound watcher's sudden-sound lines; "light" is kept for
# honesty — nothing logs light lines today, so it will report
# too little data until something does.
_KIND_MAP = {"noise": ("sound",), "light": ("light",)}

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


# ---------------------------------------------------------------------------
# The one log seam + small readers over its lines
# ---------------------------------------------------------------------------


def _events() -> list[dict]:
    """The whole senses timeline, newest first (the log's own
    order). The store caps itself at 500 lines, so asking for
    500 sees everything it holds. Never raises — senses_log
    guarantees that already."""
    return senses_log.read_events(limit=500)


def _event_dt(event: dict):
    """An event's timestamp as a local datetime, or None when
    the line carries nothing parseable."""
    ts = event.get("ts") if isinstance(event, dict) else None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
    return None


def _event_value(event: dict):
    """The measurement an event carries, if it has one: the N7
    structured "value" field when present (preferred — it is the
    number the writer actually measured), else the first "N%"
    figure in the message (pre-N7 lines carry their reading only
    in prose — the sound watcher's peak level, say). None when
    the line holds no number either way."""
    if not isinstance(event, dict):
        return None
    structured = senses_log.event_value(event)
    if structured is not None:
        return structured
    message = event.get("message")
    if not isinstance(message, str):
        return None
    match = _PERCENT_RE.search(message)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _num(value: float) -> str:
    """A measured number, whole when it lands whole."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _span(delta: timedelta) -> str:
    """A duration in plain words: '3 hours 25 minutes'."""
    minutes = max(0, int(round(delta.total_seconds() / 60)))
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour(s)")
    if mins or not parts:
        parts.append(f"{mins} minute(s)")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 1. sense_trends
# ---------------------------------------------------------------------------


def sense_trends(kind: str = "noise", hours: int = 24) -> str:
    """How the room's noise (or light) has trended, read back
    from the senses timeline — counts, peak levels, busy hours,
    and which way the second half of the window moved."""
    try:
        wanted = str(kind or "noise").strip().lower()
        if wanted not in _KIND_MAP:
            return ("I can trend 'noise' or 'light' — which "
                    "would you like?")
        try:
            window_hours = int(float(hours))
        except (TypeError, ValueError):
            window_hours = 24
        window_hours = max(1, min(168, window_hours))
        now = datetime.now()
        cutoff = now - timedelta(hours=window_hours)

        kinds = set(_KIND_MAP[wanted])
        rows = []  # (when, value-or-None), oldest first
        for event in _events():
            if not isinstance(event, dict):
                continue
            if str(event.get("kind") or "") not in kinds:
                continue
            when = _event_dt(event)
            if when is None or when < cutoff or when > now:
                continue
            rows.append((when, _event_value(event)))
        rows.sort(key=lambda row: row[0])

        subject = ("sudden-sound" if wanted == "noise"
                   else "light-reading")
        if len(rows) < _MIN_TREND_EVENTS:
            basis = ("The sound watcher only writes a line when "
                     "it hears a sudden sound, so a quiet room "
                     "leaves a quiet log." if wanted == "noise"
                     else "Light readings are logged only by the "
                     "opt-in ambient-light watcher (Settings → "
                     "Notices), which is off by default — if it "
                     "has never been switched on, there is "
                     "nothing to trend from yet.")
            return (f"Not enough logged yet for a {wanted} trend: "
                    f"only {len(rows)} {subject} event(s) in the "
                    f"last {window_hours} hour(s), and I need at "
                    f"least {_MIN_TREND_EVENTS} before a trend "
                    f"means anything. {basis}")

        values = [v for _when, v in rows if v is not None]
        lines = [f"{wanted.capitalize()} trend, last "
                 f"{window_hours} hour(s): {len(rows)} "
                 f"{subject} event(s) logged."]
        if values:
            unit = ("peak level" if wanted == "noise"
                    else "reading")
            lines.append(
                f"{unit.capitalize()}s ran from "
                f"{_num(min(values))}% to {_num(max(values))}%, "
                f"averaging {_num(sum(values) / len(values))}%"
                + (f" ({len(rows) - len(values)} event(s) carried "
                   "no reading and were left out of the levels)."
                   if len(values) < len(rows) else "."))
        else:
            lines.append("None of those lines carried a level "
                         "reading, so I can count them but can't "
                         "average them.")

        # Busiest / quietest (or brightest / darkest) hour.
        by_hour: dict[int, list] = {}
        for when, value in rows:
            by_hour.setdefault(when.hour, []).append(value)
        if len(by_hour) == 1:
            only = next(iter(by_hour))
            lines.append(f"Every event fell in the "
                         f"{only:02d}:00 hour.")
        elif wanted == "light" and values:
            hour_means = {}
            for h, vs in by_hour.items():
                hour_values = [v for v in vs if v is not None]
                if hour_values:
                    hour_means[h] = (sum(hour_values)
                                     / len(hour_values))
            bright = max(hour_means, key=hour_means.get)
            dark = min(hour_means, key=hour_means.get)
            lines.append(
                f"Brightest hour: {bright:02d}:00 (averaging "
                f"{_num(hour_means[bright])}%); darkest: "
                f"{dark:02d}:00 (averaging "
                f"{_num(hour_means[dark])}%).")
        else:
            busy = max(by_hour, key=lambda h: len(by_hour[h]))
            quiet = min(by_hour, key=lambda h: len(by_hour[h]))
            word = ("Busiest" if wanted == "noise" else "Most")
            word2 = ("Quietest" if wanted == "noise" else "Fewest")
            lines.append(
                f"{word} hour: {busy:02d}:00 "
                f"({len(by_hour[busy])} event(s)); {word2.lower()} "
                f"with any events: {quiet:02d}:00 "
                f"({len(by_hour[quiet])} event(s)).")

        # Trend: first half of the window vs the second half.
        midpoint = cutoff + timedelta(hours=window_hours / 2)
        first = [v for when, v in rows
                 if when < midpoint and v is not None]
        second = [v for when, v in rows
                  if when >= midpoint and v is not None]
        if first and second:
            mean_first = sum(first) / len(first)
            mean_second = sum(second) / len(second)
            scale = max(abs(mean_first), 1.0)
            change = (mean_second - mean_first) / scale
            if change > _TREND_DEADBAND:
                direction = "rising"
            elif change < -_TREND_DEADBAND:
                direction = "falling"
            else:
                direction = "holding steady"
            lines.append(
                f"Trend: {direction} — the second half averaged "
                f"{_num(mean_second)}% against "
                f"{_num(mean_first)}% in the first half.")
        else:
            first_count = sum(1 for when, _v in rows
                              if when < midpoint)
            second_count = len(rows) - first_count
            if second_count > first_count:
                lean = "more of the events landed in the second half"
            elif first_count > second_count:
                lean = "more of the events landed in the first half"
            else:
                lean = "the events split evenly across the window"
            lines.append(
                f"Trend: not enough level readings in both "
                f"halves to compare, but {lean} "
                f"({first_count} first, {second_count} second).")
        return " ".join(lines)
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return f"I couldn't read the senses timeline back: {e}"


# ---------------------------------------------------------------------------
# 2. presence_report
# ---------------------------------------------------------------------------


def presence_report(day: str = "today") -> str:
    """The shape of a day, sketched from the timeline's notices:
    first and last activity, the longest quiet stretch, and how
    many bursts the day broke into."""
    try:
        wanted = str(day or "today").strip().lower()
        if wanted not in ("today", "yesterday"):
            return ("I can report on 'today' or 'yesterday' — "
                    "which day would you like?")
        today = datetime.now().date()
        target = today if wanted == "today" else today - timedelta(days=1)
        label = ("today" if wanted == "today"
                 else f"yesterday ({target.strftime('%d %b')})")

        stamps = []
        returns = 0
        for event in _events():
            if not isinstance(event, dict):
                continue
            when = _event_dt(event)
            if when is None or when.date() != target:
                continue
            stamps.append(when)
            if str(event.get("kind") or "") == "welcome_back":
                returns += 1
        stamps.sort()

        if not stamps:
            return (f"No activity to report for {label}: the "
                    "Senses timeline holds nothing for that day. "
                    "The timeline only fills while VYUK is "
                    "running with her watchers on — she may have "
                    "been off, or the day was simply quiet enough "
                    "that nothing was noticed.")

        bursts = 1
        longest_gap = timedelta(0)
        gap_start = gap_end = None
        for prev, nxt in zip(stamps, stamps[1:]):
            gap = nxt - prev
            if gap > longest_gap:
                longest_gap, gap_start, gap_end = gap, prev, nxt
            if gap > timedelta(minutes=_BURST_GAP_MINUTES):
                bursts += 1

        lines = [
            f"Presence for {label}, from the Senses timeline: "
            f"first sign of activity at "
            f"{stamps[0].strftime('%H:%M')}, last at "
            f"{stamps[-1].strftime('%H:%M')}, across "
            f"{len(stamps)} logged notice(s)."]
        if len(stamps) == 1:
            lines.append("With a single notice there's no "
                         "pattern to read yet — one burst, and no "
                         "quiet stretch to measure.")
        else:
            lines.append(
                f"The day breaks into {bursts} active burst(s) "
                f"(notices no more than {_BURST_GAP_MINUTES} "
                "minutes apart count as one burst).")
            lines.append(
                f"Longest quiet stretch: {_span(longest_gap)}, "
                f"from {gap_start.strftime('%H:%M')} to "
                f"{gap_end.strftime('%H:%M')}.")
        if returns:
            lines.append(f"You came back from a break "
                         f"{returns} time(s) (the idle watcher "
                         "logs a return after 20+ quiet minutes).")
        lines.append(
            "The honest basis: this is sketched from what the "
            "watchers happened to notice and log — battery, "
            "downloads, network, returns from idle, logged "
            "sounds — not from any continuous record of your "
            "activity, so quiet spells mean 'nothing was "
            "noticed', not 'nothing happened'.")
        return " ".join(lines)
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return f"I couldn't build the presence report: {e}"


# ---------------------------------------------------------------------------
# 3. timeline_recap (S14)
# ---------------------------------------------------------------------------


def timeline_recap(day: str = "today") -> str:
    """What the watchers noticed on a day, grouped by kind:
    a counted recap plus the latest few messages verbatim."""
    try:
        wanted = str(day or "today").strip().lower()
        if wanted not in ("today", "yesterday"):
            return ("I can recap 'today' or 'yesterday' — "
                    "which day would you like?")
        today = datetime.now().date()
        target = today if wanted == "today" else today - timedelta(days=1)
        label = ("today" if wanted == "today"
                 else f"yesterday ({target.strftime('%d %b')})")

        todays = []
        for event in _events():
            if not isinstance(event, dict):
                continue
            when = _event_dt(event)
            if when is None or when.date() != target:
                continue
            todays.append((when, str(event.get("kind") or "notice"),
                           str(event.get("message") or "")))
        if not todays:
            return (f"I noticed nothing {label} — the Senses "
                    "timeline holds no events for that day. The "
                    "watchers may have been off, VYUK may "
                    "not have been running, or it was simply a "
                    "quiet day.")
        todays.sort(key=lambda row: row[0])  # chronological

        counts: dict[str, int] = {}
        for _when, kind, _msg in todays:
            counts[kind] = counts.get(kind, 0) + 1
        by_count = sorted(counts.items(), key=lambda kv: -kv[1])
        summary = ", ".join(
            f"{kind.replace('_', ' ')} ×{n}" if n > 1
            else kind.replace("_", " ")
            for kind, n in by_count)
        lines = [f"What I noticed {label} ({len(todays)} "
                 f"thing(s)): {summary}."]
        latest = todays[-5:]
        lines.append("The latest of them: " + "; ".join(
            f"{when.strftime('%H:%M')} — {msg}" for when, _k, msg
            in latest if msg) + ".")
        lines.append(
            "That's everything the watchers logged — they only "
            "record what they notice, so a quiet spell means "
            "'nothing flagged', not 'nothing happened'.")
        return " ".join(lines)
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return f"I couldn't recap the timeline: {e}"


# ---------------------------------------------------------------------------
# 4. watcher_status (S14)
# ---------------------------------------------------------------------------


def watcher_status() -> str:
    """Are the watchers able to watch, and what did they last
    notice? Probe health + the newest event per kind."""
    try:
        health = _watchers.watcher_health()
        lines = ["The watchers check every "
                 f"{_watchers.POLL_SECONDS} seconds while "
                 "VYUK is open."]
        if health.get("psutil"):
            lines.append("The machine probes are available "
                         "(psutil found).")
        else:
            lines.append("The machine probes are NOT available "
                         "(psutil missing) — battery, disk and "
                         "load notices can't run.")
        battery = health.get("battery")
        if battery:
            lines.append(f"Battery right now: {battery[0]}%"
                         + (" and charging." if battery[1]
                            else " and not charging."))
        elif health.get("battery_present") is False:
            lines.append("No battery on this machine — battery "
                         "notices stay off, which is expected on "
                         "a desktop.")
        load = health.get("load")
        if load:
            lines.append(f"Load right now: processor "
                         f"{load[0]:.0f}%, memory {load[1]:.0f}% "
                         "full.")
        seen: dict[str, tuple] = {}
        for event in _events():  # newest first
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind") or "")
            if kind and kind not in seen:
                when = _event_dt(event)
                seen[kind] = (when, str(event.get("message") or ""))
        if seen:
            today = datetime.now().date()
            bits = []
            for kind, (when, _msg) in list(seen.items())[:8]:
                if when is None:
                    bits.append(kind.replace("_", " "))
                elif when.date() == today:
                    bits.append(f"{kind.replace('_', ' ')} at "
                                f"{when.strftime('%H:%M')}")
                elif when.date() == today - timedelta(days=1):
                    bits.append(f"{kind.replace('_', ' ')} "
                                f"yesterday {when.strftime('%H:%M')}")
                else:
                    bits.append(f"{kind.replace('_', ' ')} on "
                                f"{when.strftime('%d %b')}")
            lines.append("Last noticed, by kind: "
                         + "; ".join(bits) + ".")
        else:
            lines.append("Nothing has been noticed yet — the "
                         "timeline is empty.")
        lines.append("Which notices are switched on lives in "
                     "Settings, under Notices.")
        return " ".join(lines)
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return f"I couldn't check the watchers: {e}"


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every SENSES-4 pack tool on a ToolRegistry-like object."""
    registry.register(
        name="sense_trends",
        description="Summarise how the room's noise or light has been trending over the last few hours. It reads the Senses timeline back: for noise it uses the sound watcher's logged sudden sounds (each carries a peak level) and reports the count, the lowest/highest/average level, the busiest and quietest hours, and whether the second half of the window ran louder or quieter than the first. It needs at least four logged events and says so plainly when there aren't enough — it never senses anything itself and never invents a trend from an empty log.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["noise", "light"],
                         "description": "Which sense to trend: 'noise' (default) or 'light'"},
                "hours": {"type": "integer",
                          "description": "How many hours back to look, from 1 to 168 (default 24)"},
            },
            "required": [],
        },
        handler=sense_trends,
    )
    registry.register(
        name="presence_report",
        description="Sketch the shape of today (or yesterday) from what the watchers noticed. Reports the first and last logged activity, the longest quiet stretch, and how many active bursts the day broke into, counting your returns from idle when the watcher logged them. It is a sketch from the Senses timeline's notices — battery, downloads, network, sounds — not a continuous activity record, and the report says so.",
        parameters={
            "type": "object",
            "properties": {
                "day": {"type": "string",
                        "enum": ["today", "yesterday"],
                        "description": "Which day to sketch: 'today' (default) or 'yesterday'"},
            },
            "required": [],
        },
        handler=presence_report,
    )
    registry.register(
        name="timeline_recap",
        description="Recap what VYUK's watchers noticed today (or yesterday): how many things were logged, grouped by kind (battery, downloads, network, heavy load, returns from idle, and the rest), plus the latest few notices verbatim with their times. Use it for 'what did you notice today', 'anything happen while I was away', 'recap my day'. It reads the Senses timeline only — when a day holds nothing it says so plainly instead of inventing activity.",
        parameters={
            "type": "object",
            "properties": {
                "day": {"type": "string",
                        "enum": ["today", "yesterday"],
                        "description": "Which day to recap: 'today' (default) or 'yesterday'"},
            },
            "required": [],
        },
        handler=timeline_recap,
    )
    registry.register(
        name="watcher_status",
        description="Report whether VYUK's background watchers can actually watch: whether the machine probes (psutil) are available, the battery and load readings right now, how often the watchers poll, and the most recent notice of each kind from the Senses timeline. Use it for 'are your watchers on', 'why didn't you tell me my download finished', 'what did you last notice'. It reports probe health and the log — which notices are enabled lives in Settings, and the answer says so.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=watcher_status,
    )
