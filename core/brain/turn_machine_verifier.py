from __future__ import annotations

# INTENTIONAL (skills split B20): verifier family (VP-7.7/7.8 advisory verifier, spoken notice) moved here verbatim;
# the facade re-exports every name below.
import threading

# ---------------------------------------------------------------------------
# VP-7.7/7.8 — advisory speaker verifier (fail-open, advisory only)
# ---------------------------------------------------------------------------

class AdvisorySpeakerVerifier:
    """Borrowed enrolment pattern, shipped fail_open (VP-7.8).

    Contract (§8 B-21), enforced by construction, not policy:
    - :meth:`may_authorize` ALWAYS returns False. The verifier
      may modulate (suggest a gain nudge, note a mismatch) but
      it can never authorise a confirmation, a destructive
      action, or an identity claim. There is no code path in
      this class that returns True for authorisation.
    - :meth:`score` is advisory only: ``fail_open=True`` is
      baked in, so an unavailable model, a missing enrolment,
      or a low score degrades to "unknown speaker, proceed
      normally" — never to a lockout, and never to a bypass
      of the confirmation gate (which is a separate gate
      this class cannot touch).
    - INV-4: PTT strengthens consent; this verifier does not
      weaken it. A PTT turn is directed regardless of score.
    """

    fail_open = True

    def __init__(self):
        self._lock = threading.Lock()
        self._last_score: float | None = None

    def may_authorize(self, *args, **kwargs) -> bool:
        """Always False. This is the contract, not a default
        that a caller can flip: no argument changes it."""
        return False

    def score(self, *, enrolled: bool = False,
              similarity: float | None = None) -> dict:
        """Advisory similarity reading. ``similarity`` is
        supplied by the caller (this class runs no model);
        without one the reading is "unknown" and fail-open."""
        try:
            value = None if similarity is None else float(similarity)
        except (TypeError, ValueError):
            value = None
        with self._lock:
            self._last_score = value
        if value is None or not enrolled:
            return {"verdict": "unknown", "fail_open": True,
                    "modulation": "none",
                    "authorizes": False}
        if value >= 0.8:
            modulation = "none"
        elif value >= 0.5:
            modulation = "suggest_gain_check"
        else:
            modulation = "note_mismatch_only"
        return {"verdict": "advisory", "similarity": value,
                "fail_open": True, "modulation": modulation,
                "authorizes": False}

    def last_score(self) -> float | None:
        with self._lock:
            return self._last_score


# ---------------------------------------------------------------------------
# Spoken diagnostic notice — EVALUATION ONLY (v5.57.1 gate)
# ---------------------------------------------------------------------------

#: The v5.57.1 no-response diagnostic notice is chat-only
#: because no window-level spoken route was found. This
#: function evaluates speaking it via the system-voice path
#: (core/skills_plus.speak_text, which gui/window_widgets.py
#: already calls directly for "Read aloud"). Verdict: DO NOT
#: SPEAK. The reasons are structural, not a missing feature:
#: see ``evaluate_spoken_diagnostic_notice``.
SPOKEN_NOTICE_VERDICT_DO_NOT_SPEAK = "do_not_speak"


def evaluate_spoken_diagnostic_notice() -> dict:
    """Evaluate speaking the v5.57.1 diagnostic notice via
    the system-voice path. Returns the verdict plus the
    reasoning, so the coordinator (and the user) can see
    exactly why the chat-only delivery stands.

    (a) Mic-echo / feedback risk — DECISIVE. The notice
    fires while the mic is live, during a watchdog
    reconnect. Speaking it through the room speakers puts
    the notice's own words back into the mic. The mic
    transmission queue IS gated by audio_manager.is_muted
    (core/voice/audio.py), but the wake-word ring buffer is NOT —
    it records regardless of mute — so a mute window cannot
    fully suppress re-ingestion, and the mute flag is shared
    with PTT/mute, so a mistimed restore (or a crash between
    mute and restore) leaves the mic deaf, while an early
    restore leaks notice audio into the next turn as if the
    user said it. Closing that hole needs a transcription-
    suppression window in the Live send loop — a shared-
    interface change outside this workstream's ownership —
    so it cannot be made clean here.

    (b) Local-TTS availability — NOT GUARANTEED. pyttsx3 is
    a system-voice path, distinct from Kokoro/Piper, but it
    is not self-contained: on Windows it drives SAPI
    (built in), on Linux it needs an espeak-ng binary
    installed on the box, on macOS NSSpeech. A machine
    without the local runtimes may also lack espeak-ng, so
    the spoken notice would silently degrade to nothing on
    exactly the boxes where the chat notice is the reliable
    channel — while on boxes where it does speak, risk (a)
    applies. Silent degradation is acceptable for "Read
    aloud" (an on-demand nicety); it is not acceptable as
    the delivery channel for a diagnostic the user must see.

    (c) Loop risk. The notice fires at watchdog streak 3
    precisely because the Live session just died unanswered.
    Speaking through a fresh reconnect is an unasked turn on
    a session that has just demonstrated it cannot answer
    turns — the exact loop the v5.57.1 gate avoided by
    staying chat-only. A bounded design (once per streak,
    never loops, never blocks the GUI thread — pyttsx3
    already runs on a daemon thread in skills_plus — and
    silent degrade) bounds the spam but does NOT bound the
    echo or the unasked-turn problem.

    Conclusion: keep the notice chat-only. Do NOT promise a
    spoken version. If a future wave wants one, it needs the
    Live-loop transcription-suppression window first, and a
    speaker-activity check (don't speak while the user is
    already speaking) second.
    """
    try:
        import pyttsx3  # noqa: F401
        pyttsx3_available_here = True
    except Exception:  # noqa: BLE001
        pyttsx3_available_here = False
    return {
        "verdict": SPOKEN_NOTICE_VERDICT_DO_NOT_SPEAK,
        "implemented": False,
        "pyttsx3_importable_here": pyttsx3_available_here,
        "blockers": [
            "echo: notice audio re-enters the live mic; "
            "audio_manager.is_muted gates mic_queue but NOT "
            "the wake-word ring buffer, and the mute flag is "
            "shared with PTT/mute (restore race can deafen "
            "the mic or leak notice audio as a user turn)",
            "availability: pyttsx3 needs a platform speech "
            "backend (SAPI on Windows, espeak-ng binary on "
            "Linux, NSSpeech on macOS) — not guaranteed on a "
            "machine without the local runtimes, so spoken "
            "delivery would silently vanish where it is "
            "needed most",
            "loop: speaking is an unasked turn on a session "
            "the watchdog just declared dead; a fresh "
            "reconnect speaking unasked can watchdog again",
        ],
        "bounded_design_if_it_were_clean": {
            "fires": "once per watchdog streak",
            "loops": False,
            "blocks_gui_thread": False,
            "degrades": "silently if speech unavailable",
        },
        "prerequisite_for_reconsideration": (
            "a transcription-suppression window in the Live "
            "send loop, plus a speaker-activity check"),
        "delivery_that_stands": "chat-only diagnostic notice "
        "(v5.57.1, gui/window_session.py)",
    }


