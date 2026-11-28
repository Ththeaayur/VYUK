"""SegmentBar — segmented progress with a mono percentage (guide §8).

Replaces every progress bar in HUD surfaces. The bar shows completed
segments in the accent and the remaining segments as resting surface;
the percentage is mono text, never an invented precision (callers
pass real done/total counts).

Phase 0 HELD (UI programme Phase 1 Memory confidence / Phase 4
Pulse gauges): no HUD progress surface exists yet — Pulse's
BarMeters are live meters with history, not done/total progress,
and the lesson panel / onboarding bars are not HUD surfaces.
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
from ..style_theme import _luminance
from .palette import parse_color, qcolor


class SegmentBar(QWidget):
    """A segmented progress bar with a mono label.

    The label speaks the mode the caller sets: "steps" reads
    "Step n of m" (the rail and plan rows, A-18 — never a percent
    there), "compact" reads "n/m" (the 640 rail, S-A10),
    "percent" reads "N%" (the original behaviour, kept as the
    default). The bar itself paints discrete segments — done
    segments in the accent, the rest in the resting surface,
    with a gap between neighbours — never a continuous fill.
    A mini bar (``mini=True``) is the 2 px fill-only variant the
    plan rows carry (B-A04): the label is hidden but ``label_text``
    is still computed, so the row's state stays readable.
    """

    _LABEL_MODES = ("steps", "compact", "percent")

    def __init__(self, parent=None, mini=False):
        super().__init__(parent)
        self._done = 0
        self._total = 0
        self._label_mode = "percent"
        self._mini = bool(mini)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._bar = _BarWidget(self)
        if self._mini:
            self._bar.setMinimumHeight(2)
        self._label = QLabel("0%")
        self._label.setFont(QFont(first_font_family(MONO_FONT_STACK), 12))
        self._label.setStyleSheet("color: %s;" % TEXT)
        if self._mini:
            self._label.hide()
        layout.addWidget(self._bar, 1)
        layout.addWidget(self._label)

    def set_label_mode(self, mode):
        """The label's language: steps / compact / percent."""
        if mode not in self._LABEL_MODES:
            raise ValueError(f"unknown SegmentBar label mode: {mode!r}")
        self._label_mode = mode
        self._sync_label()

    def set_mini(self, mini):
        """The 2 px fill-only variant (label hidden, still computed)."""
        self._mini = bool(mini)
        self._bar.setMinimumHeight(2 if self._mini else 8)
        self._label.setVisible(not self._mini)

    def set_progress(self, done, total):
        """Set real progress; total 0 means no progress is known."""
        self._done = max(0, int(done))
        self._total = max(0, int(total))
        self._sync_label()
        self._bar.set_progress(self._done, self._total)
        self.update()

    def set_theme(self, resolved: dict) -> None:
        """Recolour the bar + label from a resolved theme.

        Look-fix 2 (2026-11-24): the label carried the house TEXT
        (near-white) and the resting track the house SURFACE_3, so
        on Light the bar was a solid black slab and "Step 0 of 0"
        was near-white on white — it read as clipped at the panel
        edge, but the geometry fits (proven by probe: the label
        ends 16 px inside the host in both themes); the colour was
        the defect. On light themes the track follows the theme
        surface_2 and the label the theme text; on dark themes both
        stay the house values, so nothing changes. Called from the
        window's appearance pass and from the rail / plan-row build
        sites (which read the stored resolved theme).
        """
        try:
            light = _luminance(
                (resolved or {}).get("bg", "#04090E")) > 0.5
            text = (resolved or {}).get("text", TEXT)
            self._label.setStyleSheet("color: %s;" % text)
            self._bar.set_rest_colour(
                (resolved or {}).get("surface_2", SURFACE_3)
                if light else SURFACE_3)
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass
        self.update()

    def _sync_label(self):
        self._label.setText(self.label_text)

    @property
    def label_text(self):
        """The label in the current mode (tests pin the wording)."""
        if self._label_mode == "steps":
            return "Step %d of %d" % (self._done, self._total)
        if self._label_mode == "compact":
            return "%d/%d" % (self._done, self._total)
        if self._total > 0:
            pct = min(100, round(100 * self._done / self._total))
        else:
            pct = 0
        return "%d%%" % pct

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
        self._rest = SURFACE_3

    def set_progress(self, done, total):
        self._done, self._total = done, total
        self.update()

    def set_rest_colour(self, colour: str) -> None:
        """The resting-segment colour (SegmentBar.set_theme)."""
        self._rest = colour
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        # Discrete segments: each segment is its own block with a
        # gap to its neighbour, so the bar reads as steps, not a
        # continuous fill. Done segments take the accent; the rest
        # take the resting surface; the gaps show the parent.
        if self._total > 0:
            gap = 2
            width = rect.width()
            seg = (width - gap * (self._total - 1)) / self._total
            for index in range(self._total):
                x = int(round(index * (seg + gap)))
                w = max(1, int(round(seg)))
                colour = ACCENT if index < self._done else self._rest
                painter.fillRect(x, 0, w, rect.height(), qcolor(colour))
        else:
            painter.fillRect(rect, qcolor(self._rest))
        painter.end()
