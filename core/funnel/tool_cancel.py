"""Cooperative cancellation tokens for tool handlers (audit fix
Batch 3, REL-03, scoped).

The cannot-kill kernel is DOCUMENTED DESIGN and is NOT changed
here: a timed-out worker thread is abandoned, never killed
(CPython cannot safely kill a thread), and its late outcome
finalises the operation exactly once. What this module adds is
the one safe nudge that design was missing — an opt-in token
a handler may poll at safe points:

    def my_handler(..., cancel_token=None):
        for chunk in work:
            if cancel_token is not None and cancel_token.cancelled:
                return {"status": "cancelled", ...}  # or raise

Plumbing (core/funnel/tools_registry.execute): the funnel creates one
token per call (or uses a caller-supplied one — the opt-in side
channel, e.g. a future UI cancel button or the Live batch) and
passes it ONLY to handlers that declare a `cancel_token`
parameter. Handlers without the parameter are untouched: no
signature change, no dropped-argument refusal, no behaviour
change. On a deadline timeout the funnel cancels the token, so
a token-aware abandoned worker stops at its next poll point
instead of running to completion; a token-unaware worker keeps
the old abandonment semantics honestly reported.

Stdlib-only, no core imports, so importing this module can
never cycle.
"""

from __future__ import annotations

import threading


class ToolCancelled(Exception):
    """Raised by CancellationToken.raise_if_cancelled() when a
    handler prefers an exception over a return-value stop."""


class CancellationToken:
    """A thread-safe, one-way cancellation flag.

    Cancel is sticky: once cancelled, the token stays
    cancelled (a handler that already stopped must not be
    silently un-stopped by a later caller).

    B20 (2026-11-07): the cancel carries a REASON — "stop"
    (the user asked, via cancel_task or a failsafe trip) or
    "deadline" (the funnel cancelled it when the call's
    deadline fired). The reason is stop-priority: a "stop"
    overwrites a "deadline", a "deadline" never overwrites a
    "stop". Why: one token may span several calls (a routine
    passes its token to each sub-step), and a sub-step that
    timed out must not masquerade as a user stop — a routine
    skips a timed-out step per on_fail and only halts as
    cancelled when the USER stopped it. Handlers check
    ``cancelled`` (either reason ends the act promptly);
    only the continuation decision reads the reason.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str | None = None
        self._reason_lock = threading.Lock()

    def cancel(self, reason: str = "stop") -> None:
        """Request cancellation. Idempotent, never raises.

        ``reason`` is "stop" (default — the user asked) or
        "deadline" (the funnel, on a deadline timeout).
        Stop-priority: a later "deadline" never overwrites an
        earlier "stop"; an earlier "deadline" IS overwritten
        by a later "stop".
        """
        try:
            with self._reason_lock:
                if reason == "stop" or self._reason is None:
                    self._reason = reason
            self._event.set()
        except Exception:  # noqa: BLE001 — cancelling never breaks
            pass

    @property
    def cancelled(self) -> bool:
        """True once cancel() has been called."""
        return self._event.is_set()

    @property
    def cancelled_by(self) -> str | None:
        """How the token was cancelled: "stop", "deadline", or
        None when it has not been cancelled."""
        return self._reason

    def poll(self) -> bool:
        """Alias for `cancelled`, for handlers that read it as
        a verb (``if token.poll(): stop``)."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise ToolCancelled when cancelled, else return."""
        if self._event.is_set():
            raise ToolCancelled("tool call was cancelled")


