"""Waking an event loop to cancel its tasks (v5.32).

Session stop() methods run on the GUI thread, while their
sessions' tasks live on the worker thread's event loop. A bare
task.cancel() from a foreign thread only queues the
cancellation — on the Python versions this app runs on it does
not write to the loop's wakeup pipe, so a loop parked in its
poll (a session idling between turns, its next timer far away)
may not wake to deliver the cancellation for a long time: the
stop appears to do nothing. Field-shaped consequence
(2026-10-22): a Disconnect that hung until a grace timer
killed the loop outright, surfacing as an event-loop error.

The reliable pattern is to schedule the cancel ON the task's
own loop, thread-safely: the loop wakes immediately and
cancels its task itself. lite_client's stop event already
worked this way; this helper gives every session's stop()
the same guarantee for its tasks."""

from __future__ import annotations


def cancel_waking(task) -> None:
    """Cancel `task`, waking its event loop so the cancellation
    is delivered promptly even when called from another thread.
    Safe to call with None; never raises."""
    if task is None:
        return
    try:
        loop = task.get_loop()
    except Exception:  # noqa: BLE001 — a task in a strange state
        loop = None
    if loop is not None and not loop.is_closed():
        try:
            loop.call_soon_threadsafe(task.cancel)
            return
        except RuntimeError:
            pass  # loop closed between the check and the call
    try:
        task.cancel()
    except Exception:  # noqa: BLE001 — stop paths must never raise
        pass
