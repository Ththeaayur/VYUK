"""GAPS-8 (BLOCKS #7, 2026-11-13): scheduled agents — the agent layer
over the existing APScheduler engine, plus the morning digest.

THE GAP (proved first, on the backup — gap_scheduled_agents.py):
core/desktop/job_scheduler.py is a real APScheduler wrapper
(1,393 lines: cron/interval/one-shot, per-job timezone, per-job
misfire grace, coalesce, naive-time refusal, SQLite job store) with
ZERO production callers — the only production touch is the
boot_sequence health beat calling engine_status() (read-only), so no
persisted job can ever fire. There is no OperatorManifest /
OperativeAgent and no morning digest anywhere in the tree.

WHAT THIS MODULE ADDS (own stdlib code — nothing adopted):
* OperatorManifest — a plain-data manifest that declares, per
  scheduled job, exactly what it may do (which funnel tools, which
  data it may read, which side effects it may perform). The manifest
  is ENFORCED, not advisory: a job that tries a funnel tool outside
  its manifest is refused, the run stops, and the refusal is
  recorded — OpenJarvis's OperatorManifest declares
  required_capabilities but NEVER enforces them; this one does.
* OperativeAgent — runs a scheduled job through the funnel, under
  its manifest, with the run recorded in the hash-chained ledger.
  A job with no manifest cannot run (fail-closed). The agent never
  injects confirm=True and never bypasses the funnel's own confirm
  gate — a gated tool the funnel refuses is a refused step, recorded
  honestly, and the run stops.
* The morning digest — the first scheduled agent: collect the last
  24h (activity log + upcoming calendar) -> short LLM summary
  (<=200 words, self-evaluated, regenerated once if weak) -> speak
  it via the funnel's read-aloud tool (speak_text, SAPI) -> store
  the digest. Default schedule 7am in the user's timezone
  (Asia/Kolkata — NOT OpenJarvis's America/Los_Angeles default).
  Asia/Kolkata is the canonical IANA name for the user's timezone
  (IST); Asia/Calcutta is the same zone's old name, but it is not
  in this system's tzdata, so the canonical name is used — it
  resolves on both interpreters.
  The digest job is created ONLY by the user's explicit setup
  (scheduled_agents setup_digest, confirm-gated) — never
  auto-enabled at install.
* The tool `scheduled_agents` (list / status / setup_digest /
  cancel / run_now / digest / save_routine / run_routine) — the
  user-facing surface.
* RET-2 (2026-11-15): named saved routines — two new actions on
  this tool, save_routine (confirm-gated) and run_routine (free
  dispatch). A save confirms the routine's SHAPE (name, schedule,
  the full step list with args, redacted) — never a blanket
  pre-approval of future runs. A run re-validates every step
  against the live registry, builds a transient manifest whose
  allowed_tools is exactly the routine's steps, runs it through
  run_job (the OperativeAgent path — every gated step still asks
  at run time), reports per-step outcomes, and deletes the
  manifest. Args are frozen literals at save; a step whose tool
  vanished since save refuses the whole run before anything runs.

GATING (stated plainly):
* setup_digest / cancel / run_now / save_routine are
  confirm-gated (they create, remove, immediately run, or save a
  persisted job/routine). The args-aware branch in
  core/funnel/tools_gate.py gates only those four actions;
  list / status / digest are free reads, and run_routine is free
  dispatch — the run itself asks nothing, but every gated STEP
  still asks at run time through the funnel.
* A digest run speaks under the manifest the user approved at
  setup — no per-run popup (a popup at 7am with no one there would
  fail closed and the digest would never speak). The setup
  confirmation IS the approval, recorded in the manifest.
* The digest itself takes no effectful action beyond reading and
  speaking: it reads the activity log + upcoming calendar, speaks
  the summary, stores the digest record. Any effectful step it ever
  wanted would need its own manifest entry and confirm gate.
* The engine starts lazily on the first schedule (job_scheduler's
  own rule) and via ensure_engine_running(), the app-startup hook:
  call it once at startup so persisted jobs fire after a restart.
  GAPS-26 (S-V1/S-V2): that call is now wired into the GUI boot
  path (_start_background_services in gui/window_services_trust.py),
  so a persisted job fires after a restart — before this, the
  engine never started at boot and a persisted job could only
  fire if something scheduled anew.
* GAPS-26 (S-V6): the tool's list/status are the ONE listing for
  all three schedulers — agent jobs (job_id), ProactiveScheduler
  reminders (label) and scheduled routines (name), each entry
  tagged with its source and address — and status reports misfires
  in detail (missed_jobs from the engine's persisted missed log
  plus missed_reminders from get_missed_events, which had no
  production consumer before this), not just a count.

BOUNDARIES (stated, not smoothed over):
* The engine is NOT rebuilt — job_scheduler.py is used as-is
  (per-job timezone, misfire grace, coalescing, DST, SQLite store).
* The manifest enforces funnel-tool calls at runtime. Direct reads
  (activity log, calendar) are declared in allowed_data and the
  digest module reads exactly those two sources — enforcement there
  is declarative + code-reviewed, stated here, not pretended as
  runtime-enforced.
* The digest speaks via speak_text (SAPI) because that is the
  funnel tool that actually plays audio with a verified Outcome.
  Kokoro renders audio but does not play it; the digest's speak
  step is the playing step, so it uses the tool that plays.
* No real scheduled run has ever fired on the user's Windows
  machine — the lab proves the layer; the field leg is owed.
"""

import datetime
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from . import audit_chain as _ledger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HANDLER_NAME = "operative_agent"
DIGEST_JOB_ID = "morning_digest"
DIGEST_JOB_NAME = "Morning digest"
DIGEST_TIMEZONE = "Asia/Kolkata"
DIGEST_HOUR = 7
DIGEST_MINUTE = 0
DIGEST_MISFIRE_GRACE_SECONDS = 3600  # 1h: a 7am digest missed by more is reported, not run late
DIGEST_WORD_CAP = 200
DIGEST_REGEN_WORD_CAP = 150
DIGEST_MIN_COVERAGE = 0.5
DIGEST_WINDOW_HOURS = 24
DIGEST_CALENDAR_LIMIT = 20
DIGEST_ACTIVITY_KEEP = 20

_MANIFESTS_FILE_NAME = "scheduled_agents_manifests.json"
_DIGESTS_FILE_NAME = "digests.jsonl"
_ACTIVITY_FILE_NAME = "activity.log"

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# The manifest vocabularies. A manifest may only name data sources and
# side effects from these sets — an unknown entry is refused at
# creation, so a manifest can never silently claim a power it does
# not have. allowed_tools is checked against the live registry
# instead (a tool that does not exist cannot be allowed).
_KNOWN_DATA_SOURCES = frozenset({
    "activity_log",       # data/activity.log, last 24h
    "calendar_upcoming",  # google_services.next_events, next 24h
    "digest_store",       # data/digests.jsonl (read the last digest)
    "job_store",          # the scheduler's own job list (list/status)
    "reminders",          # RET-1: the scheduler's reminders (nudge counts only)
    "routines",           # RET-1: data/routines.json (nudge counts only)
})
_KNOWN_SIDE_EFFECTS = frozenset({
    "speak_digest",  # speak the digest text aloud (speak_text)
    "store_digest",  # append the digest record to digests.jsonl
    "run_tool",      # run a funnel tool (steps jobs only)
    "speak_nudge",   # RET-1: speak the daily nudge aloud (speak_text)
    "store_nudge",   # RET-1: record the nudge outcome in daily_count.json
})

_ACTIONS = ("list", "status", "setup_digest", "cancel", "run_now",
            "digest", "save_routine", "run_routine")
_GATED_ACTIONS = frozenset({"setup_digest", "cancel", "run_now"})

# Words too common to prove a summary mentioned an item (same
# stopword discipline as the focus-day file scan).
_TITLE_STOPWORDS = frozenset({
    "meeting", "call", "sync", "weekly", "daily", "standup",
    "review", "discussion", "session", "intro", "introduction",
    "with", "about", "from", "team", "project", "update",
})

