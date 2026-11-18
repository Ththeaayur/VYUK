"""P2 speed pass (2026-11-16): per-turn latency instrumentation for
the live voice loop.

The plan's budget is first sound within half a second, every time —
and a budget that is not measured is a wish. This module is the
measurement: a small, stdlib-only recorder the live session feeds
as a turn unfolds (user voice -> first model audio -> first
playback enqueue -> tool waits -> turn complete), plus a ring of
recent turns for the Doctor/status surface.

What it deliberately does NOT measure: the server side. The
model's own thinking time and its endpointing decision are not
visible locally — the first-audio stamp here is the arrival of
the server's first audio chunk, and `first_sound_ms` is measured
from the last locally-observed voice chunk, which is the closest
local proxy for "the user stopped speaking". The field test (ten
timed questions on his machine) is the only end-to-end proof;
this recorder is what makes that test report real stage numbers
instead of one stopwatch.

No tree imports (stdlib only) so the unit tests can drive it with
a fake clock and so it can never create an import cycle.
"""

from __future__ import annotations

import collections
import time
from typing import Any

# P2 budget (addictive_vyuk_plan.md, proposal 2): first sound
# within half a second of the user finishing.
FIRST_SOUND_BUDGET_S = 0.5
FIRST_SOUND_BUDGET_MS = 500.0


class TurnLatency:
    """One live turn's latency record.

    Events are stamped with a monotonic clock (injectable for
    tests). A turn is the span from the user's voice to the
    model's first audio and the turn's completion:

      note_voice()            user voice heard (updates the
                              last-voice stamp; a voice note
                              after a finalized turn opens a
                              fresh turn)
      note_first_audio()      the server's first audio chunk
                              of the turn arrived
      note_first_enqueue()    that chunk left for the speakers
                              (enqueue returned)
      note_tool_batch_start() the model asked for tools
      note_tool_results()     the tool results were delivered
      note_filler(phrase)     the filler bridge spoke
      note_interrupted()      the server reported an interruption
      note_turn_complete()    the turn ended — finalizes the
                              record and returns the summary
                              (idempotent: a second call returns
                              None)

    `summary()` is always readable (unfinalized values read as
    the story so far); `audio_seen` tells a caller whether this
    turn's first audio has already been stamped, so the receive
    loop can stamp exactly once without keeping its own flag.
    """

    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._reset_state()

    def _reset_state(self) -> None:
        self._last_voice_at: float | None = None
        self._first_audio_at: float | None = None
        self._first_enqueue_at: float | None = None
        self._tool_batch_start_at: float | None = None
        self._tool_results_at: float | None = None
        self._first_audio_after_tools_at: float | None = None
        self._filler_phrase: str | None = None
        self._interrupted = False
        self._finalized = False
        self._summary: dict[str, Any] | None = None

    # -- events ------------------------------------------------------
    def note_voice(self, at: float | None = None) -> None:
        """User voice heard. A voice note after a finalized turn
        opens a fresh turn (the previous summary stays readable
        on the ledger, not here)."""
        if self._finalized:
            self._reset_state()
        self._last_voice_at = self._clock() if at is None else at

    def note_first_audio(self, at: float | None = None) -> None:
        """The server's first audio chunk of this turn arrived.
        The first call wins; later chunks are the same turn's
        audio, not a new first sound."""
        if self._finalized:
            self._reset_state()
        if self._first_audio_at is not None:
            return
        now = self._clock() if at is None else at
        self._first_audio_at = now
        if self._tool_results_at is not None:
            self._first_audio_after_tools_at = now

    def note_first_enqueue(self, at: float | None = None) -> None:
        """The first audio chunk left for the speakers."""
        if self._first_audio_at is None:
            return
        if self._first_enqueue_at is not None:
            return
        self._first_enqueue_at = self._clock() if at is None else at

    def note_tool_batch_start(self, at: float | None = None) -> None:
        if self._tool_batch_start_at is None:
            self._tool_batch_start_at = (
                self._clock() if at is None else at)

    def note_tool_results(self, at: float | None = None) -> None:
        if self._tool_batch_start_at is None:
            return
        if self._tool_results_at is not None:
            return
        self._tool_results_at = self._clock() if at is None else at

    def note_filler(self, phrase: str, at: float | None = None) -> None:
        self._filler_phrase = str(phrase)

    def note_interrupted(self, at: float | None = None) -> None:
        self._interrupted = True

    def note_turn_complete(self, at: float | None = None) -> dict | None:
        """Finalize this turn and return its summary. A second
        call returns None — the turn is already on the ledger,
        and a straggler must not re-emit it."""
        if self._finalized:
            return None
        self._finalized = True
        self._summary = self.summary()
        return self._summary

    # -- reads -------------------------------------------------------
    @property
    def audio_seen(self) -> bool:
        return self._first_audio_at is not None

    @property
    def finalized(self) -> bool:
        return self._finalized

    @property
    def last_voice_at(self) -> float | None:
        return self._last_voice_at

    def _ms(self, start: float | None, end: float | None) -> float | None:
        if start is None or end is None:
            return None
        return (end - start) * 1000.0

    def summary(self) -> dict[str, Any]:
        """The turn's numbers, as known so far. first_sound_ms
        is measured from the last locally-observed voice chunk —
        the server's endpointing instant is not visible locally,
        so this is the honest local proxy, labelled as such in
        `basis`."""
        first_sound = self._ms(self._last_voice_at, self._first_audio_at)
        return {
            "first_sound_ms": first_sound,
            "local_ms": self._ms(
                self._first_audio_at, self._first_enqueue_at),
            "tool_wait_ms": self._ms(
                self._tool_batch_start_at, self._tool_results_at),
            "first_sound_after_tools_ms": self._ms(
                self._tool_results_at,
                self._first_audio_after_tools_at),
            "filler": self._filler_phrase,
            "interrupted": bool(self._interrupted),
            "within_budget": (
                None if first_sound is None
                else first_sound <= FIRST_SOUND_BUDGET_MS),
            "basis": ("last locally-observed voice chunk to first "
                      "server audio chunk — server endpointing and "
                      "model thinking are not visible locally"),
        }

    def reset(self) -> None:
        self._reset_state()


class LatencyLedger:
    """Ring of recent finalized turn summaries (newest last),
    for the Doctor / status surface. Bounded; never grows."""

    def __init__(self, maxlen: int = 24):
        self._turns: collections.deque = collections.deque(maxlen=maxlen)

    def add(self, summary: dict[str, Any] | None) -> None:
        if summary is not None:
            self._turns.append(summary)

    def recent(self) -> list[dict[str, Any]]:
        return list(self._turns)

    def worst_first_sound_ms(self) -> float | None:
        vals = [t["first_sound_ms"] for t in self._turns
                if t.get("first_sound_ms") is not None]
        return max(vals) if vals else None

    def over_budget_count(self) -> int:
        return sum(1 for t in self._turns
                   if t.get("within_budget") is False)
