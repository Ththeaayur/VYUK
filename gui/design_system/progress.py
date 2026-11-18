"""SegmentBar — segmented progress with a mono percentage (guide §8).

Replaces every progress bar in HUD surfaces. The bar shows completed
segments in the accent and the remaining segments as resting surface;
the percentage is mono text, never an invented precision (callers
pass real done/total counts).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..style import (
    ACCENT,
    MONO_FONT_STACK,
    SURFACE_3,
    TEXT,
    first_font_family,
)
from .palette import parse_color


class SegmentBar(QWidget):
    """A segmented progress bar with a mono percent label."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._done = 0
        self._total = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._bar = _BarWidget(self)
        self._label = QLabel("0%")
        self._label.setFont(QFont(first_font_family(MONO_FONT_STACK), 12))
        self._label.setStyleSheet("color: %s;" % TEXT)
        layout.addWidget(self._bar, 1)
        layout.addWidget(self._label)

    def set_progress(self, done, total):
        """Set real progress; total 0 means no progress is known."""
        self._done = max(0, int(done))
        self._total = max(0, int(total))
        if self._total > 0:
            pct = min(100, round(100 * self._done / self._total))
        else:
            pct = 0
        self._label.setText("%d%%" % pct)
        self._bar.set_progress(self._done, self._total)
        self.update()

    @property
    def percent_text(self):
        """The percent label text (tests pin the arithmetic)."""
        return self._label.text()

    @property
    def done(self):
        """Completed segments."""
        return self._done

    @property
    def total(self):
        """Total segments (0 = unknown)."""
        return self._total


class _BarWidget(QWidget):
    """The painted segments (internal to SegmentBar)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(8)
        self._done = 0
        self._total = 0

    def set_progress(self, done, total):
        self._done, self._total = done, total
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(SURFACE_3))
        if self._total > 0 and self._done > 0:
            width = int(rect.width() * min(1.0, self._done / self._total))
            painter.fillRect(0, 0, width, rect.height(), QColor(ACCENT))
        painter.end()