_TOOL_DESCRIPTION = (
    "Scheduled agents: manage VYUK's scheduled jobs and the morning "
    "digest. list/status/digest are reads. setup_digest creates the "
    "morning-digest job (7am, Asia/Kolkata) — confirm-gated, and only "
    "on your explicit setup, never auto-enabled. cancel removes a "
    "scheduled job; run_now runs one immediately under its manifest. "
    "save_routine saves a named routine (ordered tool calls, frozen "
    "args, optional schedule) — confirm-gated, and the confirmation "
    "shows the full step list; run_routine re-runs one by name "
    "through the same manifest path, each gated step still asking "
    "at run time. "
    "Every job runs under an OperatorManifest that declares exactly "
    "what it may do; a job that tries anything outside its manifest "
    "is refused, not run."
)


# ---------------------------------------------------------------------------
# Store paths (overridable seams — tests assign these, like
# social_send.state_path; production uses data/ under the tree root)
# ---------------------------------------------------------------------------

def manifests_path():
    """Path of the manifests store (job_id -> manifest dict)."""
    return _DATA_DIR / _MANIFESTS_FILE_NAME


def digests_path():
    """Path of the digests store (one JSON object per line)."""
    return _DATA_DIR / _DIGESTS_FILE_NAME


def activity_path():
    """Path of the activity log the digest reads."""
    return _DATA_DIR / _ACTIVITY_FILE_NAME


# ---------------------------------------------------------------------------
# OperatorManifest
# ---------------------------------------------------------------------------

def validate_manifest(manifest, known_tools=None):
    """Validate a manifest dict. Returns (ok, reason).

    known_tools is the set of registered tool names; when None it is
    built from the live registry (lazy import — the registry builds
    every tool, so callers that already have it pass it in).
    """
    if not isinstance(manifest, dict):
        return False, "manifest must be a dict"
    job_id = manifest.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        return False, "manifest job_id must be a non-empty string"
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "manifest name must be a non-empty string"
    allowed_tools = manifest.get("allowed_tools")
    if not isinstance(allowed_tools, list) or not all(
            isinstance(t, str) for t in allowed_tools):
        return False, "manifest allowed_tools must be a list of strings"
    if known_tools is None:
        try:
            from .funnel.tools_registry import tools as _tools
            known_tools = {d["name"] for d in _tools.get_declarations()}
        except Exception as exc:  # noqa: BLE001 — no registry, no trust
            return False, "cannot verify allowed_tools: registry unavailable (%s)" % exc
    unknown = [t for t in allowed_tools if t not in known_tools]
    if unknown:
        return False, "manifest allows unregistered tools: %s" % ", ".join(sorted(unknown))
    allowed_data = manifest.get("allowed_data")
    if not isinstance(allowed_data, list) or not all(
            isinstance(d, str) for d in allowed_data):
        return False, "manifest allowed_data must be a list of strings"
    unknown_data = [d for d in allowed_data if d not in _KNOWN_DATA_SOURCES]
    if unknown_data:
        return False, "manifest allows unknown data sources: %s" % ", ".join(sorted(unknown_data))
    side_effects = manifest.get("side_effects")
    if not isinstance(side_effects, list) or not all(
            isinstance(s, str) for s in side_effects):
        return False, "manifest side_effects must be a list of strings"
    unknown_fx = [s for s in side_effects if s not in _KNOWN_SIDE_EFFECTS]
    if unknown_fx:
        return False, "manifest allows unknown side effects: %s" % ", ".join(sorted(unknown_fx))
    if not isinstance(manifest.get("confirm_required", False), bool):
        return False, "manifest confirm_required must be a bool"
    payload = manifest.get("payload")
    if payload is not None:
        if not isinstance(payload, dict):
            return False, "manifest payload must be a dict"
        kind = payload.get("kind")
        if kind not in ("digest", "steps", "daily_nudge"):
            return False, ("manifest payload kind must be 'digest', "
                           "'steps' or 'daily_nudge'")
        if kind == "steps":
            steps = payload.get("steps")
            if not isinstance(steps, list) or not steps:
                return False, "manifest payload steps must be a non-empty list"
            for step in steps:
                if not isinstance(step, dict) or not isinstance(
                        step.get("tool"), str):
                    return False, "manifest payload step must be a dict with a tool name"
                if not isinstance(step.get("args", {}), dict):
                    return False, "manifest payload step args must be a dict"
                if step["tool"] not in allowed_tools:
                    return False, ("manifest payload step tool %r is not in "
                                   "allowed_tools" % step["tool"])
    return True, ""


def create_manifest(job_id, name, allowed_tools, allowed_data,
                    side_effects, confirm_required=True, payload=None):
    """Build a manifest dict (validated by the caller on store).
    `payload` is the exact payload the job fires with (the scheduler
    job carries the same dict) — it is what run_now replays, so a
    job's run can be reproduced without reaching into the
    scheduler's pickled job state."""
    manifest = {
        "job_id": job_id,
        "name": name,
        "allowed_tools": list(allowed_tools),
        "allowed_data": list(allowed_data),
        "side_effects": list(side_effects),
        "confirm_required": bool(confirm_required),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "created_by": "scheduled_agents",
    }
    if payload is not None:
        manifest["payload"] = payload
    return manifest


def digest_manifest():
    """The morning digest's manifest: it may read the activity log
    and the upcoming calendar, speak the digest, and store the digest
    record — and call exactly one funnel tool (speak_text). Anything
    else is outside it and is refused."""
    return create_manifest(
        DIGEST_JOB_ID, DIGEST_JOB_NAME,
        allowed_tools=["speak_text"],
        allowed_data=["activity_log", "calendar_upcoming", "digest_store"],
        side_effects=["speak_digest", "store_digest"],
        confirm_required=True,
        payload={"job_id": DIGEST_JOB_ID, "kind": "digest"},
    )


def load_manifests():
    """(manifests dict, error-or-None). A missing store is empty, not
    an error. A corrupt store is an error — and a corrupt store means
    no manifest can be trusted, so callers treat it as 'no manifest'
    (fail-closed) and surface the error."""
    path = Path(manifests_path())
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, "manifests store unreadable: %s" % exc
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return {}, "manifests store corrupt (not JSON): %s" % exc
    if not isinstance(data, dict):
        return {}, "manifests store corrupt (not an object)"
    return data, None


def _save_manifests(manifests):
    """Write the manifests store atomically and re-read it to prove
    the write (A1-store). Returns (ok, reason)."""
    path = Path(manifests_path())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifests, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        return False, "manifests store unwritable: %s" % exc
    back, err = load_manifests()
    if err is not None or back != manifests:
        return False, "manifests store write not verified on re-read"
    return True, ""


def store_manifest(manifest, known_tools=None):
    """Validate and store one manifest. Returns (ok, reason)."""
    ok, reason = validate_manifest(manifest, known_tools=known_tools)
    if not ok:
        return False, reason
    manifests, err = load_manifests()
    if err is not None:
        return False, err
    manifests[manifest["job_id"]] = manifest
    return _save_manifests(manifests)


def get_manifest(job_id):
    """(manifest-or-None, error-or-None)."""
    manifests, err = load_manifests()
    if err is not None:
        return None, err
    return manifests.get(job_id), None


def delete_manifest(job_id):
    """Remove one manifest. Returns (ok, reason)."""
    manifests, err = load_manifests()
    if err is not None:
        return False, err
    if job_id not in manifests:
        return False, "no manifest for job %r" % job_id
    del manifests[job_id]
    return _save_manifests(manifests)


# ---------------------------------------------------------------------------
# Summariser provider seam
# ---------------------------------------------------------------------------

_SUMMARISER_PROVIDER = None


def set_summariser_provider(fn):
    """Register a zero-arg callable returning a prompt->text
    summariser (or None). The app registers the active brain's
    make_summariser() here; tests register a fake. No provider
    means no LLM — the digest falls back to the extractive list
    and says so."""
    global _SUMMARISER_PROVIDER
    _SUMMARISER_PROVIDER = fn
    return {"ok": True}


def resolve_summariser():
    """The current summariser, or None (no LLM available). Never raises."""
    if _SUMMARISER_PROVIDER is None:
        return None
    try:
        return _SUMMARISER_PROVIDER()
    except Exception:  # noqa: BLE001 — a dead provider is no provider
        return None

# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def _record_run(kind, data):
    """Append one scheduled-agent event to the hash-chained ledger.
    Returns the event id, or "" when the ledger is unavailable.
    Never raises — a failed record is a failed record, not a crash."""
    try:
        return _ledger.append(kind, data)
    except Exception:  # noqa: BLE001 — ledger failures never crash a run
        return ""


# ---------------------------------------------------------------------------
# Morning digest — collect
# ---------------------------------------------------------------------------

_ACTIVITY_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}) \| ([^|]+) \| (.+)$")


def _parse_activity_line(line):
    """(timestamp, event, outcome) for a well-formed activity line,
    else None (free-form lines are skipped, not forced into a shape)."""
    match = _ACTIVITY_LINE_RE.match(line.strip())
    if not match:
        return None
    try:
        when = datetime.datetime.fromisoformat(match.group(1))
    except ValueError:
        return None
    return when, match.group(2).strip(), match.group(3).strip()


def _read_activity(path, now, window_hours=DIGEST_WINDOW_HOURS):
    """Activity entries in the last window_hours before now.
    A missing log is an empty window, not an error — the digest is
    allowed to say nothing happened. A corrupt log (unreadable file)
    is reported honestly in the returned dict."""
    entries = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return {"entries": [], "activity_counts": {},
                "activity_available": False,
                "activity_reason": "activity log not found"}
    except OSError as exc:
        return {"entries": [], "activity_counts": {},
                "activity_available": False,
                "activity_reason": "activity log unreadable: %s" % exc}
    window_start = now - datetime.timedelta(hours=window_hours)
    for line in lines:
        parsed = _parse_activity_line(line)
        if parsed is None:
            continue
        when, event, outcome = parsed
        if window_start <= when <= now:
            entries.append({"at": when.isoformat(timespec="seconds"),
                            "event": event, "outcome": outcome})
    counts = {}
    for entry in entries:
        counts[entry["event"]] = counts.get(entry["event"], 0) + 1
    return {"entries": entries, "activity_counts": counts,
            "activity_available": True, "activity_reason": ""}


def _read_calendar(calendar_fn, within_hours=DIGEST_WINDOW_HOURS,
                   limit=DIGEST_CALENDAR_LIMIT):
    """Upcoming calendar events. calendar_fn, when given, is a
    zero-arg callable returning the next_events list; when None the
    real Google path is used (lazy import — google_services is
    heavy). Any failure is 'calendar unavailable', not a crash."""
    if calendar_fn is None:
        try:
            from . import google_services
            events = google_services.next_events(
                within_hours=within_hours, limit=limit)
        except Exception as exc:  # noqa: BLE001 — Google down is unavailable
            return [], False, "calendar unavailable: %s" % exc
    else:
        try:
            events = calendar_fn()
        except Exception as exc:  # noqa: BLE001 — a dead fake is unavailable
            return [], False, "calendar unavailable: %s" % exc
    if not isinstance(events, list):
        return [], False, "calendar unavailable: unexpected result"
    return events, True, ""


def collect_digest_inputs(now=None, activity=None, calendar_fn=None):
    """Collect the digest's inputs: the last 24h of activity + the
    next 24h of calendar. `now` and `activity` are injectable for
    tests (activity is a path; None means the real log). Returns a
    plain dict the prompt builder and the self-eval both read."""
    now = now or datetime.datetime.now()
    path = activity if activity is not None else activity_path()
    activity_data = _read_activity(path, now)
    events, calendar_available, calendar_reason = _read_calendar(calendar_fn)
    # RET-1: the forgiving daily count rides the same input path as
    # activity/calendar (B-203) — a dict, never a parallel briefing.
    # A dead count reads as unavailable, never as a crash.
    try:
        from . import daily_count as _dc

        daily = _dc.daily_count_item(now=now, activity_path=path)
    except Exception:  # noqa: BLE001 — the count is an input, never a blocker
        daily = {"available": False, "this_week": 0, "last_week": 0,
                 "week_start": "", "display": ""}
    return {
        "now": now.isoformat(timespec="seconds"),
        "window_hours": DIGEST_WINDOW_HOURS,
        "activity": activity_data["entries"][-DIGEST_ACTIVITY_KEEP:],
        "activity_counts": activity_data["activity_counts"],
        "activity_available": activity_data["activity_available"],
        "activity_reason": activity_data["activity_reason"],
        "events": events,
        "calendar_available": calendar_available,
        "calendar_reason": calendar_reason,
        "daily_count": daily,
    }


# ---------------------------------------------------------------------------
# Morning digest — summarise
# ---------------------------------------------------------------------------

def _headline_items(items):
    """The items the summary must cover: the most frequent activity
    event names (up to 5) + the upcoming event titles (up to 5)."""
    headlines = []
    counts = items.get("activity_counts", {}) or {}
    for event in sorted(counts, key=lambda e: (-counts[e], e))[:5]:
        headlines.append(event)
    for event in (items.get("events") or [])[:5]:
        title = event.get("title") if isinstance(event, dict) else None
        if title:
            headlines.append(title)
    return headlines


def _build_digest_prompt(items, tight=False):
    """The LLM prompt. `tight` is the regenerate pass: a lower word
    cap and the headline items named explicitly."""
    lines = []
    if tight:
        lines.append(
            "Write a morning digest in at most %d words. Mention each of "
            "these items by name: %s." % (
                DIGEST_REGEN_WORD_CAP, ", ".join(_headline_items(items))))
    else:
        lines.append(
            "Write a morning digest in at most %d words: what happened in "
            "the last 24 hours (activity below) and what is coming up "
            "today (events below). Plain sentences, no greeting fluff."
            % DIGEST_WORD_CAP)
    if items.get("activity_available") and items.get("activity_counts"):
        lines.append("Activity in the last 24h (event: count):")
        for event, count in sorted(items["activity_counts"].items(),
                                   key=lambda kv: (-kv[1], kv[0])):
            lines.append("  %s: %d" % (event, count))
        for entry in items.get("activity", [])[-5:]:
            lines.append("  %s %s %s" % (entry["at"], entry["event"],
                                         entry["outcome"]))
    elif items.get("activity_available"):
        lines.append("No activity in the last 24h.")
    else:
        lines.append("Activity log unavailable: %s"
                     % items.get("activity_reason", "unknown"))
    if items.get("calendar_available") and items.get("events"):
        lines.append("Coming up in the next 24h:")
        for event in items["events"]:
            title = event.get("title", "?")
            start = event.get("start", "?")
            lines.append("  %s at %s" % (title, start))
    elif items.get("calendar_available"):
        lines.append("Nothing on the calendar in the next 24h.")
    else:
        lines.append("Calendar unavailable: %s"
                     % items.get("calendar_reason", "unknown"))
    return "\n".join(lines)


def _significant_words(text):
    """Words that prove a summary mentioned an item: lowercase,
    length >= 4, not a stopword."""
    return [w for w in re.split(r"[^a-z0-9]+", text.lower())
            if len(w) >= 4 and w not in _TITLE_STOPWORDS]


def _self_eval(summary, items):
    """Deterministic self-evaluation: word count + coverage of the
    headline items (fraction whose significant words appear in the
    summary). Weak when over the cap or under the coverage floor.
    Returns a plain dict — the numbers, not a judgement word."""
    words = len(summary.split())
    headlines = _headline_items(items)
    if not headlines:
        coverage = 1.0
    else:
        covered = 0
        summary_lower = summary.lower()
        for headline in headlines:
            sig = _significant_words(headline)
            if sig and any(word in summary_lower for word in sig):
                covered += 1
        coverage = covered / len(headlines)
    weak = words > DIGEST_WORD_CAP or coverage < DIGEST_MIN_COVERAGE
    return {"words": words, "coverage": round(coverage, 2),
            "headlines": len(headlines), "weak": bool(weak)}


