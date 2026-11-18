"""
Proactivity engine for VYUK (v5.49, programme item 35) — watchers
NOTICE; suggestions ACT. A watcher notice says "your download
finished"; a suggestion says "your download finished — shall I
open it?" and carries the exact tools.execute() call that would
do it, so acting is one tap (or one word), never a re-ask.

Design rules, all load-bearing:

- A suggestion's action is an ordinary {"tool", "args"} pair run
  through core/funnel/tools_registry.py's execute() funnel — the FULL gate stack
  (validation, panic, protected paths, the human confirm gate).
  The suggestion layer adds NO privilege of its own: a gated
  action still gates, a refused verdict is honoured, never
  forced or retried behind the user's back.
- Discipline mirrors the shipped notice path (N1 call-aware
  gating + N3 digest discipline in the watcher/GUI tell path),
  reusing its machinery rather than paralleling it: the in-call
  signal is the SAME shipped seam the N1 speech gate reads
  (core.watchers' cached foreground call-app + mic-holder state),
  the delivery throttle aligns with the notice speech throttle
  (90 s), and suggestions raised during a call are HELD and
  surface afterwards as one post-call digest — the same shape
  as the GUI's held-notice digest, delivered here through the
  digest sink / poll() because this module is headless.
- Delivery seam for the wave-2 GUI card: register_suggestion_
  listener(fn) — fn(suggestion_dict) is called on delivery,
  exactly as watchers deliver notices through their on_event
  callback. With NO listener registered, suggestions still
  record, still act/dismiss, and held ones still surface via
  poll()/the digest path.
- Settings are read through small accessor seams in the same
  style as the watchers' settings probes (_probe_ambient_light_
  enabled and friends): module-level functions the Settings
  wiring overrides. Master switch defaults ON (showing is the
  point of the feature); auto-act defaults OFF, and even when ON
  it fires ONLY for classes on the per-class whitelist — there
  is no silent auto-act outside a whitelisted class, ever.

Producers wired in v5.49 (programme item 35):
- meeting-imminent: the watcher engine polls the next calendar
  events (the google_services.next_events shape ambient.py
  already consumes); an event starting within ~10 minutes
  raises a "meeting_open" suggestion. When the event carries a
  link (an explicit link/url field, or a URL in its location —
  the usual Meet/Zoom spot) the action opens THAT via the real
  open_url tool; otherwise the action opens the event's day in
  Google Calendar, again via open_url.
- battery-forecast: when the N9 forecast notice fires (≤30%
  and projected empty within ~45 min), a "battery_saver"
  suggestion offers to dim the screen via the real
  set_brightness tool.

The N6 download follow-through is deliberately NOT routed
through this engine in v5.49: its offer is wording inside the
notice the GUI already speaks ("Say the word and I'll open
it"), with the open carried out by the brain through the normal
tool path — there is no per-download state in the engine to
attach an action record to without reworking N6's completion
bookkeeping. CLASS_DOWNLOAD_OPEN is enumerated below so the
class exists when that routing lands.

Pure stdlib; imports nothing from gui; never raises out of its
public functions (a broken producer/listener/executor degrades
to a recorded outcome, never a crash in a watcher tick).
"""

import itertools
import threading
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Discipline constants — aligned with the shipped notice path.
# ---------------------------------------------------------------------------

#: Global minimum interval between DELIVERIES. The GUI's watcher
#: speech path throttles notices to one per 90 s (N1/N3); the
#: suggestion card path holds itself to the same cadence so a
#: flurry of producers can't stack cards on the user.
GLOBAL_MIN_INTERVAL_SECONDS = 90

#: Same kind+target raised again within this window is a
#: duplicate — watcher polls run every 30 s, so a condition that
#: stays true must raise ONCE, not once per poll.
DEDUPE_WINDOW_SECONDS = 30 * 60

#: A dismiss suppresses re-raising that kind+target for this
#: long: "not now" on a card means "stop asking me about THIS
#: one", effectively for the rest of the day and beyond.
DISMISS_SUPPRESS_SECONDS = 24 * 60 * 60

#: Per-kind cap: at most this many raises of one kind inside
#: KIND_CAP_WINDOW_SECONDS, however many distinct targets.
PER_KIND_CAP = 3
KIND_CAP_WINDOW_SECONDS = 60 * 60

#: Records are kept for act/dismiss/digest lookups, but the
#: registry is not a diary — old terminal records are pruned.
_MAX_RECORDS = 200

