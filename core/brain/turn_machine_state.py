from __future__ import annotations

# INTENTIONAL (skills split B20): turn-state family (VP-2.5/2.6 constants, TurnStateMachine, directedness gate) moved here verbatim;
# the facade re-exports every name below.
import re
import threading
import time

# ---------------------------------------------------------------------------
# VP-2.5 / VP-2.6 — turn state machine constants
# ---------------------------------------------------------------------------

#: False-interruption pause-and-resume window (seconds). When a
#: barge-in is judged FALSE, assistant playback pauses and the
#: user gets exactly this long to turn it into a real turn;
#: silence past the window resumes playback from the pause point.
FALSE_INTERRUPTION_RESUME_WINDOW_S = 2.0

#: Backchannel cooldown (seconds). A backchannel ("mm-hmm",
#: "yeah", "uh-huh") while she speaks is acknowledged at most
#: once per window; further backchannels inside it are ignored
#: rather than interrupting or re-triggering anything.
BACKCHANNEL_COOLDOWN_S = 3.0

#: Adaptive interruption floor: each false barge-in inside the
#: decay window raises the floor by this fraction of the base.
ADAPTIVE_FLOOR_PER_FALSE = 0.25

#: False barge-ins older than this stop raising the floor.
ADAPTIVE_FLOOR_DECAY_WINDOW_S = 60.0

#: Hard cap on the adaptive floor, as a multiple of the base.
ADAPTIVE_FLOOR_CAP_MULTIPLE = 2.0

#: Follow-up window (seconds) for the directedness gate: a turn
#: arriving within this long after a directed turn ended still
#: counts as directed (the user is continuing the conversation).
DIRECTED_FOLLOW_UP_WINDOW_S = 20.0

#: States of the turn machine. PAUSED_FALSE_INTERRUPTION is the
#: only state in which playback is paused but the turn is NOT
#: the user's yet — the 2 s window decides which way it goes.
STATE_IDLE = "idle"
STATE_USER_SPEAKING = "user_speaking"
STATE_ASSISTANT_SPEAKING = "assistant_speaking"
STATE_PAUSED_FALSE_INTERRUPTION = "paused_false_interruption"

_BACKCHANNEL_WORDS = frozenset({
    "mm-hmm", "mmhmm", "uh-huh", "uhhuh", "yeah", "yep", "mhm",
    "okay", "ok", "right", "hmm",
})


def is_backchannel_text(text: str) -> bool:
    """True when a short transcript looks like a backchannel
    rather than a turn: one or two words, all from the
    backchannel set. The detector owns audio classification;
    this is the text-side twin used by tests and by callers
    that already have a transcript."""
    try:
        words = re.findall(r"[a-z]+(?:-[a-z]+)?", str(text or "").lower())
    except Exception:  # noqa: BLE001 — never raise on text
        return False
    return bool(words) and len(words) <= 2 and all(
        w in _BACKCHANNEL_WORDS for w in words)


