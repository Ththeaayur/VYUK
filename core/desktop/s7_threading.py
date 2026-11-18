"""§7 consumers batch 3 — funnel auto-threading of handle/stamp context.

Batches 1 and 2 gave every §7 engine a funnel tool, but each call
still needed its handle ids and snapshot anchors spelled out by
the caller. This module removes that plumbing: within one
coordinator task, the funnel remembers the most recent handle id
+ snapshot stamp a §7 tool call produced, and threads them into
subsequent calls' receipt records automatically.

Rules (all fail-closed, all load-bearing — do not soften):

* TASK-SCOPED. Threading lives exactly one task: ``begin_task``
  starts it (clearing any previous task's context), ``end_task``
  ends it (clearing everything). With no active task threading
  is INERT: productions are dropped, receipts carry no threaded
  pair. A handle minted in a previous task can therefore never
  thread into a new one — the new task's ``begin_task`` already
  cleared it. The coordinator's ``run_task`` begins/ends around
  the whole run (including failure, cancellation, and pause —
  a resumed task re-begins, so implicit threading restarts
  while explicitly declared step handles survive via the step).
  Direct funnel callers that want threading must bracket their
  calls with ``begin_task`` / ``end_task`` themselves.
* EVIDENCE ONLY. A threaded pair lands in the receipt attempt's
  ``handle_id`` / ``snapshot_stamp`` fields (the journal's
  evidence), and nowhere else. It is NEVER a handler argument:
  handlers receive exactly the args the caller gave, so the
  engines still re-prove every handle they actually use against
  a fresh snapshot. It NEVER counts as confirmation: the funnel
  gate and the engines' confirmed flags are untouched, and a
  threaded handle waves nothing through a popup.
* EXPLICIT WINS. A call that spells its own handle uses it (and
  its own stamp, when it has one); the threaded pair is fully
  ignored for that call — never mixed (a new handle with an old
  stamp would be false evidence). A call with no handle but its
  own snapshot stamp records that stamp alone.
* PAIR SEMANTICS. One stored pair: the most recent production.
  A note for the SAME handle without a new snapshot keeps the
  stored stamp (a stamp-less note must never downgrade evidence
  about a handle it did not re-observe); a new handle replaces
  the pair wholesale.
* STALENESS (item-2 rule). A stored snapshot that was never a
  successful capture (``capture_status`` not ``ok``/absent) is
  stale on arrival and never threads. When the consuming call
  carries its own snapshot, that snapshot is the freshness
  oracle: ``perception_adapter.is_stale`` judges the stored
  observation's foreground identity + bounds against it (never
  stamp equality alone — two observations of an unchanged screen
  mint different stamps). A stale pair is DROPPED (and cleared)
  rather than threaded; the call then proceeds with its own
  snapshot and no threaded handle. When the consuming call
  carries no snapshot, freshness is unproven — the pair threads
  as evidence, and the engines still re-prove any handle they
  use at dispatch. Threading never takes a fresh observation
  itself: no capture, no backend call, no funnel slowdown.
* RECEIPT-STORE BOUNDARIES. The store holds ids/stamps only —
  no receipt references — so a receipt-store reset leaves no
  dangling linkage. ``clear_context`` drops the pair while
  keeping the task token, for callers that reset receipt state
  mid-task.

Coordinator step handles (same module, so the map lives once):
``STEP_HANDLE_ARG`` maps the single-handle §7 tools to the arg
name a declared step handle injects into (``menu_action`` /
``file_dialog_action`` -> ``window_handle_id``,
``system_surface_action`` -> ``target_handle_id``). ``drag_drop``
is deliberately ABSENT: it takes two handle args (source and
target), so one step handle cannot name one without guessing —
drag steps must spell both handles explicitly, and a step
``handle_id`` on a drag step injects nothing (pinned by test).
Tools outside the map likewise get no injection; their declared
step handle is ignored, never guessed into an arg.
"""

import threading
import uuid

from . import perception_adapter as _pa  # Phase 3 S7a-2: perception_adapter already in core/desktop/  # Phase 3 S7a: perception_adapter moved to core/desktop/

__all__ = [
    "begin_task",
    "end_task",
    "current_task",
    "clear_context",
    "note_produced",
    "threaded",
    "resolve_for_call",
    "resolve_for_execute",
    "step_handle_arg",
    "STEP_HANDLE_ARG",
]

#: Single-handle §7 tools: step handle -> funnel arg name.
#: drag_drop_action is absent by design (two handle args).
STEP_HANDLE_ARG = {
    "menu_action": "window_handle_id",
    "file_dialog_action": "window_handle_id",
    "system_surface_action": "target_handle_id",
}

#: Explicit-handle scan order for a funnel call's args. First
#: non-empty string wins; for a drag (both handles present) the
#: target — the effect site — is the receipt's handle. The full
#: args (both handles) are still recorded in the attempt's
#: signature/preview, so nothing is lost.
_EXPLICIT_HANDLE_KEYS = (
    "window_handle_id",
    "target_handle_id",
    "source_handle_id",
    "handle_id",
)

#: Explicit-stamp scan order for a funnel call's args.
_EXPLICIT_STAMP_KEYS = (
    "snapshot",
    "source_observation",
    "target_observation",
)

_lock = threading.Lock()
_task_token = None
_handle_id = None
_snapshot_slice = None


def _slice(snapshot):
    """Staleness-relevant slice of a snapshot dict, or None."""
    if not isinstance(snapshot, dict):
        return None
    fg = snapshot.get("foreground_identity")
    stamp = snapshot.get("stamp")
    return {
        "stamp": stamp if isinstance(stamp, str) else None,
        "foreground_identity": dict(fg) if isinstance(fg, dict) else {},
        "bounds": snapshot.get("bounds"),
        "capture_status": snapshot.get("capture_status"),
    }


