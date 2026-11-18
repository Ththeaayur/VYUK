"""HudPanel — the shared resting surface (guide §8).

A chamfered panel with a 1 px role line, bright corner ticks at the
top-left and bottom-right, and a faint top wash. It replaces the
rounded glass cards: callers put content in content_layout() exactly
as they did with a plain frame, and the frame is painted here instead
of by a stylesheet gradient. The edge colour is a role line by
default; set_edge("danger") or set_edge("amber") re-roles it. All
colours arrive as runtime token values — this file holds no literals.
"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from ..style import (
    ACCENT,
    AMBER,
    DANGER,
    DISPLAY_FONT_STACK,
    first_font_family,
    LINE_STRONG,
    LINE_SUBTLE,
    SURFACE_2,
    TEXT_FAINT,
)
from .motion import CHAMFER_PX, TICK_PX
from .palette import lighten

_EDGES = {
    "line": LINE_STRONG,
    "subtle": LINE_SUBTLE,
    "amber": AMBER,
    "danger": DANGER,
}


def chamfered_path(rect, cut):
    """A rectangle with all four corners cut by ``cut`` pixels."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath()
    path.moveTo(x + cut, y)
    path.lineTo(x + w - cut, y)
    path.lineTo(x + w, y + cut)
    path.lineTo(x + w, y + h - cut)
    path.lineTo(x + w - cut, y + h)
    path.lineTo(x + cut, y + h)
    path.lineTo(x, y + h - cut)
    path.lineTo(x, y + cut)
    path.closeSubpath()
    return path


class HudPanel(QFrame):
    """A resting surface with a chamfered role-line frame.

    Drop-in for a plain frame: content goes in content_layout().
    An optional eyebrow micro-label is rendered first, in tracked
    mono capitals, when ``eyebrow`` is given.
    """

    def __init__(self, parent=None, eyebrow=None, fill=None, edge="line"):
        super().__init__(parent)
        self.setFrameStyle(QFrame.NoFrame)
        self._fill = fill or SURFACE_2
        self._edge = _EDGES.get(edge, LINE_STRONG)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(6)
        if eyebrow:
            label = QLabel(eyebrow)
            font = QFont(first_font_family(DISPLAY_FONT_STACK), 11)
            font.setLetterSpacing(QFont.PercentageSpacing, 120)
            label.setFont(font)
            label.setStyleSheet("color: %s;" % TEXT_FAINT)
            self._layout.addWidget(label)

    def content_layout(self):
        """The layout callers add content to."""
        return self._layout

    def set_edge(self, kind):
        """Re-role the frame line: line, subtle, amber or danger."""
        self._edge = _EDGES.get(kind, LINE_STRONG)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = chamfered_path(rect, CHAMFER_PX)
        painter.fillPath(path, QBrush(QColor(self._fill)))
        # Faint top wash: the fill lightened toward the top edge,
        # fading back to the fill over the top strip.
        wash = QLinearGradient(0, rect.top(), 0, rect.top() + 28)
        wash.setColorAt(0.0, QColor(lighten(self._fill, 0.12)))
        wash.setColorAt(1.0, QColor(self._fill))
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(rect.x(), rect.top(), rect.width(), 28, wash)
        painter.restore()
        painter.setPen(QPen(QColor(self._edge), 1))
        painter.drawPath(path)
        # Bright corner ticks, top-left and bottom-right.
        tick_pen = QPen(QColor(ACCENT), 2)
        painter.setPen(tick_pen)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        painter.drawPolyline(
            [QPoint(x, y + TICK_PX), QPoint(x, y), QPoint(x + TICK_PX, y)]
        )
        painter.drawPolyline(
            [
                QPoint(x + w - TICK_PX, y + h),
                QPoint(x + w, y + h),
                QPoint(x + w, y + h - TICK_PX),
            ]
        )
        painter.end()
