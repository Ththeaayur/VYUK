"""B14 — per-task wall-clock budget + cost accounting (autonomy L11).

LOPHOLE FAMILY (proved from the tree before building): "a task
that runs forever and costs silently".

* No coordinator time budget: ``run_task``
  (task_coordinator_execution.py) checks stop/pause signals at
  every step boundary but measures no elapsed wall-clock time;
  the only "budget" in that file is the jump budget
  (``max_jumps``). ``run_goal`` has hard budgets (25 steps /
  600 s, planner_rungoal.py) but the coordinator loop — the
  loop that actually executes a plan's steps, retries, jumps,
  and verification phases — has none. A task with a long plan,
  repeated retries, and jumps could run indefinitely.
* No per-task cost accounting. CORRECTION to the audit's
  premise, proved from the tree: a GLOBAL cost ledger already
  exists (core/cost_ledger.py — append-only SQLite per-turn
  brain spend, ``estimate_cost`` with a rate snapshot, exact vs
  estimated measurement, NULL cost when no usage was
  returned). But its callers are only the voice clients
  (cloud_text_helpers, live_client_error_cost,
  local_client_cost); the coordinator never touches it, and no
  task record — receipt or ledger — carries what a task spent.
  The gap this batch closes is per-task accounting, not the
  existence of a ledger.

ONE DESIGN: a per-task wall-clock budget + a cost ledger, both
carried in the receipt and the ledger record (the one home, like
B11 — never a second store). The budget is checked at the same
step boundary where stop/pause signals, strikes, and landing
verdicts are judged (one place, not a second loop); a task
that exceeds it pauses resumably (B11 shape: fresh budget
window on resume, windows + 1, visible in the record), never
a silent kill, never a fake completion — and the receipt says
what it spent (elapsed time, steps run, executions, cloud
calls by leg, and cost where the provider's published rate
lets it be computed, honest-unknown where it does not).
Local calls are counted as time, never money: their wall time
is inside elapsed_s and they are never priced as dollars.

REJECTED ALTERNATIVES (recorded here, not rebuilt):
* A hard kill at the budget — a long task killed mid-step
  leaves a half-applied action. The pause lands at a step
  boundary, like B11.
* Pricing local calls as dollars — no provider charges for
  them; a dollar figure would be invented.
* Inventing token counts where none are recorded — the ledger
  says what it measured and what it did not. A call whose
  provider returned no usage (the second-opinion check returns
  text only — second_opinion.cross_check) records its leg and
  its seconds, and its cost as honest-unknown, never a
  fabricated zero.
* Using the cost ledger's DEFAULT_RATES placeholders as if
  they were vendor quotes — a cost computed from a placeholder
  rate is labelled so in the note (rate_source "default"), per
  cost_ledger.py's own house rule.
* A second budget store — the receipt + task ledger are the
  one home, like B11.

BUDGET VALUES: this batch builds the mechanism, not the
number. The default is the run_goal 600 s shape
(``DEFAULT_TASK_TIME_BUDGET_S``) — stated explicitly here so
the owner can set the value; the picker never sees cost (the
coordinator never calls the picker — steps are pre-planned —
and nothing in this module is imported by planner_rungoal or
planner).

Stdlib only. No new dependency, no new tool, no VERSION /
manifest / installer change.
"""

from __future__ import annotations

import math
import time

__all__ = [
    "DEFAULT_TASK_TIME_BUDGET_S",
    "monotonic",
    "new_spend",
    "validate_time_budget",
    "record_cloud_call",
    "record_local_call",
    "elapsed_s",
    "begin_window",
    "close_window",
    "time_budget_reason",
    "spend_snapshot",
    "spend_snapshot_for_task",
    "spend_for_record",
    "spend_from_record",
]

# The mechanism's default, in the run_goal 600 s shape
# (planner_rungoal.py goal_max_seconds). The owner sets the
# value; this is the number the mechanism uses until then.
DEFAULT_TASK_TIME_BUDGET_S = 600.0

# A cloud-call entry is kept whole only up to this many
# aggregated (leg, model) rows in a stored record; the
# aggregation itself is unbounded in memory.
_MAX_CLOUD_CALL_ROWS = 16


