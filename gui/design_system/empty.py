"""EmptyState — one sentence, one suggested task, one safe action
(guide §8).

Empty states never show decorative filler. Example data is labelled
EXAMPLE so a showcase can never be mistaken for real content.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class EmptyState(QWidget):
    """An honest empty state with a single safe action."""

    action_requested = Signal()

    def __init__(self, parent=None, sentence="", suggested_task="",
                 action_label="", example=False):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._sentence = QLabel(sentence)
        self._sentence.setWordWrap(True)
        layout.addWidget(self._sentence)
        self._suggested = QLabel(suggested_task)
        self._suggested.setWordWrap(True)
        layout.addWidget(self._suggested)
        self._tag = QLabel("EXAMPLE")
        self._tag.setVisible(bool(example))
        layout.addWidget(self._tag)
        self._button = QPushButton(action_label) if action_label else None
        if self._button is not None:
            self._button.clicked.connect(self.action_requested.emit)
            layout.addWidget(self._button)

    @property
    def example_shown(self):
        """True when the EXAMPLE tag is set to show (parent need not be shown)."""
        return not self._tag.isHidden()

    @property
    def sentence_text(self):
        """The empty-state sentence."""
        return self._sentence.text()

    @property
    def has_action(self):
        """True when a safe action button exists."""
        return self._button is not None
