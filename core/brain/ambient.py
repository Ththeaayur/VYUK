"""
Ambient context packet for VYUK (v5.44, NOSE N4) — the nose
finally feeds the mind.

One small structured snapshot of the machine's here-and-now —
battery, network, idleness, the next calendar event, and the
kinds of things the senses timeline recorded most recently —
assembled ONLY from sources that already exist:

- battery / network / idle: the watchers' own probes
  (core.watchers — the same readings the WatcherEngine polls
  and caches on its tick). Network is the exception that
  proves the rule (v5.52, item 6): the packet never raw-probes
  for a state — it mirrors the live engine's streak-filtered
  announcement state (watchers.network_state_snapshot), and
  omits the field when no watcher state exists;
- next calendar event: core.google_services.next_events — the
  helper the window's calendar heads-up already uses (returns
  [] when Google isn't connected; never raises);
- recent timeline kinds: core.senses_log.read_events.

ambient_snapshot() -> dict is the structured form;
ambient_packet() -> str is the compact speakable rendering the
brains get (~200 tokens max, and an empty string when nothing
is worth saying — no battery reading, no event, small idle,
network fine, empty timeline).

This module is PURE and NEVER raises: every source is fetched
behind its own guard, and a failing source yields an ABSENT
field — never an exception, never a made-up value. It imports
nothing from gui.

v5.56 (Wave 2, PR-4 — boot-health → prompt honesty): the
packet also carries a DEGRADATION line when a capability is
CONFIRMED down (see degradation_prompt_line). The line says
what is down and what still works, and forbids promising the
down capability — it never vetoes an attempt: the attempt-first
framing still rides at the end of every packet, so a tool
attempt settles its own question. Only confirmed states feed
it: the watcher engine's ANNOUNCED offline (never an
unconfirmed drop — v5.52's lesson), explicit degradation flags
published by the boot doctor / a dead Live session through
set_degradation_flags(), and doctor FAIL results mapped by
degradation_from_doctor(). A doctor WARN never marks a
capability down (doctor.py: WARN = degraded but survivable).
When nothing is down, the packet is byte-identical to before
(no degradation field in the snapshot, no extra line).
"""

import datetime

#: Hard cap for ambient_packet(): ~200 tokens at the usual
#: ~4-chars-per-token rule of thumb. The packet is meant to be a
#: glance, not a briefing — anything longer gets truncated.
_MAX_PACKET_CHARS = 800

#: Idle seconds that count as "away" — mirrors the watchers'
#: IDLE_AWAY_SECONDS (20 minutes) without importing a private.
_AWAY_SECONDS = 20 * 60

#: Idle seconds below which idleness alone is not worth a line.
_IDLE_WORTH_SAYING_SECONDS = 300


def _battery(snap: dict) -> None:
    try:
        from .. import watchers  # Phase 3 B1b: moved to core/brain/

        reading = watchers._probe_battery()
        if reading:
            snap["battery_pct"] = int(reading[0])
            snap["charging"] = bool(reading[1])
    except Exception:  # noqa: BLE001 — absent field, never a raise
        pass


#: The brain-facing hedge for an UNCONFIRMED network drop
#: (v5.52, item 6): one check says down, the streak/ladder
#: discipline has not confirmed it. Worded as an advisory, not
#: a state — the brain must never be handed a bare binary it
#: can cite as fact (the field failure: her deliberation blamed
#: browser failures on "the 'Network offline' status detected
#: in the ambient context" during an outage that never was).
_NETWORK_ADVISORY = ("Network looks down on one check; "
                     "other checks disagree")

#: Attempt-first doctrine, appended to every non-empty packet
#: (v5.52, item 6 extension — see ambient_packet). Short on
#: purpose: the packet is prompt budget.
_ATTEMPT_FIRST = (" Background only — not a capability "
                  "verdict: attempt the tool, its result "
                  "speaks.")

#: Outcome-not-process doctrine (v5.53, F4): the packet
#: reaches ALL brains — including Live, whose turns the
#: deliberation guard cannot intercept — so the same rule
#: the guard enforces on text brains rides here as one
#: steering sentence: after a task, report the outcome
#: (what was asked, what state it is in), never the
#: automation's own struggles. Steering, not enforcement —
#: on Live nothing can withhold a reply.
_OUTCOME_NOT_PROCESS = (" Report outcomes, not process: "
                        "what was asked and what state it "
                        "is in — never the tooling, "
                        "connections or retries.")


