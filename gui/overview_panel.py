"""Live overview panel (Wave 9 / v5.64, Leaf 3).

FIXED-LAYOUT default. The panel is a plain vertical stack: a plot, a
duration strip, and a native activity list. There is NO docking here,
no detachable panes, no floating windows — that behaviour lives in
the separate pilot module ``gui/docking.py`` and is NEVER
imported by this module by default (importing it requires an explicit
flag plus a licence ruling; see that module's gate record).

Plotting engine: pyqtgraph is the native plotting engine, but it is
NEVER a hard dependency. It is imported lazily inside
:func:`probe_pyqtgraph` / :func:`make_plot_widget` only, and the
panel always works without it via :class:`FallbackPlotWidget`, a
small QWidget that paints the same series as a polyline. If
pyqtgraph is absent the panel renders the fallback and reports
``engine="fallback"`` — it never crashes, never blocks, and never
asks the user to install anything.

Activity stream: built natively as list rows (QListWidget items) —
there is no Qt timeline widget, and none is invented here. Each row
is ``time · kind · text``.

Duration strip: a custom painted QWidget. Segments are proportional
bars (label + share of the total); it is NOT a timeline widget, it
does not scroll through time, and it makes no claim about wall-clock
history beyond the labels the caller supplies.

Licence (verified from the sdist primary, not metadata):
pyqtgraph 0.13.7, LICENSE.txt — MIT (Univ. of North Carolina at
Chapel Hill, Luke Campagnola). The wheel installed test-only for
rendering (0.14.0) carries the same MIT text. pyqtgraph is NOT in
requirements.txt and is NOT bundled: the lazy probe is the whole
integration. If a future build wants it bundled, that is a
coordinator decision with the licence recorded in
THIRD_PARTY_LICENSES.md.

MODULE_VERSION = "5.64.0" (per-leaf version; core/version.py is
coordinator-owned and still reads 5.63.0 until assembly).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen

# Command Deck Phase E: the overview paints from the token palette.
from .style import ACCENT_2, AMBER, BG, OK, TEXT_MUTED
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

MODULE_VERSION = "5.64.0"

# ---------------------------------------------------------------- probe


def probe_pyqtgraph() -> dict:
    """Lazy availability probe. Never raises, never imports at module load.

    Returns ``{"available": bool, "version": str | None, "error": str | None}``.
    """
    spec = importlib.util.find_spec("pyqtgraph")
    if spec is None:
        return {"available": False, "version": None,
                "error": "pyqtgraph not installed"}
    try:
        import pyqtgraph  # noqa: PLC0415 — lazy by design

        return {"available": True,
                "version": getattr(pyqtgraph, "__version__", "unknown"),
                "error": None}
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return {"available": False, "version": None, "error": str(exc)}


# ---------------------------------------------------------------- data


@dataclass
class ActivityEvent:
    """One row of the activity stream."""

    time_label: str
    kind: str
    text: str


@dataclass
class DurationSegment:
    """One bar of the duration strip (seconds; labels are caller-supplied)."""

    label: str
    seconds: float
    color: str = ACCENT_2


@dataclass
class PlotSeries:
    """A single named series for the plot widget."""

    label: str
    xs: list = field(default_factory=list)
    ys: list = field(default_factory=list)


# ---------------------------------------------------------------- widgets


class FallbackPlotWidget(QWidget):
    """Polyline plot used when pyqtgraph is unavailable.

    Paints every series in :attr:`series` scaled to the widget rect.
    Deliberately plain: it is a fallback, not a second plotting engine.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.series: list[PlotSeries] = []
        self.setMinimumHeight(140)

    def set_series(self, series: list[PlotSeries]) -> None:
        self.series = list(series)
        self.update()

    def paintEvent(self, _event):  # noqa: N802 — Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BG))
        if not self.series:
            painter.setPen(QPen(QColor(TEXT_MUTED)))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "No data — pyqtgraph unavailable, fallback idle")
            return
        all_x = [x for s in self.series for x in s.xs]
        all_y = [y for s in self.series for y in s.ys]
        if not all_x or not all_y:
            return
        x0, x1 = min(all_x), max(all_x)
        y0, y1 = min(all_y), max(all_y)
        if x1 == x0:
            x1 = x0 + 1
        if y1 == y0:
            y1 = y0 + 1
        w, h = max(self.width() - 16, 1), max(self.height() - 16, 1)
        colors = [ACCENT_2, AMBER, OK]
        for i, s in enumerate(self.series):
            if len(s.xs) < 2:
                continue
            pen = QPen(QColor(colors[i % len(colors)]))
            pen.setWidth(2)
            painter.setPen(pen)
            pts = []
            for x, y in zip(s.xs, s.ys):
                px = 8 + (x - x0) / (x1 - x0) * w
                py = 8 + h - (y - y0) / (y1 - y0) * h
                pts.append((px, py))
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                painter.drawLine(int(ax), int(ay), int(bx), int(by))


