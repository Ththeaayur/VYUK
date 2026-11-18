"""Wave 3 (v5.58.0) turn surfaces for the main window.

VP-8.3 PTT peer surface, VP-8.4 dictation surface, VP-8.1
failover announcement surface, VP-8.2 ducking surface and
VP-8.6 confirmation-prompt surface — as a duck-typed mixin
that operates on whatever the window already has.

WIRING NOTE (coordinator): this mixin is NOT yet in the
MainWindow bases — adding it is a one-line change to
gui/main_window.py's mixin list, deliberately left to the
coordinator because other Wave 3 workers are editing the
window tree in parallel. Until then every method here is
still fully testable against a fake window (see
test_v558_turns.py), and nothing in the shipped window
changes behaviour. The existing PTT button (btn_mic, wired
in gui/window_shell.py to _on_ptt_pressed) is untouched;
this mixin ADDS the peer-surface state (first-partial
budget, directedness) around it instead of rewiring it.

Deliberately Qt-free: no PySide import, so the surfaces
can be tested without a GUI. ``_add_system`` (chat line)
is called only when the host window provides it, and every
method degrades to returning its string when it does not.

The spoken diagnostic notice is deliberately NOT surfaced
here as a speak action: see
``spoken_diagnostic_notice_status`` — the v5.57.1 notice
stays chat-only (evaluation verdict: do_not_speak).
"""

from __future__ import annotations

try:
    from voice_assistant.core.gui_facade import (  # package import style
        AdvisorySpeakerVerifier,
        DictationMode,
        DirectednessGate,
        MediaDucking,
        PushToTalk,
        VoiceFailoverGate,
        confirmation_shape,
        explicit_confirmation_prompt,
        evaluate_spoken_diagnostic_notice,
        DICTATION_BANNER_TEXT,
        DICTATION_ENTER_ANNOUNCEMENT,
        DICTATION_EXIT_ANNOUNCEMENT,
        PTT_BUTTON_LABEL,
        PTT_BUTTON_TOOLTIP,
    )
except Exception:  # noqa: BLE001 — install-root import style
    from core.gui_facade import (
        AdvisorySpeakerVerifier,
        DictationMode,
        DirectednessGate,
        MediaDucking,
        PushToTalk,
        VoiceFailoverGate,
        confirmation_shape,
        explicit_confirmation_prompt,
        evaluate_spoken_diagnostic_notice,
        DICTATION_BANNER_TEXT,
        DICTATION_ENTER_ANNOUNCEMENT,
        DICTATION_EXIT_ANNOUNCEMENT,
        PTT_BUTTON_LABEL,
        PTT_BUTTON_TOOLTIP,
    )


def _post(host, text: str) -> None:
    """Post a chat line when the host can, else silently skip
    (the returned string is still the record)."""
    if not text:
        return
    add = getattr(host, "_add_system", None)
    if callable(add):
        try:
            add(text)
        except Exception:  # noqa: BLE001 — posting never breaks state
            pass