class TurnStateMachine:
    """Behaviour layer above the turn detector (VP-2.5 + VP-2.6).

    Callers feed events; the machine returns plain action
    strings the caller maps onto real effects:

    - ``"pause_playback"`` — pause her audio, keep the position.
    - ``"resume_playback"`` — resume from the pause point.
    - ``"yield_to_user"`` — a genuine interruption: stop her
      audio and serve the user's turn.
    - ``"ignore"`` — backchannel inside cooldown, or an event
      with no effect in the current state.
    - ``"none"`` — state changed, no playback action needed.

    The machine never touches audio itself, so it cannot
    accidentally cut a sentence: the caller applies the action.
    """

    def __init__(self, *, base_interruption_rms: float = 0.008,
                 now_fn=None):
        try:
            self._base_rms = float(base_interruption_rms)
        except (TypeError, ValueError):
            self._base_rms = 0.008
        if not (self._base_rms > 0):
            self._base_rms = 0.008
        self._now = now_fn or time.monotonic
        self.state = STATE_IDLE
        self._pause_deadline: float | None = None
        self._last_backchannel_at: float | None = None
        self._false_barge_times: list[float] = []
        # RLock, not Lock: stats() holds it while calling
        # interruption_floor(), which also locks. A plain Lock
        # deadlocked there (caught by the v5.58 test suite).
        self._lock = threading.RLock()
        # Counters the §10 evaluation reads (T5/T6): cut-offs
        # are genuine interruptions that ended her sentence;
        # false barge-ins are the ones that paused and resumed.
        self.cut_off_count = 0
        self.false_barge_count = 0
        self.resumed_count = 0
        self.backchannels_ignored = 0

    # -- adaptive floor -------------------------------------------------
    def interruption_floor(self, now: float | None = None) -> float:
        """Current RMS floor a barge-in must clear. Rises with
        recent false barge-ins, decays as they age out, and is
        capped at 2x the base so adaptation can never deafen
        her to a real interruption."""
        at = self._now() if now is None else float(now)
        with self._lock:
            cutoff = at - ADAPTIVE_FLOOR_DECAY_WINDOW_S
            self._false_barge_times = [
                t for t in self._false_barge_times if t >= cutoff]
            recent = len(self._false_barge_times)
        multiple = min(1.0 + ADAPTIVE_FLOOR_PER_FALSE * recent,
                       ADAPTIVE_FLOOR_CAP_MULTIPLE)
        return self._base_rms * multiple

    # -- events ---------------------------------------------------------
    def on_assistant_started(self) -> str:
        with self._lock:
            self.state = STATE_ASSISTANT_SPEAKING
            self._pause_deadline = None
        return "none"

    def on_assistant_finished(self) -> str:
        with self._lock:
            self.state = STATE_IDLE
            self._pause_deadline = None
        return "none"

    def on_user_speech_start(self, now: float | None = None) -> str:
        """User speech began. From PAUSED_FALSE_INTERRUPTION
        this is the window converting into a real turn; from
        ASSISTANT_SPEAKING without a barge-in classification it
        is treated as a genuine turn (the detector, not this
        machine, classifies barge-ins — see on_barge_in)."""
        with self._lock:
            if self.state == STATE_PAUSED_FALSE_INTERRUPTION:
                self.state = STATE_USER_SPEAKING
                self._pause_deadline = None
                return "yield_to_user"
            self.state = STATE_USER_SPEAKING
            self._pause_deadline = None
        return "none"

    def on_user_speech_end(self) -> str:
        with self._lock:
            if self.state == STATE_USER_SPEAKING:
                self.state = STATE_IDLE
        return "none"

    def on_barge_in(self, *, genuine: bool, rms: float = 0.0,
                    now: float | None = None) -> str:
        """A barge-in classified by the detector while she
        speaks. ``genuine=False`` is the false-interruption
        path: pause, do NOT cut her sentence off, and let the
        2 s window decide (see ``tick``). A genuine barge-in
        below the adaptive floor is also treated as false —
        a loud room alone must not cut her off (this is the
        adaptive floor doing its job, and it is counted as a
        false barge-in for the §10 T6 tally)."""
        at = self._now() if now is None else float(now)
        try:
            level = float(rms)
        except (TypeError, ValueError):
            level = 0.0
        with self._lock:
            if self.state not in (STATE_ASSISTANT_SPEAKING,
                                  STATE_PAUSED_FALSE_INTERRUPTION):
                return "ignore"
            if genuine and level >= self._current_floor_locked(at):
                self.state = STATE_USER_SPEAKING
                self._pause_deadline = None
                self.cut_off_count += 1
                return "yield_to_user"
            # False path (judged false, or too quiet to count).
            self.false_barge_count += 1
            self._false_barge_times.append(at)
            if self.state == STATE_PAUSED_FALSE_INTERRUPTION:
                # Already paused: refresh the window, do not
                # double-pause (a second pause is a no-op).
                self._pause_deadline = at + FALSE_INTERRUPTION_RESUME_WINDOW_S
                return "ignore"
            self.state = STATE_PAUSED_FALSE_INTERRUPTION
            self._pause_deadline = at + FALSE_INTERRUPTION_RESUME_WINDOW_S
            return "pause_playback"

    def _current_floor_locked(self, at: float) -> float:
        cutoff = at - ADAPTIVE_FLOOR_DECAY_WINDOW_S
        self._false_barge_times = [
            t for t in self._false_barge_times if t >= cutoff]
        multiple = min(
            1.0 + ADAPTIVE_FLOOR_PER_FALSE * len(self._false_barge_times),
            ADAPTIVE_FLOOR_CAP_MULTIPLE)
        return self._base_rms * multiple

    def on_backchannel(self, now: float | None = None) -> str:
        """A backchannel arrived while she speaks. Inside the
        cooldown it is ignored outright; outside it, it is
        noted (for the T5/T6 tally the caller reads) and her
        speech is untouched — a backchannel never pauses,
        resumes, or cuts anything off."""
        at = self._now() if now is None else float(now)
        with self._lock:
            if (self._last_backchannel_at is not None
                    and at - self._last_backchannel_at < BACKCHANNEL_COOLDOWN_S):
                self.backchannels_ignored += 1
                return "ignore"
            self._last_backchannel_at = at
        return "none"

    def tick(self, now: float | None = None) -> str:
        """Drive the 2 s pause window. Returns
        ``"resume_playback"`` exactly once when a paused false
        interruption expires in silence — the §10 T6 resume
        path. Callers should call this on a timer while
        paused; it is harmless in every other state."""
        at = self._now() if now is None else float(now)
        with self._lock:
            if (self.state == STATE_PAUSED_FALSE_INTERRUPTION
                    and self._pause_deadline is not None
                    and at >= self._pause_deadline):
                self.state = STATE_ASSISTANT_SPEAKING
                self._pause_deadline = None
                self.resumed_count += 1
                return "resume_playback"
        return "none"

    def pause_deadline(self) -> float | None:
        with self._lock:
            return self._pause_deadline

    def stats(self) -> dict:
        """§10 T5/T6 tallies, as plain numbers."""
        with self._lock:
            total_barges = self.cut_off_count + self.false_barge_count
            resume_rate = (self.resumed_count / self.false_barge_count
                           if self.false_barge_count else 1.0)
            return {
                "state": self.state,
                "cut_off_count": self.cut_off_count,
                "false_barge_count": self.false_barge_count,
                "resumed_count": self.resumed_count,
                "false_barge_in_resume_rate": resume_rate,
                "total_barge_ins": total_barges,
                "backchannels_ignored": self.backchannels_ignored,
                "interruption_floor_rms": self.interruption_floor(),
            }


