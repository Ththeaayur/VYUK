"""Command Deck Phase D — state-bus producers (core side).

One tiny, never-raising emit helper for core modules. The bus
itself lives in ``core/state_bus.py`` (GAPS-28: moved out of the
GUI package — it is Qt-optional by design, so a direct import
here never pulls the GUI package in). This module imports it
directly at the top; the old importlib dance over two GUI paths
is gone.

One module object (full-run incident, Phase D, closed by the
move): the two paths used to load the SAME file as TWO module
objects with distinct event classes. A leaked
``voice_assistant`` shim in a test process (as a full-run suite
may leave) used to make this helper emit
voice_assistant-flavoured events while gui-imported subscribers
listened for gui-flavoured classes — every event silently
dropped (5 Phase D tests failed in the full run with zero
events, green standalone). With the bus in core there is one
module per import spelling, and within a process the spelling
is consistent (top-level ``core.*`` in unit tests,
``voice_assistant.core.*`` in production) — bus and event
classes always come from the same module object.

SAFETY (guide §8, hard): the bus is cosmetic. Every helper here
swallows its own failures and returns None, so a bus problem can
never break a task, a trip, or a capture. ``emit()`` itself only
appends to a locked deque and posts a queued drain — it never runs
a subscriber synchronously.

Producers wired in Phase D:
* coordinator ``run_task`` — MissionState / StepEvent / Tool events
* planner ``run_goal`` — StepEvent / Tool events (no totals: an
  open-ended goal has no known step count, so ``m`` stays 0 and no
  MissionState is claimed for it)
* failsafe ``_trip`` — FailsafeTripped
* capture_guard note_capture_started/ended — CaptureStarted/Finished
  on 0→1 / 1→0 count transitions only (overlapping captures of one
  kind produce exactly one Started and one Finished)

Deliberately NOT produced in Phase D (no authoritative source):
* TargetRect — ``core/desktop/uia.py`` formats text for the model only; it
  produces no numeric rectangle (guide §8 already says so).
* InputAcquired/Released — no input-acquisition boundary exists in
  the tree; a tool running is not the same as input held, so no
  event is invented for it. Phase F may define the boundary.
* SensorState for most sensors — the Senses page owns those
  producers (Phase E). The one Phase D sensor is the microphone
  health tick in ``gui/window_services.py`` (a GUI-side producer
  driven by the live audio manager's drop counter).

Formerly hidden, now real (wiring item 3, v6.0.0):
* Verified / the Verifying display state — Phase D HID Verifying
  because the coordinator had no verification phase (guide D9
  producer-or-hide). The coordinator verification phase
  (``core/brain/task_coordinator.py``) is now that producer: it writes
  the ledger's ``"verifying"`` state while it runs, and it drives
  ``uia.verify_expectation``'s Verifying/Verified emissions for
  coordinator tasks. No broader Verifying claim is made for
  anything else.

Producers wired in Phase F (guide §10 agent overlay):
* cursor ``_follow_path`` — CursorPath (the planned glide points)
* cursor ``click_cursor`` / ``scroll_cursor`` / ``drag_cursor`` —
  InputAcquired around the dispatch (released in a finally) plus
  AgentAction (click label from the click-point identification,
  scroll direction, drag endpoints). Pure moves emit a path but
  no acquired/action event: mouse moves alone are not enough.
* control ``type_text`` / ``press_hotkey`` — InputAcquired around
  the injection (released in a finally) plus AgentAction (type
  carries a character COUNT only, never text; hotkey carries
  the combo). Typing into a password field is refused before
  any keystroke by the shared password rule, so a type chip
  never describes password input.
* uia ``click_by_mark`` / ``_uia_click_control_impl`` — TargetRect
  from the numeric grounding rectangle, plus the NAMED AgentAction
  (emitted after the cursor path's generic one, so the named chip
  wins). ``uia_type_text`` — AgentAction with the password flag
  from ``_is_password_control`` (password targets show "•••").
* uia ``verify_expectation`` — Verifying at entry (kind only,
  never the expectation text) and Verified on a "met" verdict.
  The in-progress VERIFYING display state is real for this
  engine's checks, and the coordinator verification phase
  (wiring item 3) drives these calls for coordinator tasks, so
  the task-level Verifying state is real too — no Verifying
  claim is made for anything else.
* vision ``_ocr_words_for_image`` — CaptureStarted/Finished with
  scope "ocr", bracketing the OCR pass the overlay's scan sweep
  follows. (Capture kinds from capture_guard keep their own
  scopes; "ocr" is new and collides with none of them.)
"""

from __future__ import annotations

from . import state_bus as _state_bus
from .state_bus import get_state_bus as _get_state_bus


