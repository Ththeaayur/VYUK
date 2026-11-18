"""First-open prompt dialog — the surface, not the choice.

Renders the plain-language descriptor from
core/firstopen_optin.first_open_prompt() verbatim: the
title, the paragraphs, and the two choices, in the same
words, with no re-wording and no new copy. The dialog
itself chooses nothing: it reports whether the user pressed
Yes, and every other path — No, window closed, dialog
rejected — reports No, so the default is OFF on every path
except an explicit Yes.

Shape follows the scrollable/resizable onboarding pattern:
content in a QScrollArea (frameless, widget-resizable) so
small displays still reach every paragraph, a modest
minimum size, and an initial size constrained to the
primary screen's available work area. Back/forward
navigation does not exist here — the only controls are
the two choice buttons, fixed below the scroll area, so
they never scroll away.

Own code, no capture: this module imports no watcher,
screen, or clipboard code, starts nothing, and persists
nothing — persistence is the call site's job through
core/firstopen_surface.

Phase G: the dialog rides HudDialog (Command Deck chrome),
never a bare QDialog — a first run completes without a
stock-looking window. The copy stays verbatim from
core/firstopen_optin; only the chrome changed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .design_system.dialogs import HudDialog
from voice_assistant.core.gui_facade import first_open_prompt


class FirstOpenPromptDialog(HudDialog):
    """The first-open continuous choice, as a dialog.

    ``choice_yes`` is True ONLY when the user pressed the
    Yes button. It starts False and stays False on No, on
    close, and on reject — closing without choosing is No,
    never a deferral. Callers persist through
    core/firstopen_surface.apply_choice; this dialog writes
    no setting and no file itself."""

    def __init__(self, parent=None):
        super().__init__(parent)
        prompt = first_open_prompt()
        self.setWindowTitle(prompt["title"])
        self.choice_yes = False

        root = self.content_layout()
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        heading = QLabel(prompt["title"])
        heading.setObjectName("pageTitle")
        heading.setWordWrap(True)
        root.addWidget(heading)

        # Scrollable paragraphs: small displays must still
        # reach every paragraph, and the buttons below must
        # never scroll away.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        for paragraph in prompt["paragraphs"]:
            label = QLabel(paragraph)
            label.setWordWrap(True)
            body_layout.addWidget(label)
        scroll.setWidget(body)
        # Phase G: QScrollArea.setWidget forces autoFillBackground
        # True on its widget — and the viewport follows — so the
        # body/viewport would paint their palette Window (light)
        # and wash out the dialog's light text. Reset both AFTER
        # setWidget, so the dialog's dark surface shows through
        # the paragraphs.
        body.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        root.addWidget(scroll, stretch=1)

        choices = prompt["choices"]
        self.btn_yes = QPushButton(choices[0])
        self.btn_no = QPushButton(choices[1])
        # Default is No: No is the default button, and any
        # path that is not an explicit Yes press leaves
        # choice_yes False.
        self.btn_no.setDefault(True)
        self.btn_yes.clicked.connect(self._on_yes)
        self.btn_no.clicked.connect(self.reject)
        root.addWidget(self.btn_yes)
        root.addWidget(self.btn_no)

        # Resizable, never fixed: a modest floor (HudDialog's
        # 480 px width floor kept), and an initial size fitted
        # to the available work area.
        self.setMinimumSize(480, 300)
        try:
            screen = QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            if avail is not None:
                self.resize(min(560, max(420, avail.width() - 40)),
                            min(480, max(300, avail.height() - 40)))
            else:
                self.resize(560, 480)
        except Exception:  # noqa: BLE001 — headless fallback
            self.resize(560, 480)

    def _on_yes(self):
        """The ONLY path that records a Yes: an explicit
        press of the Yes button. Everything else — No,
        close, reject — leaves choice_yes False."""
        self.choice_yes = True
        self.accept()
