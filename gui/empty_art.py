"""Generative empty-state art (SENSES v5.24, Eyes E34).

A small seeded constellation that gives VYUK's empty states
a face instead of a void: golden-angle spiral points, faint threads
between near neighbours, soft dots in the accent colours, and one
halo ring near the centre. The art is a pure function of
(seed, size) — deterministic, identical on every machine, and
static: no timers, no animation, so it is reduced-motion-proof by
construction and costs one paint and nothing else.
"""

import math
import random

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

_PRIMARY = QColor(124, 140, 255)    # indigo accent
_SECONDARY = QColor(61, 214, 140)   # green accent, used sparingly


class GenerativeArt(QWidget):
    """Seeded constellation art for empty states."""

    POINT_COUNT = 46
    HEIGHT = 132
    LINK_DISTANCE = 48.0

    def __init__(self, seed: int = 7, parent=None):
        super().__init__(parent)
        self._seed = int(seed)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    @property
    def seed(self) -> int:
        return self._seed

    def set_seed(self, seed: int):
        """Re-seed the constellation and repaint."""
        self._seed = int(seed)
        self.update()

    # ------------------------------------------------------------ paint
    def _points(self, width: int, height: int):
        """The constellation's points for a given size: a
        golden-angle spiral (unit disc) mapped into the rect, with
        a seeded jitter so the figure feels grown, not stamped."""
        rng = random.Random(self._seed)
        golden = math.pi * (3.0 - math.sqrt(5.0))
        cx, cy = width / 2.0, height / 2.0
        rx = max(10.0, width / 2.0 - 12.0)
        ry = max(10.0, height / 2.0 - 12.0)
        pts = []
        for i in range(self.POINT_COUNT):
            frac = (i + 0.5) / self.POINT_COUNT
            r = math.sqrt(frac)
            a = i * golden
            x = cx + math.cos(a) * r * rx + rng.uniform(-3.5, 3.5)
            y = cy + math.sin(a) * r * ry + rng.uniform(-3.5, 3.5)
            pts.append((x, y))
        return pts

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 4 or h <= 4:
            painter.end()
            return
        pts = self._points(w, h)

        # Threads between near neighbours — the web the eye reads
        # as a constellation.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        link_pen = QPen(QColor(_PRIMARY.red(), _PRIMARY.green(),
                               _PRIMARY.blue(), 26), 1.0)
        painter.setPen(link_pen)
        limit2 = self.LINK_DISTANCE * self.LINK_DISTANCE
        for i in range(len(pts)):
            xi, yi = pts[i]
            for j in range(i + 1, len(pts)):
                dx = pts[j][0] - xi
                dy = pts[j][1] - yi
                if dx * dx + dy * dy <= limit2:
                    painter.drawLine(int(xi), int(yi),
                                     int(pts[j][0]), int(pts[j][1]))

        # Halo ring near the golden centre (the centroid of the
        # innermost spiral points), drawn under the dots.
        inner = pts[:12]
        hx = sum(p[0] for p in inner) / len(inner)
        hy = sum(p[1] for p in inner) / len(inner)
        halo_r = min(w, h) * 0.21
        painter.setPen(QPen(QColor(_PRIMARY.red(), _PRIMARY.green(),
                                   _PRIMARY.blue(), 50), 1.2))
        painter.drawEllipse(int(hx - halo_r), int(hy - halo_r),
                            int(2 * halo_r), int(2 * halo_r))

        # Dots — radius and alpha vary deterministically by index;
        # every seventh is the secondary accent.
        painter.setPen(Qt.PenStyle.NoPen)
        for i, (x, y) in enumerate(pts):
            radius = 1.0 + 1.6 * (((i * 37) % 10) / 9.0)
            alpha = 90 + ((i * 53) % 150)
            base = _SECONDARY if i % 7 == 3 else _PRIMARY
            painter.setBrush(QColor(base.red(), base.green(),
                                    base.blue(), alpha))
            painter.drawEllipse(int(x - radius), int(y - radius),
                                int(2 * radius), int(2 * radius))
        painter.end()
