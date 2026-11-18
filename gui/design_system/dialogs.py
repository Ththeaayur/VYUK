"""HudDialog — the one dialog for confirmations, notices and forms
(guide §8, §9.4, §10 Phase C).

It replaces every stock dialog in the product. Behaviour and wording
at each migrated call site stay exactly as they were; only the chrome
changes (chamfered role-line frame, corner ticks, solid surface).
The dialog applies its own token stylesheet, so it also works before
the main window exists (the already-running notice is shown from
main() with no parent and no window sheet).

Button contract (the deny-default rule, unchanged from the stock
dialogs it replaces): confirm() shows Yes / No with No holding
initial focus and the default, unless default=True is passed
explicitly. A dialog failure is the caller's No, exactly as before.
"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..style import (
    ACCENT,
    AMBER,
    DANGER,
    DISPLAY_FONT_STACK,
    first_font_family,
    LINE_STRONG,
    MONO_FONT_STACK,
    PRIMARY_WASH,
    SURFACE_1,
    SURFACE_2,
    SURFACE_3,
    TEXT,
    TEXT_DISABLED,
    TEXT_MUTED,
)
from .motion import CHAMFER_PX, TICK_PX
from .panels import chamfered_path

_SEVERITY_EDGE = {
    "info": LINE_STRONG,
    "warning": AMBER,
    "danger": DANGER,
}


class HudDialog(QDialog):
    """A Command Deck dialog: chamfered frame, role-line edge, ticks.

    Construct it directly for custom content (use content_layout()),
    or use the information / warning / critical / confirm / details
    classmethods for the standard shapes.
    """

    def __init__(self, parent=None, title="", eyebrow=None,
                 severity="info", destructive=False):
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(480)
        if title:
            self.setWindowTitle(title)
        self._severity = severity
        self._destructive = destructive
        self._layout = None
        self._buttons = {}
        self._eyebrow = eyebrow
        self.setStyleSheet(
            "QDialog { background-color: %s; }"
            "QDialog QLabel { color: %s; }"
            "QDialog QLabel#muted { color: %s; }"
            "QDialog QPushButton { background-color: %s;"
            " border: 1px solid %s; border-radius: 2px;"
            " padding: 8px 16px; color: %s; }"
            "QDialog QPushButton:hover { background-color: %s; }"
            "QDialog QPushButton:pressed { background-color: %s; }"
            "QDialog QPushButton:disabled { color: %s; }"
            "QDialog QPushButton#hudDefaultBtn { border: 1px solid %s; }"
            "QDialog QPushButton#hudDangerBtn { border: 1px solid %s;"
            " color: %s; }"
            "QDialog QPushButton#chipBtn { padding: 8px 14px;"
            " font-size: 12px; }"
            # Phase G: text edits are document surfaces too — a
            # QTextEdit (TermsWidget) fell through to the default
            # light palette inside an otherwise dark dialog.
            # Same treatment as DIALOG_STYLE's QPlainTextEdit rule.
            "QDialog QTextEdit { background-color: %s; color: %s;"
            " border: 1px solid %s; border-radius: 2px; }"
            % (
                SURFACE_1, TEXT, TEXT_MUTED, SURFACE_2, LINE_STRONG,
                TEXT, SURFACE_3, PRIMARY_WASH, TEXT_DISABLED,
                ACCENT, DANGER, DANGER,
                SURFACE_1, TEXT, LINE_STRONG,
            )
        )

    # ------------------------------------------------------------ content
    def content_layout(self):
        """The layout custom content goes in (created on first use)."""
        if self._layout is None:
            self._layout = QVBoxLayout(self)
            self._layout.setContentsMargins(20, 20, 20, 16)
            self._layout.setSpacing(10)
        return self._layout

    def add_title(self, text, eyebrow=None):
        """Title label (16 px semibold) with an optional eyebrow above."""
        if eyebrow is not None:
            micro = QLabel(eyebrow)
            font = QFont(first_font_family(DISPLAY_FONT_STACK), 11)
            font.setLetterSpacing(QFont.PercentageSpacing, 120)
            micro.setFont(font)
            micro.setStyleSheet("color: %s;" % TEXT_MUTED)
            self.content_layout().addWidget(micro)
        title = QLabel(text)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        title.setWordWrap(True)
        self.content_layout().addWidget(title)
        return title

    def add_body(self, text):
        """A word-wrapped body label."""
        body = QLabel(text)
        body.setWordWrap(True)
        self.content_layout().addWidget(body)
        return body

    def add_details(self, details_text):
        """A collapsed details section with a Details toggle button.

        Mirrors the stock detailed-text box: the details start hidden
        and the toggle reveals them. Returns (toggle, label).
        """
        label = QLabel(details_text)
        label.setWordWrap(True)
        label.setFont(QFont(first_font_family(MONO_FONT_STACK), 11))
        label.setVisible(False)
        toggle = QPushButton("Details")
        toggle.setObjectName("chipBtn")
        toggle.setCheckable(True)
        toggle.toggled.connect(label.setVisible)
        self.content_layout().addWidget(toggle)
        self.content_layout().addWidget(label)
        return toggle, label

    def add_buttons(self, specs):
        """Add a button row.

        ``specs`` is a list of (key, label, role, is_default, danger)
        with role "accept" or "reject". The default button holds
        initial focus. Returns {key: button}.
        """
        row = QHBoxLayout()
        row.addStretch(1)
        for key, label, role, is_default, danger in specs:
            button = QPushButton(label)
            if is_default:
                button.setObjectName("hudDefaultBtn")
                button.setDefault(True)
                button.setFocus()
            if danger:
                button.setObjectName("hudDangerBtn")
            if role == "accept":
                button.clicked.connect(self.accept)
            else:
                button.clicked.connect(self.reject)
            row.addWidget(button)
            self._buttons[key] = button
        self.content_layout().addLayout(row)
        return self._buttons

    def button(self, key):
        """The button built for ``key`` (or None)."""
        return self._buttons.get(key)

    # ------------------------------------------------------------ chrome
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        edge = DANGER if self._destructive else _SEVERITY_EDGE.get(
            self._severity, LINE_STRONG
        )
        painter.setPen(QPen(QColor(edge), 1))
        painter.drawPath(chamfered_path(rect, CHAMFER_PX))
        painter.setPen(QPen(QColor(ACCENT), 2))
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        painter.drawPolyline(
            [QPoint(x, y + TICK_PX), QPoint(x, y), QPoint(x + TICK_PX, y)]
        )
        painter.drawPolyline(
            [
                QPoint(x + w - TICK_PX, y + h),
                QPoint(x + w, y + h),
                QPoint(x + w, y + h - TICK_PX),
            ]
        )
        painter.end()

    # ------------------------------------------------------------ standard shapes
    @classmethod
    def _notice(cls, parent, title, text, severity, eyebrow=None):
        dialog = cls(parent, title=title, eyebrow=eyebrow, severity=severity)
        dialog.add_title(title, eyebrow=eyebrow)
        dialog.add_body(text)
        dialog.add_buttons([("ok", "OK", "accept", True, False)])
        dialog.exec()
        return None

    @classmethod
    def information(cls, parent, title, text, eyebrow=None):
        """A notice box. Returns None (the stock information shape)."""
        return cls._notice(parent, title, text, "info", eyebrow)

    @classmethod
    def warning(cls, parent, title, text, eyebrow=None):
        """A warning box. Returns None (the stock warning shape)."""
        return cls._notice(parent, title, text, "warning", eyebrow)

    @classmethod
    def critical(cls, parent, title, text, eyebrow=None):
        """A critical box. Returns None (the stock critical shape)."""
        return cls._notice(parent, title, text, "danger", eyebrow)

    @classmethod
    def confirm(cls, parent, title, text, default=False, destructive=False,
                eyebrow=None):
        """A Yes / No question. Returns True only on Yes.

        ``default`` names the button holding initial focus and the
        default: False (the product default) means No. A failure to
        show the dialog is the caller's No, as before.
        """
        dialog = cls(parent, title=title, eyebrow=eyebrow,
                     severity="danger" if destructive else "info",
                     destructive=destructive)
        dialog.add_title(title, eyebrow=eyebrow)
        dialog.add_body(text)
        dialog.add_buttons([
            ("yes", "Yes", "accept", bool(default), bool(destructive)),
            ("no", "No", "reject", not bool(default), False),
        ])
        return dialog.exec() == QDialog.Accepted

    @classmethod
    def details(cls, parent, title, text, details_text, eyebrow=None):
        """A notice box with a collapsed details section."""
        dialog = cls(parent, title=title, eyebrow=eyebrow, severity="info")
        dialog.add_title(title, eyebrow=eyebrow)
        dialog.add_body(text)
        dialog.add_details(details_text)
        dialog.add_buttons([("ok", "OK", "accept", True, False)])
        dialog.exec()
        return None
