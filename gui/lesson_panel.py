"""U6 — Visual channel widgets (tutor programme, 2026-11-08).

The lesson panel and the progressive board for the settled home.
These widgets render state dataclasses from
``core.brain.visual_channel`` only — they hold no bite, no gold,
no worked solution, and no model. Every string they show was
gold-guarded before it reached them; they never guard anything
themselves, because they must never know the gold.

Placement: the panel is a child of the settled home's docked chat
panel (``gui/window_shell_assistant.py`` inserts it above the chat
scroll area, hidden until a lesson starts). It is NOT a top-level
window, NOT a dialog, and it does not redesign Home or touch Aura.
The board is a child of the panel. The premium bar applies: plain
stack, themed object names, no debug-overlay look.

Painting: the board paints its elements natively with QPainter —
no plotting dependency, following the overview_panel fallback
pattern (paint natively, never crash, never ask for installs).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import (
    LADDER_LEVELS,
    LessonPanelState,
)

# Board space is 0–100 in both axes (visual_channel); the widget
# scales it to its own rect with a small margin.
_MARGIN = 8


class BoardWidget(QWidget):
    """The progressive board: paints the board's current elements
    — the picture as it stands, accumulated across steps. Stepping
    back repaints the earlier picture; no step ever erases an
    earlier element (that is the board's state, not the widget)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("lessonBoard")
        self.setMinimumSize(320, 200)
        self._board = None

    def set_board(self, board):
        """Attach a ProgressiveBoard (or None for an empty board)."""
        self._board = board
        self.update()

    def board(self):
        return self._board

    def current_elements(self):
        board = self._board
        if board is None:
            return ()
        return board.current_elements()

    def board_labels(self):
        """Every label currently on the board (for tests/prover)."""
        board = self._board
        if board is None:
            return ()
        return board.current_labels()

    def step_back(self) -> bool:
        if self._board is None:
            return False
        moved = self._board.step_back()
        if moved:
            self.update()
        return moved

    def step_forward(self) -> bool:
        if self._board is None:
            return False
        moved = self._board.step_forward()
        if moved:
            self.update()
        return moved

    def to_image(self):
        """Render the board as it stands to a QImage — the chat
        image path (the diagram the tutor draws, as a message)."""
        size = self.size()
        width = max(1, size.width() or self.minimumWidth())
        height = max(1, size.height() or self.minimumHeight())
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(self.palette().base().color())
        painter = QPainter(image)
        try:
            # PySide6's render(painter) overload needs the target
            # offset — the bare one-argument call is not bound.
            self.render(painter, QPoint(0, 0))
        finally:
            painter.end()
        return image

    def _map(self, x, y):
        """Board space (0–100) -> widget pixels."""
        rect = self.rect()
        width = max(1, rect.width() - 2 * _MARGIN)
        height = max(1, rect.height() - 2 * _MARGIN)
        return (
            _MARGIN + (float(x) / 100.0) * width,
            _MARGIN + (float(y) / 100.0) * height,
        )

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color = self.palette().text().color()
        accent = self.palette().highlight().color()
        board = self._board
        if board is None or board.step_count == 0:
            painter.setPen(text_color)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "No diagram yet — the board builds as the lesson goes.")
            return
        for element in board.current_elements():
            x0, y0 = self._map(element.x0, element.y0)
            x1, y1 = self._map(element.x1, element.y1)
            if element.kind in ("rect", "ellipse", "line", "arrow"):
                painter.setPen(QPen(text_color, 2))
            if element.kind == "rect":
                painter.drawRect(int(x0), int(y0),
                                 int(x1 - x0), int(y1 - y0))
            elif element.kind == "ellipse":
                painter.drawEllipse(int(x0), int(y0),
                                    int(x1 - x0), int(y1 - y0))
            elif element.kind == "line":
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))
            elif element.kind == "arrow":
                painter.setPen(QPen(accent, 2))
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))
                self._arrow_head(painter, x0, y0, x1, y1, accent)
            elif element.kind == "point":
                painter.setBrush(text_color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(int(x0) - 3, int(y0) - 3, 6, 6)
            # A label rides on its element: drawn near the end
            # point for lines/arrows (offset so it never sits on
            # the geometry — an "n" at an arrow tip hid behind the
            # arrowhead before this), at its anchor for a bare
            # label element.
            if element.label:
                painter.setPen(accent)
                if element.kind == "label":
                    painter.drawText(int(x0) + 8, int(y0) - 4,
                                     element.label)
                else:
                    painter.drawText(int(x1) + 8, int(y1) - 4,
                                     element.label)

    @staticmethod
    def _arrow_head(painter, x0, y0, x1, y1, color):
        """Two short strokes at the head of an arrow."""
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1:
            return
        angle = math.atan2(dy, dx)
        size = 10.0
        for delta in (math.pi * 5 / 6, -math.pi * 5 / 6):
            head_angle = angle + delta
            hx = x1 + size * math.cos(head_angle)
            hy = y1 + size * math.sin(head_angle)
            painter.setPen(QPen(color, 2))
            painter.drawLine(int(x1), int(y1), int(hx), int(hy))


class LessonPanel(QWidget):
    """The lesson panel: bite text, check question, hint-ladder
    level, mastery bar, source attribution, and the progressive
    board — all from one LessonPanelState. The panel stores only
    the state dataclass. It never holds the bite, the gold, or the
    model, so it cannot write mastery and cannot leak the answer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("lessonPanel")
        self._state = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.title_lbl = QLabel("Lesson")
        self.title_lbl.setObjectName("lessonTitle")
        layout.addWidget(self.title_lbl)

        self.bite_lbl = QLabel()
        self.bite_lbl.setObjectName("lessonBite")
        self.bite_lbl.setWordWrap(True)
        layout.addWidget(self.bite_lbl)

        self.check_lbl = QLabel()
        self.check_lbl.setObjectName("lessonCheck")
        self.check_lbl.setWordWrap(True)
        layout.addWidget(self.check_lbl)

        ladder_row = QHBoxLayout()
        self.ladder_lbl = QLabel()
        self.ladder_lbl.setObjectName("lessonLadder")
        ladder_row.addWidget(self.ladder_lbl)
        self.ladder_dots = []
        for _ in range(LADDER_LEVELS):
            dot = QLabel("○")
            dot.setObjectName("lessonLadderDot")
            self.ladder_dots.append(dot)
            ladder_row.addWidget(dot)
        ladder_row.addStretch()
        layout.addLayout(ladder_row)

        mastery_row = QHBoxLayout()
        self.mastery_bar = QProgressBar()
        self.mastery_bar.setObjectName("lessonMasteryBar")
        self.mastery_bar.setRange(0, 100)
        self.mastery_bar.setValue(0)
        self.mastery_bar.setTextVisible(False)
        mastery_row.addWidget(self.mastery_bar, stretch=1)
        self.mastery_lbl = QLabel()
        self.mastery_lbl.setObjectName("lessonMastery")
        mastery_row.addWidget(self.mastery_lbl)
        layout.addLayout(mastery_row)

        self.source_lbl = QLabel()
        self.source_lbl.setObjectName("lessonSource")
        layout.addWidget(self.source_lbl)

        self.board = BoardWidget(self)
        layout.addWidget(self.board)

    def show_state(self, state: LessonPanelState):
        """Render a panel state and show the panel. The state is
        the only thing the panel ever holds."""
        self._state = state
        self.bite_lbl.setText(state.bite_text)
        self.check_lbl.setText(state.check)
        self.ladder_lbl.setText(
            f"Hint level {state.ladder_level} of {state.ladder_max}")
        for i, dot in enumerate(self.ladder_dots):
            dot.setText("●" if i < state.ladder_level else "○")
        self.mastery_bar.setValue(int(round(state.mastery_p * 100)))
        transfers = state.transfers
        word = "transfer" if transfers == 1 else "transfers"
        self.mastery_lbl.setText(
            f"{transfers} clean {word}"
            + (" — mastered" if state.mastered else ""))
        if state.source:
            self.source_lbl.setText(f"Source: {state.source}")
            self.source_lbl.setVisible(True)
        else:
            self.source_lbl.setText("")
            self.source_lbl.setVisible(False)
        self.setVisible(True)

    def clear(self):
        """Hide the panel again (no lesson in progress)."""
        self._state = None
        self.setVisible(False)

    def state(self):
        """The state currently shown, or None."""
        return self._state

    def rendered_texts(self):
        """Every text the panel currently shows (for tests/prover
        gold-containment: no rendered string may contain the gold)."""
        texts = [
            self.bite_lbl.text(),
            self.check_lbl.text(),
            self.ladder_lbl.text(),
            self.mastery_lbl.text(),
            self.source_lbl.text(),
        ]
        texts.extend(self.board.board_labels())
        return [t for t in texts if t]
