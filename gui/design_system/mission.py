"""MissionRail and ActionTrace — the mission surface (guide §8).

MissionRail shows goal, phase, target app and window, permission
state, one current step, and stop / pause. ActionTrace runs the
request, plan, permission, action, observation, verification and
outcome steps as they arrive on the state bus. Both are consumers
only: with no producers they show the idle state honestly (no
mission, empty trace), never invented progress. The bus is cosmetic
here — killing these widgets never stops or delays an action.

Phase 0 HELD (UI programme Phase 1): no live surface exists yet —
Home has no "now doing" strip and Activity's In progress list is
ledger rows, not a live mission rail; wiring here would invent one.
BUILT in UI Phase 1 (2026-11-24): the rail is wired to the ledger
through the producer bridge (see below).

UI Phase 1 (Activity): the rail is wired to the ledger through
the producer bridge (the ledger's newest live record becomes a
MissionState on the bus; idle publishes the idle state). The
rail's left edge is a fixed 56 px ring zone — three concentric
rings, lit rings = live step progress (capped at 3); idle hides
the ring zone entirely (Activity empty-state fix 2026-11-26 —
the hollow ring + "Step 0 of 0" bar read as a broken widget);
Minimal shows a single accent dot. Under the
rail sits a SegmentBar ("Step n of m", never a percent). Stop
goes to the coordinator's cancel path; Pause goes to its pause
path (the seam exists — TurnCoordinator.pause_task). The
ActionTrace is QLabel rows now (the list's selection chrome
fought the row language); append_step / row_count / row_text
keep their exact semantics.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
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
from ..style import ACCENT, TEXT_FAINT
from .palette import qcolor
from .progress import SegmentBar

_RING_RADII = (8, 16, 24)
_RING_RADII_COMPACT = (6, 12, 18)
_RING_ALPHAS = (220, 160, 110)


class _RingZone(QWidget):
    """Three concentric rings in a fixed square (56 px; 40 px compact)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lit = 0
        self._compact = False
        self._minimal = False
        self.setFixedSize(56, 56)

    def set_lit(self, lit):
        """Lit rings = live step progress, capped at 3."""
        self._lit = max(0, min(3, int(lit or 0)))
        self.update()

    def set_compact(self, compact):
        """The 640 rail: a 40 px zone, radii 6/12/18."""
        self._compact = bool(compact)
        side = 40 if self._compact else 56
        self.setFixedSize(side, side)
        self.update()

    def set_minimal(self, minimal):
        """Minimal: rings off, a single 12 px accent dot."""
        self._minimal = bool(minimal)
        self.update()

    @property
    def lit_count(self):
        """Lit rings right now."""
        return self._lit

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        centre = self.rect().center()
        if self._minimal:
            painter.setPen(Qt.NoPen)
            painter.setBrush(qcolor(ACCENT))
            painter.drawEllipse(centre.x() - 6, centre.y() - 6, 12, 12)
            painter.end()
            return
        radii = _RING_RADII_COMPACT if self._compact else _RING_RADII
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._lit <= 0:
            # Idle: one hollow ring at the outer radius, text_faint.
            outer = radii[2]
            painter.setPen(QPen(qcolor(TEXT_FAINT), 1.5))
            painter.drawEllipse(centre.x() - outer, centre.y() - outer,
                                outer * 2, outer * 2)
            painter.end()
            return
        for index, radius in enumerate(radii):
            if index < self._lit:
                colour = qcolor(ACCENT)
                colour.setAlpha(_RING_ALPHAS[index])
                painter.setPen(QPen(colour, 2.0))
            else:
                colour = qcolor(TEXT_FAINT)
                colour.setAlpha(110)
                painter.setPen(QPen(colour, 1.0))
            painter.drawEllipse(centre.x() - radius, centre.y() - radius,
                                radius * 2, radius * 2)
        painter.end()