def _network(snap: dict) -> None:
    """Mirror the watcher's streak-filtered state (v5.52,
    item 6) — NEVER a fresh raw probe.

    - confirmed offline (the engine announced the drop): the
      state is "offline" and the N5 ladder's verdict from that
      announcement rides along as network_verdict, so the
      packet can hedge in the ladder's own words;
    - failures accumulating below the announce threshold:
      NO network_state at all — only the network_advisory
      hedge, so the state can never be cited as fact;
    - online: only when the engine's probe has succeeded with
      no streak running;
    - no live engine / no watcher state yet: the field is
      ABSENT, per this module's discipline — a one-probe
      verdict is never minted here."""
    try:
        from .. import watchers  # Phase 3 B1b: moved to core/brain/

        state = watchers.network_state_snapshot()
        if not state:
            return
        kind = state.get("state")
        if kind == "offline":
            snap["network_state"] = "offline"
            verdict = state.get("verdict")
            if verdict:
                snap["network_verdict"] = str(verdict)
        elif kind == "online":
            snap["network_state"] = "online"
        elif kind == "unconfirmed":
            snap["network_advisory"] = _NETWORK_ADVISORY
    except Exception:  # noqa: BLE001
        pass


def _idle(snap: dict) -> None:
    try:
        from .. import watchers  # Phase 3 B1b: moved to core/brain/

        idle = watchers._probe_idle_seconds()
        if idle is not None:
            seconds = int(float(idle))
            snap["idle_s"] = seconds
            snap["away"] = bool(seconds >= _AWAY_SECONDS)
    except Exception:  # noqa: BLE001
        pass


def _calendar(snap: dict) -> None:
    try:
        from .. import google_services  # Phase 3 B1b: moved to core/brain/

        events = google_services.next_events(within_hours=24, limit=5)
        first = None
        for event in events or []:
            if isinstance(event, dict):
                first = event
                break
        if first is None:
            snap["next_calendar_event"] = None
            return
        title = str(first.get("title") or "").strip() or "Event"
        start = first.get("start")
        if isinstance(start, datetime.datetime):
            when = start.strftime("%a %H:%M")
        elif start:
            when = str(start)
        else:
            when = ""
        snap["next_calendar_event"] = {"title": title, "when": when}
    except Exception:  # noqa: BLE001
        pass


def _timeline(snap: dict) -> None:
    try:
        from .. import senses_log  # Phase 3 B1b: moved to core/brain/

        events = senses_log.read_events(limit=20) or []
        kinds: list = []
        for event in events:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind") or "").strip()
            if kind and kind not in kinds:
                kinds.append(kind)
            if len(kinds) >= 5:
                break
        snap["recent_timeline_kinds"] = kinds
    except Exception:  # noqa: BLE001
        pass


def next_event_full():
    """v5.49 (programme item 35): the next calendar event as the
    FULL google_services dict ({id, title, start, location,
    all_day}), or None — the suggestions engine's meeting
    producer needs the id/start/location the speakable snapshot
    deliberately reduces to {title, when}, so this sibling seam
    serves it from the same source without changing the
    snapshot's shape. Never raises."""
    try:
        from .. import google_services  # Phase 3 B1b: moved to core/brain/

        for event in google_services.next_events(within_hours=1,
                                                 limit=5) or []:
            if isinstance(event, dict):
                return event
    except Exception:  # noqa: BLE001 — absent event, never a raise
        pass
    return None


# ---------------------------------------------------------------------------
# v5.56 (Wave 2, PR-4): boot-health → prompt honesty.
#
# Degradation state is the SAME state the PF-7 status surface
# (gui/window_trust.degraded_mode_summary) renders — one source
# for the prompt and the display, so they cannot disagree. A
# capability is "down" only when CONFIRMED: a watcher-ANNOUNCED
# offline, an explicit flag published through
# set_degradation_flags() (the boot doctor's FAILs, a dead Live
# session — the callers that own those facts publish them; this
# module never mints a verdict from one probe), or a doctor FAIL
# mapped by degradation_from_doctor(). A doctor WARN never
# marks anything down.
#
# DEGRADATION -> PROMPT mapping (the whole contract):
#   live_voice   "Live voice conversation is currently
#                unavailable — do not promise it"
#                still works: text chat and local tools
#   microphone   "Voice listening (microphone input) is
#                currently unavailable — do not promise it"
#                still works: typed text and local tools
#   cloud_brain  "The cloud brain and live web answers are
#                currently unavailable — do not promise them"
#                still works: the on-device text brain and
#                local tools
#   local_brain  "The on-device brain is currently
#                unavailable — do not promise it"
#                still works: the cloud text brain (when a key
#                is set) and local tools
# The line forbids PROMISING, never ATTEMPTING: the
# attempt-first framing still closes every packet, so a tool
# attempt settles its own question (v5.52's lesson, kept).
# ---------------------------------------------------------------------------

