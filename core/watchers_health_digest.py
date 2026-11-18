"""Health-digest probes, split from core/watchers.py (skills split B45).

Home of the G4 family (watcher_health, digest composition, timeline/event
writer defaults).
"""

# INTENTIONAL (skills split B45): stdlib names the moved code resolves
# at module level, found by the import-completeness prover.
from datetime import datetime

# INTENTIONAL (skills split B45): watcher_health calls G1 probes and the
# G3 live-engine reader by bare name; imported here so they resolve
# locally (§3.5 P3 trap).
from .watchers_network_system import (
    POLL_SECONDS,
    _probe_battery,
    _probe_load,
)
from .watchers_foreground_mic_camera import _get_live_engine

def watcher_health() -> dict:
    """One-shot snapshot for doctor.py / diagnostics; never raises.

    {"psutil": bool, "battery_present": bool,
     "battery": (percent, charging) | None}
    Plus (v5.42) live-engine state: "engine_running" (bool),
    "last_tick_age_s" (float|None), "tick_count" (int),
    "poll_seconds" (float) and "probes":
    {name: {"last_success_age_s": float|None,
            "ever_succeeded": bool}}.
    With no engine running, engine_running is False,
    last_tick_age_s is None and probes is {} — a not-yet-started
    engine is not a failure.
    """
    try:
        import importlib.util

        psutil_ok = importlib.util.find_spec("psutil") is not None
    except Exception:  # noqa: BLE001
        psutil_ok = False
    battery = _probe_battery()
    result = {"psutil": psutil_ok,
              "battery_present": battery is not None,
              # Documented shape stays (percent, charging); the
              # probe's third element (minutes left) is the
              # watcher's business, not the snapshot's.
              "battery": (battery[0], battery[1]) if battery else None,
              "load": _probe_load(),
              "engine_running": False,
              "last_tick_age_s": None,
              "tick_count": 0,
              "poll_seconds": POLL_SECONDS,
              "probes": {}}
    # Live-engine surface — wrapped so health never raises even
    # if the engine's bookkeeping is in an odd state.
    try:
        engine = _get_live_engine()
        if engine is not None:
            # Engine counts as running if its thread is alive or
            # it is being driven manually (has ticked, not stopped).
            thread_alive = False
            try:
                thread = getattr(engine, "_thread", None)
                thread_alive = bool(thread is not None
                                    and thread.is_alive())
            except Exception:  # noqa: BLE001
                thread_alive = False
            tick_count = int(getattr(engine, "_tick_count", 0) or 0)
            is_running = bool(thread_alive or tick_count > 0)
            result["engine_running"] = is_running
            result["tick_count"] = tick_count
            try:
                # N10 (v5.45): the live interval is state-dependent
                # — report the CURRENT one when the engine offers it.
                current = getattr(engine, "current_poll_seconds", None)
                result["poll_seconds"] = float(
                    current() if callable(current)
                    else getattr(engine, "_poll_seconds", POLL_SECONDS)
                    or POLL_SECONDS)
            except Exception:  # noqa: BLE001
                result["poll_seconds"] = POLL_SECONDS
            if is_running:
                # Ages are measured on the engine's own clock so a
                # fake now_fn in tests controls them deterministically.
                now_ref = None
                try:
                    now_ref = engine._now_fn()
                except Exception:  # noqa: BLE001
                    now_ref = None
                if now_ref is None:
                    now_ref = datetime.now()
                last_tick = getattr(engine, "_last_tick_at", None)
                if last_tick is not None:
                    try:
                        result["last_tick_age_s"] = float(
                            (now_ref - last_tick).total_seconds())
                    except Exception:  # noqa: BLE001
                        result["last_tick_age_s"] = None
                probes_state = {}
                last_map = getattr(engine, "_probe_last_success", {}) or {}
                ever_map = getattr(engine, "_probe_ever_succeeded", {}) or {}
                names = set(last_map) | set(ever_map)
                # Include every probe the engine knows about.
                try:
                    names |= set((getattr(engine, "_probes", {}) or {}).keys())
                except Exception:  # noqa: BLE001
                    pass
                for pname in sorted(names):
                    last_ok = last_map.get(pname)
                    age = None
                    if last_ok is not None:
                        try:
                            age = float(
                                (now_ref - last_ok).total_seconds())
                        except Exception:  # noqa: BLE001
                            age = None
                    probes_state[pname] = {
                        "last_success_age_s": age,
                        "ever_succeeded": bool(ever_map.get(pname, False)),
                    }
                result["probes"] = probes_state
    except Exception:  # noqa: BLE001 — health must never raise
        pass
    return result

_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five",
                 6: "six", 7: "seven", 8: "eight", 9: "nine",
                 10: "ten"}

# Kinds that are never narrated in the digest: a welcome_back is
# the greeting itself; a network_up is implied by a drop that
# recovered (the drop is the clause); a fullscreen event is a
# nudge VYUK made, not something that happened to the machine.
_DIGEST_SKIP_KINDS = frozenset({"welcome_back", "network_up",
                                "fullscreen"})