# ---------------------------------------------------------------------------
# Suggestion classes — the auto-act whitelist names these, and
# ONLY these. Enumerated here so a whitelist entry that names
# anything else is rejected, never honoured.
# ---------------------------------------------------------------------------

CLASS_MEETING_OPEN = "meeting_open"
CLASS_BATTERY_SAVER = "battery_saver"
CLASS_DOWNLOAD_OPEN = "download_open"  # reserved (see docstring)

AUTO_ACT_CLASSES = frozenset({
    CLASS_MEETING_OPEN,
    CLASS_BATTERY_SAVER,
    CLASS_DOWNLOAD_OPEN,
})

#: Producer kinds.
KIND_MEETING_IMMINENT = "meeting_imminent"
KIND_BATTERY_FORECAST = "battery_forecast_suggestion"

# ---------------------------------------------------------------------------
# Presentation templates (v5.51, polish item N-1) — the ONE place
# suggestion-card wording lives. Every producer formats its card
# body from here and the chat card reads its title + icon from
# here, so a card's voice cannot drift per producer: factual,
# brief, and offering rather than announcing ("… — shall I …?").
# No internal vocabulary (producer / kind / throttle / class)
# ever reaches the user. download_open gets a template so the
# reserved class is presentation-ready; N-1 wires NO producer
# for it — the reservation in the module docstring stands.
# ---------------------------------------------------------------------------

SUGGESTION_TEMPLATES = {
    CLASS_MEETING_OPEN: {
        "icon": "clock",
        "title": "Meeting coming up",
        "body": "{event_title} starts at {start_time} "
                "— shall I open {destination}?",
    },
    CLASS_BATTERY_SAVER: {
        "icon": "monitor",
        "title": "Battery running low",
        "body": "At {percent}%, it could be empty in about "
                "{minutes} minutes — shall I dim the screen to "
                "{brightness}% to stretch it?",
    },
    CLASS_DOWNLOAD_OPEN: {
        "icon": "check",
        "title": "Download finished",
        "body": "{file_name} has finished downloading "
                "— shall I open it?",
    },
}

#: Any class/kind without a template renders through this one —
#: a sane generic card, never a crash and never a raw identifier.
SUGGESTION_GENERIC_TEMPLATE = {
    "icon": "spark",
    "title": "A suggestion for you",
    "body": "{text}",
}

_KIND_TO_CLASS = {
    KIND_MEETING_IMMINENT: CLASS_MEETING_OPEN,
    KIND_BATTERY_FORECAST: CLASS_BATTERY_SAVER,
}


def suggestion_template(suggestion_class=None, kind=None) -> dict:
    """The presentation template for a suggestion: looked up by
    class first, then by producer kind; unknown or missing values
    fall back to the generic template. Returns a copy."""
    for key in (str(suggestion_class or ""), str(kind or "")):
        if key in SUGGESTION_TEMPLATES:
            return dict(SUGGESTION_TEMPLATES[key])
        mapped = _KIND_TO_CLASS.get(key)
        if mapped in SUGGESTION_TEMPLATES:
            return dict(SUGGESTION_TEMPLATES[mapped])
    return dict(SUGGESTION_GENERIC_TEMPLATE)


def format_suggestion_body(suggestion_class=None, kind=None,
                           **fields) -> str:
    """Format a template's body with the producer's facts. A
    missing field degrades to the caller's plain `text` field
    (or ""), never an exception in a watcher tick."""
    template = suggestion_template(suggestion_class, kind=kind)
    try:
        return template["body"].format(**fields)
    except Exception:  # noqa: BLE001
        return str(fields.get("text") or "")

#: Meeting producer: an event starting within this many minutes
#: is imminent (the GUI's spoken heads-up fires at 15; a card
#: offering to open the doc wants to be a little closer in).
MEETING_WINDOW_MINUTES = 10

#: Battery producer: the brightness the saver suggestion offers.
BATTERY_SAVER_BRIGHTNESS = 40


# ---------------------------------------------------------------------------
# Settings seams — same style as the watchers' settings probes
# (module-level accessors the Settings wiring overrides; the
# defaults below are the shipped behaviour). NOT core/config.py:
# watcher/suggestion toggles live with the watchers settings.
# ---------------------------------------------------------------------------

_settings = {
    "enabled": True,        # master switch — showing defaults ON
    "auto_act": False,      # auto-act tier — OFF by default
    "auto_act_whitelist": frozenset(),
}


def suggestions_enabled() -> bool:
    """Master switch for RAISING/showing suggestions."""
    try:
        return bool(_settings["enabled"])
    except Exception:  # noqa: BLE001
        return True