#: Canonical capability ids, in the order the prompt line and
#: the PF-7 surface list them.
_DEGRADATION_ORDER = ("live_voice", "microphone", "cloud_brain",
                      "local_brain")

#: capability -> (down phrase, still-works phrase).
_DEGRADATION_COPY = {
    "live_voice": ("Live voice conversation",
                   "text chat and local tools"),
    "microphone": ("voice listening (microphone input)",
                   "typed text and local tools"),
    "cloud_brain": ("the cloud brain and live web answers",
                    "the on-device text brain and local tools"),
    "local_brain": ("the on-device brain",
                    "the cloud text brain and local tools"),
}

#: What the user is told to do, per down capability (the PF-7
#: "what to do" column; the prompt line itself carries only the
#: do-not-promise + still-works halves, to save prompt budget).
_DEGRADATION_ACTIONS = {
    "live_voice": "Reconnect Live, or keep using text chat.",
    "microphone": "Run the microphone doctor check, or type instead.",
    "cloud_brain": "Check the network, or switch to the on-device brain.",
    "local_brain": "Check the local brain, or switch to the cloud brain.",
}

#: Explicit flags published by owners of the facts (boot
#: doctor FAILs, a dead Live session). Keys are capability ids,
#: values are truthy = down. Merged with the watcher-derived
#: network state by current_degradation(); never raises.
_DEGRADATION_OVERRIDE: dict = {}


def set_degradation_flags(flags) -> None:
    """Publish confirmed-down capabilities: flags maps a
    capability id to truthy (down) / falsy (clear). Unknown ids
    are kept verbatim but ignored by the prompt line (only
    _DEGRADATION_ORDER ids render). Never raises. This is the
    seam the boot doctor (its FAILs) and a dead Live session
    publish through — ambient never mints these itself."""
    try:
        if isinstance(flags, dict):
            for key, value in flags.items():
                if value:
                    _DEGRADATION_OVERRIDE[str(key)] = True
                else:
                    _DEGRADATION_OVERRIDE.pop(str(key), None)
    except Exception:  # noqa: BLE001 — a bad publish is dropped
        pass


def clear_degradation_flags() -> None:
    """Clear every published flag (fresh boot / recovery).
    Never raises."""
    try:
        _DEGRADATION_OVERRIDE.clear()
    except Exception:  # noqa: BLE001
        pass


def degradation_flags() -> dict:
    """A copy of the currently published flags. Never raises."""
    try:
        return dict(_DEGRADATION_OVERRIDE)
    except Exception:  # noqa: BLE001
        return {}


def degradation_from_flags(flags) -> dict:
    """Pure mapping: flags (dict capability -> truthy, or an
    iterable of down capability ids) -> {"down": [...],
    "works": [...], "actions": [...], "prompt_line": str|None}.
    `down` is in _DEGRADATION_ORDER; `works` is the union of
    the still-works phrases; `actions` is one "what to do" line
    per down capability. Unknown ids are dropped, silently —
    the prompt line must never name a capability it cannot
    describe. Never raises."""
    try:
        if isinstance(flags, dict):
            down_ids = [c for c in _DEGRADATION_ORDER
                        if flags.get(c)]
        elif flags:
            wanted = {str(f) for f in flags}
            down_ids = [c for c in _DEGRADATION_ORDER
                        if c in wanted]
        else:
            down_ids = []
    except Exception:  # noqa: BLE001
        down_ids = []
    works: list = []
    for cap in down_ids:
        phrase = _DEGRADATION_COPY[cap][1]
        if phrase not in works:
            works.append(phrase)
    actions = [_DEGRADATION_ACTIONS[c] for c in down_ids]
    line = None
    if down_ids:
        if len(down_ids) == 1:
            subject = _DEGRADATION_COPY[down_ids[0]][0]
            verb = "is currently unavailable — do not promise it"
        else:
            subject = " and ".join(
                _DEGRADATION_COPY[c][0] for c in down_ids)
            verb = ("are currently unavailable — "
                    "do not promise them")
        line = (f"Degraded mode: {subject} {verb}; "
                f"{' and '.join(works)} still work.")
    return {"down": down_ids, "works": works, "actions": actions,
            "prompt_line": line}


