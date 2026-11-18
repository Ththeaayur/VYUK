"""ContextDrawer — the right rail for evidence and detail (guide §8).

Pinnable: pinned rails stay visible, unpinned rails behave as a
drawer. The under-1120 px drawer behaviour is the page layout's job
(Phase D/E); this component only owns the rail, its pin state and
its content area.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ContextDrawer(QWidget):
    """A pinnable right rail for evidence, detail and telemetry."""

    pin_toggled = Signal(bool)

    def __init__(self, parent=None, title="", pinned=False):
        super().__init__(parent)
        self._pinned = pinned
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self._title = QLabel(title)
        self._title.setObjectName("sectionTitle")
        self._pin = QPushButton("Pin")
        self._pin.setCheckable(True)
        self._pin.setChecked(pinned)
        self._pin.toggled.connect(self._on_pin)
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._pin)
        layout.addLayout(header)
        self._content = QVBoxLayout()
        layout.addLayout(self._content)

    def _on_pin(self, checked):
        self._pinned = checked
        self.pin_toggled.emit(checked)

    def content_layout(self):
        """The layout callers add evidence/detail widgets to."""
        return self._content

    @property
    def pinned(self):
        """True when the rail is pinned (stays visible)."""
        return self._pinned

    def set_pinned(self, pinned):
        """Pin or unpin the rail (updates the toggle with it)."""
        self._pin.setChecked(bool(pinned))
