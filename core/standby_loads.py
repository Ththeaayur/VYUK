"""Standby-load coalescer (fifth audit F06, Phase 2).

The standby path preloads a local model session in the
background so a later fallback is instant. Two failure
shapes had to be fixed:

1. Unbounded duplication: repeated prepare calls for the
   same model each loaded a session, ballooning memory.
   Loads are now coalesced per model id — one shared task
   per model — so N concurrent prepares cost one load.
2. Uncancellable standby: cancelling the awaiting task used
   to leave the load running with no record of it. The
   shared task is now tracked to completion (a done-callback
   removes it from the registry) and an awaiter's
   cancellation is shielded from the shared load: cancelling
   a waiter never cancels the load, and the load's result is
   simply not used by that waiter.

The actual load step is also gated by a process-wide
semaphore (exactly one model load at a time), released in a
finally block so a failed load never wedges the gate. The
coalescer's contract — one task per model id, tracked to
completion, semaphore-released — is what the regression
tests pin; "verify release on connection/stop" is exactly
this: after the shared task completes (or the waiters are
cancelled), the registry is empty and the semaphore is free
for the next prepare.

Phase 4 (fourth audit C04): abandonment. When the cloud
wins (or the user stops), the standby session is no longer
wanted — but a worker thread already running a synchronous
preload cannot be forcibly killed, so abandonment does NOT
stop the thread mid-load. Instead `abandon()` marks the
model id: when the shared load completes, its done-callback
calls the registered release function on the result (the
never-started session) and the registry drains as usual.
A later `prepare()` for the same id revokes the mark — a
new waiter owns the load again, so nothing is released
under a live owner. If no load is in flight when abandon
is called there is nothing to mark (a completed load's
session is the waiter's to release directly — brain.py's
_discard_standby does exactly that for an already-done
standby task). One honest race remains: a load that
completes in the same loop tick as its abandonment may
drain before the mark lands, leaving the session
unreferenced for the garbage collector rather than
deterministically released. The registry and semaphore
still drain; the session is never installed, never
executed, and never leaks a second preload.

Stdlib-only, asyncio + threading.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable, Hashable

# Process-wide: exactly one model load at a time.
_load_semaphore = threading.Semaphore(1)


class StandbyLoadCoalescer:
    """Coalesce standby model loads by model id."""

    def __init__(self) -> None:
        # model_id -> shared task; a task is removed by its own
        # done-callback, so the registry always reflects loads
        # still in flight (tracked to completion).
        self._tasks: dict[Hashable, asyncio.Task] = {}
        # model_id -> release callable for an abandoned load;
        # set by abandon(), cleared by prepare() (new owner)
        # or by the done-callback once it has fired.
        self._abandoned: dict[Hashable,
                              Callable[[Any], None] | None] = {}

    def prepare(self, model_id: Hashable,
                build_and_preload: Callable[[], Awaitable[Any]],
                ) -> asyncio.Task:
        """Return the shared task for this model id, creating
        it on first call. Concurrent prepares for the same id
        get the SAME task object (one load, many waiters).
        Different ids load independently, serialized by the
        process-wide semaphore inside the load step. A new
        prepare revokes any abandonment for this id: a live
        waiter owns the load again."""
        self._abandoned.pop(model_id, None)
        task = self._tasks.get(model_id)
        if task is not None and not task.done():
            return task

        async def _load() -> Any:
            # The semaphore gates the actual load step; the
            # awaiting coroutine never holds it across an
            # await of another task, so a cancelled waiter
            # cannot strand the gate.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, _load_semaphore.acquire)
            try:
                return await build_and_preload()
            finally:
                _load_semaphore.release()

        task = asyncio.ensure_future(_load())
        self._tasks[model_id] = task

        def _done(_t: asyncio.Task, _mid: Hashable = model_id) -> None:
            if self._tasks.get(_mid) is _t:
                del self._tasks[_mid]
            # Abandoned at completion: release the result
            # (the never-started session) best-effort. A
            # cancelled task has no result to release, and a
            # failed task's exception is the load's own
            # failure, not a session to release.
            release = self._abandoned.pop(_mid, None)
            if release is not None and not _t.cancelled():
                try:
                    result = _t.result()
                except Exception:  # noqa: BLE001 — failed load
                    result = None
                    release = None
                if release is not None:
                    try:
                        release(result)
                    except Exception:  # noqa: BLE001 — never
                        pass  # break the loop on a release bug

        task.add_done_callback(_done)
        return task

    def abandon(self, model_id: Hashable,
                release: Callable[[Any], None] | None = None) -> None:
        """Mark an in-flight load abandoned (Phase 4, C04):
        when it completes, its done-callback releases the
        result via `release` instead of handing it to any
        waiter. A no-op when no load is in flight — a
        completed load's session belongs to its waiter, and
        the caller releases it directly (see brain.py's
        _discard_standby). Never raises."""
        try:
            task = self._tasks.get(model_id)
            if task is None or task.done():
                return
            self._abandoned[model_id] = release
        except Exception:  # noqa: BLE001 — abandon never breaks
            pass  # the caller (cloud win / stop are on their way)

    def abandoned(self, model_id: Hashable) -> bool:
        """True when this model id currently carries an
        abandonment mark (load still in flight, release
        pending on completion)."""
        return model_id in self._abandoned

    async def await_shared(self, task: asyncio.Task) -> Any:
        """Await a shared load without owning it: if THIS
        waiter is cancelled, the shared load continues for the
        other waiters (asyncio.shield), and the waiter gets
        CancelledError — the load's result is simply not used
        by the cancelled waiter."""
        return await asyncio.shield(task)

    def in_flight(self) -> list[Hashable]:
        """Model ids with loads still in flight."""
        return [mid for mid, task in self._tasks.items()
                if not task.done()]

    def semaphore_available(self) -> bool:
        """True when no load currently holds the gate."""
        return _semaphore_free()

    def cancel_all(self) -> None:
        """Cancel every in-flight load (stop/disconnect path).
        The done-callbacks still fire and the registry drains."""
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()


def _semaphore_free() -> bool:
    if _load_semaphore.acquire(blocking=False):
        _load_semaphore.release()
        return True
    return False


# Process-wide default coalescer (brain.py's standby path).
default_coalescer = StandbyLoadCoalescer()
