"""Live cancellation tokens for running steps (B20, 2026-11-07).

The gap (audit L13): the coordinator reads the stop signal only
at step boundaries, and the funnel's cooperative token (REL-03,
core/funnel/tool_cancel.py) existed but no caller ever supplied
one — zero handlers declared it and the coordinator never passed
it. A step that hangs on a slow tool holds the task hostage for
up to 15 minutes (the "long" deadline, 900 s) while the user is
pressing Stop.

ONE design: a token per running step, registered here while the
step runs, cancelled from outside by exactly two callers:

* ``cancel_task`` — the typed "stop" and the GUI cancel buttons
  call it for one task id; it cancels every live token of that
  task (a parallel group has one token per branch).
* the failsafe trip listener — the goal pill's Stop trips the
  failsafe; ONE module-level listener (registered once, never
  leaked — there is no remove API, so the listener holds no
  task references and only reads this registry) cancels the
  live tokens of tasks that honour stop signals
  (``_honor_stop_signals``). A task that does not honour them
  keeps today's behaviour: the trip is ignored for it, exactly
  as ``_stop_reason`` promises.

A token is cancelled by a user stop OR by its own call's
deadline — never by another call's deadline. That is why the
token is PER STEP (created fresh in ``_run_step``, unregistered
in its finally): a deadline cancels only the token of the call
that timed out, and the next step gets a fresh one. A routine
shares one token across its sub-steps (its sub-steps cannot
each mint one — the routine has no task id); the token's
``cancelled_by`` reason ("stop" beats "deadline") is what lets
it tell a timed-out sub-step from a user stop.

Never raises: every entry point guards itself, so a broken
registry never breaks a task. Stdlib-only at import time —
the funnel leaf (tool_cancel) is imported lazily inside
live_token, the one place a token is constructed: the guards
suite allows brain -> funnel import-time edges ONLY to
tool_result, so this module must not import tool_cancel at
module top (its annotations are strings under
``from __future__ import annotations`` and never need the
import at runtime).
"""

from __future__ import annotations

import contextlib
import threading

_LOCK = threading.Lock()
# task-or-goal id -> set of (token, honors_stop). A parallel
# group holds one token per live branch; sequential steps hold
# one at a time (each step's token is unregistered when the
# step ends).
_LIVE: dict[str, set[tuple[CancellationToken, bool]]] = {}
_TRIP_LISTENER_INSTALLED = False


def _cancel_entry(entry: tuple[CancellationToken, bool]) -> None:
    try:
        entry[0].cancel()
    except Exception:  # noqa: BLE001 — cancelling never breaks
        pass


def register_live_token(task_id: str, token: CancellationToken,
                        honors_stop: bool) -> None:
    """Register a step's token while the step runs. Never raises."""
    try:
        with _LOCK:
            _LIVE.setdefault(str(task_id), set()).add(
                (token, bool(honors_stop)))
    except Exception:  # noqa: BLE001 — registration never breaks a task
        pass


def unregister_live_token(task_id: str,
                          token: CancellationToken) -> None:
    """Drop a step's token when the step ends. Never raises."""
    try:
        with _LOCK:
            entries = _LIVE.get(str(task_id))
            if not entries:
                return
            entries.discard((token, True))
            entries.discard((token, False))
            if not entries:
                _LIVE.pop(str(task_id), None)
    except Exception:  # noqa: BLE001 — unregistration never breaks a task
        pass


def cancel_live_tokens(task_id: str) -> None:
    """Cancel every live token of one task (cancel_task)."""
    try:
        with _LOCK:
            entries = list(_LIVE.get(str(task_id), ()))
        for entry in entries:
            _cancel_entry(entry)
    except Exception:  # noqa: BLE001 — cancelling never breaks
        pass


def cancel_honoring_tokens() -> None:
    """Cancel the live tokens of tasks that honour stop signals
    (the failsafe trip listener). A token that does not honour
    them is left alone — the trip is ignored for its task,
    exactly as _stop_reason promises."""
    try:
        with _LOCK:
            entries = [entry for bucket in _LIVE.values()
                       for entry in bucket if entry[1]]
        for entry in entries:
            _cancel_entry(entry)
    except Exception:  # noqa: BLE001 — cancelling never breaks
        pass


def ensure_trip_listener() -> None:
    """Register the ONE failsafe trip listener (idempotent).

    The listener holds no task references — it only reads this
    registry — so registering it once per process leaks nothing.
    There is no remove API in core/failsafe, so the listener is
    deliberately permanent once installed; a broken listener
    never blocks the trip (failsafe guards each listener).
    """
    global _TRIP_LISTENER_INSTALLED
    try:
        with _LOCK:
            if _TRIP_LISTENER_INSTALLED:
                return
            _TRIP_LISTENER_INSTALLED = True
        from .. import failsafe as _failsafe  # lazy: leaf module

        def _on_trip(_reason: str) -> None:
            cancel_honoring_tokens()

        _failsafe.add_trip_listener(_on_trip)
    except Exception:  # noqa: BLE001 — a missing listener never breaks a task
        pass


@contextlib.contextmanager
def live_token(task_id: str, honors_stop: bool):
    """A step's token, registered while the step runs.

    Yields a fresh CancellationToken; unregisters it on exit.
    The token is per step (never shared across steps): a
    deadline cancels only the token of the call that timed out,
    and the next step mints a fresh one, so one timeout can
    never poison the task.
    """
    # Lazy (see the module docstring): the guards suite allows
    # brain -> funnel import-time edges only to tool_result.
    from ..funnel.tool_cancel import CancellationToken

    token = CancellationToken()
    ensure_trip_listener()
    register_live_token(task_id, token, honors_stop)
    try:
        yield token
    finally:
        unregister_live_token(task_id, token)