class TurnSurfacesMixin:
    """GUI surfaces for Wave 3 turn behaviour.

    The host calls ``_turn_surfaces_init()`` once (typically
    from __init__); all other methods are safe to call even
    if it forgot — they lazily initialise. State lives on
    the host under ``_turn_*`` attributes so the window's
    existing state stays untouched.
    """

    def _turn_surfaces_init(self) -> None:
        if getattr(self, "_turn_surfaces_ready", False):
            return
        self._turn_dictation = DictationMode()
        self._turn_ptt = PushToTalk()
        self._turn_failover = VoiceFailoverGate()
        self._turn_ducking = MediaDucking()
        self._turn_directedness = DirectednessGate()
        self._turn_verifier = AdvisorySpeakerVerifier()
        self._turn_surfaces_ready = True

    def _turn_ensure(self) -> None:
        if not getattr(self, "_turn_surfaces_ready", False):
            self._turn_surfaces_init()

    # -- PTT peer surface (VP-8.3) --------------------------------------
    @property
    def turn_ptt_label(self) -> str:
        return PTT_BUTTON_LABEL

    @property
    def turn_ptt_tooltip(self) -> str:
        return PTT_BUTTON_TOOLTIP

    def turn_ptt_pressed(self, now=None) -> bool:
        """Peer-surface press: records the press for the
        first-partial budget and marks the session directed.
        Returns whether PTT is now active. The host's
        existing _on_ptt_pressed (mic unmute) still runs
        separately — this method does not replace it."""
        self._turn_ensure()
        active = self._turn_ptt.press(now=now)
        return bool(active)

    def turn_ptt_released(self, now=None) -> bool:
        self._turn_ensure()
        return bool(self._turn_ptt.release(now=now))

    def turn_ptt_first_partial(self, now=None) -> dict:
        self._turn_ensure()
        return self._turn_ptt.mark_first_partial(now=now)

    # -- Dictation surface (VP-8.4) -------------------------------------
    def turn_dictation_toggle(self) -> dict:
        """Toggle dictation explicitly. Returns the
        announcement (posted in chat when possible), the new
        state, and the banner text. No other transition
        exists: dictation never toggles itself."""
        self._turn_ensure()
        if self._turn_dictation.active:
            announcement = self._turn_dictation.exit()
            active = False
        else:
            announcement = self._turn_dictation.enter()
            active = True
        _post(self, announcement)
        return {
            "active": active,
            "announcement": announcement,
            "banner_text": DICTATION_BANNER_TEXT if active else "",
        }

    def turn_dictation_banner(self) -> str:
        self._turn_ensure()
        return DICTATION_BANNER_TEXT if self._turn_dictation.active else ""

    def turn_dictation_route(self, text: str) -> tuple[str, str]:
        """Route transcribed text through dictation. While
        dictating this is always ("insert", text) — the host
        inserts it and executes nothing (T18 hard gate)."""
        self._turn_ensure()
        return self._turn_dictation.route_text(text)

    # -- Failover surface (VP-8.1) --------------------------------------
    def turn_announce_voice_failover(self, to_voice: str,
                                     *, mid_sentence: bool = False) -> dict:
        """Announce a voice failover (or queue it when
        mid-sentence). The announcement is posted in chat
        when the switch applies; a queued switch posts
        nothing until the turn boundary (see
        turn_note_turn_boundary)."""
        self._turn_ensure()
        result = self._turn_failover.request_failover(
            to_voice, mid_sentence=mid_sentence)
        if result.get("announcement"):
            _post(self, result["announcement"])
        return result

    def turn_note_turn_boundary(self) -> dict:
        """A turn ended: apply any queued failover, announced."""
        self._turn_ensure()
        result = self._turn_failover.note_turn_boundary()
        if result.get("announcement"):
            _post(self, result["announcement"])
        return result

    # -- Ducking surface (VP-8.2) ---------------------------------------
    def turn_register_media(self, stream_id: str, *, kind: str = "media") -> None:
        self._turn_ensure()
        self._turn_ducking.register(stream_id, kind=kind)

    def turn_media_level(self, stream_id: str,
                         *, assistant_speaking: bool) -> float:
        self._turn_ensure()
        return self._turn_ducking.level_for(
            stream_id, assistant_speaking=assistant_speaking)

    # -- Confirmation surface (VP-8.6) ----------------------------------
    def turn_confirmation_prompt(self, action: str, target: str,
                                 consequence: str, *, kind: str) -> dict:
        """Build the confirmation prompt for an action. For
        irreversible kinds the shape is explicit (restate
        what / to what / what cannot be undone); for plain
        kinds it is a plain yes/no question. The shape, not
        just the words, is the UX rule."""
        shape = confirmation_shape(kind)
        if shape == "explicit":
            prompt = explicit_confirmation_prompt(action, target, consequence)
        else:
            prompt = f"Just to check — shall I {action} {target}?"
        return {"shape": shape, "prompt": prompt, "kind": kind}

    # -- Spoken diagnostic notice (v5.57.1 gate) -------------------------
    def spoken_diagnostic_notice_status(self) -> dict:
        """Why the diagnostic notice stays chat-only. This is
        an evaluation report, NOT a speak action: there is
        deliberately no method here that speaks the notice."""
        return evaluate_spoken_diagnostic_notice()


__all__ = [
    "TurnSurfacesMixin",
    "DICTATION_BANNER_TEXT",
    "DICTATION_ENTER_ANNOUNCEMENT",
    "DICTATION_EXIT_ANNOUNCEMENT",
    "PTT_BUTTON_LABEL",
    "PTT_BUTTON_TOOLTIP",
]