def degradation_from_doctor(results) -> dict:
    """Pure mapping from doctor-style results to a degradation
    state (same shape as degradation_from_flags). Each result
    is a dict with "label"/"status" (+ optional "detail"), or a
    (label, status[, detail]) tuple. ONLY status FAIL maps —
    WARN is doctor.py's "degraded but survivable" and never
    marks a capability down. Label matching is substring-based
    on label + detail, local-brain patterns first (a bare
    "Brain" FAIL with no local marker maps to cloud_brain —
    the Gemini-key case is the common one; a detail naming
    Ollama / the local brain maps to local_brain). Unknown
    labels are ignored. Never raises."""
    flags: dict = {}
    try:
        items = list(results or [])
    except Exception:  # noqa: BLE001
        return degradation_from_flags({})
    for item in items:
        try:
            if isinstance(item, dict):
                label = str(item.get("label") or "")
                status = str(item.get("status") or "")
                detail = str(item.get("detail") or "")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                label = str(item[0] or "")
                status = str(item[1] or "")
                detail = str(item[2] or "") if len(item) > 2 else ""
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if status.strip().upper() != "FAIL":
            continue
        text = f"{label} {detail}".lower()
        if "microphone" in text or "mic " in text:
            flags["microphone"] = True
        elif "live" in text:
            flags["live_voice"] = True
        elif "ollama" in text or "local brain" in text:
            flags["local_brain"] = True
        elif "gemini" in text or "api key" in text \
                or "brain" in text:
            flags["cloud_brain"] = True
    return degradation_from_flags(flags)


def current_degradation(snapshot=None) -> dict:
    """The live degradation state: published flags merged with
    the watcher engine's CONFIRMED offline (snapshot's
    network_state == "offline" -> cloud_brain down; an
    unconfirmed drop never maps — v5.52). `snapshot` may be
    the ambient_snapshot dict being built (its network_state
    is reused, so no second watcher read); without one, the
    watcher state is mirrored the same way _network() does.
    Never raises; returns the degradation_from_flags shape."""
    flags = dict(_DEGRADATION_OVERRIDE)
    try:
        network_state = None
        if isinstance(snapshot, dict):
            network_state = snapshot.get("network_state")
        if network_state is None:
            from .. import watchers  # Phase 3 B1b: moved to core/brain/

            state = watchers.network_state_snapshot()
            if isinstance(state, dict):
                network_state = state.get("state")
        if network_state == "offline":
            flags["cloud_brain"] = True
    except Exception:  # noqa: BLE001 — flag merge is best-effort
        pass
    return degradation_from_flags(flags)


def degradation_prompt_line(state=None) -> str | None:
    """The PR-4 prompt line for a degradation state (or the
    current one when state is None), or None when nothing is
    down. Never raises."""
    try:
        if state is None:
            state = current_degradation()
        if isinstance(state, dict):
            return state.get("prompt_line")
        return None
    except Exception:  # noqa: BLE001
        return None


def ambient_snapshot() -> dict:
    """The ambient snapshot: a dict that may carry battery_pct
    (int) + charging (bool), network_state ("online"/"offline"
    — v5.52 item 6: CONFIRMED states only, mirrored from the
    watcher engine; an announced offline may carry
    network_verdict, an unconfirmed drop appears only as
    network_advisory), idle_s (int) + away (bool),
    next_calendar_event ({"title", "when"} or None) and
    recent_timeline_kinds (newest-first kind names, at most 5).
    v5.56 (PR-4): when any capability is confirmed down, a
    `degradation` dict rides along (see current_degradation);
    it is ABSENT when nothing is down, exactly like every other
    absent-on-failure field here.
    A source that fails — or has never produced a reading —
    leaves its field ABSENT. Never raises."""
    snap: dict = {}
    for _fill in (_battery, _network, _idle, _calendar, _timeline):
        try:
            _fill(snap)
        except Exception:  # noqa: BLE001 — total by construction
            pass
    try:
        degradation = current_degradation(snap)
        if degradation["down"]:
            snap["degradation"] = degradation
    except Exception:  # noqa: BLE001 — absent field, never a raise
        pass
    return snap