def _extractive_digest(items):
    """The no-LLM fallback: a deterministic list of what happened
    and what is coming up, labelled as extractive in the text so it
    can never be mistaken for an LLM summary."""
    lines = []
    if items.get("activity_counts"):
        top = sorted(items["activity_counts"].items(),
                     key=lambda kv: (-kv[1], kv[0]))[:3]
        lines.append("In the last 24 hours: %s." % ", ".join(
            "%s %d times" % (event, count) for event, count in top))
    else:
        lines.append("Nothing recorded in the last 24 hours.")
    if items.get("events"):
        lines.append("Coming up: %s." % ", ".join(
            "%s at %s" % (e.get("title", "?"), e.get("start", "?"))
            for e in items["events"][:5]))
    else:
        lines.append("Nothing on the calendar in the next 24 hours.")
    lines.append("(Extractive list — no LLM summary was available.)")
    return " ".join(lines)


def _call_summariser(summariser, prompt):
    """Call the summariser and normalise the result: a non-empty
    string, or None (empty / wrong type / raised = no summary)."""
    try:
        text = summariser(prompt)
    except Exception:  # noqa: BLE001 — a dead LLM is no LLM
        return None
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text or None

# ---------------------------------------------------------------------------
# Morning digest — run, store, speak
# ---------------------------------------------------------------------------

def _store_digest(record):
    """Append one digest record to digests.jsonl and read the last
    line back to prove the write (A1-store). Returns (ok, reason)."""
    path = Path(digests_path())
    line = json.dumps(record, sort_keys=True)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        return False, "digest store unwritable: %s" % exc
    last = load_last_digest()
    if last is None or last.get("id") != record.get("id"):
        return False, "digest store write not verified on re-read"
    return True, ""


def load_last_digest():
    """The most recent stored digest record, or None (no digest yet
    or the store is unreadable — callers say so honestly)."""
    path = Path(digests_path())
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("id"):
            return record
    return None


def _interpret_speak_result(result):
    """Turn a funnel execute() return (or a raw speak return) into a
    plain {"ok": bool, "reason": str} spoken verdict. Fail-closed:
    only a verified Outcome (or the verified message wording) counts
    as spoken — an unverified or failed outcome is 'not spoken'."""
    if isinstance(result, dict):
        if result.get("status") != "success":
            reason = result.get("message") or result.get("kind") or "speak refused"
            return {"ok": False, "reason": str(reason)}
        return _interpret_speak_result(result.get("result"))
    verified = getattr(result, "verified", None)
    if verified is not None:
        if verified:
            return {"ok": True, "reason": ""}
        message = getattr(result, "message", "") or "speak not verified"
        return {"ok": False, "reason": str(message)}
    if isinstance(result, str):
        if "Reading aloud now" in result:
            return {"ok": True, "reason": ""}
        return {"ok": False, "reason": result or "speak not verified"}
    if result is None:
        return {"ok": False, "reason": "speak returned nothing"}
    return {"ok": False, "reason": "speak returned an unverified result"}


def _default_speak_fn(registry):
    """The digest's speak step, routed through the funnel so the
    manifest check is real: the agent checks speak_text is allowed,
    then the funnel runs it (confirm gate, audit). Returns a
    speak_fn(text) -> {"ok": bool, "reason": str}."""
    def _speak(text):
        try:
            result = registry.execute("speak_text", {"text": text})
        except Exception as exc:  # noqa: BLE001 — a dead funnel is not spoken
            return {"ok": False, "reason": "speak_text call failed: %s" % exc}
        return _interpret_speak_result(result)
    return _speak


def run_digest(now=None, summariser=None, speak_fn=None, registry=None,
               activity=None, calendar_fn=None, speak=True):
    """Run one morning digest: collect -> summarise (self-eval,
    regenerate once if weak, extractive fallback) -> speak (when
    `speak` is True) -> store. Returns {"ok": bool, "digest": record}
    or {"ok": False, "reason": ...} when the record could not be
    stored (a digest that cannot prove itself is not reported as
    done). The run is NOT recorded in the ledger here — the caller
    (OperativeAgent.run_job) records the run event once, with the
    outcome."""
    now = now or datetime.datetime.now()
    items = collect_digest_inputs(now=now, activity=activity,
                                  calendar_fn=calendar_fn)
    summ = summariser if summariser is not None else resolve_summariser()
    text = None
    kind = "extractive"
    evaluation = None
    if summ is not None:
        text = _call_summariser(summ, _build_digest_prompt(items))
        if text is not None:
            evaluation = _self_eval(text, items)
            if evaluation["weak"]:
                retry = _call_summariser(
                    summ, _build_digest_prompt(items, tight=True))
                if retry is not None:
                    evaluation = _self_eval(retry, items)
                    text, kind = retry, "llm_regenerated"
                    if evaluation["weak"]:
                        text, kind = None, "extractive"
                else:
                    text, kind = None, "extractive"
            else:
                kind = "llm"
    if text is None:
        text = _extractive_digest(items)
        kind = "extractive"
        evaluation = _self_eval(text, items)
    # RET-1: the daily count renders as a fixed suffix on the final
    # text (spoken + stored), appended AFTER the self-eval — the eval
    # measures the summary (word cap + headline coverage), while the
    # suffix is a deterministic fact line, not summary prose. The
    # record's words count covers the full spoken text.
    daily = items.get("daily_count") or {}
    if daily.get("available") and daily.get("display"):
        text = "%s %s" % (text, daily["display"])
    spoken = {"ok": False, "reason": "speaking not allowed by this run"}
    if speak:
        if speak_fn is None:
            if registry is None:
                try:
                    from .funnel.tools_registry import tools as _tools
                    registry = _tools
                except Exception as exc:  # noqa: BLE001 — no registry, no speak
                    spoken = {"ok": False,
                              "reason": "speak unavailable: registry unavailable (%s)" % exc}
                    registry = None
            if registry is not None:
                speak_fn = _default_speak_fn(registry)
        if speak_fn is not None:
            try:
                spoken = speak_fn(text)
            except Exception as exc:  # noqa: BLE001 — a dead speaker is not spoken
                spoken = {"ok": False, "reason": "speak failed: %s" % exc}
            if not isinstance(spoken, dict) or "ok" not in spoken:
                spoken = {"ok": False,
                          "reason": "speaker returned an unusable result"}
    record = {
        "id": uuid.uuid4().hex,
        "at": now.isoformat(timespec="seconds"),
        "kind": kind,
        "text": text,
        "words": len(text.split()),
        "self_eval": evaluation,
        "spoken": spoken,
        "items": {
            "activity_events": len(items.get("activity", [])),
            "upcoming_events": len(items.get("events", [])),
            "activity_available": items.get("activity_available"),
            "calendar_available": items.get("calendar_available"),
            "daily_count": daily,
        },
    }
    ok, reason = _store_digest(record)
    if not ok:
        return {"ok": False, "reason": reason}
    return {"ok": True, "digest": record}

# ---------------------------------------------------------------------------
# OperativeAgent
# ---------------------------------------------------------------------------

def _refuse_run(job_id, reason, payload=None):
    """The refusal record: a refused run is a recorded run, not a
    silent skip. Returns the refusal dict."""
    record = {"ok": False, "reason": reason}
    _record_run("scheduled_agent_run", {
        "job_id": job_id, "kind": (payload or {}).get("kind"),
        "ok": False, "reason": reason,
    })
    return record