def monotonic() -> float:
    """Wall-clock source for budgets (patch point for tests)."""
    return time.monotonic()


def validate_time_budget(value) -> float:
    """A per-task wall-clock budget in seconds, or the default.

    Anything unusable (missing, non-numeric, zero, negative,
    non-finite) falls back to the default — a bad budget must
    never break a task or silently uncap it.
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TASK_TIME_BUDGET_S
    if not math.isfinite(seconds) or seconds <= 0:
        return DEFAULT_TASK_TIME_BUDGET_S
    return seconds


def new_spend() -> dict:
    """An empty per-task spend record (see spend_snapshot)."""
    return {
        "elapsed_s": 0.0,
        "steps_run": 0,
        "executions": 0,
        "cloud_calls": [],
        "local_calls": 0,
        "local_seconds": None,
        "cost_usd": None,
        "cost_known": True,
        "cost_note": "no cloud calls — nothing spent",
    }


def record_cloud_call(spend: dict, *, leg: str, model: str = "",
                      prompt_tokens=None, completion_tokens=None,
                      seconds=None, cost_usd=None,
                      rate_source=None) -> None:
    """One cloud call onto the task's spend, aggregated by leg+model.

    Token counts and cost are recorded ONLY when the caller
    measured them (a provider usage payload, or a cost computed
    through core.cost_ledger.estimate_cost). A call with no
    measured tokens records its leg, its call count, and its
    measured seconds, and its cost stays unknown — the entry's
    measurement is "unknown" and the task's cost_known goes
    False. No rate is ever applied here: this module never
    invents a price, and it never uses the cost ledger's
    placeholder DEFAULT_RATES as a quote (a caller that did
    must pass rate_source="default", and the snapshot says so).
    """
    if not isinstance(spend, dict):
        return
    leg = str(leg or "").strip() or "unknown"
    model = str(model or "").strip()
    calls = spend.setdefault("cloud_calls", [])
    entry = None
    for existing in calls:
        if isinstance(existing, dict) \
                and existing.get("leg") == leg \
                and existing.get("model", "") == model:
            entry = existing
            break
    if entry is None:
        entry = {
            "leg": leg,
            "model": model,
            "calls": 0,
            "prompt_tokens": None,
            "completion_tokens": None,
            "measurement": "unknown",
            "cost_usd": None,
            "rate_source": None,
            "seconds": None,
        }
        calls.append(entry)
    entry["calls"] = int(entry.get("calls", 0) or 0) + 1
    if prompt_tokens is not None:
        try:
            entry["prompt_tokens"] = (
                int(entry.get("prompt_tokens") or 0)
                + int(prompt_tokens))
        except (TypeError, ValueError):
            pass
    if completion_tokens is not None:
        try:
            entry["completion_tokens"] = (
                int(entry.get("completion_tokens") or 0)
                + int(completion_tokens))
        except (TypeError, ValueError):
            pass
    if entry.get("prompt_tokens") is not None \
            or entry.get("completion_tokens") is not None:
        entry["measurement"] = "exact"
    if cost_usd is not None:
        try:
            entry["cost_usd"] = (
                float(entry.get("cost_usd") or 0.0)
                + float(cost_usd))
        except (TypeError, ValueError):
            pass
    if rate_source is not None:
        entry["rate_source"] = str(rate_source)
    if seconds is not None:
        try:
            entry["seconds"] = (
                float(entry.get("seconds") or 0.0) + float(seconds))
        except (TypeError, ValueError):
            pass


def record_local_call(spend: dict) -> None:
    """One local-leg call onto the task's spend (count only).

    A local call is counted as TIME, never money: its wall
    time is inside elapsed_s and it is never priced. No
    duration is recorded per call — the split of elapsed time
    into local vs cloud is derived in spend_snapshot from the
    cloud calls' measured seconds, when every cloud call
    reported one.
    """
    if not isinstance(spend, dict):
        return
    spend["local_calls"] = int(spend.get("local_calls", 0) or 0) + 1


def elapsed_s(task) -> float:
    """Running seconds for a task: finished windows + this one.

    Pause time is never charged: the base accumulates only
    while a window is open (close_window), and a resumed task
    starts a fresh window (begin_window).
    """
    base = float(getattr(task, "_spend_elapsed_base", 0.0) or 0.0)
    started = getattr(task, "_budget_started_at", None)
    if started is None:
        return base
    try:
        return base + max(0.0, monotonic() - float(started))
    except (TypeError, ValueError):
        return base


def begin_window(task) -> None:
    """Open a budget window for this run_task entry, if none is open.

    The first iteration of each run_task entry opens a fresh
    window (windows + 1), so a resumed task is measured from
    the resume (B11 shape) and is never charged twice for
    work done before the stop. A no-op re-entry (a paused
    task whose loop breaks before the first step) never opens
    one — the caller checks the pause flags first.
    """
    if getattr(task, "_budget_started_at", None) is not None:
        return
    try:
        task._budget_started_at = monotonic()
        task._budget_windows = int(
            getattr(task, "_budget_windows", 0) or 0) + 1
    except Exception:  # noqa: BLE001 — a broken window never breaks a task
        pass


def close_window(task) -> float:
    """Close the open budget window, banking its seconds. Returns elapsed."""
    started = getattr(task, "_budget_started_at", None)
    if started is None:
        return elapsed_s(task)
    try:
        banked = max(0.0, monotonic() - float(started))
    except (TypeError, ValueError):
        banked = 0.0
    try:
        base = float(getattr(task, "_spend_elapsed_base", 0.0) or 0.0)
        task._spend_elapsed_base = base + banked
        task._budget_started_at = None
        spend = getattr(task, "_spend", None)
        if isinstance(spend, dict):
            spend["elapsed_s"] = float(task._spend_elapsed_base)
    except Exception:  # noqa: BLE001 — a broken close never breaks a task
        pass
    return elapsed_s(task)


def time_budget_reason(task) -> str | None:
    """The pause reason when this window's budget is spent, else None.

    The budget is per WINDOW (fresh on resume), not per task
    lifetime — a resumed task gets its full window for the
    remaining work, stated in the record (B11 shape). Checked
    at a step boundary only: a step already running is never
    killed, so a long step can overshoot the budget and the
    pause lands on the next boundary.
    """
    started = getattr(task, "_budget_started_at", None)
    if started is None:
        return None
    try:
        budget = float(getattr(task, "_time_budget_s",
                               DEFAULT_TASK_TIME_BUDGET_S)
                       or DEFAULT_TASK_TIME_BUDGET_S)
        spent = max(0.0, monotonic() - float(started))
    except (TypeError, ValueError):
        return None
    if spent <= budget:
        return None
    windows = int(getattr(task, "_budget_windows", 1) or 1)
    return (
        f"time budget spent ({spent:.0f}s of {budget:.0f}s in "
        f"window {windows}) — paused; resume to continue from "
        f"the next step")


def spend_snapshot(spend: dict, *, elapsed_s: float = 0.0,
                   steps_run: int = 0) -> dict:
    """A clean spend dict for a receipt / ledger record.

    Cost is the sum of measured call costs, or None when ANY
    cloud call's cost is unknown — a partial sum is never
    presented as the total. local_seconds is wall time outside
    measured cloud calls, computable only when every cloud
    call reported a duration; otherwise None with the note
    saying so. The note names what was measured and what was
    not, so an unknown is never read as a zero.
    """
    cloud = []
    for entry in (spend or {}).get("cloud_calls", []) or []:
        if not isinstance(entry, dict):
            continue
        cloud.append({
            "leg": str(entry.get("leg", "") or ""),
            "model": str(entry.get("model", "") or ""),
            "calls": int(entry.get("calls", 0) or 0),
            "prompt_tokens": entry.get("prompt_tokens"),
            "completion_tokens": entry.get("completion_tokens"),
            "measurement": str(entry.get("measurement", "unknown")
                               or "unknown"),
            "cost_usd": entry.get("cost_usd"),
            "rate_source": entry.get("rate_source"),
            "seconds": entry.get("seconds"),
        })
    cloud = cloud[:_MAX_CLOUD_CALL_ROWS]
    known_cost = 0.0
    unknown_legs = []
    for entry in cloud:
        if entry.get("cost_usd") is None:
            unknown_legs.append(entry.get("leg") or "?")
        else:
            try:
                known_cost += float(entry["cost_usd"])
            except (TypeError, ValueError):
                unknown_legs.append(entry.get("leg") or "?")
    cost_known = not unknown_legs
    if not cloud:
        cost_note = "no cloud calls — nothing spent"
    elif cost_known:
        cost_note = ("cost from provider rates as recorded per "
                     "call (rate_source per call)")
        if any(e.get("rate_source") == "default" for e in cloud):
            cost_note += ("; a default rate is a placeholder, "
                          "not a vendor quote")
    else:
        cost_note = (
            "cost unknown: " + ", ".join(sorted(set(unknown_legs)))
            + " returned no usage (no token counts, no rate "
              "applied) — never a fabricated zero")
    if all(e.get("seconds") is not None for e in cloud):
        try:
            cloud_seconds = sum(float(e["seconds"]) for e in cloud)
            local_seconds = max(0.0, float(elapsed_s) - cloud_seconds)
        except (TypeError, ValueError):
            local_seconds = None
    else:
        local_seconds = None
    if cloud and local_seconds is None:
        cost_note += ("; local time not separable — a cloud call "
                      "reported no duration")
    return {
        "elapsed_s": float(elapsed_s or 0.0),
        "steps_run": int(steps_run or 0),
        "executions": int((spend or {}).get("executions", 0) or 0),
        "cloud_calls": cloud,
        "local_calls": int((spend or {}).get("local_calls", 0) or 0),
        "local_seconds": local_seconds,
        "cost_usd": known_cost if cost_known else None,
        "cost_known": cost_known,
        "cost_note": cost_note,
    }


def spend_snapshot_for_task(task) -> dict:
    """The task's spend as a receipt-ready dict (live elapsed)."""
    spend = getattr(task, "_spend", None)
    if not isinstance(spend, dict):
        spend = new_spend()
    return spend_snapshot(
        spend,
        elapsed_s=elapsed_s(task),
        steps_run=len(getattr(task, "outcomes", []) or []))


