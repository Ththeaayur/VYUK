"""Wave 1 (v6 build flow) — voice -> text-loop bridge.

A voice session hands a goal to the text loop and narrates the
report back. Design: ~/workspace/code-lab/wave1_bridge_design.md.

Seams (measured, not assumed):
- Text loop: ``core.brain.planner.run_goal`` — one picked action at a time,
  budgets 25 steps / 600 s / 3 strikes, failsafe + epoch before every
  step, every action through registry ``execute()`` with the full
  gate stack (C-14 included). This module does NOT reimplement it.
- Text brains: ``core.cloud_text_client.GroqSession.run_goal`` and
  ``core.local_client.LocalLiveSession.run_goal`` — real pickers,
  one chat call per step, validated against tool declarations.
- Voice brain: ``core.live_client.GeminiLiveSession.run_goal`` —
  declared-plan match via ``run_plan``, otherwise an honest refusal
  (no picker invented, no action guessed). The bridge is called
  from that method, after the plan match and before the refusal.

Flag discipline (cf. ``core.context`` run_goal ship flag,
default OFF): the bridge is OFF unless explicitly enabled with
``enable_voice_bridge()``. Wiring Build 1 adds a Settings row
(``gui/window_settings.py``, copying the wake toggle's pattern)
that calls enable/disable and shows the state — session state
only, exactly like wake, which has no persistence: every launch
starts OFF and the user must toggle it on again. No auto-enable,
no config mutation. The text brain is INJECTED, never created here:
no model choice, no API keys, no new weights, no new engine, no
new dependency. Stdlib only. A voice brain can never be its own
text brain: ``hand_off`` refuses (returns ``None``) when the
resolved brain is the voice session itself or a GeminiLiveSession,
so a mis-injection cannot recurse through the bridge.

C-14: the bridge itself takes no consequential action. Every action
the text loop takes passes ``planner.run_goal``'s gate stack. The
bridge grants no screen scope, clears no taint, and bypasses no gate.
Narration is a transcript line, not a consequential action.

Narration limit (stated, not hidden): narration goes through the
voice session's ``on_transcript("assistant", report)`` — the same
channel ``run_plan``/``run_goal`` progress uses, folded by the GUI
into the goal pill and an assistant bubble. Injecting transcript
text does NOT synthesize Live audio, and no new TTS engine is added
here; spoken delivery on his machine is a Wave 1 field item, owed.

Refusal honesty: when the bridge is OFF or no text brain is
available, ``hand_off`` returns ``None`` so the caller falls through
to its existing honest refusal unchanged. An empty goal, a brain
without ``run_goal``, or a brain that raises all produce honest
reports — never a guessed action, never a fake "done".

Gemini-mode gate (VOICE_FREEFORM_COMPANION_GATE, 2026-11-03): in the
shipped single-engine app, no text brain can arrive through
production wiring — the app binds the Gemini singleton as its only
inner session, the Settings toggle's attach is skipped by identity
and would be refused by type name, no production code assigns a
per-session text brain, and no second session is ever created.
The bridge + adoption flow end to end whenever a text brain IS
injected (lab/tests, or a future companion session); until one of
the gate's unblockers lands, the toggle alone cannot open the loop
in Gemini mode, and the shipped path stays saved plans + honest
refusal for free-form goals. See the constant below.
"""

from __future__ import annotations

import inspect

from ..provider_identity import Engine  # GAPS-22 (B-V3): engine identity

__all__ = [
    "VOICE_FREEFORM_COMPANION_GATE",
    "enable_voice_bridge",
    "disable_voice_bridge",
    "is_voice_bridge_enabled",
    "set_text_brain",
    "get_text_brain",
    "attach_text_brain_candidate",
    "resolve_text_brain",
    "run_goal_accepts_interruption_check",
    "narrate_report",
    "hand_off",
]

