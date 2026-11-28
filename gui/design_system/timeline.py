"""Timeline spine — the Scheduled tab's vertical spine + now-dot.

The spine is a 1 px line down the timeline; the now-dot sits on
it at the boundary between the last past event and the first
future one. The position is a pure function of the event epochs
and ``now`` at each Activity refresh — no background timer (a
stale hinge on a long-open tab is accepted and stated). The
spine + now-dot render only when the timeline has events.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..style import ACCENT, LINE_SUBTLE
from .palette import qcolor


def now_marker_fraction(epochs, now):
    """Where ``now`` sits between the first and last event epochs.

    The fraction of the span (first..last) that has passed,
    clamped to [0, 1]: 0.0 when now is at or before the first
    event, 1.0 when it is at or after the last. Fewer than two
    epochs means no span to measure — 0.0.
    """
    epochs = sorted(float(e) for e in (epochs or []))
    if len(epochs) < 2:
        return 0.0
    first, last = epochs[0], epochs[-1]
    if last <= first:
        return 0.0
    fraction = (float(now) - first) / (last - first)
    return max(0.0, min(1.0, fraction))


class TimelineSpine(QWidget):
    """A vertical spine with a now-dot at the live fraction."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._epochs = []
        self._now = None
        self.setFixedWidth(12)

    def set_epochs(self, epochs):
        """The timeline's event epochs (seconds since the epoch)."""
        self._epochs = sorted(float(e) for e in (epochs or []))
        self.update()

    def set_now(self, now):
        """The current epoch; the dot moves to its fraction."""
        self._now = float(now) if now is not None else None
        self.update()

    @property
    def now_fraction(self):
        """The dot's fraction down the spine, or None (no events)."""
        if not self._epochs or self._now is None:
            return None
        return now_marker_fraction(self._epochs, self._now)

    def paintEvent(self, event):
        # No events, no spine: the spine renders only when the
        # timeline has events (B-A01).
        if not self._epochs:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        x = rect.center().x()
        painter.setPen(qcolor(LINE_SUBTLE))
        painter.drawLine(x, 0, x, rect.height())
        fraction = self.now_fraction
        if fraction is not None:
            y = int(round(fraction * rect.height()))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(x - 4, max(0, y - 4), 8, 8)
        painter.end()
