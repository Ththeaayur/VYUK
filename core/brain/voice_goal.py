"""Voice-goal adoption — free-form goals on the voice brain, owned by
the coordinator (identity-gap adoption batch).

Design (investigated first, then built — rejected alternatives below):

The voice brain has no native picker loop and none is invented here:
the Live session is model-paced audio with no text chat seam, so a
picker cannot be driven from it (the same B15 limit run_plan
documents). Planning therefore comes from the injected text brain's
run_goal through the Wave 1 bridge (default OFF, session-only,
explicit opt-in) — the loop that already exists, with its full gate
stack. What this batch ADDS around that loop:

* The goal runs as a coordinator TASK (goal-level, not per-step —
  the text loop picks its own steps internally, so per-step
  coordinator visibility is not claimed): task id, lifecycle,
  ledger record, receipt, mission conversion, narrated summary.
* Cancellation: the coordinator cancel flag and the session's
  running flag feed the loop's interruption_check (threaded
  bridge -> text brains' run_goal -> planner.run_goal step
  boundaries). A fired interruption halts the loop with a
  "Goal stopped: interrupted" report and the task records
  cancelled; remaining loop steps genuinely never run.
* Receipts: every executed goal yields a TaskReceipt (plus the
  mission conversion the coordinator bridge provides) and a
  one-line narrated summary. The bridge's own narration of the
  full report is unchanged and is NOT duplicated.

Rejected alternatives, with reasons:

* A voice-native planner (driving planner.run_goal's picker from
  the Live session): no chat-call seam exists to pick with, so
  this would invent a fake picker — the exact failure the tree's
  run_goal docstring refuses. Rejected on evidence, not taste.
* Whole-goal delegation to the coordinator's declared-step
  machinery: the coordinator cannot decompose free text into tool
  steps (its own documented limit); the planning must come from
  the model loop. Rejected — it would move the gap, not close it.
* Running the goal without a coordinator task (bare bridge call):
  preserves the old gap (no task, no ledger, no receipt, no
  cancellation surface). Rejected — the benchmark's X03/X04
  measure exactly that absence.

Classification of the loop report is fail-closed: only an explicit
completion marker counts as completed; a fired interruption counts
as cancelled; anything else (halt, stop, bridge error text, or an
unrecognised report) counts as failed — never completed on a guess.

Honest limits (stated, not hidden):

* Spoken delivery: narration goes through the voice session's
  on_transcript channel (the same channel run_plan progress uses,
  folded by the GUI into the goal pill and an assistant bubble).
  Injecting transcript text does NOT synthesize Live audio — true
  Live-audio synthesis of goal progress is a field item, exactly
  as the bridge docstring already states.
* Voice approval: there is no spoken-"yes" approval channel, and
  none is invented — a model utterance must never approve its own
  gated action. Gated steps in a voice goal wait on the existing
  confirm handler (GUI dialog, 60 s timeout, fail-closed), exactly
  as text-loop goals do; the loop never auto-approves.
* Legacy brains: a text brain whose run_goal does not accept
  interruption_check runs without voice-side interruption. The
  receipt summary says so ("best-effort") instead of claiming a
  cancellation that cannot happen. Both shipped text brains accept
  it after this batch.
* Session stop: ending the session halts the loop at the next step
  boundary (the interruption check reads session._running), and
  the task records cancelled. A goal invoked while the session is
  not running cancels immediately on the same rule — correct, since
  a stopped session cannot proceed.
"""

from __future__ import annotations

from ..provider_identity import Engine  # GAPS-22 (B-V3): engine identity

__all__ = [
    "classify_voice_goal_report",
    "run_bridged_voice_goal",
    "cancel_voice_goal",
]

_COMPLETE_MARKERS = ("Goal complete:", "Goal finished:")
# Halt vocabulary: the driver's own "Goal halted:" / "Goal stopped:"
# lines, plus the per-step gated/refused halt line ("✗ Goal step N
# [tool] — halted: ... The goal stops here; ..."), which is how a
# refused action actually surfaces in a loop report.
_HALT_MARKERS = ("Goal halted", "Goal stopped", "— halted:")


