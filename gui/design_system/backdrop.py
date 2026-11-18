"""HudBackdrop — grid, scanlines and vignette, painted once (guide §8).

The backdrop is rendered to a cached pixmap and repainted only when
the widget resizes, so ambient layers cost nothing per frame. Grid
cells are 48 px; scanlines are drawn at low alpha. Profiles switch
layers off (Phase D wiring); this component only owns the painting.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from ..style import BG, LINE_SUBTLE
from .palette import parse_color

GRID_PX = 48


class HudBackdrop(QWidget):
    """A cached ambient backdrop: grid + scanlines + vignette."""

    def __init__(self, parent=None, grid=True, scanlines=True, vignette=True):
        super().__init__(parent)
        self._show_grid = grid
        self._show_scanlines = scanlines
        self._show_vignette = vignette
        self._cache = None

    def _render(self):
        size = self.size()
        pixmap = QPixmap(size)
        pixmap.fill(QColor(BG))
        if size.width() <= 0 or size.height() <= 0:
            return pixmap
        painter = QPainter(pixmap)
        line = QColor(LINE_SUBTLE)
        painter.setPen(line)
        if self._show_grid:
            x = 0
            while x <= size.width():
                painter.drawLine(x, 0, x, size.height())
                x += GRID_PX
            y = 0
            while y <= size.height():
                painter.drawLine(0, y, size.width(), y)
                y += GRID_PX
        if self._show_scanlines:
            _r, _g, _b, alpha = parse_color(LINE_SUBTLE)
            faint = QColor(line)
            faint.setAlpha(max(0, alpha // 3))
            painter.setPen(faint)
            y = 2
            while y <= size.height():
                painter.drawLine(0, y, size.width(), y)
                y += 4
        if self._show_vignette:
            gradient = QRadialGradient(
                size.width() / 2, size.height() / 2,
                max(size.width(), size.height()) * 0.75,
            )
            clear = QColor(BG)
            clear.setAlpha(0)
            dark = QColor(BG)
            dark.setAlpha(90)
            gradient.setColorAt(0.0, clear)
            gradient.setColorAt(1.0, dark)
            painter.fillRect(0, 0, size.width(), size.height(), gradient)
        painter.end()
        return pixmap

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cache = self._render()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._cache is None or self._cache.size() != self.size():
            self._cache = self._render()
        painter.drawPixmap(0, 0, self._cache)
        painter.end()

    @property
    def cache_size(self):
        """The cached pixmap size (tests assert it tracks resizes)."""
        return None if self._cache is None else self._cache.size()