# ---------------------------------------------------------------------------
# Live-dispatch registry (GAPS-24, K-V3 / K-V8)
#
# The funnel registers each in-flight call's cancellation token
# here, so a failsafe trip can STOP RUNNING WORK — not just refuse
# new calls. Before this, a trip released held inputs, killed
# registered subprocesses and cancelled task tokens, but the
# funnel's own per-call token had no registry and no trip
# listener: a token-aware handler (run_command, winget_install,
# download_file, the 17 handlers that declare cancel_token)
# kept running until its deadline. Now:
#
#   - dispatch registers the token on entry and unregisters on
#     exit (the deadline worker owns the registration while it
#     is abandoned, so a later trip can still cancel it);
#   - a failsafe trip listener (installed once, lazily, on the
#     first registration) cancels every live token;
#   - cancel_all_live() is the kill primitive for running
#     handlers at this layer: cooperative for threads (a handler
#     stops at its next poll point; run_with_cancel kills the
#     child process), never a thread kill — CPython cannot safely
#     kill a thread, and the documented cannot-kill kernel stands.
#
# Handles are ints; 0 means "no registration" (None token).
# live_calls() is the listing a future cancel/reconcile surface
# (tool or GUI — owed, not built here) addresses. All functions
# are best-effort and never raise into a call path.
# ---------------------------------------------------------------------------

_LIVE_LOCK = threading.Lock()
_LIVE_CALLS: dict[int, dict] = {}
_LIVE_NEXT_HANDLE = 0
_TRIP_LISTENER_INSTALLED = False


def _on_trip_cancel_live(_reason: str) -> None:
    """Failsafe trip listener: cancel every live dispatch token."""
    cancel_all_live(reason="stop")


def ensure_trip_cancels_live_calls() -> None:
    """Install the trip listener once (idempotent).

    Lazy import of failsafe (this module is stdlib-only at
    import time). A failed install leaves the flag clear so the
    next registration retries; a missing listener never breaks
    a dispatch — the funnel's failsafe stage also cancels live
    calls when it observes a trip (see _exec_failsafe)."""
    global _TRIP_LISTENER_INSTALLED
    with _LIVE_LOCK:
        if _TRIP_LISTENER_INSTALLED:
            return
    try:
        from .. import failsafe as _failsafe  # lazy: leaf module
        _failsafe.add_trip_listener(_on_trip_cancel_live)
    except Exception:  # noqa: BLE001 — no failsafe, no wiring
        return
    with _LIVE_LOCK:
        _TRIP_LISTENER_INSTALLED = True


def register_live_call(token: "CancellationToken | None",
                       tool: str,
                       thread: "threading.Thread | None" = None) -> int:
    """Register one in-flight call's token. Returns a handle (0
    when there is no token to register). The trip listener is
    installed on the first registration. Never raises."""
    if token is None:
        return 0
    global _LIVE_NEXT_HANDLE
    try:
        ensure_trip_cancels_live_calls()
        with _LIVE_LOCK:
            _LIVE_NEXT_HANDLE += 1
            handle = _LIVE_NEXT_HANDLE
            _LIVE_CALLS[handle] = {
                "token": token,
                "tool": str(tool),
                "thread": thread,
            }
        return handle
    except Exception:  # noqa: BLE001 — registration never breaks
        return 0  # a dispatch


def unregister_live_call(handle: int) -> bool:
    """Drop one live-call registration. Idempotent; True when an
    entry was present. Never raises."""
    if not handle:
        return False
    try:
        with _LIVE_LOCK:
            return _LIVE_CALLS.pop(handle, None) is not None
    except Exception:  # noqa: BLE001
        return False


def _prune_dead_locked() -> None:
    """Drop entries whose deadline worker thread is dead (the
    worker unregisters in its own finally; this covers a worker
    that died without doing so). Callers hold _LIVE_LOCK."""
    for handle in [h for h, e in _LIVE_CALLS.items()
                   if e.get("thread") is not None
                   and not e["thread"].is_alive()]:
        _LIVE_CALLS.pop(handle, None)


def live_calls() -> list[dict]:
    """Snapshot of live calls: [{handle, tool}]. Never raises."""
    try:
        with _LIVE_LOCK:
            _prune_dead_locked()
            return [{"handle": h, "tool": e["tool"]}
                    for h, e in sorted(_LIVE_CALLS.items())]
    except Exception:  # noqa: BLE001
        return []