def _excerpt(report: str, markers) -> str:
    """First report line containing any marker, capped, else ""."""
    for line in str(report or "").splitlines():
        if any(m in line for m in markers):
            return line.strip()[:500]
    return ""


def classify_voice_goal_report(report: str, *, interrupted: bool):
    """Classify a text-loop report into (state, kind, message).

    Fail-closed by construction: only an explicit completion marker
    counts as completed. A fired interruption is cancelled whatever
    the report says. Anything else is failed — a halt, a stop, a
    bridge error string, or an unrecognised report never reads as
    done. Returns (state, kind, message) with state in
    {"completed", "failed", "cancelled"}.
    """
    if interrupted:
        return ("cancelled", "cancelled", "voice goal cancelled")
    text = str(report or "")
    if any(m in text for m in _COMPLETE_MARKERS):
        return ("completed", "",
                _excerpt(text, _COMPLETE_MARKERS) or "goal complete")
    if any(m in text for m in _HALT_MARKERS):
        return ("failed", "halted",
                _excerpt(text, _HALT_MARKERS) or "goal halted")
    return ("failed", "unknown",
            "loop report did not confirm completion")


def cancel_voice_goal(session) -> bool:
    """Cancel the session's active voice goal task, if one is running.

    Returns True when a task was found and its cancel flag set (the
    loop then halts at its next step boundary); False when no voice
    goal task is stashed or it is already terminal. Setting the flag
    is all this does — the loop observes it through the wrapper's
    interruption check, so a cancelled task's remaining steps
    genuinely never run.
    """
    try:
        coord = getattr(session, "_voice_goal_coordinator", None)
        task = getattr(session, "_voice_goal_task", None)
        if coord is None or task is None:
            return False
        return bool(coord.cancel_task(task.id))
    except Exception:  # noqa: BLE001 — cancel failure is "not cancelled"
        return False


