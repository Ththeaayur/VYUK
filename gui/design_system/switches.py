"""HudSwitch — a square-knob toggle with a 40 px hit area (guide §8).

The visible switch is 34 by 16 px; the widget itself is at least
40 by 40 px so the hit area passes the §11 hit-area guard. Keyboard
space toggles it (QAbstractButton behaviour). Checked state fills
the accent; unchecked state rests neutral.

Phase 0: the switch carries its label as text() (QAbstractButton
stores it; the paint never draws it — the label lives on the
toggle_row beside it), so Settings search and tests that read a
toggle's text keep working after the QCheckBox replacement.
set_theme(resolved) re-resolves the painted colours from the
window's appearance pass (the HudPanel precedent); under Cyan
the values are the house tokens, so nothing changes.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from ..style import ACCENT, LINE_SUBTLE, NEUTRAL, SURFACE_2, TEXT
from .palette import luminance, qcolor


class HudSwitch(QAbstractButton):
    """A 34x16 square-knob switch on a 40x40 hit area."""

    def __init__(self, parent=None, checked=False, text=""):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        if text:
            self.setText(text)
        self.setMinimumSize(40, 40)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._track = SURFACE_2
        self._line = LINE_SUBTLE
        self._accent = ACCENT
        self._knob_on = TEXT
        self._knob_off = NEUTRAL

    def set_theme(self, resolved: dict) -> None:
        """Recolour the painted track/knob from a resolved theme.

        The unchecked knob stays the house neutral and the border
        the house hairline (neither key exists in the resolved
        theme; both read on light and dark). The track follows the
        theme surface_2, the checked fill the theme accent. The
        checked knob sits ON the accent fill, so it must contrast
        with the accent, not follow the page text — the page text
        is near-black on Light, which made the checked knob a black
        square on the blue track (Memory Profile, look-fix 2). On
        light themes the checked knob is white; on dark themes it
        stays the theme text (near-white), so nothing changes
        there. Called from the window's appearance pass; under
        Cyan the values are the house tokens, so nothing changes.
        """
        try:
            self._track = resolved.get("surface_2", SURFACE_2)
            self._accent = resolved.get("accent", ACCENT)
            if luminance(resolved.get("bg", "#04090E")) > 0.5:
                self._knob_on = "#FFFFFF"
            else:
                self._knob_on = resolved.get("text", TEXT)
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass
        self.update()

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
        painter.fillRect(tx, ty, 34, 16, qcolor(self._track))
        painter.setPen(qcolor(self._line))
        painter.drawRect(tx, ty, 34, 16)
        if self.isChecked():
            painter.fillRect(tx, ty, 34, 16, qcolor(self._accent))
            painter.fillRect(tx + 20, ty + 2, 12, 12, qcolor(self._knob_on))
            painter.setPen(qcolor(self._accent))
            painter.drawRect(tx, ty, 34, 16)
        else:
            painter.fillRect(tx + 2, ty + 2, 12, 12, qcolor(self._knob_off))
        if self.hasFocus():
            # Keyboard focus must stay visible (the QCheckBox this
            # replaces showed a focus indicator): a 1 px accent
            # outline around the hit area, not the track.
            painter.setPen(qcolor(self._accent))
            painter.drawRect(rect.x(), rect.y(),
                             rect.width() - 1, rect.height() - 1)
        painter.end()


def toggle_row(switch: HudSwitch) -> QWidget:
    """One settings toggle row: the switch's label + the switch.

    The label is the switch's own text() (single source of truth);
    the row is a plain QWidget so call sites add it to a card or
    row layout exactly where the QCheckBox used to go. The switch
    keeps its attribute name, so every isChecked / setChecked /
    toggled call site is unchanged.

    Lifetime: adding the switch to the row's layout re-parents
    the switch to the row, so the row must outlive the switch.
    In a real window the card owns the row (re-parented by the
    card layout) and this is moot — but a card built into a
    parentless layout (the Settings-mixin test harness, and any
    caller that builds a card standalone) leaves the row with
    no Qt parent, and its Python wrapper is then collected as
    soon as the builder returns, deleting the row and the switch
    with it (proven: loophole_batch4/5/7 Qt legs, "Internal C++
    object (HudSwitch) already deleted"). The switch therefore
    holds the row itself: the row lives exactly as long as the
    switch, in every layout shape.
    """
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    label = QLabel(switch.text())
    label.setWordWrap(True)
    layout.addWidget(label, stretch=1)
    layout.addWidget(switch)
    switch._hud_toggle_row = row
    return row