def _count_word(n: int) -> str:
    return _NUMBER_WORDS.get(int(n), str(int(n)))

def _digest_clause(kind: str, count: int):
    """One plain clause for `count` events of `kind`, or None
    when the kind has no narration of its own."""
    if kind == "download_done":
        return ("a download finished" if count == 1
                else f"{_count_word(count)} downloads finished")
    if kind == "network_down":
        if count == 1:
            return "the connection dropped once"
        if count == 2:
            return "the connection dropped twice"
        return f"the connection dropped {_count_word(count)} times"
    if kind == "network_flap":
        return "the connection kept dropping"
    if kind == "battery_low":
        return "the battery ran low"
    if kind == "battery_critical":
        return "the battery got critical"
    if kind == "battery_full":
        return "the battery finished charging"
    if kind == "disk_low":
        return "the main drive nearly filled up"
    if kind == "drive_connected":
        return ("a drive was connected" if count == 1
                else f"{_count_word(count)} drives were connected")
    if kind == "system_load":
        return "the machine strained under a heavy load"
    if kind == "privacy":
        return "the camera or microphone was used"
    if kind == "clipboard":
        return "something was copied"
    if kind == "calendar_heads_up":
        return ("a calendar reminder came up" if count == 1
                else f"{_count_word(count)} calendar reminders came up")
    return None

def _parse_event_ts(raw):
    """An event's "ts" as a datetime, or None when it cannot be
    parsed (such an event cannot prove it happened while the
    user was away, so the digest excludes it)."""
    if isinstance(raw, datetime):
        return raw
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None

def compose_welcome_digest(events, away_since):
    """Compose the welcome-back digest sentence, or None.

    `events` are senses-log dicts ({"ts", "kind", "message"})
    in any order; `away_since` is the datetime the away episode
    armed. Only events timestamped at or after away_since count
    (unparseable timestamps are excluded — recency unproven).
    Events are grouped by kind in the order they first happened
    and narrated as at most three plain clauses; kinds with no
    narration of their own are gathered into one honest
    "a few other things happened" clause, which also becomes
    the third clause when more than three groups compete.
    Returns None when nothing narratable fired — the caller
    then keeps the plain greeting, byte for byte.
    """
    if away_since is None or not events:
        return None
    try:
        candidates = list(events)
    except TypeError:
        return None
    during: list = []
    for event in candidates:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        if not kind or kind in _DIGEST_SKIP_KINDS:
            continue
        stamp = _parse_event_ts(event.get("ts"))
        if stamp is None:
            continue
        try:
            if stamp < away_since:
                continue
        except TypeError:
            continue  # naive/aware mismatch — recency unproven
        during.append((stamp, kind))
    if not during:
        return None
    during.sort(key=lambda pair: pair[0])
    counts: dict = {}
    order: list = []
    others = 0
    for _stamp, kind in during:
        if _digest_clause(kind, 1) is None:
            others += 1
            continue
        if kind not in counts:
            counts[kind] = 0
            order.append(kind)
        counts[kind] += 1
    clauses = [_digest_clause(kind, counts[kind]) for kind in order]
    if others:
        clauses.append("a few other things happened")
    clauses = [c for c in clauses if c]
    if not clauses:
        return None
    if len(clauses) > 3:
        clauses = clauses[:2] + ["a few other things happened"]
    if len(clauses) == 1:
        body = clauses[0]
    elif len(clauses) == 2:
        body = f"{clauses[0]}, and {clauses[1]}"
    else:
        body = f"{clauses[0]}, {clauses[1]}, and {clauses[2]}"
    return f"While you were out: {body}."

def _default_timeline_events(limit: int = 500) -> list:
    """The senses log's events (newest first) for the digest.

    Imported lazily, inside the call, so importing watchers
    never pulls in the package (see the module docstring);
    any failure reads as an empty timeline. The log's own cap
    is 500 lines, so the default limit fetches everything and
    compose_welcome_digest does the since-away filtering —
    read_events' since_hours cannot express an exact start.
    """
    try:
        try:
            from .senses_log import read_events
        except Exception:  # noqa: BLE001 — not imported as a package
            from voice_assistant.core.senses_log import read_events
        return list(read_events(limit=limit) or [])
    except Exception:  # noqa: BLE001 — digest degrades to no digest
        return []

def _default_event_writer(kind, message, value=None, unit=None) -> None:
    """Timeline-only writes (v5.45): the quiet network_changed
    line, the hourly light reading, a held-back break nudge.
    Lazily imports senses_log (same pattern as the timeline
    reader); any failure is silent — a timeline write must never
    break a watcher. N7 fields ride along when present."""
    try:
        try:
            from .senses_log import append_event
        except Exception:  # noqa: BLE001 — not imported as a package
            from voice_assistant.core.senses_log import append_event
        append_event(kind, message, value=value, unit=unit)
    except Exception:  # noqa: BLE001
        pass