async def run_bridged_voice_goal(session, text, *, ledger=None,
                                 coordinator=None):
    """Run a voice free-form goal as a coordinator task, or None.

    Returns None when the bridge cannot run the goal — the caller
    (GeminiLiveSession.run_goal) then falls through to its existing
    honest refusal, byte-identical. That covers: bridge OFF, no
    text brain, a voice brain injected as its own text brain, and a
    bridge that vanishes mid-flight (which additionally leaves a
    failed task record, since an attempt was genuinely made).

    When the goal runs, returns the bridge report plus a receipt
    line. Side effects on the run path only: a coordinator task
    (stashed on the session as _voice_goal_task/_voice_goal_coordinator
    for cancel_voice_goal), a ledger record per transition (default
    production ledger, guarded — ledger failure runs in-memory-only;
    tests inject via the ``ledger`` param or the session's
    _voice_goal_ledger attribute), a TaskReceipt stashed as
    _voice_goal_receipt, and a one-line narrated receipt summary
    (the bridge's own narration of the full report is untouched).

    Saved-plan goals never reach here: live_client.run_goal matches
    plans first and only calls this for free-form goals.
    """
    from ..voice import voice_text_bridge as _bridge  # GAPS-25 (V-8): bridge moved to core/voice/
    from .task_coordinator import StepOutcome, TurnCoordinator  # Phase 3 B1b: voice_goal moved into core/brain/; task_coordinator is intra-package now

    goal_text = str(text or "")
    # Unavailable paths preserve existing behaviour exactly: hand_off
    # itself returns None (bridge OFF / no brain / self-brain) or its
    # own error string (empty goal, brain without run_goal), and the
    # caller falls through to the refusal for None. No task is
    # created for a goal that never ran.
    if not _bridge.is_voice_bridge_enabled():
        return await _bridge.hand_off(session, goal_text)
    brain = _bridge.resolve_text_brain(session)
    # GAPS-22 (B-V3): a Gemini session is identified by its
    # engine, never by its class name.
    if (brain is None or brain is session
            or getattr(brain, "engine", None) is Engine.GEMINI):
        return await _bridge.hand_off(session, goal_text)
    _run_goal = getattr(brain, "run_goal", None)
    if not callable(_run_goal) and not callable(brain):
        return await _bridge.hand_off(session, goal_text)

    # Runnable: own the goal as a coordinator task.
    if coordinator is None:
        _ledger = ledger
        if _ledger is None:
            _ledger = getattr(session, "_voice_goal_ledger", None)
        if _ledger is None:
            try:
                from .task_coordinator import _default_ledger  # Phase 3 B1b: voice_goal moved into core/brain/; task_coordinator is intra-package now
                _ledger = _default_ledger()
            except Exception:  # noqa: BLE001 — ledger failure is in-memory-only
                _ledger = None
        coordinator = TurnCoordinator(ledger=_ledger)
    task = coordinator.new_task(goal_text, "multi_step", [],
                                turn_text=goal_text,
                                brain_id="gemini_live")
    try:
        session._voice_goal_task = task
        session._voice_goal_coordinator = coordinator
    except Exception:  # noqa: BLE001 — stash failure only loses the cancel seam
        pass
    task.state = "running"
    coordinator._ledger_write(task)

    supported = _bridge.run_goal_accepts_interruption_check(brain)
    fired: list[bool] = []

    def _check():
        # Coordinator cancel OR session stop halts the loop at the
        # next step boundary. A session that is not running cannot
        # proceed, so it cancels immediately on the same rule.
        if task._cancelled or not getattr(session, "_running", False):
            fired.append(True)
            return {"reason": "voice goal cancelled", "resume": False}
        return None

    try:
        report = await _bridge.hand_off(
            session, goal_text,
            interruption_check=_check if supported else None)
    except Exception:  # noqa: BLE001 — bridge failure is a refusal
        report = None
    if report is None:
        # The bridge vanished mid-flight (flag flipped or brain
        # cleared between the check and the call). An attempt was
        # made, so it leaves a failed record — but the caller still
        # falls through to the refusal, since no goal ran.
        task.state = "failed"
        task.outcomes = [StepOutcome(
            tool="voice_goal", label="voice goal", status="failure",
            kind="unavailable",
            message="voice bridge became unavailable during the goal")]
        coordinator._ledger_write(task)
        try:
            session._voice_goal_receipt = task.receipt()
        except Exception:  # noqa: BLE001 — receipt stash never breaks the turn
            pass
        return None

    state, kind, message = classify_voice_goal_report(
        report, interrupted=bool(fired))
    task.state = state
    # Goal-level outcome: the text loop picks its own steps, so the
    # receipt honestly records ONE outcome for the whole goal
    # execution, labelled as such — never fake per-step visibility.
    # NOTE on counts: a cancelled goal outcome counts in
    # receipt.failed (it is a non-success outcome); readers must use
    # the receipt's state for the verdict, not the counts alone.
    task.outcomes = [StepOutcome(
        tool="voice_goal", label="voice goal (text-loop report)",
        status=("success" if state == "completed" else state),
        kind=kind, message=message)]
    coordinator._ledger_write(task)
    receipt = task.receipt()
    try:
        session._voice_goal_receipt = receipt
    except Exception:  # noqa: BLE001 — receipt stash never breaks the turn
        pass
    # Mission receipt conversion stays available through the
    # coordinator bridge (no sink in the voice path, same as the
    # GUI typed-turn hook — the conversion, not a second store).
    summary = f"Voice goal {state}: task {task.id[:8]}."
    if not supported:
        summary += (" Cancellation is best-effort for this brain "
                    "(its run_goal does not accept interruption checks).")
    try:
        _narrate = getattr(session, "on_transcript", None)
        if callable(_narrate):
            _narrate("assistant", summary)
    except Exception:  # noqa: BLE001 — narration never breaks the goal
        pass
    return f"{report}\n\nVoice goal receipt: task {task.id}, {state}."