def _bus():
    """Return the process-wide state bus, or None when unavailable.

    Direct import of the core bus (GAPS-28) — no importlib dance.
    Never raises.
    """
    try:
        return _get_state_bus()
    except Exception:  # noqa: BLE001 — try the next path
        return None


def _cls(name):
    """Return one bus event class by name, or None when unavailable.

    Same core module as the bus, so subscribers never miss it.
    """
    try:
        return getattr(_state_bus, name)
    except Exception:  # noqa: BLE001 — no such class, no event
        return None


def emit(event) -> None:
    """Queue one bus event. Never raises, never blocks a producer."""
    try:
        bus = _bus()
        if bus is None:
            return
        bus.emit(event)
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass


def emit_mission(goal="", phase="", step=0, steps_total=0, target_app="",
                 target_window="", permission="none", outcome="running",
                 evidence=()) -> None:
    """Emit a MissionState for a coordinator task transition."""
    cls = _cls("MissionState")
    if cls is None:
        return
    emit(cls(goal=goal, phase=phase, step=step, steps_total=steps_total,
             target_app=target_app, target_window=target_window,
             permission=permission, outcome=outcome,
             evidence=tuple(evidence)))


def emit_step(n, m, label="") -> None:
    """Emit a StepEvent (n-th step of m; m == 0 means unknown)."""
    cls = _cls("StepEvent")
    if cls is None:
        return
    emit(cls(n=n, m=m, label=label))


def emit_tool_started(kind, target="") -> None:
    """Emit a ToolStarted event for a tool execution."""
    cls = _cls("ToolStarted")
    if cls is None:
        return
    emit(cls(kind=kind, target=target))


def emit_tool_finished(status, kind="", target="") -> None:
    """Emit a ToolFinished event (success/failed/unknown/skipped)."""
    cls = _cls("ToolFinished")
    if cls is None:
        return
    emit(cls(status=status, kind=kind, target=target))


def emit_failsafe(reason="") -> None:
    """Emit a FailsafeTripped event for a watchdog trip."""
    cls = _cls("FailsafeTripped")
    if cls is None:
        return
    emit(cls(reason=reason))


def emit_capture_started(scope="") -> None:
    """Emit a CaptureStarted event for a 0→1 capture transition."""
    cls = _cls("CaptureStarted")
    if cls is None:
        return
    emit(cls(scope=scope))


def emit_capture_finished(scope="") -> None:
    """Emit a CaptureFinished event for a 1→0 capture transition."""
    cls = _cls("CaptureFinished")
    if cls is None:
        return
    emit(cls(scope=scope))


# ------------------------------------------------------- Phase F producers
def emit_target_rect(x, y, w, h, role="", name="") -> None:
    """Emit a TargetRect (numeric rectangle, Phase F overlay lock)."""
    cls = _cls("TargetRect")
    if cls is None:
        return
    try:
        emit(cls(x=int(x), y=int(y), w=int(w), h=int(h),
                 role=str(role or ""), name=str(name or "")))
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass


def emit_input_acquired(source="") -> None:
    """Emit an InputAcquired event (assistant input held, Phase F)."""
    cls = _cls("InputAcquired")
    if cls is None:
        return
    emit(cls(source=str(source or "")))


def emit_input_released(source="") -> None:
    """Emit an InputReleased event (assistant input free, Phase F)."""
    cls = _cls("InputReleased")
    if cls is None:
        return
    emit(cls(source=str(source or "")))


def emit_verified(evidence="") -> None:
    """Emit a Verified event (a "met" verification verdict, Phase F)."""
    cls = _cls("Verified")
    if cls is None:
        return
    emit(cls(evidence=str(evidence or "")))


def emit_verifying(kind="") -> None:
    """Emit a Verifying event (a check is running, Phase F).

    ``kind`` only — never expectation text (it may carry user
    content).
    """
    cls = _cls("Verifying")
    if cls is None:
        return
    emit(cls(kind=str(kind or "")))


def emit_cursor_path(points, source="") -> None:
    """Emit a CursorPath event (planned glide points, Phase F)."""
    cls = _cls("CursorPath")
    if cls is None:
        return
    try:
        clean = tuple((int(px), int(py)) for px, py in points)
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        return
    emit(cls(points=clean, source=str(source or "")))


def emit_agent_action(kind, label="", chars=0, password=False,
                      x=0, y=0) -> None:
    """Emit an AgentAction event (overlay chip, Phase F).

    Callers pass a control name, a direction, or a combo as
    ``label`` — NEVER typed text. ``type`` actions pass a
    character count in ``chars`` (0 + password=True for a
    password target, so not even a length leaks).
    """
    cls = _cls("AgentAction")
    if cls is None:
        return
    try:
        emit(cls(kind=str(kind or ""), label=str(label or ""),
                 chars=int(chars), password=bool(password),
                 x=int(x), y=int(y)))
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