def auto_act_enabled() -> bool:
    """The auto-act tier's master switch. OFF by default."""
    try:
        return bool(_settings["auto_act"])
    except Exception:  # noqa: BLE001
        return False


def auto_act_whitelist() -> frozenset:
    """The per-class auto-act whitelist, intersected with the
    classes enumerated in code — an entry naming a class that
    does not exist is dropped, never honoured."""
    try:
        return frozenset(_settings["auto_act_whitelist"]) & AUTO_ACT_CLASSES
    except Exception:  # noqa: BLE001
        return frozenset()


def configure_suggestions(enabled=None, auto_act=None,
                          auto_act_whitelist=None) -> None:
    """The Settings wiring's entry point (and the tests'). Only
    the arguments given are changed. Never raises."""
    try:
        if enabled is not None:
            _settings["enabled"] = bool(enabled)
        if auto_act is not None:
            _settings["auto_act"] = bool(auto_act)
        if auto_act_whitelist is not None:
            _settings["auto_act_whitelist"] = frozenset(
                str(c) for c in auto_act_whitelist)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Shipped-seam defaults: in-call detection + the execute funnel.
# ---------------------------------------------------------------------------

def _default_in_call() -> bool:
    """In a call RIGHT NOW, read from the SAME shipped seam the
    N1 speech gate uses: core.watchers' cached state — a known
    call app in the foreground (current_foreground_call_app) or
    another app holding the microphone (current_mic_users).
    Reading the caches runs no probe. Any failure reads as
    "not in a call" (the pre-N1 behaviour), never a raise."""
    try:
        try:
            from .. import watchers  # Phase 3 B1b: moved to core/brain/
        except Exception:  # noqa: BLE001 — not imported as a package
            from voice_assistant.core import watchers
        if watchers.current_foreground_call_app() is not None:
            return True
        return bool(watchers.current_mic_users())
    except Exception:  # noqa: BLE001
        return False


def _default_execute(tool, args):
    """The real funnel: core/funnel/tools_registry.py's registry execute(). Lazy —
    importing suggestions must not pull the tool registry in."""
    try:
        from ..funnel.tools_registry import tools  # Phase 3 B1b: moved to core/brain/; Phase 4 S1b INTENTIONAL: registry split to funnel
    except Exception:  # noqa: BLE001 — not imported as a package
        from voice_assistant.core.funnel.tools_registry import tools  # Phase 4 S1b INTENTIONAL: registry split to funnel
    return tools.execute(tool, dict(args or {}))


# ---------------------------------------------------------------------------
# The registry.
# ---------------------------------------------------------------------------