# ---------------------------------------------------------------------------
# VP-2.6 — directedness gate
# ---------------------------------------------------------------------------

#: Outcomes of the directedness gate. ``respond`` serves the
#: turn; ``stand_down`` means ambient speech that was never
#: addressed to her — she stays silent, always (ambient-never-
#: respond is unconditional, not a threshold).
DIRECTED_RESPOND = "respond"
DIRECTED_STAND_DOWN = "stand_down"


class DirectednessGate:
    """Was that speech FOR her? (VP-2.6, original VYUK pattern.)

    A turn is directed when ANY of these holds:
    - the wake word fired for it,
    - push-to-talk is held (the user physically driving),
    - it was typed (typing is always directed),
    - it explicitly addresses her (name / "hey VYUK" — the
      caller supplies ``addressed``; this gate does not parse
      audio),
    - it arrives inside the follow-up window after a directed
      turn ended (continuing the conversation needs no new
      wake word).

    Everything else is ambient and stands down — including
    loud, clear, perfectly transcribed ambient speech. There
    is no confidence threshold that lets ambient speech
    through: ambient-never-respond is the rule, not a tuning.
    """

    def __init__(self, *, follow_up_window_s: float = DIRECTED_FOLLOW_UP_WINDOW_S,
                 now_fn=None):
        try:
            self._window = float(follow_up_window_s)
        except (TypeError, ValueError):
            self._window = DIRECTED_FOLLOW_UP_WINDOW_S
        self._now = now_fn or time.monotonic
        self._last_directed_turn_end: float | None = None
        self._lock = threading.Lock()
        self.responded_count = 0
        self.stood_down_count = 0

    def note_directed_turn_ended(self, now: float | None = None) -> None:
        at = self._now() if now is None else float(now)
        with self._lock:
            self._last_directed_turn_end = at

    def decide(self, *, wake_word: bool = False, ptt_active: bool = False,
               typed: bool = False, addressed: bool = False,
               now: float | None = None) -> str:
        at = self._now() if now is None else float(now)
        with self._lock:
            in_window = (
                self._last_directed_turn_end is not None
                and 0 <= at - self._last_directed_turn_end <= self._window)
        directed = bool(wake_word or ptt_active or typed or addressed or in_window)
        with self._lock:
            if directed:
                self.responded_count += 1
            else:
                self.stood_down_count += 1
        return DIRECTED_RESPOND if directed else DIRECTED_STAND_DOWN

    def stats(self) -> dict:
        with self._lock:
            total = self.responded_count + self.stood_down_count
            return {
                "responded": self.responded_count,
                "stood_down": self.stood_down_count,
                "stood_down_rate": (self.stood_down_count / total
                                    if total else 0.0),
                "follow_up_window_s": self._window,
            }