def cancel_live_call(handle: int, reason: str = "stop") -> bool:
    """Cancel one live call's token. True when the handle was
    live (the entry stays registered — the dispatch unregisters
    it on exit). Never raises."""
    if not handle:
        return False
    try:
        with _LIVE_LOCK:
            entry = _LIVE_CALLS.get(handle)
        if entry is None:
            return False
        entry["token"].cancel(reason=reason)
        return True
    except Exception:  # noqa: BLE001 — cancelling never breaks
        return False  # a call


def cancel_all_live(reason: str = "stop") -> int:
    """Cancel every live dispatch token. Returns how many were
    cancelled (0 when nothing is running). Best-effort per
    token; never raises."""
    try:
        with _LIVE_LOCK:
            _prune_dead_locked()
            entries = list(_LIVE_CALLS.values())
        cancelled = 0
        for entry in entries:
            try:
                entry["token"].cancel(reason=reason)
                cancelled += 1
            except Exception:  # noqa: BLE001 — one token never
                pass  # blocks the rest
        return cancelled
    except Exception:  # noqa: BLE001
        return 0


# B20 (2026-11-07): the ONE cancellable subprocess runner, shared
# by every `_run` copy (skills_system_helpers, skills_system2_,
# skills_system3_) so the long subprocess tools (winget install /
# upgrade, restore points, driver inventory) all end promptly on
# Stop instead of running to their 900 s / 180 s / 120 s timeout.
# A plain subprocess.run cannot be interrupted mid-call; this
# polls the child instead.
#
# Returns (returncode, stdout, stderr) exactly like `_run`:
# returncode -1 when the command could not be started, -2 on
# timeout, and -3 when the token cancelled the run — the caller
# maps -3 to a cancelled outcome (never to a timeout, never to
# success). Killing the CHILD PROCESS here is not killing the
# step's thread: the timeout path already kills the child the
# same way (subprocess.run's TimeoutExpired kills it), and the
# step's own thread is never touched — the handler returns
# normally with the -3 sentinel.
#
# Only used when a token is supplied; without one the callers
# keep their exact subprocess.run path. Never raises.
def run_with_cancel(args, timeout: float,
                    cancel_token: "CancellationToken | None",
                    **popen_kwargs) -> tuple[int, str, str]:
    import subprocess
    import time

    if cancel_token is None:
        raise ValueError("run_with_cancel needs a token")
    deadline = time.monotonic() + max(0.0, float(timeout or 0))
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace",
                                **popen_kwargs)
    except FileNotFoundError:
        return -1, "", f"{args[0]} was not found on this PC."
    except Exception as exc:  # noqa: BLE001 — never raises
        return -1, "", str(exc)
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.1)
                # GAPS-24 (K-V3): a child killed from outside (the
                # failsafe trip's proc-kill) lands here via
                # communicate() returning, not via TimeoutExpired —
                # the token check in the except branch never runs.
                # A cancelled token reports cancelled (-3), never
                # the child's kill exit code as a success.
                if cancel_token.cancelled:
                    return -3, out or "", (
                        "cancelled at the user's request while the "
                        "command was still running")
                return proc.returncode, out or "", err or ""
            except subprocess.TimeoutExpired:
                if cancel_token.cancelled:
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001 — already gone
                        pass
                    try:
                        out, err = proc.communicate(timeout=5)
                    except Exception:  # noqa: BLE001 — reaping never breaks
                        out, err = "", ""
                    return -3, out or "", (
                        "cancelled at the user's request while the "
                        "command was still running")
                if time.monotonic() >= deadline:
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001 — already gone
                        pass
                    try:
                        out, err = proc.communicate(timeout=5)
                    except Exception:  # noqa: BLE001 — reaping never breaks
                        out, err = "", ""
                    return -2, out or "", (
                        f"{args[0]} took longer than {timeout:g} "
                        "seconds and was stopped.")
    except Exception as exc:  # noqa: BLE001 — never raises
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 — already gone
            pass
        return -1, "", str(exc)