def ambient_packet() -> str:
    """The snapshot as one compact line for the brain's context,
    e.g. "Ambient: Battery 62%; Network online; Away for ~25 min;
    Next: Team sync at Fri 14:30; Recent: download_done,
    network_down." — capped at ~200 tokens, and "" when nothing
    is worth saying. Never raises."""
    try:
        snap = ambient_snapshot()
    except Exception:  # noqa: BLE001
        return ""
    try:
        parts: list = []
        battery_part = None
        if "battery_pct" in snap:
            battery_part = f"Battery {snap['battery_pct']}%"
            if snap.get("charging"):
                battery_part += " (charging)"
        offline = snap.get("network_state") == "offline"
        advisory = snap.get("network_advisory")
        away = bool(snap.get("away"))
        idle_s = snap.get("idle_s")
        idle_part = None
        if away and idle_s is not None:
            idle_part = f"Away for ~{max(1, int(idle_s) // 60)} min"
        elif idle_s is not None and idle_s >= _IDLE_WORTH_SAYING_SECONDS:
            idle_part = f"Idle for ~{max(1, int(idle_s) // 60)} min"
        event = snap.get("next_calendar_event")
        event_part = None
        if isinstance(event, dict) and event.get("title"):
            event_part = f"Next: {event['title']}"
            if event.get("when"):
                event_part += f" at {event['when']}"
        kinds = snap.get("recent_timeline_kinds") or []
        kinds_part = None
        if kinds:
            kinds_part = "Recent: " + ", ".join(str(k) for k in kinds)
        # Worth saying at all? A battery reading, an outage, being
        # away, an upcoming event or a non-empty timeline each
        # qualify on their own; "online + just sat down + nothing
        # else" does not. v5.56 (PR-4): a degradation line
        # qualifies on its own, and rides FIRST in the parts —
        # truncation cuts from the end, so the do-not-promise
        # line is the last thing truncation can take.
        degradation = snap.get("degradation") or {}
        degradation_line = degradation.get("prompt_line") \
            if isinstance(degradation, dict) else None
        worthwhile = bool(battery_part or offline or advisory or away
                          or event_part or kinds_part
                          or degradation_line)
        if not worthwhile:
            return ""
        if degradation_line:
            # The line carries its own full stop standalone; as
            # a packet part the packet's own full stop serves.
            parts.append(str(degradation_line).rstrip().rstrip("."))
        if battery_part:
            parts.append(battery_part)
        if "network_state" in snap:
            # v5.52 (item 6): a confirmed offline renders with
            # the ladder's verdict attached — the same hedge
            # the user hears ("You've gone offline — though
            # quick checks look fine, so it may be a brief
            # blip") — never as a bare, citable binary.
            network_part = f"Network {snap['network_state']}"
            verdict = snap.get("network_verdict")
            if offline and verdict:
                network_part += f" — {verdict}"
            parts.append(network_part)
        elif advisory:
            parts.append(str(advisory))
        if idle_part:
            parts.append(idle_part)
        if event_part:
            parts.append(event_part)
        if kinds_part:
            parts.append(kinds_part)
        if not parts:
            return ""
        text = "Ambient: " + "; ".join(parts) + "."
        # v5.52 (item 6, extension): ATTEMPT-FIRST framing.
        # The packet's colour pre-emptively vetoed a capability
        # in the field ("I am unable to provide current world
        # events, sir, as the ambient network is currently
        # offline" — web_search never attempted; no executor or
        # tool gate consumes network_state, the refusal was the
        # model treating packet colour as a capability verdict).
        # So every packet now carries the doctrine in one short
        # line: these fields are background, and a capability
        # question is settled by attempting the tool and letting
        # its real result speak — a truly-offline search fails
        # honestly in one step, which is the correct failure
        # path. Appended AFTER the cap is applied to the parts,
        # so truncation can never cut the doctrine away.
        # v5.53 (F4): the outcome-not-process sentence rides in
        # the same appended framing, BEFORE attempt-first (the
        # netstate suite pins the packet's ending) — one
        # combined framing, truncated against as a unit.
        framing = _OUTCOME_NOT_PROCESS + _ATTEMPT_FIRST
        if len(text) + len(framing) > _MAX_PACKET_CHARS:
            text = text[:_MAX_PACKET_CHARS - len(framing) - 1]
            text = text.rstrip() + "…"
        return text + framing
    except Exception:  # noqa: BLE001 — a packet must never break a turn
        return ""
