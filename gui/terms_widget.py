"""Wave 10 (v5.65) Leaf 2 — PF-19 terms presentation (STAGED, unwired).

Presents the Terms + Privacy text with an explicit Accept control.
NOT wired into any live window — the coordinator wires it.

Separation of duties, deliberate: clicking Accept emits
``accept_requested`` and NOTHING else. The coordinator — acting on
that explicit signal — calls ``core/terms.record_acceptance``.
This widget never records acceptance itself, so acceptance can
never happen as a side effect of merely showing the text, and the
gate predicate (``terms.may_proceed``) stays the sole judge.

Qt degradation: when PySide6 is unavailable, this module still
imports cleanly, ``QT_AVAILABLE`` is False, ``TermsWidget`` is
None, and the text constants remain importable for tests.
"""

from __future__ import annotations

try:
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import (QPushButton, QTextEdit, QVBoxLayout,
                                   QWidget)

    QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt absent is a supported state
    QT_AVAILABLE = False
    Signal = QPushButton = QTextEdit = QVBoxLayout = QWidget = None  # type: ignore[assignment]

try:
    from ..core.gui_facade import terms as _terms
except Exception:  # noqa: BLE001 — direct-module import fallback
    _terms = None  # type: ignore[assignment]


def combined_text() -> str:
    """Terms + Privacy text, in reading order."""
    if _terms is None:
        return ""
    return _terms.TERMS_TEXT.strip() + "\n\n" + _terms.PRIVACY_TEXT.strip()


if QT_AVAILABLE:
    class TermsWidget(QWidget):
        """Terms + Privacy text with an explicit Accept button.

        Signals:
            accept_requested — emitted ONLY when the user clicks
                Accept. The coordinator records acceptance from it.
            declined — emitted when the user clicks Decline.
        There is no auto-accept, no timer, and no acceptance on
        close: closing without Accepting leaves the gate closed.
        """

        accept_requested = Signal()
        declined = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            layout = QVBoxLayout(self)
            self.text_view = QTextEdit()
            self.text_view.setReadOnly(True)
            self.text_view.setPlainText(combined_text())
            layout.addWidget(self.text_view)
            self.accept_button = QPushButton("I accept")
            self.accept_button.clicked.connect(
                lambda _checked=False: self.accept_requested.emit())
            layout.addWidget(self.accept_button)
            self.decline_button = QPushButton("Not now")
            self.decline_button.clicked.connect(
                lambda _checked=False: self.declined.emit())
            layout.addWidget(self.decline_button)
else:
    TermsWidget = None  # type: ignore[assignment]