def spend_for_record(task) -> dict:
    """The task's spend as a ledger-record dict (same shape)."""
    return spend_snapshot_for_task(task)


def spend_from_record(record: dict) -> dict:
    """A stored spend record as live spend (coerced, unknown keys dropped).

    Old records without "spend" load as an empty spend — no
    schema bump. Cost fields are re-derived by spend_snapshot
    from the cloud-call entries, so they are not restored
    directly (a stored total can never disagree with its
    entries). elapsed_s restores as the finished-windows base:
    a resumed task is never charged twice for time before the
    stop.
    """
    spend = new_spend()
    raw = (record or {}).get("spend") if isinstance(record, dict) else None
    if not isinstance(raw, dict):
        return spend
    try:
        spend["elapsed_s"] = float(raw.get("elapsed_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        spend["elapsed_s"] = 0.0
    try:
        spend["steps_run"] = int(raw.get("steps_run", 0) or 0)
    except (TypeError, ValueError):
        spend["steps_run"] = 0
    try:
        spend["executions"] = int(raw.get("executions", 0) or 0)
    except (TypeError, ValueError):
        spend["executions"] = 0
    try:
        spend["local_calls"] = int(raw.get("local_calls", 0) or 0)
    except (TypeError, ValueError):
        spend["local_calls"] = 0
    cloud = []
    for entry in raw.get("cloud_calls", []) or []:
        if not isinstance(entry, dict):
            continue
        try:
            cloud.append({
                "leg": str(entry.get("leg", "") or ""),
                "model": str(entry.get("model", "") or ""),
                "calls": int(entry.get("calls", 0) or 0),
                "prompt_tokens": entry.get("prompt_tokens"),
                "completion_tokens": entry.get("completion_tokens"),
                "measurement": str(entry.get("measurement", "unknown")
                                   or "unknown"),
                "cost_usd": entry.get("cost_usd"),
                "rate_source": entry.get("rate_source"),
                "seconds": entry.get("seconds"),
            })
        except (TypeError, ValueError):
            continue
    spend["cloud_calls"] = cloud[:_MAX_CLOUD_CALL_ROWS]
    return spend