# Voice free-form loop gate, named 2026-11-03 (code-rulings
# delegation) — the reconciliation of the two programme records:
# the bridge + coordinator adoption DO flow end to end when a text
# brain is injected (lab/tests prove it), but the programme record
# calling the loop a build gap is ALSO true in the shipped app,
# because in Gemini mode no text brain can arrive through
# production wiring at all. This constant states the gap precisely
# so it cannot drift back into "the loop exists" shorthand.
VOICE_FREEFORM_COMPANION_GATE = (
    "Voice free-form goals on the Gemini voice brain are GATED, not "
    "built: the Wave 1 bridge + coordinator adoption "
    "(voice_text_bridge.py, voice_goal.py) flow end to end ONLY when "
    "a compatible text brain is injected, and in the shipped "
    "single-engine app no text brain can ever arrive through "
    "production wiring — the app binds the Gemini singleton as its "
    "only inner session (core/brain/brain.py start(), 'reuse the "
    "singleton — never a second one'), so the Settings toggle's "
    "attach is skipped by identity (gui/window_settings_voice.py) "
    "and would be refused by type name "
    "(attach_text_brain_candidate), no production code assigns a "
    "per-session text_brain attribute, and no second session is "
    "ever created in gui/. The Lite tier cannot substitute: "
    "LiteLiveSession has no run_goal (no picker). Unblockers, any "
    "one: (a) a companion Groq session started alongside Gemini "
    "(needs a Groq key configured), (b) a companion local session "
    "started alongside Gemini (needs the landed local model), or "
    "(c) a Gemini text-chat picker built on the existing genai "
    "client (a new brain implementation, not wiring). Until one "
    "lands, the shipped path is: saved plans run their DECLARED "
    "steps via run_plan, and free-form voice goals refuse honestly "
    "(live_client.py run_goal refusal, byte-identical with the "
    "bridge off). Building a voice entry that routes spoken "
    "utterances to run_goal is NOT part of this gate's unblockers: "
    "it would be a new voice execution path, and no production "
    "caller of GeminiLiveSession.run_goal exists today."
)

_ENABLED = False
_TEXT_BRAIN = None


def enable_voice_bridge() -> None:
    """Explicit opt-in for the voice -> text-loop bridge (default OFF)."""
    global _ENABLED
    _ENABLED = True


def disable_voice_bridge() -> None:
    """Explicit opt-out; restores the pre-bridge refusal behaviour."""
    global _ENABLED
    _ENABLED = False


def is_voice_bridge_enabled() -> bool:
    """Whether the bridge is currently enabled (default False)."""
    return bool(_ENABLED)


def set_text_brain(brain) -> None:
    """Inject the text brain whose ``run_goal`` drives the loop.

    ``brain`` may be an object with an async ``run_goal(goal_text)``
    method (the Groq/local client entries) or a callable with that
    shape. Pass ``None`` to clear the injection. This module never
    creates a brain, picks a model, or touches an API key.
    """
    global _TEXT_BRAIN
    _TEXT_BRAIN = brain


def get_text_brain():
    """The injected text brain, or ``None`` when none is injected."""
    return _TEXT_BRAIN


def attach_text_brain_candidate(candidate) -> bool:
    """Wiring Build 1: the Settings toggle's Qt-free attach logic.

    Attaches ``candidate`` as the text brain when it is a usable
    text brain (has a callable ``run_goal``) and is NOT a voice
    brain — a GeminiLiveSession's ``run_goal`` is the caller of
    ``hand_off`` itself, so injecting one would recurse. Returns
    True when attached, False otherwise (nothing is attached on
    failure, and a previous injection is left untouched). The GUI
    toggle calls this with the app session's existing inner
    session; it never creates a brain, picks a model, or touches
    a key — if no usable inner session exists, the toggle still
    flips the flag, but voice goals refuse honestly until a
    Groq or on-PC brain session is running.
    """
    if candidate is None:
        return False
    # GAPS-22 (B-V3): a Gemini session is identified by its
    # engine, never by its class name.
    if getattr(candidate, "engine", None) is Engine.GEMINI:
        return False
    if not callable(getattr(candidate, "run_goal", None)):
        return False
    set_text_brain(candidate)
    return True


def _resolve_brain(voice_session, explicit):
    """Resolve the text brain: explicit arg, then injected, then the
    voice session's own ``text_brain`` attribute when present.
    Returns ``None`` when no candidate exists."""
    if explicit is not None:
        return explicit
    if _TEXT_BRAIN is not None:
        return _TEXT_BRAIN
    try:
        return getattr(voice_session, "text_brain", None)
    except Exception:  # noqa: BLE001 — a broken session attr is no brain
        return None


