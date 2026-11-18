"""Executor boundary for sync tools on async loops
(fifth audit F03, Phase 2).

The Groq brain runs on an async event loop, but the tool
registry's dispatch is synchronous. Calling it directly from
the loop blocks every concurrent task (heartbeat, progress,
cancel) for the tool's whole duration. run_blocking() makes
the boundary explicit: sync tool work runs in the loop's
default executor, and the awaiting coroutine stays
responsive.

Cancellation policy (pinned by tests): cancelling the
awaiting coroutine propagates CancelledError — a
BaseException, so no except-Exception handler on the turn
path can swallow it — while the worker thread runs to
completion and its result is DISCARDED, never applied after
the turn moved on. There is no thread kill in Python; the
contract is honest about that.

Concurrency bound: the Groq tool loop dispatches one tool
at a time, so at most one run_blocking call is in flight
per turn — the bound is the loop's own sequential shape,
stated here so a future parallel dispatch knows it must
add an explicit semaphore rather than assuming one.

Stdlib-only.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")


async def run_blocking(func: Callable[..., T], *args: Any,
                       **kwargs: Any) -> T:
    """Run a sync callable on the running loop's default
    executor and await its result. Cancellation of the waiter
    propagates CancelledError; the worker thread still finishes
    but its result is discarded (never applied late)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: func(*args, **kwargs))