def _stale_reason(stored_slice, current_snapshot):
    """None when the stored observation is still fresh, else why.

    No backend call: freshness is judged from the stored slice
    against the consuming call's own snapshot (the oracle), or
    from the stored capture status alone when no oracle exists.
    """
    if stored_slice is None:
        return None
    status = stored_slice.get("capture_status")
    if status not in (None, "ok"):
        return ("stored snapshot was not a successful capture "
                "(%s) — re-observe before acting" % status)
    if not isinstance(current_snapshot, dict):
        return None
    probe = {
        "foreground_identity":
            stored_slice.get("foreground_identity") or {},
        "bounds": stored_slice.get("bounds"),
        "capture_status": status,
    }
    cur_fg = current_snapshot.get("foreground_identity") or {}
    cur_bounds = current_snapshot.get("bounds")
    return _pa.is_stale(probe, current=(cur_fg, cur_bounds))


def begin_task(token=None):
    """Start task-scoped threading, clearing any previous task.

    Returns the task token (the given one, or a fresh id).
    """
    global _task_token, _handle_id, _snapshot_slice
    with _lock:
        _task_token = str(token) if token else uuid.uuid4().hex
        _handle_id = None
        _snapshot_slice = None
        return _task_token


def end_task():
    """End task-scoped threading, clearing token and pair."""
    global _task_token, _handle_id, _snapshot_slice
    with _lock:
        _task_token = None
        _handle_id = None
        _snapshot_slice = None


def current_task():
    """Active task token, or None when threading is inert."""
    with _lock:
        return _task_token


def clear_context():
    """Drop the threaded pair, keep the task token.

    For callers that reset receipt state mid-task; the task
    itself stays active, so later productions thread again.
    """
    global _handle_id, _snapshot_slice
    with _lock:
        _handle_id = None
        _snapshot_slice = None


def _clear_if_handle(handle):
    """Drop the pair only if it still names this handle."""
    global _handle_id, _snapshot_slice
    with _lock:
        if _handle_id == handle:
            _handle_id = None
            _snapshot_slice = None


def note_produced(handle_id, snapshot=None, stamp=None):
    """Record a §7 tool call's production for later calls.

    No-op without an active task, and no-op without a handle.
    ``snapshot`` (a full item-2 snapshot dict) is preferred —
    it carries the foreground identity + bounds the staleness
    rule needs; ``stamp`` alone (an engine result's stamp when
    the handler never held the snapshot) threads stamp-only
    evidence whose freshness is unproven. A stamp-less note for
    the SAME handle keeps the stored stamp (never downgrades
    evidence about a handle it did not re-observe).
    """
    global _handle_id, _snapshot_slice
    if not isinstance(handle_id, str) or not handle_id.strip():
        return
    with _lock:
        if _task_token is None:
            return
        if handle_id == _handle_id and snapshot is None \
                and stamp is None:
            return
        if isinstance(snapshot, dict):
            new_slice = _slice(snapshot)
        elif isinstance(stamp, str):
            new_slice = {
                "stamp": stamp,
                "foreground_identity": {},
                "bounds": None,
                "capture_status": None,
            }
        else:
            new_slice = None
        _handle_id = handle_id
        _snapshot_slice = new_slice


def threaded():
    """Currently threaded (handle_id, stamp), or (None, None)."""
    with _lock:
        if _task_token is None:
            return None, None
        stamp = (_snapshot_slice or {}).get("stamp")
        return _handle_id, stamp


def resolve_for_call(explicit_handle=None, explicit_stamp=None,
                     current_snapshot=None):
    """Effective (handle_id, stamp) for one receipt attempt.

    Explicit handle wins whole (with its own stamp, when it has
    one); otherwise the threaded pair when fresh, else the
    caller's own stamp alone. A stale threaded pair is dropped
    and cleared, never threaded. Returns (None, None)-shaped
    pairs, never raises.
    """
    exp_handle = explicit_handle \
        if isinstance(explicit_handle, str) and explicit_handle.strip() \
        else None
    exp_stamp = explicit_stamp \
        if isinstance(explicit_stamp, str) else None
    if exp_handle is not None:
        return exp_handle, exp_stamp
    with _lock:
        if _task_token is None:
            return None, exp_stamp
        handle = _handle_id
        stored = _snapshot_slice
    if handle is None:
        return None, exp_stamp
    reason = _stale_reason(stored, current_snapshot)
    if reason is not None:
        _clear_if_handle(handle)
        return None, exp_stamp
    stamp = (stored or {}).get("stamp")
    return handle, stamp


def resolve_for_execute(args):
    """Resolve a funnel call's receipt evidence from its args.

    Extracts the explicit handle (first present, in scan order)
    and stamp, takes the call's own snapshot as the staleness
    oracle, and resolves against the threaded pair. Never
    mutates args, never raises.
    """
    args = args if isinstance(args, dict) else {}
    exp_handle = None
    for key in _EXPLICIT_HANDLE_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            exp_handle = value
            break
    exp_stamp = None
    for key in _EXPLICIT_STAMP_KEYS:
        value = args.get(key)
        if isinstance(value, dict):
            stamp = value.get("stamp")
            if isinstance(stamp, str):
                exp_stamp = stamp
                break
    current = args.get("snapshot")
    if not isinstance(current, dict):
        current = None
    return resolve_for_call(exp_handle, exp_stamp, current)


def step_handle_arg(tool):
    """Funnel arg name a declared step handle injects into, or None."""
    return STEP_HANDLE_ARG.get(tool)
