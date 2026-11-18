"""Capability contract per brain (identity-gap P1).

A single, machine-checkable place stating what each brain can honestly
do — and, just as importantly, what it cannot. The contract DESCRIBES
brains; it does not upgrade, downgrade, or rewire them. Every entry
carries its evidence: the module and function the claim traces to, read
from the tree. A claim without a code citation does not belong here.

Brains (surveyed from the tree, none invented):

* ``gemini_live`` — GeminiLiveSession (core/voice/live_client.py), reached
  through the core/brain/brain.py BrainSession proxy. The voice brain.
* ``local_text`` — LocalLiveSession (core/voice/local_client.py). Text brain.
* ``groq_text`` — GroqSession (core/voice/cloud_text_client.py). Text brain,
  instantiated on the cloud path (core/brain/brain.py, cloud start).
* ``lite_first_run`` — LiteLiveSession (core/voice/lite_client.py). The
  first-run session: full session surface, matched-intent tools only,
  upgrades to a real LocalLiveSession. Not a goal brain.

The sharpest honest gap, pinned here rather than papered over: the
voice brain has NO free-form goal loop. Its ``run_goal`` is a
saved-plan-or-refuse entry (core/voice/live_client.py): a goal naming a saved
plan delegates to that plan's DECLARED steps via ``run_plan``; anything
else returns the plain refusal, unless the Wave 1 voice bridge
(core/voice/voice_text_bridge.py, default OFF) hands it to an injected text
brain. The free-form loop (one picker call per step through
core/planner.run_goal) exists only on the two text brains.

Other pinned absences: ``research_loop`` (core/memory/research_edition_deltas.py) has
no BRAIN-native loop — no brain runs the loop itself, so every
brain declares it absent. The loop IS wired, at TOOL level, since
the research wiring batch: the ``deep_research`` tool runs the
A6 kernel over the local KnowledgeStore through the normal
funnel, so any brain with full tool scope can reach research
that way via tools.execute. The per-brain flags describe
brain-native loops, not tool availability — the two must not be
conflated. No brain owns a task registry — task lifecycle lives
in core/brain/task_coordinator.py (P0), not in any brain.

C-14: every brain executes tools through the same tools.execute funnel
(see each entry's evidence), so approve-first applies uniformly; the
contract grants nothing and pre-approves nothing.

Coordinator integration (P1, minimal): the coordinator consults this
contract at exactly one decision point — the free-text multi-step
fall-through in core/brain/task_coordinator.py handle_turn. Local
decomposition of free text is model work no brain provides through the
coordinator, so the routing does NOT change: the turn still flows to
the live session. What the contract adds is the record: the
fall-through result carries which brain the turn is going to and
whether THAT brain can run a free-form goal loop (None when the brain
is unknown — an unknown brain claims no capability). Voice-brain
adoption of the coordinator (a free-form loop on the voice brain) is a
named follow-up, not this batch; this contract is the routing table it
will read.

GUI engine picker note: the Settings brain picker offers gemini/local
only (gui/window_brain.py BrainMixin._brain_engine). groq_text is
reachable via the cloud path, not the picker; lite_first_run is the
first-run session, not an engine value. ENGINE_TO_BRAIN maps only what
the picker can actually select.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BrainCapability:
    """One brain's declared capabilities. All booleans are load-bearing:
    False means the tree was read and the capability is absent, not that
    nobody looked. ``evidence`` names the code each claim traces to."""

    brain_id: str
    session_class: str
    module: str
    modality_in: str
    modality_out: str
    text_replies: bool
    tools_via_funnel: bool
    # "full" = arbitrary model-chosen tools through the funnel;
    # "matched_intents_only" = a fixed intent set, no free tool choice.
    tool_scope: str
    declared_step_plans: bool
    freeform_goal_loop: bool
    research_loop: bool
    holds_task_registry: bool
    confirm_gated: bool
    evidence: str
    # Conditionality for freeform_goal_loop: None means the flag is
    # unconditional; a string names the condition the flag depends
    # on (the voice brain's flag is bridge-gated — see
    # can_freeform_goal_now for the runtime answer). A blanket
    # True with no condition field would be a lie for a gated
    # capability, and this field is what keeps it honest.
    freeform_goal_condition: str | None = None


BRAIN_CONTRACTS: dict[str, BrainCapability] = {
    "gemini_live": BrainCapability(
        brain_id="gemini_live",
        session_class="GeminiLiveSession",
        module="core/voice/live_client.py",  # Phase 3 V1b INTENTIONAL: moved to core/voice/
        modality_in="voice (mic)",
        modality_out="voice (AUDIO) + text bubbles via output transcription",
        text_replies=True,
        tools_via_funnel=True,
        tool_scope="full",
        declared_step_plans=True,
        freeform_goal_loop=True,
        freeform_goal_condition=(
            "voice bridge enabled + compatible text brain injected "
            "(voice_text_bridge; default OFF, session-only); the voice "
            "brain itself has no native picker loop — planning comes "
            "from the injected text brain, execution + receipts via "
            "the coordinator task wrapper (voice_goal.py)"),
        research_loop=False,
        holds_task_registry=False,
        confirm_gated=True,
        evidence=(
            "tools: _execute_tool_sync -> tools.execute "
            "(live_client.py, funnel, all gates intact); "
            "declared plans: run_plan walks DECLARED steps only, picker "
            "returns None, undeclared step halts (live_client.py run_plan); "
            "free-form loop CONDITIONAL (voice-brain adoption): run_goal "
            "runs a free-form goal as a coordinator task through the "
            "Wave 1 bridge when the bridge is enabled and a compatible "
            "text brain is injected (live_client.py run_goal, "
            "voice_goal.py run_bridged_voice_goal, voice/voice_text_bridge.py); "
            "when the bridge is OFF or no text brain is available the "
            "goal refuses exactly as before (live_client.py refusal); "
            "the voice brain itself has no native picker loop — the "
            "flag is YES-when-bridge-available, NO otherwise (see "
            "can_freeform_goal_now); holds_task_registry stays False "
            "because the registry lives in the coordinator/ledger, not "
            "the brain; "
            "text replies: output_audio_transcription + "
            "response_modalities=[\"AUDIO\"], no input transcription "
            "(live_client.py, v5.94 text replies); "
            "loop gates: planner.run_goal gate stack, C-14 included "
            "(planner.py, live_client.py run_goal docstring)."
        ),
    ),
    "local_text": BrainCapability(
        brain_id="local_text",
        session_class="LocalLiveSession",
        module="core/voice/local_client.py",  # Phase 3 V1b INTENTIONAL: moved to core/voice/
        modality_in="text",
        modality_out="text",
        text_replies=True,
        tools_via_funnel=True,
        tool_scope="full",
        declared_step_plans=True,
        freeform_goal_loop=True,
        research_loop=False,
        holds_task_registry=False,
        confirm_gated=True,
        evidence=(
            "tools: tool calls loop through tools.execute() "
            "(local_client.py module docstring, toolbox.py); "
            "declared plans: run_plan (local_client.py); "
            "free-form loop: run_goal drives planner.run_goal, one chat "
            "call per step, UIA-snapshot picker, expect verification, "
            "lite-tier gate (local_client.py run_goal); "
            "loop gates: planner.run_goal gate stack, C-14 included "
            "(planner.py)."
        ),
    ),
    "groq_text": BrainCapability(
        brain_id="groq_text",
        session_class="GroqSession",
        module="core/voice/cloud_text_client.py",  # Phase 3 V1b INTENTIONAL: moved to core/voice/
        modality_in="text",
        modality_out="text",
        text_replies=True,
        tools_via_funnel=True,
        tool_scope="full",
        declared_step_plans=True,
        freeform_goal_loop=True,
        research_loop=False,
        holds_task_registry=False,
        confirm_gated=True,
        evidence=(
            "tools: native tool_call replies executed through the REAL "
            "tools.execute() (cloud_text_client.py, incl. line ~1160); "
            "declared plans: run_plan (cloud_text_client.py); "
            "free-form loop: run_goal drives planner.run_goal, one Groq "
            "chat call per step, UIA-snapshot picker, expect verification "
            "(cloud_text_client.py run_goal); "
            "instantiated on the cloud path (brain.py cloud start); "
            "loop gates: planner.run_goal gate stack, C-14 included "
            "(planner.py)."
        ),
    ),
    "lite_first_run": BrainCapability(
        brain_id="lite_first_run",
        session_class="LiteLiveSession",
        module="core/voice/lite_client.py",  # Phase 3 V1b INTENTIONAL: moved to core/voice/
        modality_in="voice (mic, first-run flow)",
        modality_out="TTS (LocalLiveSession used only as a TTS engine)",
        text_replies=False,
        tools_via_funnel=True,
        tool_scope="matched_intents_only",
        declared_step_plans=False,
        freeform_goal_loop=False,
        research_loop=False,
        holds_task_registry=False,
        confirm_gated=True,
        evidence=(
            "tools: matched intents execute a REAL tool through "
            "tools.execute() (lite_client.py, intent path); "
            "no run_goal/run_plan methods on the class (lite_client.py); "
            "upgrades to a real LocalLiveSession (lite_client.py _inner); "
            "surface: full session callbacks identical to "
            "GeminiLiveSession (lite_client.py class docstring)."
        ),
    ),
}

# What the GUI Settings brain picker can actually select
# (gui/window_brain.py BrainMixin._brain_engine: gemini/local only,
# anything else defaults to gemini). groq_text is reachable via the
# cloud path, not the picker; lite_first_run is not an engine value.
ENGINE_TO_BRAIN: dict[str, str] = {
    "gemini": "gemini_live",
    "local": "local_text",
}


def contract_for(brain_id: str) -> BrainCapability | None:
    """The contract for a brain id, or None for an unknown id. Unknown
    brains claim nothing — callers must treat None as 'no capability
    declared', never as a default set."""
    return BRAIN_CONTRACTS.get(brain_id)