class MissionRail(QWidget):
    """Goal, phase, target, permission, current step, stop / pause."""

    stop_requested = Signal()
    pause_requested = Signal()

    def __init__(self, parent=None, bus=None):
        super().__init__(parent)
        self._bus = bus if bus is not None else get_state_bus()
        self._tokens = []
        self._live_count = 0
        self._steps_total = 0
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self._rings = _RingZone(self)
        outer.addWidget(self._rings, 0)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(2)
        self._goal = QLabel("No active mission")
        self._goal.setObjectName("sectionTitle")
        self._phase = QLabel("")
        self._target = QLabel("")
        self._permission = QLabel("")
        self._step = QLabel("")
        for label in (self._phase, self._target, self._permission,
                      self._step):
            label.setWordWrap(True)
        right.addWidget(self._goal)
        right.addWidget(self._phase)
        right.addWidget(self._target)
        right.addWidget(self._permission)
        right.addWidget(self._step)
        self._bar = SegmentBar(self)
        self._bar.set_label_mode("steps")
        self._bar.set_progress(0, 0)
        right.addWidget(self._bar)
        # The empty state (B-A03, as amended by the Activity
        # empty-state fix 2026-11-26): ONE clear line + one quiet
        # suggestion naming a real capability. The resting hollow
        # ring used to sit beside it, but the ring + "Step 0 of 0"
        # bar read as a broken widget when idle, so the rail
        # hides them (and the "No active mission" goal line,
        # which said the same thing a third time) while no
        # mission is live — _apply_mission owns that visibility.
        # The empty label is the page's one empty statement; the
        # headline + journal empty lines were removed with it.
        self._empty = QLabel(
            "Nothing in progress right now.\n"
            "Ask VYUK to start something.")
        self._empty.setObjectName("missionRailEmpty")
        self._empty.setWordWrap(True)
        right.addWidget(self._empty)
        row = QHBoxLayout()
        stop = QPushButton("Stop")
        pause = QPushButton("Pause")
        stop.clicked.connect(self.stop_requested.emit)
        pause.clicked.connect(self.pause_requested.emit)
        row.addWidget(stop)
        row.addWidget(pause)
        row.addStretch(1)
        right.addLayout(row)
        outer.addLayout(right, 1)
        self._tokens.append(self._bus.subscribe(MissionState, self._on_mission))
        self._tokens.append(self._bus.subscribe(StepEvent, self._on_step))
        self._tokens.append(self._bus.subscribe(ToolStarted, self._on_tool))
        # D-01 (Phase 6): closeEvent never fires for a child widget
        # when its parent window is deleted — unsubscribe on
        # destroyed too (closure captures only the bus and the token
        # list, never the rail).

        _bus = self._bus
        _tokens = self._tokens
        self.destroyed.connect(
            lambda: [_bus.unsubscribe(t) for t in list(_tokens)])

    def set_live_count(self, count):
        """Live missions in the same read (the goal line names it)."""
        self._live_count = max(0, int(count or 0))

    def set_compact(self, compact):
        """The 640 rail: the ring zone shrinks to 40 px."""
        self._rings.set_compact(compact)

    def set_minimal(self, minimal):
        """Minimal: rings off, a single accent dot."""
        self._rings.set_minimal(minimal)

    def set_theme(self, resolved: dict) -> None:
        """Recolour the rail's SegmentBar from a resolved theme
        (look-fix 2: the bar + "Step 0 of 0" label). The ring zone
        paints from the accent / faint tokens, which the window
        sheet already resolves, so only the bar needs it."""
        try:
            self._bar.set_theme(resolved)
        except Exception:  # noqa: BLE001 — theme never breaks the rail
            pass

    def lit_ring_count(self):
        """Lit rings right now (= step progress, capped at 3)."""
        return self._rings.lit_count

    def set_mission_state(self, mapped):
        """Apply one mapped ledger record (or None for idle).

        The producer bridge calls this directly (synchronous —
        the rail is correct immediately) and also publishes the
        MissionState on the bus; both paths converge here.
        """
        if not mapped:
            self._apply_mission(MissionState())
            return
        self.set_live_count(mapped.get("live_count", 1))
        self._apply_mission(MissionState(
            goal=mapped.get("goal", ""),
            phase=mapped.get("phase", ""),
            step=int(mapped.get("step", 0) or 0),
            steps_total=int(mapped.get("steps_total", 0) or 0),
            permission=mapped.get("permission", "none"),
            outcome="running"))

    def _apply_mission(self, state):
        goal = state.goal or "No active mission"
        if self._live_count > 1 and state.goal:
            # Two live missions: the rail shows the newest, and
            # the goal line names the count.
            goal = (f"{self._live_count} missions running — "
                    f"showing the newest\n{state.goal}")
        self._goal.setText(goal)
        # Plan §10: the goal line carries the theme accent while a
        # mission is live (and the rail host's edge does the same —
        # set by the page via set_edge("accent")); idle it is the
        # plain section title.
        try:
            self._goal.setStyleSheet(
                "color: %s;" % ACCENT if goal and state.goal else "")
        except Exception:  # noqa: BLE001
            pass
        self._phase.setText(f"Phase: {state.phase}" if state.phase else "")
        target = state.target_app or ""
        if state.target_window:
            target = (f"{target} — {state.target_window}"
                      if target else state.target_window)
        self._target.setText(f"Target: {target}" if target else "")
        # Phase D: "none" is the absence of a permission claim,
        # not a claim — the line stays empty until a producer
        # reports required/granted/denied.
        self._permission.setText(
            f"Permission: {state.permission}"
            if state.permission not in ("", "none") else "")
        self._steps_total = int(state.steps_total or 0)
        self._bar.set_progress(int(state.step or 0), self._steps_total)
        self._rings.set_lit(int(state.step or 0))
        live = bool(state.goal)
        self._empty.setVisible(not live)
        # Activity empty-state fix (2026-11-26): while no mission
        # is live the rail is ONLY the empty label. The SegmentBar
        # ("Step 0 of 0" + an empty track), the hollow ring, the
        # "No active mission" goal line, and the four detail
        # labels (empty while idle, so pure dead space) all hide —
        # together they read as a broken widget, not a designed
        # empty state. A live mission re-shows them all here, so
        # the rail is correct immediately on every refresh.
        self._bar.setVisible(live)
        self._rings.setVisible(live)
        self._goal.setVisible(live)
        for _label in (self._phase, self._target, self._permission,
                       self._step):
            _label.setVisible(live)
        if not live:
            self._step.setText("")

    def _on_mission(self, state):
        self._apply_mission(state)

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
    """The evidence trace: plan, action, verification, outcome rows.

    QLabel rows (the list's selection chrome fought the row
    language). append_step / row_count / row_text keep their
    exact semantics: a row reads "KIND › text".
    """

    def __init__(self, parent=None, bus=None):
        super().__init__(parent)
        self._bus = bus if bus is not None else get_state_bus()
        self._tokens = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._rows: list[QLabel] = []
        self._tokens.append(self._bus.subscribe(StepEvent, self._on_step))
        self._tokens.append(self._bus.subscribe(ToolStarted, self._on_tool))
        self._tokens.append(self._bus.subscribe(ToolFinished, self._on_done))
        self._tokens.append(self._bus.subscribe(Verified, self._on_verified))
        # D-01 (Phase 6): as above — unsubscribe on destroyed too.
        _bus = self._bus
        _tokens = self._tokens
        self.destroyed.connect(
            lambda: [_bus.unsubscribe(t) for t in list(_tokens)])

    def append_step(self, kind, text):
        """Append one trace row (kind is uppercased in the row)."""
        label = QLabel(f"{str(kind).upper()} › {text}")
        label.setWordWrap(True)
        self._layout.addWidget(label)
        self._rows.append(label)

    def clear(self):
        """Drop every trace row."""
        for label in self._rows:
            self._layout.removeWidget(label)
            label.deleteLater()
        self._rows = []

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
        return len(self._rows)

    def row_text(self, index):
        """The text of one trace row."""
        if 0 <= index < len(self._rows):
            return self._rows[index].text()
        return ""

    def closeEvent(self, event):
        for token in self._tokens:
            self._bus.unsubscribe(token)
        super().closeEvent(event)
