"""Task/Turn Coordinator — identity-gap audit P0 (v5.95 follow-up).

The audit confirmed ``run_goal`` is
called only inside the two text-brain goal methods: no normal
GUI/chat call site invokes a provider goal method, and the voice
brain has no free-form goal loop at all. Autonomy lived in the
wrong brain. This module is the P0 remedy: a coordinator that sits
between a chat turn and execution and owns the turn's task.

WHAT IT DOES
  * ``classify_turn`` — a pure, deterministic classifier. Every
    turn is exactly one of: ``conversation`` (no action),
    ``single_action`` (one imperative, no task needed),
    ``multi_step`` (explicit multi-step structure),
    ``saved_plan`` (an explicit, resolvable J9 playbook name),
    ``routine`` (an explicit, resolvable saved routine name).
  * ``TurnCoordinator`` — owns task IDs and the lifecycle
    pending -> running -> paused -> running -> completed /
    failed / cancelled, with a ``verifying`` phase between the
    action loop and the terminal verdict for tasks carrying
    step expectations (see VERIFICATION PHASE above).
    Cancellation is a flag the run loop reads before EVERY
    step, so a cancelled task's remaining steps genuinely
    steps genuinely never run. Pause stops the loop at the next
    step boundary; resume continues from the next uncompleted
    step, never from scratch.
  * Structured receipts — every terminal task (and every failed
    task build) yields a ``TaskReceipt``: task id, class, state,
    per-step outcomes, counts, remaining steps, and a reason for
    any non-completed task. Receipts convert to
    ``mission_control.Receipt`` (``to_mission_receipt``) so they
    join the existing mission binder — the coordinator receipt
    IS the record; the mission form is a view of it, not a
    second incompatible store. The coordinator approves nothing,
    ever (no gate_decisions are ever attached).
  * ``handle_typed_turn`` — the session-handler-level entry the
    GUI calls: explicit saved-plan / routine invocations execute
    locally through the funnel with a task id + receipt, and the
    receipt is posted through the session's system-message path.
  * Durable ledger (P2, ``core/brain/task_ledger.py``) — a
    ``TurnCoordinator`` built with ``ledger=`` persists every
    task transition (creation, each step, terminal/pause/
    cancel) to ``data/task_ledger.json``; ``handle_typed_turn``
    wires the default ledger for freshly created session
    coordinators. ``resume_ledger_task`` resumes an interrupted
    record from its next uncompleted step (completed steps never
    re-run; unknown outcomes resume as not-completed; gated
    steps re-gate); ``list_interrupted_tasks`` is the startup
    recovery query. Ledger failure never breaks a task.

WHAT IT DOES NOT DO (documented limits, not gaps to paper over)
  * Free-text multi-step turns (``multi_step``) are classified
    but NOT locally executed: decomposing free text into tool
    steps is model work, and this batch invents no fake
    decomposition. Such turns flow to the live session exactly
    as before, with the classification recorded. Local
    multi-step execution covers saved plans and routines, whose
    steps are known data — the exit gate proves 3+ step tasks
    through both of those paths.
  * Unresolvable plan/routine names NEVER claim the class: a
    "run routine X" with no routine X classifies as
    ``single_action`` and flows to the live session, which
    reports the missing routine naturally. The coordinator never
    guesses a plan (unlike ``planner.run_goal``'s similar-name
    fallback) and never hallucinates steps.
  * Steps execute ONLY through the injected executor, whose
    default is the funnel (``tools.execute``) with every gate
    intact (confirm gate, validation, budget, taint). A gated
    action inside a task is refused without approval exactly as
    outside one — C-14 approve-first stands; the coordinator
    never injects ``confirm=True``, never pre-approves, never
    batches approvals (A1).
  * Pause/resume/cancel are synchronous-loop semantics: the
    loop checks the flags between steps, and a step callback
    may pause/cancel mid-run. Asynchronous interruption (a user
    pressing Stop while a task runs on another thread) needs an
    async runner and is future work — the flags are the seam.
  * Voice-brain adoption (the free-form goal loop in the voice
    brain) is a NAMED FOLLOW-UP: this module is
    provider-agnostic (executor + resolvers injected), and the
    adoption path is documented in ``ADOPTION`` below, but the
    voice brain is NOT rewired here.
  * No anticipation/capture, no voiceprint, no payment surface,
    no secrets-fallback change, no rung-5 change — all
    separately gated (see the brief's hard limits).

ADOPTION (voice brain, follow-up, not built here)
  The voice brain adopts the coordinator by calling
  ``handle_turn``/``TurnCoordinator`` with its own executor
  (the funnel, same as the default) and its own step source
  (a model-decomposed step list, validated through
  ``StepDef.from_mapping`` before any step runs). Until then,
  voice turns keep their current path unchanged.

Step model: a step is ``{"tool": str, "args": dict}`` plus
optional ``"label"`` (display name), ``"on_fail"`` ("halt" |
"skip" | ``{"goto": n}``, default "halt", mirroring
``planner.run_routine``), optional ``"when"`` (a probe
``{"tool", "args"}`` executed through the funnel before the
step; a failed probe follows ``on_fail``), and optional
``"expect"`` (a free-text STRING expectation of what the
step's action should have achieved — a dict ``"expect"``
is rejected, mirroring ``planner``'s distinction between a
free-text expect and a B12 result check). ``"goto"`` jumps
to the named step (1-based) when the step fails (B4):
one jump design — per-step strikes at the retry limit
(3), a global budget of len(steps)*3+10 (the legacy
formula), every jump recorded in the ledger + receipt +
one Activity line, jumps survive pause/resume, and a jump
to a D4/C-14 step is refused outright (the target never
runs). A verification strike-out does NOT jump: the
phase's bounded retry is the recovery for an unmet
expectation, and re-entering the action loop from the
phase would break the retry-resume markers.

VERIFICATION PHASE (wiring item 3, v6.0.0): after the action
loop completes with every step success/skipped, and before
the task reports, a task carrying at least one ``"expect"``
enters a genuine verification phase — it runs after action
and before report, and it uses the existing
``core/desktop/uia.verify_expectation`` producer (injected as
``verifier``, defaulting to that engine; never rebuilt here)
to check each executed step's expectation against a
before-snapshot taken when the step ran and a fresh
after-snapshot. While the phase runs the task's state is
``"verifying"`` — that state, written to the ledger by the
phase itself, is the real verifier producer the D9
producer-or-hide rule requires, so the Activity surface may
honestly render Verifying (see ``gui/task_state.py``).
Transitions: every verdict ``"met"`` or ``"unverifiable"``
(or a skipped step, whose expectation was never checkable)
→ the task completes, with every verdict recorded on the
receipt and in the ledger; a verdict ``"not-met"`` → the step
is RETRIED, not failed outright (see RETRY-ON-NOT-MET below):
only when the step's not-met strikes exhaust does the task
FAIL honestly (state ``"failed"``, reason naming the step,
its expectation, the verifier's reason, and the strike count)
— never a silent success, never a fake pass. A task with no
expects, or whose steps all skipped, runs no phase and
completes exactly as before. Pause/cancel are step-boundary
semantics: once entered, the phase (observation only, except
for its own retries — see below) runs to its verdicts; a
crash mid-phase leaves a ``"verifying"`` ledger record that
resumes into a full phase re-run.

RETRY-ON-NOT-MET (regression-batch follow-up, closing the
phase's named follow-up): a ``"not-met"`` verdict retries the
SAME step's action, mirroring planner's three-strikes rule
(``core/brain/planner.py`` ``_EXPECT_STRIKE_LIMIT = 3``):
``"not-met"`` retries the same step until the three-strikes
limit; ``"unverifiable"`` never strikes and never retries —
an inconclusive verdict means the effect's state is UNKNOWN,
and re-running an action whose effect may already have
landed could double-apply it. Strikes count CONSECUTIVE
not-met verdicts for the step (a ``"met"`` resets them, as in
planner); the budget survives a phase re-run, because strikes
live in the task's retry history (receipt + ledger), not in
phase-local state — a crash must not grant fresh retries.
A retry re-runs exactly the one step through ``_run_step``
(same executor, same gates, same confirm-stop handling — a
retried step that needs approval pauses for approval through
the existing pending-approval path, never around it; the
run-loop resume for such a pause re-runs only that step,
then re-enters verification — later steps never re-run).
A retry re-run that fails as an action (refusal, gate
refusal, when-probe halt) fails the task honestly at once:
refusals are decisions, not misses, and are never retried.
A retry re-run that skips per ``on_fail=skip`` records a
``"skipped"`` verification entry, exactly like any skipped
step. A user-cancelled task never retries: cancel is checked
before every retry re-run, at phase entry, and before the
phase completes — a cancelled task ends cancelled, never
completed by the phase. Every not-met verdict is recorded in
the retry history (step, strike number, verifier reason,
whether a retry followed); the receipt carries it as
``retries`` and the ledger stores it, so a retried-then-
succeeded task and a retried-then-failed task both tell one
honest story: the first attempt's miss is never overwritten,
and the final verdict is never presented as first-try.

MENU STEPS (§7 consumers batch 4): a step with tool
``"menu_action"`` and args ``{"action": "invoke", "path": ...}``
is a menu step — it names a menu path instead of spelling raw
clicks, and it is validated at build time (an invoke step with
no path is rejected, never dispatched to guess). At dispatch it
flows through the funnel like any step (batch-3 handle injection
applies: the step's handle_id lands in window_handle_id). Two
menu-specific behaviours live in the coordinator, nowhere else:

* Refusal rewrite. The funnel's menu handler renders engine
  refusals as success-carrying sentences ("Menu path … refused:
  …", "Menu list refused (…)"), so without a rewrite a refused
  menu path would record as a successful step. The coordinator
  rewrites those two sentence shapes into a refused outcome
  (kind "menu_refused", message the engine's sentence) before
  the step completes — a refused menu path fails the task
  honestly, exactly like any other step failure, and an
  on_fail=skip step still skips per its declared rule.
* Menu consequence verification. In the verification phase, a
  menu step's expectation is judged by the menu consequence
  verifier (``core.desktop.s7_menu_consequence``, injected as
  ``menu_verifier``) instead of the generic engine: generic
  window / text shapes still delegate to
  ``core.desktop.uia.verify_expectation``, and menu shapes
  (foreground-changed-to, window-closed, menu-opened,
  dialog-opened) are judged as that module defines them. A
  "not-met" menu consequence fails the task exactly like any
  other not-met expectation; "met" / "unverifiable" complete it
  with the verdict recorded. The engine's
  "invoked-without-verification" note is never upgraded by
  this phase without snapshot or invoke-result evidence.

PENDING APPROVAL (§7 consumers batch 6): an engine confirm
stop is NOT a failure — it is a question. When a step's
handler output is an engine needs_confirmation stop (menu
C-14 leaf, dialog overwrite, surface confirm-gated path,
adapter confirm-gated resolution, drag destructive target,
journal reconcile/compensate — each shape read off the
engine/handler rendering, see _detect_s7_confirmation_stop),
the task PAUSES with a pending-approval record instead of
failing: the step is not appended to outcomes, the index
still points at it, and the record (step, tool, label,
engine sentence verbatim, confirm flag name) lives on the
task and in the ledger. The GUI presents that record through its EXISTING authorization dialog
(the S-1 popup, confirmed 2026-11-03 under the code-rulings delegation:
"Don't allow" default + initial focus so Enter denies, explicit "Allow
once" required, Details collapsed with Action + masked summary, 60 s
silence = deny via confirm_gate; no four-field format, no visible ring,
no red edge)
and reports the answer through resolve_pending: approval
re-issues exactly the pending step with exactly its engine
confirmed flag (confirmed / confirm_destructive /
confirm_overwrite) and the task continues; denial fails the
step honestly (on_fail=skip still skips); the dialog's
silence-timeout arrives as denial. The coordinator approves
nothing itself, and a re-issue still passes through the
funnel with every gate intact — a funnel-gate refusal on
re-issue fails the step honestly, as any gate refusal does.
A spoken "yes" never resolves a pending approval, and a
model-set flag on the step's args is never consulted: only
resolve_pending resolves, and only the GUI calls it. The
one-shot re-issue flag is in-memory only, never
ledger-serialized, so a prior approval never carries across
a restart (C-14 re-gating: resumed tasks re-gate through
the funnel, and a rebuilt pending task re-stops on resume
until it is resolved again). A genuine refusal still fails
exactly as batches 4/5 left it — only confirm stops pause.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from ..state_bus_producers import (
    emit_mission,
    emit_step,
    emit_tool_finished,
    emit_tool_started,
)

# INTENTIONAL (skills split B42): refusal family moved to
# task_coordinator_refusal; re-exported here so the facade namespace
# (and every importer / module-object caller) is unchanged.
from .task_coordinator_refusal import (
    _S7_REFUSAL_SHAPES,
    _detect_s7_confirmation_stop,
    _funnel_execute,
    _rewrite_menu_refusal,
    _rewrite_s7_refusal,
)


# INTENTIONAL (skills split B43): remaining families moved to sibling
# modules; re-exported here so the facade namespace (and every importer /
# module-object caller) is unchanged.
from .task_coordinator_classify import (
    TURN_KINDS,
    TASK_STATES,
    _RETRY_STRIKE_LIMIT,
    _MISSION_STATUS,
    _SINGLE_VERBS,
    _PLAN_PATTERNS,
    _ROUTINE_PATTERNS,
    _NUMBERED_STEP,
    _norm,
    _clean_name,
    _match_names,
    _is_multi_step_structure,
    TurnClassification,
    classify_turn,
)
from .task_coordinator_steps import (
    StepDef,
    StepOutcome,
    TaskReceipt,
    format_receipt,
)
from .task_coordinator_task import Task
from .task_coordinator_opinion import (
    _menu_leaf,
    _second_opinion_question,
    _second_opinion_pause_sentence,
)
from .task_coordinator_lifecycle import TurnCoordinatorLifecycleMixin
from .task_coordinator_execution import TurnCoordinatorExecutionMixin
from .task_coordinator_tail import (
    default_plan_names,
    default_routine_names,
    default_plan_steps,
    default_routine_steps,
    _typed_turn_brain_id,
    _default_ledger,
    list_interrupted_tasks,
    handle_typed_turn,
)


class TurnCoordinator(
    TurnCoordinatorLifecycleMixin, TurnCoordinatorExecutionMixin
):
    """Owns task IDs, lifecycle, and receipts for chat turns.

    The coordinator never calls a tool handler directly: every
    step goes through the injected executor (default: the
    funnel). It never injects confirmations, never batches
    approvals, and never approves a gate decision.
    """
