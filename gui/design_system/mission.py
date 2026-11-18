"""MissionRail and ActionTrace — the mission surface (guide §8).

MissionRail shows goal, phase, target app and window, permission
state, one current step, and stop / pause. ActionTrace runs the
request, plan, permission, action, observation, verification and
outcome steps as they arrive on the state bus. Both are consumers
only: with no producers they show the idle state honestly (no
mission, empty trace), never invented progress. The bus is cosmetic
here — killing these widgets never stops or delays an action.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..state_bus import (
    MissionState,
    StepEvent,
    ToolFinished,
    ToolStarted,
    Verified,
    get_state_bus,
)


class MissionRail(QWidget):
    """Goal, phase, target, permission, current step, stop / pause."""

    stop_requested = Signal()
    pause_requested = Signal()

    def __init__(self, parent=None, bus=None):
        super().__init__(parent)
        self._bus = bus if bus is not None else get_state_bus()
        self._tokens = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._goal = QLabel("No active mission")
        self._goal.setObjectName("sectionTitle")
        self._phase = QLabel("")
        self._target = QLabel("")
        self._permission = QLabel("")
        self._step = QLabel("")
        for label in (self._phase, self._target, self._permission, self._step):
            label.setWordWrap(True)
        layout.addWidget(self._goal)
        layout.addWidget(self._phase)
        layout.addWidget(self._target)
        layout.addWidget(self._permission)
        layout.addWidget(self._step)
        row = QHBoxLayout()
        stop = QPushButton("Stop")
        pause = QPushButton("Pause")
        stop.clicked.connect(self.stop_requested.emit)
        pause.clicked.connect(self.pause_requested.emit)
        row.addWidget(stop)
        row.addWidget(pause)
        row.addStretch(1)
        layout.addLayout(row)
        self._tokens.append(self._bus.subscribe(MissionState, self._on_mission))
        self._tokens.append(self._bus.subscribe(StepEvent, self._on_step))
        self._tokens.append(self._bus.subscribe(ToolStarted, self._on_tool))

    def _on_mission(self, state):
        self._goal.setText(state.goal or "No active mission")
        self._phase.setText(f"Phase: {state.phase}" if state.phase else "")
        target = state.target_app or ""
        if state.target_window:
            target = f"{target} — {state.target_window}" if target else state.target_window
        self._target.setText(f"Target: {target}" if target else "")
        # Phase D: "none" is the absence of a permission claim,
        # not a claim — the line stays empty until a producer
        # reports required/granted/denied.
        self._permission.setText(
            f"Permission: {state.permission}"
            if state.permission not in ("", "none") else "")
        if state.steps_total:
            self._step.setText(f"Step {state.step} of {state.steps_total}")

    def _on_step(self, event):
        if event.m:
            self._step.setText(f"Step {event.n} of {event.m}")
        if event.label:
            self._step.setText(f"{self._step.text()} — {event.label}".strip(" —"))

    def _on_tool(self, event):
        text = event.kind
        if event.target:
            text = f"{text} › {event.target}"
        self._step.setText(f"Current: {text}")

    @property
    def goal_text(self):
        """The goal line (idle text when no mission exists)."""
        return self._goal.text()

    @property
    def step_text(self):
        """The current-step line."""
        return self._step.text()

    def closeEvent(self, event):
        for token in self._tokens:
            self._bus.unsubscribe(token)
        super().closeEvent(event)


class ActionTrace(QWidget):
    """The evidence trace: plan, action, verification, outcome rows."""

    def __init__(self, parent=None, bus=None):
        super().__init__(parent)
        self._bus = bus if bus is not None else get_state_bus()
        self._tokens = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        layout.addWidget(self._list)
        self._tokens.append(self._bus.subscribe(StepEvent, self._on_step))
        self._tokens.append(self._bus.subscribe(ToolStarted, self._on_tool))
        self._tokens.append(self._bus.subscribe(ToolFinished, self._on_done))
        self._tokens.append(self._bus.subscribe(Verified, self._on_verified))

    def append_step(self, kind, text):
        """Append one trace row (kind is uppercased in the row)."""
        self._list.addItem(f"{kind.upper()} › {text}")

    def _on_step(self, event):
        label = event.label or f"step {event.n} of {event.m}"
        self.append_step("plan", label)

    def _on_tool(self, event):
        text = event.kind
        if event.target:
            text = f"{text} › {event.target}"
        self.append_step("action", text)

    def _on_done(self, event):
        self.append_step("outcome", event.status)

    def _on_verified(self, event):
        self.append_step("verification", event.evidence or "verified")

    @property
    def row_count(self):
        """Trace rows currently shown."""
        return self._list.count()

    def row_text(self, index):
        """The text of one trace row."""
        item = self._list.item(index)
        return "" if item is None else item.text()

    def closeEvent(self, event):
        for token in self._tokens:
            self._bus.unsubscribe(token)
        super().closeEvent(event)
