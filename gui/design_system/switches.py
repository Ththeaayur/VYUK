"""HudSwitch — a square-knob toggle with a 40 px hit area (guide §8).

The visible switch is 34 by 16 px; the widget itself is at least
40 by 40 px so the hit area passes the §11 hit-area guard. Keyboard
space toggles it (QAbstractButton behaviour). Checked state fills
the accent; unchecked state rests neutral.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton

from ..style import ACCENT, LINE_SUBTLE, NEUTRAL, SURFACE_2, TEXT


class HudSwitch(QAbstractButton):
    """A 34x16 square-knob switch on a 40x40 hit area."""

    def __init__(self, parent=None, checked=False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setMinimumSize(40, 40)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def sizeHint(self):
        """The hit area: 40 by 40 logical pixels, always."""
        return QSize(40, 40)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect()
        # Centre the 34x16 track in the hit area.
        tx = rect.x() + (rect.width() - 34) // 2
        ty = rect.y() + (rect.height() - 16) // 2
        painter.fillRect(tx, ty, 34, 16, QColor(SURFACE_2))
        painter.setPen(QColor(LINE_SUBTLE))
        painter.drawRect(tx, ty, 34, 16)
        if self.isChecked():
            painter.fillRect(tx, ty, 34, 16, QColor(ACCENT))
            painter.fillRect(tx + 20, ty + 2, 12, 12, QColor(TEXT))
            painter.setPen(QColor(ACCENT))
            painter.drawRect(tx, ty, 34, 16)
        else:
            painter.fillRect(tx + 2, ty + 2, 12, 12, QColor(NEUTRAL))
        painter.end()
