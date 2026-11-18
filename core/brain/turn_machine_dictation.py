from __future__ import annotations

# INTENTIONAL (skills split B20): dictation family (VP-8.4 announcements, DictationMode) moved here verbatim;
# the facade re-exports every name below.
import threading

# ---------------------------------------------------------------------------
# VP-8.4 — dictation mode (PILOT)
# ---------------------------------------------------------------------------

#: Announcement spoken/posted when dictation starts and ends.
#: The mode is EXPLICIT (the user turns it on), STICKY (it
#: survives across turns until explicitly turned off), and
#: ANNOUNCED (both transitions say so in plain words).
DICTATION_ENTER_ANNOUNCEMENT = (
    "Dictation on — I'll write down exactly what you say and "
    "won't act on any of it until you say dictation off.")
DICTATION_EXIT_ANNOUNCEMENT = (
    "Dictation off — back to normal. Nothing you dictated was "
    "acted on.")
DICTATION_BANNER_TEXT = (
    "Dictation on — your words are written down, not acted on.")

#: Dictation endpointing profile. SmartTurn is OFF inside
#: dictation: a 2 s pause mid-dictation is a thinking pause,
#: not an end of turn, so no end-of-turn prediction may fire
#: at 2 s (or at any silence below the dictation silence).
#: Only a long trailing silence, or an explicit stop, ends a
#: dictated segment.
DICTATION_TRAILING_SILENCE_S = 5.0
DICTATION_MAX_SEGMENT_S = 300.0


class DictationExecutionBlocked(Exception):
    """Raised when any execution path is attempted while
    dictation is active. Dictated text is output, never an
    action (INV-2: dictated-text insertion is state-changing
    output, quarantined to the mode)."""


class DictationMode:
    """Explicit, sticky, announced dictation (VP-8.4, PILOT).

    The hard guarantee (§10 T18, HARD GATE): while this mode
    is active, ZERO commands execute from dictated text — not
    even destructive ones, not even ones phrased as perfect
    commands. The enforcement is structural, not a filter:
    :meth:`route_text` returns ``("insert", text)`` and never
    calls an executor, and :meth:`guarded_execute` raises
    before any executor runs. There is no allow-list of
    "safe" dictated commands, because a filter is exactly
    what a destructive phrase would be written to slip past.

    Entry/exit are the ONLY transitions (no auto-entry from
    a transcript, no auto-exit on silence — stickiness is the
    point: a long pause must not silently flip dictation into
    execution). The mode also mirrors
    ``core/brain/turn_context.set_dictation_active`` so the existing
    echo guard sees the honest seam; the mode's own flag is
    authoritative if that sync ever fails.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._active = False
        self._segments: list[str] = []

    # -- transitions ----------------------------------------------------
    def enter(self) -> str:
        """Turn dictation on. Returns the announcement."""
        with self._lock:
            self._active = True
        self._sync_turn_context(True)
        return DICTATION_ENTER_ANNOUNCEMENT

    def exit(self) -> str:
        """Turn dictation off. Returns the announcement. The
        collected segments stay readable via ``segments()``;
        exiting never executes, sends, or files them."""
        with self._lock:
            self._active = False
        self._sync_turn_context(False)
        return DICTATION_EXIT_ANNOUNCEMENT

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def _sync_turn_context(self, active: bool) -> None:
        try:
            try:
                from . import turn_context as _tc
            except Exception:  # noqa: BLE001 — package import style
                from voice_assistant.core.brain import turn_context as _tc  # Phase 3 B1a-1: moved to core/brain/
            _tc.set_dictation_active(bool(active))
        except Exception:  # noqa: BLE001 — own flag is authoritative
            pass

    # -- endpointing ----------------------------------------------------
    def endpoint_profile(self) -> dict:
        """The profile the detector MUST use while dictating.
        ``smart_turn_enabled`` False is the whole point: the
        detector's 2 s end-of-turn prediction is suppressed,
        and the trailing silence that ends a segment is 5 s."""
        return {
            "smart_turn_enabled": False,
            "trailing_silence_s": DICTATION_TRAILING_SILENCE_S,
            "max_segment_s": DICTATION_MAX_SEGMENT_S,
            "smart_turn_suppressed_below_s": DICTATION_TRAILING_SILENCE_S,
        }

    def smart_turn_may_fire(self, trailing_silence_s: float) -> bool:
        """False for every silence below the dictation
        threshold — in particular False at 2 s. Callers that
        only have the normal detector path should ask this
        before firing any end-of-turn prediction."""
        if not self.active:
            return True
        try:
            return float(trailing_silence_s) >= DICTATION_TRAILING_SILENCE_S
        except (TypeError, ValueError):
            return False

    # -- routing (the T18 hard gate) ------------------------------------
    def route_text(self, text: str) -> tuple[str, str]:
        """Route transcribed text. While dictating this ALWAYS
        returns ``("insert", text)`` — the caller inserts the
        text and does nothing else. While not dictating it
        returns ``("execute", text)`` (normal turn handling).
        Dictated text is never returned as "execute"."""
        value = "" if text is None else str(text)
        if self.active:
            with self._lock:
                self._segments.append(value)
            return ("insert", value)
        return ("execute", value)

    def guarded_execute(self, executor, *args, **kwargs):
        """Call ``executor`` only when dictation is OFF. While
        dictation is ON this raises BEFORE the executor runs
        — the executor is never invoked, so it cannot have
        side effects, log lines, or partial runs."""
        if self.active:
            raise DictationExecutionBlocked(
                "Dictation is on: dictated text is never executed.")
        return executor(*args, **kwargs)

    def segments(self) -> list[str]:
        with self._lock:
            return list(self._segments)

    def full_text(self) -> str:
        with self._lock:
            return "\n".join(self._segments)

    def clear_segments(self) -> None:
        with self._lock:
            self._segments.clear()