def run_job(payload, registry=None, summariser=None, speak_fn=None,
            activity=None, calendar_fn=None):
    """Run one scheduled job under its manifest. `payload` is the
    scheduler's payload ({"job_id", "kind", ...}); the manifest is
    looked up by job_id. Every outcome — done, refused, failed —
    is recorded in the ledger as one scheduled_agent_run event
    (receipts only: no digest text, no step arguments in the
    ledger). Never raises.

    Enforcement, in order:
    1. No manifest -> refused (fail-closed). A corrupt manifests
       store -> refused.
    2. An invalid manifest (unknown tool / data / side effect) ->
       refused.
    3. Digest kind: the manifest must allow the two reads
       (activity_log, calendar_upcoming) and the store side effect
       (store_digest); speaking happens only when the manifest
       allows speak_digest AND the speak_text tool.
    4. Daily-nudge kind (RET-1): the manifest must allow the three
       pinned reads (activity_log, calendar_upcoming, reminders,
       routines) and both nudge side effects (speak_nudge,
       store_nudge); speaking happens only when the manifest
       allows speak_nudge AND the speak_text tool — otherwise the
       run is refused, never silently unspoken.
    5. Steps kind: "run_tool" must be in side_effects, and every
       step's tool must be in allowed_tools — a step outside the
       manifest refuses the WHOLE run and stops it (no later step
       runs). Each step runs through the funnel (registry.execute),
       so the funnel's own confirm gate still applies — a gated
       tool the funnel refuses is a refused step, and the run stops.
    """
    if not isinstance(payload, dict):
        return _refuse_run(None, "job payload must be a dict")
    job_id = payload.get("job_id")
    kind = payload.get("kind")
    if not isinstance(job_id, str) or not job_id:
        return _refuse_run(None, "job payload has no job_id")
    manifest, err = get_manifest(job_id)
    if err is not None:
        return _refuse_run(job_id, "manifests store unreadable: %s" % err,
                           payload)
    if manifest is None:
        return _refuse_run(
            job_id, "no manifest for job %r — refused, not run" % job_id,
            payload)
    if registry is None:
        try:
            from .funnel.tools_registry import tools as _tools
            registry = _tools
        except Exception as exc:  # noqa: BLE001 — no registry, no run
            return _refuse_run(
                job_id, "registry unavailable: %s" % exc, payload)
    known_tools = {d["name"] for d in registry.get_declarations()}
    ok, reason = validate_manifest(manifest, known_tools=known_tools)
    if not ok:
        return _refuse_run(job_id, "invalid manifest: %s" % reason, payload)
    if kind == "digest":
        return _run_digest_job(job_id, manifest, registry,
                               summariser=summariser, speak_fn=speak_fn,
                               activity=activity, calendar_fn=calendar_fn)
    if kind == "daily_nudge":
        return _run_nudge_job(job_id, manifest, registry,
                              speak_fn=speak_fn, activity=activity,
                              calendar_fn=calendar_fn)
    if kind == "steps":
        return _run_steps_job(job_id, manifest, payload, registry)
    return _refuse_run(job_id, "unknown job kind %r" % (kind,), payload)


def _run_digest_job(job_id, manifest, registry, summariser=None,
                    speak_fn=None, activity=None, calendar_fn=None):
    """The digest run: collect + summarise + speak + store, under
    the digest manifest. Speaking is allowed only when the manifest
    allows both the speak_digest side effect and the speak_text
    tool; without either, the digest still runs and stores, and the
    record says it was not spoken (partial delivery, stated)."""
    allowed_data = manifest.get("allowed_data", [])
    for source in ("activity_log", "calendar_upcoming"):
        if source not in allowed_data:
            return _refuse_run(
                job_id, "manifest does not allow reading %s" % source,
                {"job_id": job_id, "kind": "digest"})
    if "store_digest" not in manifest.get("side_effects", []):
        return _refuse_run(
            job_id, "manifest does not allow storing the digest",
            {"job_id": job_id, "kind": "digest"})
    speak = ("speak_digest" in manifest.get("side_effects", [])
             and "speak_text" in manifest.get("allowed_tools", []))
    result = run_digest(summariser=summariser, speak_fn=speak_fn,
                        registry=registry, activity=activity,
                        calendar_fn=calendar_fn, speak=speak)
    if not result.get("ok"):
        _record_run("scheduled_agent_run", {
            "job_id": job_id, "kind": "digest", "ok": False,
            "reason": result.get("reason", "digest failed"),
        })
        return {"ok": False, "reason": result.get("reason", "digest failed")}
    digest = result["digest"]
    _record_run("scheduled_agent_run", {
        "job_id": job_id, "kind": "digest", "ok": True,
        "digest_id": digest["id"], "digest_kind": digest["kind"],
        "spoken": digest["spoken"].get("ok", False),
        "words": digest["words"],
    })
    return {"ok": True, "job_id": job_id, "kind": "digest",
            "digest_id": digest["id"], "digest_kind": digest["kind"],
            "spoken": digest["spoken"]}


def _run_nudge_job(job_id, manifest, registry, speak_fn=None,
                   activity=None, calendar_fn=None):
    """The daily-nudge run (RET-1): fire once under the nudge
    manifest. Speaking is allowed only when the manifest allows
    both the speak_nudge side effect and the speak_text tool;
    without either, the run is refused — a nudge that cannot speak
    is not a nudge, and the day must not be consumed by a
    misconfigured job. Every outcome is recorded in the ledger
    as one scheduled_agent_run event."""
    allowed_data = manifest.get("allowed_data", [])
    for source in ("activity_log", "calendar_upcoming",
                   "reminders", "routines"):
        if source not in allowed_data:
            return _refuse_run(
                job_id, "manifest does not allow reading %s" % source,
                {"job_id": job_id, "kind": "daily_nudge"})
    for effect in ("speak_nudge", "store_nudge"):
        if effect not in manifest.get("side_effects", []):
            return _refuse_run(
                job_id, "manifest does not allow %s" % effect,
                {"job_id": job_id, "kind": "daily_nudge"})
    if "speak_text" not in manifest.get("allowed_tools", []):
        return _refuse_run(
            job_id, "manifest does not allow the speak_text tool",
            {"job_id": job_id, "kind": "daily_nudge"})
    from . import daily_count as _dc

    result = _dc.fire_nudge(speak_fn=speak_fn, registry=registry,
                            activity_path=activity,
                            calendar_fn=calendar_fn)
    _record_run("scheduled_agent_run", {
        "job_id": job_id, "kind": "daily_nudge",
        "ok": bool(result.get("ok")),
        "outcome": result.get("outcome"),
        "reason": result.get("reason", ""),
    })
    return {"ok": bool(result.get("ok")), "job_id": job_id,
            "kind": "daily_nudge",
            "outcome": result.get("outcome"),
            "reason": result.get("reason", "")}


def _run_steps_job(job_id, manifest, payload, registry):
    """A steps job: run each step through the funnel, in order,
    under the manifest. A step outside the manifest, or a step the
    funnel refuses, stops the run — no later step runs."""
    if "run_tool" not in manifest.get("side_effects", []):
        return _refuse_run(
            job_id, "manifest does not allow running tools", payload)
    allowed = set(manifest.get("allowed_tools", []))
    steps = payload.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return _refuse_run(job_id, "steps job has no steps", payload)
    outcomes = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
            _record_run("scheduled_agent_run", {
                "job_id": job_id, "kind": "steps", "ok": False,
                "reason": "step %d is not a tool call" % index,
                "step": index,
            })
            # RET-2: the halt carries the outcomes so far, where it
            # stopped, and the halting step — the run report needs
            # all three, and no earlier caller pinned the old shape
            # (only "steps_run" is pinned, by
            # test_scheduled_agents.py).
            return {"ok": False, "job_id": job_id, "kind": "steps",
                    "reason": "step %d is not a tool call" % index,
                    "steps_run": index, "steps": outcomes,
                    "halted_at": index,
                    "halted_step": {"tool": None, "status": "failed",
                                    "kind": "malformed",
                                    "reason": "step %d is not a tool call"
                                              % index,
                                    "duration_ms": None}}
        tool = step["tool"]
        if tool not in allowed:
            _record_run("scheduled_agent_run", {
                "job_id": job_id, "kind": "steps", "ok": False,
                "reason": "tool %r is outside the manifest" % tool,
                "step": index, "tool": tool,
            })
            return {"ok": False, "job_id": job_id, "kind": "steps",
                    "reason": "tool %r is outside the manifest — refused, "
                              "not run" % tool,
                    "steps_run": index, "steps": outcomes,
                    "halted_at": index,
                    "halted_step": {"tool": tool, "status": "refused",
                                    "kind": "refused",
                                    "reason": "tool %r is outside the "
                                              "manifest — refused, not run"
                                              % tool,
                                    "duration_ms": None}}
        t0 = time.monotonic()
        try:
            result = registry.execute(tool, step.get("args", {}))
        except Exception as exc:  # noqa: BLE001 — a dead funnel is a failed step
            result = {"status": "error", "kind": "exception",
                      "message": str(exc)}
        duration_ms = int(round((time.monotonic() - t0) * 1000))
        if not isinstance(result, dict) or result.get("status") != "success":
            reason = ""
            if isinstance(result, dict):
                reason = str(result.get("message") or result.get("kind")
                             or "funnel refused the step")
            _record_run("scheduled_agent_run", {
                "job_id": job_id, "kind": "steps", "ok": False,
                "reason": "step %d (%s) refused: %s" % (index, tool, reason),
                "step": index, "tool": tool,
            })
            return {"ok": False, "job_id": job_id, "kind": "steps",
                    "reason": "step %d (%s) refused: %s" % (index, tool, reason),
                    "steps_run": index, "steps": outcomes,
                    "halted_at": index,
                    "halted_step": {
                        "tool": tool,
                        # The step's own reason is the funnel's
                        # words, bare — the job-level "reason" above
                        # keeps the "step N (tool) refused: ..." form
                        # the suite pins.
                        "status": (result.get("status")
                                   if isinstance(result, dict) else "error"),
                        "kind": (result.get("kind")
                                 if isinstance(result, dict) else "unknown"),
                        "reason": reason,
                        "duration_ms": duration_ms}}
        outcomes.append({"tool": tool, "status": "success",
                         "duration_ms": duration_ms})
    _record_run("scheduled_agent_run", {
        "job_id": job_id, "kind": "steps", "ok": True,
        "steps": len(outcomes),
    })
    return {"ok": True, "job_id": job_id, "kind": "steps",
            "steps": outcomes, "steps_run": len(outcomes)}


