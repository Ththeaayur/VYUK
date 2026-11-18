"""Voice-turn origin flag (RET-1).

A voice turn is the one place a funnel call can be "voice-driven":
the three mic-loop brains (local, Groq text, Gemini Live lite) each
drive one `_process_utterance` per finished utterance, and every
funnel call a turn makes — direct tool calls and coordinator steps
alike — happens inside that handler, in the session's event-loop
thread.

This module is the single marker for that fact: a ContextVar the
utterance handlers set for the duration of a turn. The funnel's
activity logger reads it when a call succeeds and writes a second
line, ``timestamp | voice_task | success``, beside the tool's own
line. The daily-count derivation (core/daily_count.py) reads those
marker lines — they are the only record of a voice-driven funnel
success, and the only thing that can mark a day "used" or unlock
the nudge opt-in.

Why a ContextVar and not a parameter: the funnel's `execute()` is
the choke point for every caller, and the voice turn reaches it
through two shapes — direct `tools.execute()` calls and
`planner.run_goal` coordinator steps. Threading an `origin`
parameter through the coordinator would re-plumb the task engine;
the contextvar rides the existing call stack instead. It is safe
across the funnel's deadline worker threads because the success
logging happens in the CALLER thread after the join (the worker
thread starts with fresh contextvars — GAPS-23 F-V7 — but the
caller thread is the voice turn's thread, so the flag is visible
where the marker is written). Tasks created inside a turn inherit
the flag (asyncio copies the context at creation); the typed-text
path never sets it, so typed calls are never marked voice-driven.

Behaviour is unchanged for every other caller: the flag defaults
to False, the marker line is additive (the tool's own line is
byte-identical), and nothing here gates, confirms, routes, or
re-orders a call.
"""

from __future__ import annotations

import contextlib
import contextvars

_VOICE_TURN_ACTIVE: contextvars.ContextVar[bool] = (
    contextvars.ContextVar("vyuk_voice_turn", default=False))


def voice_turn_active() -> bool:
    """True while the current task is inside a voice turn."""
    return bool(_VOICE_TURN_ACTIVE.get())


@contextlib.contextmanager
def voice_turn():
    """Mark the enclosed block as a voice turn.

    Nesting is safe: the token restores the previous value on exit.
    """
    token = _VOICE_TURN_ACTIVE.set(True)
    try:
        yield
    finally:
        _VOICE_TURN_ACTIVE.reset(token)