def contract_for_engine(engine: str) -> BrainCapability | None:
    """The contract for a GUI engine value, or None when the engine is
    not a picker value (groq/lite are not engine values)."""
    brain_id = ENGINE_TO_BRAIN.get(str(engine or "").strip().lower())
    return BRAIN_CONTRACTS.get(brain_id) if brain_id else None


def can_freeform_goal(brain_id: str | None) -> bool | None:
    """Whether the named brain can run a free-form goal loop. None when
    the brain is unknown — an unknown brain claims no capability (this
    is the fail-closed answer, not a default).

    For the voice brain this is the STATIC declaration: True means
    the capability exists conditionally (see the contract's
    freeform_goal_condition and can_freeform_goal_now for the runtime
    answer), not that a goal runs right now.
    """
    if brain_id is None:
        return None
    contract = BRAIN_CONTRACTS.get(brain_id)
    if contract is None:
        return None
    return bool(contract.freeform_goal_loop)


def can_freeform_goal_now(brain_id: str | None) -> bool | None:
    """Whether the named brain can run a free-form goal loop RIGHT
    NOW. None when the brain is None or unknown (fail-closed).

    For non-voice brains this equals the static declaration. For the
    voice brain it additionally requires the bridge to be enabled
    AND a compatible text brain (callable run_goal) to be injected
    — the condition the contract declares. The check reads the
    bridge's global injection only (the production path: the
    Settings toggle injects globally; no production code assigns a
    per-session text_brain attribute, so a session-attached brain
    would still run through the bridge but is not visible here —
    documented, not hidden). Any failure answers False, never a
    guess.
    """
    if brain_id is None:
        return None
    contract = BRAIN_CONTRACTS.get(brain_id)
    if contract is None:
        return None
    if brain_id != "gemini_live":
        return bool(contract.freeform_goal_loop)
    try:
        from .voice import voice_text_bridge as _bridge
        if not _bridge.is_voice_bridge_enabled():
            return False
        brain = _bridge.get_text_brain()
        if brain is None:
            return False
        _run_goal = getattr(brain, "run_goal", None)
        return bool(callable(_run_goal) or callable(brain))
    except Exception:  # noqa: BLE001 — runtime check failure is False
        return False