def operative_agent_handler(payload):
    """The scheduler handler (registered under HANDLER_NAME): the
    function the engine calls when a job fires. Never raises — an
    unexpected failure is recorded and returned as a dict, so the
    engine's missed log does not mistake a policy refusal for an
    engine error."""
    try:
        return run_job(payload)
    except Exception as exc:  # noqa: BLE001 — the handler never raises
        job_id = payload.get("job_id") if isinstance(payload, dict) else None
        _record_run("scheduled_agent_run", {
            "job_id": job_id, "kind": None, "ok": False,
            "reason": "agent error: %s" % exc,
        })
        return {"ok": False, "reason": "agent error: %s" % exc}


def ensure_engine_running():
    """Start the engine (idempotent) so persisted jobs fire after a
    restart, with the operative-agent handler registered. This is
    the app-startup hook: call it once at startup. Returns the
    engine's start result."""
    try:
        from .desktop import job_scheduler
    except Exception as exc:  # noqa: BLE001 — no scheduler module, no engine
        return {"ok": False, "reason": "engine_unavailable",
                "detail": "job_scheduler unavailable: %s" % exc}
    try:
        job_scheduler.register_handler(HANDLER_NAME, operative_agent_handler)
    except Exception as exc:  # noqa: BLE001 — registration failure is honest
        return {"ok": False, "reason": "handler registration failed",
                "detail": str(exc)}
    return job_scheduler.start()


# ---------------------------------------------------------------------------
# Tool: scheduled_agents
# ---------------------------------------------------------------------------

def _scheduler():
    """The scheduler module (lazy import — the engine is heavy)."""
    from .desktop import job_scheduler
    return job_scheduler


def _find_job(job_scheduler, job_id):
    """The listed job with this id, or None (plus the list error)."""
    listed = job_scheduler.list_jobs()
    if not isinstance(listed, dict) or not listed.get("ok"):
        return None, listed
    for job in listed.get("jobs", []):
        if isinstance(job, dict) and job.get("job_id") == job_id:
            return job, listed
    return None, listed


def _collect_reminders():
    """(reminders, error-or-None): the ProactiveScheduler's reminders,
    each tagged source="reminder" + address=label. Never raises — a
    failure reads as an empty list plus the error, so one broken
    source never hides the other two (GAPS-26, S-V6)."""
    try:
        from .scheduler import scheduler as _sched
        out = []
        for r in _sched.get_all_reminders():
            entry = dict(r)
            entry["source"] = "reminder"
            entry["address"] = r.get("label")
            out.append(entry)
        return out, None
    except Exception as exc:  # noqa: BLE001 — a broken source reports, never raises
        return [], "reminders unreadable: %s" % exc


def _collect_routines():
    """(routines, error-or-None): the scheduled routines from
    data/routines.json with their next run (planner.next_run) and
    last run (routine_state.json), each tagged source="routine" +
    address=name. Routines with no schedule are listed too (their
    next_run is None) — an unscheduled routine is still a job the
    user owns. Times are local wall time, the same rendering the
    reminders list uses. Never raises (GAPS-26, S-V6)."""
    try:
        from .brain import planner as _planner
        from .brain import routine_scheduler as _rs
        import datetime as _dt
        state = _rs._load_state()
        now = _dt.datetime.now()
        out = []
        for routine in _planner.get_routines():
            if not isinstance(routine, dict):
                continue
            name = routine.get("name")
            last_raw = state.get(name) if isinstance(name, str) else None
            last = _rs._parse_iso(last_raw) if last_raw else None
            try:
                nxt = _planner.next_run(routine, now, last)
            except Exception:  # noqa: BLE001 — an unparseable schedule reads as None
                nxt = None
            out.append({
                "name": name,
                "schedule": routine.get("schedule"),
                "next_run": (nxt.strftime("%Y-%m-%d %H:%M")
                             if nxt is not None else None),
                "last_run": (last.strftime("%Y-%m-%d %H:%M")
                             if last is not None else None),
                "source": "routine",
                "address": name,
            })
        return out, None
    except Exception as exc:  # noqa: BLE001 — a broken source reports, never raises
        return [], "routines unreadable: %s" % exc


# ---------------------------------------------------------------------------
# RET-2: named saved routines — save_routine / run_routine actions
# ---------------------------------------------------------------------------


def _routine_job_id(name):
    """The transient manifest's job id for a routine run."""
    return "routine:%s" % name


def render_routine_steps_for_confirmation(steps):
    """Render a routine's steps for the save confirmation: one line
    per step, tool + args, redacted by the same H22 rules the
    confirm popup uses (confirm_gate_scrub.summarize_args — a
    secret-looking value is masked wherever it appears, and a
    sensitive exact key is masked too). `steps` may be the parsed
    list or the raw JSON string the caller passed; an unparseable
    value renders as-is — the save handler refuses it afterwards,
    but the human still sees what was asked. A name-only confirm
    is not a confirmation: the caller must show this list."""
    from .confirm_gate_scrub import summarize_args
    parsed = steps
    if isinstance(steps, str):
        try:
            parsed = json.loads(steps)
        except Exception:  # noqa: BLE001 — render the raw value
            return "steps: %s" % steps
    if not isinstance(parsed, list):
        return "steps: %r" % (steps,)
    lines = []
    for index, step in enumerate(parsed):
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
            lines.append("%d. (not a tool call: %r)" % (index + 1, step))
            continue
        args = step.get("args") or {}
        if not isinstance(args, dict):
            lines.append("%d. %s (args not an object: %r)"
                         % (index + 1, step["tool"], args))
            continue
        lines.append("%d. %s %s" % (index + 1, step["tool"],
                                    summarize_args(args, limit=1000)))
    return "\n".join(lines)


def _validate_routine_steps(steps, registry):
    """Validate every step against the registry: the tool must
    exist and its args must validate against its schema. Returns
    (ok, reason) — the save and the run both refuse whole on the
    first dead step, with the step named. `registry` may be a fake
    with validate_args (the suite/prover pass one); None means the
    live registry."""
    if registry is None:
        from .funnel.tools_registry import tools as _tools
        registry = _tools
    for index, step in enumerate(steps):
        tool = step.get("tool")
        args = step.get("args") or {}
        if not isinstance(args, dict):
            return False, ("step %d (%s): args must be an object"
                           % (index + 1, tool))
        try:
            err = registry.validate_args(tool, args)
        except Exception as exc:  # noqa: BLE001 — no validation, no trust
            return False, ("step %d (%s): args could not be validated: %s"
                           % (index + 1, tool, exc))
        if err is not None:
            return False, "step %d (%s): %s" % (index + 1, tool, err)
    return True, None