def resolve_text_brain(voice_session, explicit=None):
    """Public form of _resolve_brain: the text brain hand_off would use.

    Resolution order: explicit arg, then the injected brain, then the
    voice session's own ``text_brain`` attribute when present. Returns
    ``None`` when no candidate exists. The voice-brain adoption wrapper
    (core/brain/voice_goal.py) uses this to decide, before running anything,
    whether a goal can run as a coordinator task at all.
    """
    return _resolve_brain(voice_session, explicit)


def run_goal_accepts_interruption_check(brain) -> bool:
    """Whether a brain's run_goal accepts an interruption_check kwarg.

    True when the resolved run_goal callable declares the parameter or
    accepts **kwargs. Any inspection failure answers False (legacy
    path: the loop runs without voice-side interruption, and the
    caller must record that instead of claiming cancellation support).
    """
    try:
        target = getattr(brain, "run_goal", None)
        if target is None and callable(brain):
            target = brain
        if target is None or not callable(target):
            return False
        params = inspect.signature(target).parameters
        return ("interruption_check" in params
                or any(p.kind == inspect.Parameter.VAR_KEYWORD
                       for p in params.values()))
    except (TypeError, ValueError):
        return False


def _brain_run_goal(brain, goal_text, run_kwargs=None):
    """Call a brain's ``run_goal`` (method or callable) and await it
    when awaitable. Returns ``(report, error)`` with exactly one set."""
    run_goal = None
    if brain is not None:
        run_goal = getattr(brain, "run_goal", None)
        if run_goal is None and callable(brain):
            run_goal = brain
    if run_goal is None or not callable(run_goal):
        return None, ("The text brain supplied has no run_goal to "
                      "drive the loop with, so nothing was run.")
    return run_goal(goal_text, **(run_kwargs or {})), None


def narrate_report(voice_session, report: str) -> bool:
    """Narrate a report through the voice session's transcript seam.

    Returns True when an ``on_transcript`` callback accepted the
    line, False otherwise. A narration failure never rewrites the
    report itself; the caller still returns the report.
    """
    try:
        callback = getattr(voice_session, "on_transcript", None)
    except Exception:  # noqa: BLE001 — a broken session is no narration
        return False
    if not callable(callback):
        return False
    try:
        callback("assistant", report)
    except Exception:  # noqa: BLE001 — narration is a nicety, not a gate
        return False
    return True


async def hand_off(voice_session, goal_text, *, text_brain=None,
                  interruption_check=None):
    """Hand a voice goal to the text loop and narrate the report back.

    Returns ``None`` when the bridge is OFF or no text brain is
    available — the caller then falls through to its existing honest
    refusal unchanged. Otherwise returns a handoff note plus the
    text-loop report VERBATIM; the note never claims completion,
    and a halted/stopped report stays halted/stopped.

    Voice-brain adoption: ``interruption_check`` is forwarded to the
    text brain's run_goal ONLY when that run_goal accepts it (see
    run_goal_accepts_interruption_check) — the loop then halts at
    the next step boundary when the check fires (coordinator cancel
    or session stop). For a legacy brain that does not accept it,
    the loop runs without voice-side interruption and the caller
    must record that instead of claiming cancellation support.
    """
    if not is_voice_bridge_enabled():
        return None
    text = str(goal_text or "").strip()
    if not text:
        return "Cannot run a goal: no goal text was given."
    brain = _resolve_brain(voice_session, text_brain)
    if brain is None:
        return None
    # A voice brain can never be its own text brain: its run_goal
    # is the caller of this function, so driving the loop with it
    # would recurse. Refuse (None) instead, exactly as if no brain
    # were available — the caller then gives its honest refusal.
    if brain is voice_session or getattr(brain, "engine", None) is Engine.GEMINI:
        return None
    run_kwargs = {}
    if (interruption_check is not None
            and run_goal_accepts_interruption_check(brain)):
        run_kwargs["interruption_check"] = interruption_check
    result, error = _brain_run_goal(brain, text, run_kwargs)
    if error is not None:
        return error
    try:
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001 — a failed loop is reported
        return (f"The text loop failed on this goal ({exc}), so the "
                "goal did not complete. Nothing was claimed as done.")
    report = str(result or "")
    note = ("Handed to the text loop (voice -> text-loop bridge, "
            "Wave 1): the report below is the text loop's own "
            "report, unchanged.\n")
    full = f"{note}{report}"
    narrate_report(voice_session, full)
    return full