class SuggestionRegistry:
    """Records, dedupes, throttles, holds, delivers, acts.

    Injectable seams (tests fake all three): now_fn (a zero-arg
    datetime callable, matching the watcher engine's), execute_fn
    ((tool, args) -> execute()-style result dict), in_call_fn
    (() -> bool). digest_sink, when given, is called with the
    composed post-call digest text when held suggestions flush.
    """

    def __init__(self, now_fn=datetime.now, execute_fn=None,
                 in_call_fn=None, digest_sink=None):
        self._now_fn = now_fn
        self._execute_fn = execute_fn or _default_execute
        self._in_call_fn = in_call_fn or _default_in_call
        self._digest_sink = digest_sink
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._order: list[str] = []
        self._ids = itertools.count(1)
        self._listeners: list = []
        self._last_delivery_at = None
        self._last_raised: dict[tuple, datetime] = {}
        self._kind_raises: dict[str, list] = {}
        self._suppressed_until: dict[tuple, datetime] = {}

    # -- delivery seam --------------------------------------------------

    def register_listener(self, fn) -> None:
        """fn(suggestion_dict) on each delivery. The wave-2 GUI
        card consumes this; mirror of the watchers' on_event."""
        if callable(fn) and fn not in self._listeners:
            self._listeners.append(fn)

    def unregister_listener(self, fn) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _deliver(self, record) -> None:
        record["status"] = "pending"  # delivered, awaiting the user
        record["delivered_at"] = self._now_fn()
        self._last_delivery_at = record["delivered_at"]
        for fn in list(self._listeners):
            try:
                fn(dict(record))
            except Exception as e:  # noqa: BLE001 — a bad card must not kill the engine
                print(f"[Suggestions] listener failed: {e}")
        self._maybe_auto_act(record)

    # -- raising --------------------------------------------------------

    def raise_suggestion(self, kind, suggestion_class, text, action,
                         target, source_event=None):
        """Raise one suggestion; returns the record dict, or None
        when a discipline rule (master switch, dedupe, dismiss
        suppression, per-kind cap) says this raise does not
        happen. `action` is {"tool": name, "args": {...}} — an
        execute() call, nothing bespoke."""
        try:
            if not suggestions_enabled():
                return None
            tool = str((action or {}).get("tool") or "").strip()
            if not tool or not str(text or "").strip():
                return None
            now = self._now_fn()
            key = (str(kind), str(target))
            with self._lock:
                until = self._suppressed_until.get(key)
                if until is not None and now < until:
                    return None
                last = self._last_raised.get(key)
                if last is not None and \
                        (now - last).total_seconds() < DEDUPE_WINDOW_SECONDS:
                    return None
                raises = [t for t in self._kind_raises.get(key[0], [])
                          if (now - t).total_seconds()
                          < KIND_CAP_WINDOW_SECONDS]
                if len(raises) >= PER_KIND_CAP:
                    self._kind_raises[key[0]] = raises
                    return None
                raises.append(now)
                self._kind_raises[key[0]] = raises
                self._last_raised[key] = now
                sid = f"sug-{next(self._ids)}"
                record = {
                    "id": sid,
                    "kind": str(kind),
                    "class": str(suggestion_class),
                    "text": str(text),
                    "action": {"tool": tool,
                               "args": dict((action or {}).get("args")
                                            or {})},
                    "target": str(target),
                    "created_at": now,
                    "source_event": source_event,
                    "status": "raised",
                    "delivered_at": None,
                    "outcome": None,
                }
                self._records[sid] = record
                self._order.append(sid)
                self._prune_locked()
            # Delivery decision (outside the lock: listeners and
            # the execute funnel must never run under it).
            if self._in_call_fn():
                # N1 discipline: never pop over a call — hold for
                # the post-call digest (poll() flushes).
                record["status"] = "held"
                return dict(record)
            if self._last_delivery_at is not None and \
                    (now - self._last_delivery_at).total_seconds() \
                    < GLOBAL_MIN_INTERVAL_SECONDS:
                # N3 discipline: recorded, delivery deferred to
                # poll() once the global interval opens.
                return dict(record)
            self._deliver(record)
            return dict(record)
        except Exception as e:  # noqa: BLE001 — producers must not crash a tick
            print(f"[Suggestions] raise failed: {e}")
            return None

    # -- post-call digest / deferred delivery ---------------------------

    def poll(self) -> list:
        """Flush what discipline held back; returns the records
        delivered by this call. Held (in-call) suggestions go out
        TOGETHER as the post-call digest once the call clears —
        mirroring the GUI's _deliver_call_digest — and deferred
        (throttle) ones follow one per global interval."""
        delivered: list = []
        try:
            if self._in_call_fn():
                return delivered
            now = self._now_fn()
            with self._lock:
                held = [self._records[i] for i in self._order
                        if self._records[i]["status"] == "held"]
                deferred = [self._records[i] for i in self._order
                            if self._records[i]["status"] == "raised"]
            if held:
                for record in held:
                    self._deliver(record)
                    delivered.append(dict(record))
                self._send_digest(held)
                return delivered
            if deferred:
                if self._last_delivery_at is None or \
                        (now - self._last_delivery_at).total_seconds() \
                        >= GLOBAL_MIN_INTERVAL_SECONDS:
                    record = deferred[0]
                    self._deliver(record)
                    delivered.append(dict(record))
            return delivered
        except Exception as e:  # noqa: BLE001
            print(f"[Suggestions] poll failed: {e}")
            return delivered

    def _send_digest(self, records) -> None:
        """The held suggestions as ONE digest line through the
        digest sink — the headless counterpart of the GUI's
        "While you were in your call: …" sentence. With no sink
        the records are still delivered via the listeners above;
        the sink is how a no-listener run surfaces them."""
        if self._digest_sink is None:
            return
        try:
            parts = [r["text"].rstrip(".") for r in records
                     if r.get("text")]
            if not parts:
                return
            if len(parts) == 1:
                body = parts[0]
            elif len(parts) == 2:
                body = f"{parts[0]}, and {parts[1]}"
            else:
                body = ", ".join(parts[:-1]) + f", and {parts[-1]}"
            if not body.endswith((".", "?", "!")):
                body += "."
            self._digest_sink(f"While you were in your call: {body}")
        except Exception as e:  # noqa: BLE001
            print(f"[Suggestions] digest sink failed: {e}")

    def held(self) -> list:
        """Records currently held for the post-call digest."""
        return [dict(self._records[i]) for i in self._order
                if self._records[i]["status"] == "held"]

    def pending(self) -> list:
        """Delivered records still awaiting the user's tap/word."""
        return [dict(self._records[i]) for i in self._order
                if self._records[i]["status"] == "pending"]

    def get(self, suggestion_id):
        record = self._records.get(str(suggestion_id))
        return dict(record) if record else None

    def all_records(self) -> list:
        return [dict(self._records[i]) for i in self._order]

    # -- act / dismiss ----------------------------------------------------

    def _maybe_auto_act(self, record) -> None:
        """Auto-act tier: ONLY when the tier is on AND this
        suggestion's class is whitelisted. Runs through act() —
        the same execute() funnel, gates included."""
        try:
            if not auto_act_enabled():
                return
            if record.get("class") not in auto_act_whitelist():
                return
            if record.get("status") != "pending":
                return
            self.act(record["id"])
        except Exception as e:  # noqa: BLE001
            print(f"[Suggestions] auto-act failed: {e}")

    def act(self, suggestion_id) -> dict:
        """Run the suggestion's action through tools.execute —
        the full gate stack; a refusal is the outcome, recorded
        and returned, never forced. Returns an outcome dict."""
        record = self._records.get(str(suggestion_id))
        if record is None:
            return {"status": "error", "kind": "unknown_suggestion",
                    "message": f"No suggestion with id {suggestion_id}."}
        if record["status"] in ("acted", "dismissed", "act_refused",
                                 "act_failed"):
            return dict(record.get("outcome") or
                        {"status": "error", "kind": "already_resolved",
                         "message": "That suggestion was already resolved."})
        action = record["action"]
        try:
            result = self._execute_fn(action["tool"], dict(action["args"]))
        except Exception as e:  # noqa: BLE001 — the funnel failing is an outcome too
            result = {"status": "error", "kind": "handler_error",
                      "message": str(e)}
        if not isinstance(result, dict):
            result = {"status": "success", "message": str(result)}
        outcome = dict(result)
        outcome["suggestion_id"] = record["id"]
        outcome["tool"] = action["tool"]
        record["outcome"] = outcome
        # Audit A5: contract statuses, not just legacy "error".
        from ..funnel.tool_result import is_non_success as _is_non_success  # Phase 3 B1b: moved to core/brain/

        if _is_non_success(result):
            # A gated verdict (refused / not_confirmed) lands here
            # exactly like any other error: honoured, recorded,
            # and the suggestion stays resolvable-by-nobody — it
            # is done; re-acting a refused action is a NEW raise's
            # business, not a quiet retry.
            kind = str(result.get("kind") or "")
            record["status"] = ("act_refused" if kind in
                                 ("refused", "not_confirmed")
                                 else "act_failed")
        else:
            record["status"] = "acted"
        return outcome

    def dismiss(self, suggestion_id) -> bool:
        """Record the dismissal and suppress re-raising that
        kind+target for DISMISS_SUPPRESS_SECONDS."""
        record = self._records.get(str(suggestion_id))
        if record is None:
            return False
        record["status"] = "dismissed"
        record["outcome"] = {"status": "dismissed",
                             "suggestion_id": record["id"]}
        key = (record["kind"], record["target"])
        self._suppressed_until[key] = (
            self._now_fn() + timedelta(
                seconds=DISMISS_SUPPRESS_SECONDS))
        return True

    # -- bookkeeping ------------------------------------------------------

    def _prune_locked(self) -> None:
        while len(self._order) > _MAX_RECORDS:
            oldest = self._order.pop(0)
            record = self._records.pop(oldest, None)
            if record and record["status"] in ("pending", "held",
                                               "raised"):
                # Never prune a live one out from under the user;
                # put it back and stop (records are small).
                self._records[oldest] = record
                self._order.insert(0, oldest)
                break


