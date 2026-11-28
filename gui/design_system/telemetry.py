"""TelemetryPanel — metric rows with a named source (guide §8).

Every row carries metric, value, unit, last updated, trend and
source, plus a text alternative (the row itself is text). The
honesty rule is enforced here: a metric without a source renders
its value as NOT MEASURED, never as a plausible number.

Phase 0 HELD (UI programme Phase 1 Home readouts / Phase 4 Pulse):
no readout surface exists yet that this replaces without a
restructure — Home's briefing is one line, Pulse's meters are live
BarMeters. The panel's labels carry no colours, so it is
theme-safe as-is when its phase wires it.
"""

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

_TREND_GLYPHS = {"up": "▲", "down": "▼", "flat": "–"}


class TelemetryPanel(QWidget):
    """Metric rows that name their source — or say NOT MEASURED."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._rows = 0
        self._cells = {}

    def add_metric(self, name, value, unit="", source="", trend=None,
                   updated_at=None):
        """Add one metric row. Empty source forces NOT MEASURED."""
        row = self._rows
        self._rows += 1
        shown_value = value if source else "NOT MEASURED"
        shown_source = source if source else "NOT MEASURED"
        stamp = updated_at or QTime.currentTime().toString("hh:mm")
        trend_text = _TREND_GLYPHS.get(trend, "") if trend else ""
        cells = {
            "name": QLabel(name),
            "value": QLabel(f"{shown_value} {unit}".strip()),
            "trend": QLabel(trend_text),
            "source": QLabel(shown_source),
            "updated": QLabel(stamp),
        }
        for col, key in enumerate(("name", "value", "trend", "source", "updated")):
            self._layout.addWidget(cells[key], row, col)
        self._cells[name] = cells
        return cells

    def metric_value_text(self, name):
        """The rendered value cell for one metric (or '')."""
        cells = self._cells.get(name)
        return "" if cells is None else cells["value"].text()

    def metric_source_text(self, name):
        """The rendered source cell for one metric (or '')."""
        cells = self._cells.get(name)
        return "" if cells is None else cells["source"].text()

    @property
    def row_count(self):
        """Metric rows currently shown."""
        return self._rows
