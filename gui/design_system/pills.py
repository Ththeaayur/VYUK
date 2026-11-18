"""StatusPill — icon plus text in a severity (guide §8, §7).

Glyphs follow the state language: ring for idle/info, diamond for
done, triangle for warning, square for fault. Colour never carries
the meaning alone — the text always names the state.
"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..style import ACCENT, AMBER, DANGER, NEUTRAL, OK, TEXT

_GLYPHS = {
    "neutral": ("○", NEUTRAL),
    "info": ("○", ACCENT),
    "success": ("◆", OK),
    "warning": ("▲", AMBER),
    "danger": ("■", DANGER),
    "offline": ("○", NEUTRAL),
}


class StatusPill(QWidget):
    """A small icon-plus-text pill for one state word."""

    def __init__(self, parent=None, text="", severity="neutral"):
        super().__init__(parent)
        self._severity = severity if severity in _GLYPHS else "neutral"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._glyph = QLabel()
        self._label = QLabel(text)
        self._label.setStyleSheet("color: %s;" % TEXT)
        layout.addWidget(self._glyph)
        layout.addWidget(self._label)
        self._refresh()

    def _refresh(self):
        glyph, colour = _GLYPHS[self._severity]
        self._glyph.setText(glyph)
        self._glyph.setStyleSheet("color: %s;" % colour)

    @property
    def severity(self):
        """The pill's severity (neutral, info, success, warning, danger, offline)."""
        return self._severity

    @property
    def glyph(self):
        """The rendered glyph character for the severity."""
        return self._glyph.text()

    @property
    def text(self):
        """The pill's text."""
        return self._label.text()

    def set_text(self, text):
        """Replace the pill's text (severity unchanged)."""
        self._label.setText(text)

    def set_severity(self, severity):
        """Re-role the pill; unknown severities fall back to neutral."""
        self._severity = severity if severity in _GLYPHS else "neutral"
        self._refresh()