# ---------------------------------------------------------------------------
# Module-level default registry + the convenience API producers
# and the wave-2 card use.
# ---------------------------------------------------------------------------

_default_registry = SuggestionRegistry()


def default_registry() -> SuggestionRegistry:
    return _default_registry


def register_suggestion_listener(fn) -> None:
    """The delivery seam: fn(suggestion_dict) per delivery."""
    _default_registry.register_listener(fn)


def raise_suggestion(kind, suggestion_class, text, action, target,
                     source_event=None, registry=None):
    return (registry or _default_registry).raise_suggestion(
        kind, suggestion_class, text, action, target,
        source_event=source_event)


def act(suggestion_id, registry=None) -> dict:
    return (registry or _default_registry).act(suggestion_id)


def dismiss(suggestion_id, registry=None) -> bool:
    return (registry or _default_registry).dismiss(suggestion_id)


def poll_suggestions(registry=None) -> list:
    return (registry or _default_registry).poll()


# ---------------------------------------------------------------------------
# Producers (v5.49, programme item 35).
# ---------------------------------------------------------------------------

def _event_link(event: dict):
    """The openable link an event carries, or None: an explicit
    link/url/html_link field first, then a URL standing in the
    location (where Meet/Zoom links usually live)."""
    for key in ("link", "url", "html_link", "hangout_link"):
        value = str(event.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    location = str(event.get("location") or "").strip()
    if location.startswith(("http://", "https://")):
        return location.split()[0]
    return None


def meeting_action(event: dict) -> dict:
    """The execute() call for a meeting suggestion: open_url on
    the event's own link when it has one; otherwise open_url on
    the event's day in Google Calendar (the event itself, in
    the calendar UI) — both real, registered tools today."""
    link = _event_link(event)
    if link:
        return {"tool": "open_url", "args": {"url": link}}
    start = event.get("start")
    if isinstance(start, datetime):
        url = ("https://calendar.google.com/calendar/u/0/r/day/"
               f"{start.year}/{start.month}/{start.day}")
    else:
        url = "https://calendar.google.com/calendar/u/0/r"
    return {"tool": "open_url", "args": {"url": url}}


def poll_meeting_suggestions(events, now=None, registry=None):
    """One producer poll over next-events dicts (the
    google_services.next_events shape: id/title/start/location/
    all_day). An event starting within MEETING_WINDOW_MINUTES
    raises exactly one meeting_open suggestion (the registry's
    dedupe absorbs the 30 s re-polls). Returns the raised
    record or None."""
    try:
        now = now or datetime.now().astimezone()
        for event in events or []:
            if not isinstance(event, dict) or event.get("all_day"):
                continue
            start = event.get("start")
            if not isinstance(start, datetime):
                continue
            if start.tzinfo is None and now.tzinfo is not None:
                start = start.replace(tzinfo=now.tzinfo)
            elif start.tzinfo is not None and now.tzinfo is None:
                # The watcher engine's clock is naive-local; read
                # the aware start as local wall-clock to match.
                start = start.replace(tzinfo=None)
            minutes = (start - now).total_seconds() / 60.0
            if not 0 < minutes <= MEETING_WINDOW_MINUTES:
                continue
            title = str(event.get("title") or "").strip() or "Event"
            target = str(event.get("id") or "") or \
                f"{title}|{start.isoformat()}"
            link = _event_link(event)
            # v5.51 N-1: wording comes from the one template map.
            text = format_suggestion_body(
                CLASS_MEETING_OPEN, event_title=title,
                start_time=start.strftime('%H:%M'),
                destination=("its link" if link
                             else "it in your calendar"))
            return raise_suggestion(
                KIND_MEETING_IMMINENT, CLASS_MEETING_OPEN, text,
                meeting_action(event), target,
                source_event={"kind": "calendar_event",
                              "event_id": str(event.get("id") or ""),
                              "start": start.isoformat()},
                registry=registry)
        return None
    except Exception as e:  # noqa: BLE001 — a producer must not crash a tick
        print(f"[Suggestions] meeting producer failed: {e}")
        return None


def raise_battery_forecast_suggestion(percent, minutes, registry=None):
    """The battery producer, called by the watcher engine where
    the N9 forecast notice fires: at the current drain the
    battery is nearly out — offer the one action that stretches
    it, dimming the screen via the real set_brightness tool."""
    try:
        _mins = int(round(float(minutes)))
        _pct = int(percent)
    except Exception:  # noqa: BLE001
        return None
    return raise_suggestion(
        KIND_BATTERY_FORECAST, CLASS_BATTERY_SAVER,
        # v5.51 N-1: wording comes from the one template map.
        format_suggestion_body(
            CLASS_BATTERY_SAVER, percent=_pct, minutes=_mins,
            brightness=BATTERY_SAVER_BRIGHTNESS),
        {"tool": "set_brightness",
         "args": {"level": BATTERY_SAVER_BRIGHTNESS}},
        target="battery",
        source_event={"kind": "battery_forecast",
                      "percent": _pct, "minutes": _mins},
        registry=registry)