def _save_routine_action(name, steps, schedule, registry=None):
    """save_routine's work, separated from the handler so the suite
    and prover can drive it with a fake registry. Validates the
    steps against the registry, then saves through
    planner_routines.save_routine — which re-parses the steps and
    the schedule, refuses an unparseable schedule whole, and
    preserves a stored schedule when none is given (the
    preserve-schedule rule: a re-save without a schedule must not
    silently unschedule). Returns the tool-shaped dict; "replaces"
    is True when the name already existed (the planner's verb
    says "updated")."""
    from .brain import planner_routines as _proutines
    clean = str(name or "").strip()
    if not clean:
        return {"ok": False, "reason": "save_routine needs a name"}
    parsed, error = _proutines._parse_actions(steps)
    if error:
        return {"ok": False,
                "reason": "could not save routine %r: %s" % (clean, error)}
    ok, reason = _validate_routine_steps(parsed, registry)
    if not ok:
        return {"ok": False,
                "reason": "could not save routine %r: %s" % (clean, reason)}
    result = _proutines.save_routine(clean, parsed, schedule)
    if result.startswith(("Could not save", "Cannot save")):
        return {"ok": False, "reason": result}
    replaces = " updated " in result
    record = next((r for r in _proutines.get_routines()
                   if r.get("name") == clean), {})
    return {"ok": True, "routine": clean, "steps": len(parsed),
            "schedule": record.get("schedule_text"),
            "replaces": replaces, "message": result}


def _routine_report(name, steps, result, elapsed_ms, manifest_note):
    """Build the run report: one outcome per step (done / refused /
    failed / not_run), elapsed time, step count, and a speakable
    summary. The outcome mapping, from the funnel's result: status
    "success" is done; a halted step whose funnel kind is a refusal
    (refused / not_confirmed / confirm_refused) is refused; any
    other halt is failed, carrying the funnel's reason verbatim.
    Steps after the halt never ran — they are not_run, never
    silently counted as done. The durations are execution times
    only; v1 has no user-supplied baseline, so no comparison
    against doing it by hand is ever made."""
    ran = result.get("steps") or []
    halted_at = result.get("halted_at")
    halted = result.get("halted_step") or {}
    outcomes = []
    for index, step in enumerate(steps):
        tool = step.get("tool")
        if index < len(ran):
            record = ran[index]
            outcomes.append({"tool": tool, "outcome": "done",
                             "reason": "",
                             "duration_ms": record.get("duration_ms")})
        elif halted_at is not None and index == halted_at:
            kind = halted.get("kind") or ""
            status = halted.get("status") or ""
            refused = (status == "refused"
                       or kind in ("refused", "not_confirmed",
                                   "confirm_refused"))
            outcomes.append({"tool": tool,
                             "outcome": "refused" if refused else "failed",
                             "reason": halted.get("reason") or "",
                             "duration_ms": halted.get("duration_ms")})
        else:
            outcomes.append({
                "tool": tool, "outcome": "not_run",
                "reason": ("routine stopped at step %d" % (halted_at + 1))
                          if halted_at is not None else "",
                "duration_ms": None})
    seconds = elapsed_ms / 1000.0
    count = len(steps)
    plural = "" if count == 1 else "s"
    if result.get("ok"):
        summary = ("Routine '%s' ran %d step%s in %.1f seconds: all done."
                   % (name, count, plural, seconds))
    elif halted_at is not None:
        halted_outcome = outcomes[halted_at]
        remaining = count - halted_at - 1
        summary = ("Routine '%s' ran %d of %d steps in %.1f seconds, then "
                   "stopped at step %d (%s): %s — %s."
                   % (name, halted_at + 1, count, seconds, halted_at + 1,
                      halted_outcome["tool"], halted_outcome["outcome"],
                      halted_outcome["reason"]))
        if remaining:
            summary += (" The remaining %d step%s did not run."
                        % (remaining, "" if remaining == 1 else "s"))
    else:
        summary = ("Routine '%s' did not run: %s"
                   % (name, result.get("reason") or "refused"))
    report = {"ok": bool(result.get("ok")), "routine": name,
              "steps": outcomes, "steps_total": count,
              "steps_run": len(ran) + (1 if halted_at is not None else 0),
              "elapsed_ms": elapsed_ms, "halted_at": halted_at,
              "halt_reason": result.get("reason") if not result.get("ok") else None,
              "summary": summary}
    if manifest_note:
        report["manifest_note"] = manifest_note
    return report


def _run_routine_action(name, registry=None):
    """run_routine's work, separated from the handler so the suite
    and prover can drive it with a fake registry. The run action
    itself is free (dispatch); each gated STEP is confirmed at run
    time by the funnel — the creation-time confirmation covered
    the routine's SHAPE only, never a blanket pre-approval of
    future runs. At run start every step is re-validated against
    the live registry; a step whose tool no longer exists (or
    whose args no longer validate) refuses the run WHOLE, before
    anything runs. The run goes through run_job under a transient
    manifest whose allowed_tools is exactly the routine's steps;
    the manifest is deleted in a finally (an orphan from a crash
    is overwritten by the next run's upsert, and the args are
    already durably stored in data/routines.json)."""
    from .brain import planner_routines as _proutines
    clean = str(name or "").strip()
    if not clean:
        return {"ok": False, "reason": "run_routine needs a name"}
    routine = next((r for r in _proutines.get_routines()
                    if r.get("name") == clean), None)
    if routine is None:
        return {"ok": False,
                "reason": "no routine named %r — save one first" % clean}
    steps = routine.get("actions") or []
    if not steps:
        return {"ok": False,
                "reason": "routine %r has no steps" % clean}
    ok, reason = _validate_routine_steps(steps, registry)
    if not ok:
        return {"ok": False,
                "reason": ("routine %r cannot run: %s — the routine has "
                           "changed since it was saved; save it again to "
                           "fix it" % (clean, reason))}
    tools = []
    for step in steps:
        if step.get("tool") not in tools:
            tools.append(step.get("tool"))
    job_id = _routine_job_id(clean)
    payload = {"job_id": job_id, "kind": "steps", "steps": steps}
    manifest = create_manifest(
        job_id, "routine: %s" % clean, allowed_tools=tools,
        allowed_data=[], side_effects=["run_tool"],
        confirm_required=True, payload=payload)
    if registry is None:
        from .funnel.tools_registry import tools as _tools
        registry = _tools
    known = {d["name"] for d in registry.get_declarations()}
    stored, store_reason = store_manifest(manifest, known_tools=known)
    if not stored:
        return {"ok": False,
                "reason": "could not build the run manifest: %s"
                          % store_reason}
    t0 = time.monotonic()
    manifest_note = None
    try:
        result = run_job(payload, registry=registry)
    finally:
        deleted, delete_reason = delete_manifest(job_id)
        if not deleted:
            manifest_note = "manifest not removed: %s" % delete_reason
    elapsed_ms = int(round((time.monotonic() - t0) * 1000))
    return _routine_report(clean, steps, result, elapsed_ms, manifest_note)


