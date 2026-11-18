"""Live-origin marker for the funnel (live staleness stage, P2).

The live loop's tool calls travel through the same
``ToolRegistry.execute()`` as every other caller, and the
envelope drops the origin at the boundary (``execute_envelope``
passes only ``(tool, args)`` — pinned by
``tests/test_live_verification_gap.py``). The staleness stage
must key on LIVE origin without guessing from tool names, and
without changing ``execute()``'s signature or the route_ /
use_toolbox re-entry call sites. A contextvar is the mechanism
the design doc (LIVE_VERIFIER_COVERAGE_DESIGN.md, P2) recommends:
set and cleared in ``_execute_tool_sync`` (executor thread) for
the duration of one call, it survives route_/toolbox re-entry
(same thread, nested ``execute()`` call) without signature churn,
and it cannot leak back to the event-loop thread (contextvars
do not propagate from a worker thread to its submitter).

Stdlib only, no tree imports — this module must never create an
import cycle (the funnel imports it, the live receive loop
imports it).
"""

from __future__ import annotations

import contextvars

_LIVE_ORIGIN: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "vyuk_live_origin", default=False)


def set_live_origin() -> contextvars.Token:
    """Mark the current context as live-origin. Returns the token
    the caller must pass to :func:`reset_live_origin` (finally)."""
    return _LIVE_ORIGIN.set(True)


def reset_live_origin(token: contextvars.Token) -> None:
    """Clear a live-origin mark set by :func:`set_live_origin`."""
    _LIVE_ORIGIN.reset(token)


def is_live_origin() -> bool:
    """True when the current call arrived through the live loop."""
    return bool(_LIVE_ORIGIN.get())