def make_plot_widget(series: list[PlotSeries]):
    """Return ``(widget, engine)`` where engine is ``"pyqtgraph"`` or ``"fallback"``.

    pyqtgraph is imported here, lazily, and only here. Callers that
    need to know which engine rendered must read the returned tag —
    the widget itself never advertises more than it is.
    """
    probe = probe_pyqtgraph()
    if probe["available"]:
        import pyqtgraph as pg  # noqa: PLC0415 — lazy by design

        widget = pg.PlotWidget()
        widget.setMinimumHeight(140)
        for s in series:
            widget.plot(s.xs, s.ys, pen=pg.mkPen(width=2), name=s.label)
        widget.setTitle("Activity")
        return widget, "pyqtgraph"
    widget = FallbackPlotWidget()
    widget.set_series(series)
    return widget, "fallback"


class DurationStrip(QWidget):
    """Proportional bars for a list of :class:`DurationSegment`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments: list[DurationSegment] = []
        self.setMinimumHeight(36)

    def set_segments(self, segments: list[DurationSegment]) -> None:
        self.segments = [s for s in segments if s.seconds > 0]
        self.update()

    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.segments)

    def paintEvent(self, _event):  # noqa: N802 — Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BG))
        total = self.total_seconds()
        if total <= 0:
            painter.setPen(QPen(QColor(TEXT_MUTED)))
            painter.drawText(self.rect(), Qt.AlignCenter, "No durations")
            return
        x = 0
        width = self.width()
        for seg in self.segments:
            seg_w = max(int(width * seg.seconds / total), 1)
            painter.fillRect(x, 4, seg_w, self.height() - 8,
                             QColor(seg.color))
            x += seg_w


# ---------------------------------------------------------------- panel


class OverviewPanel(QWidget):
    """Fixed-layout live overview: plot + duration strip + activity rows.

    Layout is a plain QVBoxLayout. Nothing here is dockable,
    detachable, or floating. ``engine`` reports which plot engine
    actually rendered (``"pyqtgraph"`` or ``"fallback"``).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self.title = QLabel("Overview")
        self._layout.addWidget(self.title)
        self.plot_widget, self.engine = make_plot_widget([])
        self._layout.addWidget(self.plot_widget)
        self.duration_strip = DurationStrip()
        self._layout.addWidget(self.duration_strip)
        self.activity_list = QListWidget()
        self._layout.addWidget(self.activity_list)

    # -- data in -------------------------------------------------
    def set_series(self, series: list[PlotSeries]) -> None:
        """Replace the plotted series, keeping the same engine widget."""
        if self.engine == "pyqtgraph":
            self.plot_widget.clear()
            import pyqtgraph as pg  # noqa: PLC0415 — lazy by design

            for s in series:
                self.plot_widget.plot(s.xs, s.ys, pen=pg.mkPen(width=2),
                                      name=s.label)
        else:
            self.plot_widget.set_series(series)

    def set_durations(self, segments: list[DurationSegment]) -> None:
        self.duration_strip.set_segments(segments)

    def add_activity(self, event: ActivityEvent) -> None:
        item = QListWidgetItem(f"{event.time_label} · {event.kind} · {event.text}")
        self.activity_list.addItem(item)

    def activity_count(self) -> int:
        return self.activity_list.count()