def scheduled_agents(action, job_id="", timezone=DIGEST_TIMEZONE,
                     name="", steps=None, schedule=None):
    """The user-facing tool. Actions: list / status / setup_digest /
    cancel / run_now / digest / save_routine / run_routine.
    setup_digest, cancel, run_now and save_routine are
    confirm-gated (the args-aware branch in tools_gate.py); the
    other four are free reads, except run_routine, which is free
    dispatch — the run itself asks nothing, but every gated STEP
    still asks at run time through the funnel. list is the ONE
    listing for all three schedulers (agent jobs, reminders,
    routines — each entry tagged with its source and address);
    status reports misfires in detail (missed_jobs +
    missed_reminders), not just a count (GAPS-26, S-V6)."""
    if action not in _ACTIONS:
        return {"ok": False,
                "reason": "unknown action %r (actions: %s)"
                          % (action, ", ".join(_ACTIONS))}
    if action == "list":
        job_scheduler = _scheduler()
        listed = job_scheduler.list_jobs()
        if not isinstance(listed, dict) or not listed.get("ok"):
            return {"ok": False,
                    "reason": "could not list jobs: %s"
                              % (listed.get("reason") if isinstance(listed, dict)
                                 else "unexpected result")}
        manifests, err = load_manifests()
        jobs = []
        for job in listed.get("jobs", []):
            entry = dict(job)
            manifest = manifests.get(entry.get("job_id")) if err is None else None
            entry["manifest"] = manifest.get("name") if manifest else None
            entry["has_manifest"] = manifest is not None
            # GAPS-26 (S-V6): the unified listing — every entry
            # carries its source and its address, so the three
            # schedulers (agent jobs: job_id, reminders: label,
            # routines: name) read as one list, not three.
            entry["source"] = "agent_job"
            entry["address"] = entry.get("job_id")
            jobs.append(entry)
        reminders, reminders_error = _collect_reminders()
        routines, routines_error = _collect_routines()
        return {"ok": True, "jobs": jobs,
                "reminders": reminders,
                "routines": routines,
                "manifests": len(manifests) if err is None else 0,
                "manifests_error": err,
                "reminders_error": reminders_error,
                "routines_error": routines_error}
    if action == "status":
        job_scheduler = _scheduler()
        status = job_scheduler.engine_status()
        listed = job_scheduler.list_jobs()
        missed = job_scheduler.get_missed()
        missed_jobs = (missed.get("missed", [])
                       if isinstance(missed, dict) else [])
        # GAPS-26 (S-V6): misfire reporting is the DETAILS, not a
        # count — the missed jobs (persisted across restarts) and
        # the missed reminders (get_missed_events had no production
        # consumer before this) are both surfaced here.
        try:
            from .scheduler import scheduler as _sched
            missed_reminders = _sched.get_missed_events()
            missed_reminders_error = None
        except Exception as exc:  # noqa: BLE001 — a broken source reports
            missed_reminders = []
            missed_reminders_error = "missed reminders unreadable: %s" % exc
        manifests, err = load_manifests()
        last = load_last_digest()
        return {"ok": True,
                "engine": status,
                "jobs": (len(listed.get("jobs", []))
                         if isinstance(listed, dict) and listed.get("ok") else 0),
                "manifests": len(manifests) if err is None else 0,
                "manifests_error": err,
                "missed": len(missed_jobs),
                "missed_jobs": missed_jobs,
                "missed_reminders": missed_reminders,
                "missed_reminders_error": missed_reminders_error,
                "last_digest": ({
                    "id": last["id"], "at": last["at"],
                    "kind": last["kind"], "words": last["words"],
                    "spoken": last["spoken"].get("ok", False),
                } if last else None)}
    if action == "digest":
        last = load_last_digest()
        if last is None:
            return {"ok": False, "reason": "no digest stored yet"}
        return {"ok": True, "digest": last}
    if action == "setup_digest":
        try:
            ZoneInfo(timezone)
        except Exception:  # noqa: BLE001 — ZoneInfo raises on bad names
            return {"ok": False,
                    "reason": "unknown timezone %r" % timezone}
        job_scheduler = _scheduler()
        existing, listed = _find_job(job_scheduler, DIGEST_JOB_ID)
        if isinstance(listed, dict) and not listed.get("ok"):
            return {"ok": False,
                    "reason": "could not check existing jobs: %s"
                              % listed.get("reason", "unknown")}
        if existing is not None:
            return {"ok": False,
                    "reason": "morning digest is already set up "
                              "(next fire %s) — cancel it first"
                              % existing.get("next_fire")}
        manifest = digest_manifest()
        ok, reason = store_manifest(manifest)
        if not ok:
            return {"ok": False, "reason": "could not store manifest: %s" % reason}
        payload = {"job_id": DIGEST_JOB_ID, "kind": "digest"}
        scheduled = job_scheduler.schedule_cron(
            job_id=DIGEST_JOB_ID, handler=HANDLER_NAME, timezone=timezone,
            payload=payload,
            misfire_grace_time=DIGEST_MISFIRE_GRACE_SECONDS,
            name=DIGEST_JOB_NAME, hour=DIGEST_HOUR, minute=DIGEST_MINUTE)
        if not isinstance(scheduled, dict) or not scheduled.get("ok"):
            delete_manifest(DIGEST_JOB_ID)  # roll back: no orphan manifest
            return {"ok": False,
                    "reason": "could not schedule the digest: %s"
                              % (scheduled.get("reason")
                                 if isinstance(scheduled, dict) else "unexpected result")}
        started = ensure_engine_running()
        result = {"ok": True, "job_id": DIGEST_JOB_ID,
                  "timezone": timezone, "hour": DIGEST_HOUR,
                  "minute": DIGEST_MINUTE,
                  "next_fire": scheduled.get("next_fire"),
                  "engine_running": started.get("running", False)}
        if not started.get("ok"):
            result["note"] = ("digest is persisted, but the engine could "
                              "not start (%s) — it will fire when the engine "
                              "starts" % started.get("reason"))
        return result
    if action == "cancel":
        if not isinstance(job_id, str) or not job_id.strip():
            return {"ok": False, "reason": "cancel needs a job_id"}
        job_scheduler = _scheduler()
        cancelled = job_scheduler.cancel(job_id)
        if not isinstance(cancelled, dict) or not cancelled.get("ok"):
            return {"ok": False,
                    "reason": "could not cancel job %r: %s"
                              % (job_id, cancelled.get("reason")
                                 if isinstance(cancelled, dict) else "unexpected result")}
        ok, reason = delete_manifest(job_id)
        result = {"ok": True, "job_id": job_id,
                  "was_next": cancelled.get("next_run_time")}
        if not ok:
            result["manifest_note"] = "manifest not removed: %s" % reason
        return result
    if action == "run_now":
        if not isinstance(job_id, str) or not job_id.strip():
            return {"ok": False, "reason": "run_now needs a job_id"}
        job_scheduler = _scheduler()
        existing, listed = _find_job(job_scheduler, job_id)
        if isinstance(listed, dict) and not listed.get("ok"):
            return {"ok": False,
                    "reason": "could not check scheduled jobs: %s"
                              % listed.get("reason", "unknown")}
        if existing is None:
            return {"ok": False,
                    "reason": "no scheduled job %r — set it up first" % job_id}
        manifest, err = get_manifest(job_id)
        if err is not None:
            return {"ok": False, "reason": "manifests store unreadable: %s" % err}
        if manifest is None:
            return {"ok": False,
                    "reason": "no manifest for job %r — refused, not run" % job_id}
        payload = manifest.get("payload")
        if not isinstance(payload, dict):
            return {"ok": False,
                    "reason": "manifest for job %r has no payload" % job_id}
        return run_job(payload)
    if action == "save_routine":
        return _save_routine_action(name, steps, schedule)
    if action == "run_routine":
        return _run_routine_action(name)
    return {"ok": False, "reason": "unhandled action %r" % action}


def register_all(registry):
    """Register the tool and the scheduler handler. Called by the
    tool pack (tools_handlers_pack_waves._register_scheduled_agents_tools)."""
    registry.register(
        name="scheduled_agents",
        description=_TOOL_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "list, status, setup_digest, cancel, run_now, digest, save_routine, or run_routine",
                },
                "job_id": {
                    "type": "string",
                    "description": "Job id, for cancel and run_now (morning_digest for the digest)",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone for setup_digest (default Asia/Kolkata)",
                },
                "name": {
                    "type": "string",
                    "description": "Routine name, for save_routine and run_routine",
                },
                "steps": {
                    "type": ["array", "string"],
                    "description": "Ordered steps for save_routine: a list of {tool, args} calls, or a JSON string of the same (frozen args — never re-resolved from live state)",
                },
                "schedule": {
                    "type": "string",
                    "description": "Schedule for save_routine ('daily HH:MM', 'interval 45m', 'once YYYY-MM-DD HH:MM'); omit on re-save to keep the stored schedule",
                },
            },
            "required": ["action"],
        },
        handler=scheduled_agents,
    )
    from .desktop import job_scheduler
    job_scheduler.register_handler(HANDLER_NAME, operative_agent_handler)
