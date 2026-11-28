"""HudToast — a top-right slide-in notice (guide §8).

Amber when attention is needed, auto-dismissed after the motion
token's toast interval, silent otherwise. Native tray notices stay
for hidden-window cases; this toast is for in-app notices only.
The slide animation is skipped when animate=False (tests and
reduced-motion callers).
"""

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..style import AMBER, LINE_STRONG, SURFACE_1, TEXT
from .motion import TOAST_MS


class HudToast(QWidget):
    """A transient notice pinned to its parent's top-right corner."""

    def __init__(self, parent, text, attention=False, animate=True):
        super().__init__(
            parent,
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint,
        )
        self._attention = attention
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            "background-color: %s; border: 1px solid %s; color: %s;"
            " padding: 10px 14px;" % (SURFACE_1, AMBER if attention else LINE_STRONG, TEXT)
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        layout.addWidget(self._label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TOAST_MS)
        self._timer.timeout.connect(self.hide)
        self._animate = animate
        self._animation = None

    def set_theme(self, resolved: dict) -> None:
        """Rebuild the toast sheet from a resolved theme (Phase 0).

        The toast is a ToolTip window, so the window sheet cannot
        reach it: without this seam it stays house-dark on Light.
        The fill follows the theme surface_1, the text the theme
        text; the attention edge stays amber (a semantic role
        colour shared across themes), the calm edge the house
        hairline. Called from the window's appearance pass; under
        Cyan the values are the house tokens, so nothing changes.
        """
        try:
            self.setStyleSheet(
                "background-color: %s; border: 1px solid %s; color: %s;"
                " padding: 10px 14px;"
                % (resolved.get("surface_1", SURFACE_1),
                   AMBER if self._attention else LINE_STRONG,
                   resolved.get("text", TEXT))
            )
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass

    @property
    def attention(self):
        """True when this toast needs attention (amber edge)."""
        return self._attention

    @property
    def text(self):
        """The notice text."""
        return self._label.text()

    def show_at_parent_corner(self):
        """Show at the parent's top-right corner, then auto-dismiss."""
        parent = self.parentWidget()
        if parent is not None:
            corner = parent.mapToGlobal(parent.rect().topRight())
            self.move(corner.x() - self.sizeHint().width() - 12, corner.y() + 12)
        self.show()
        if self._animate:
            start = self.pos()
            start.setX(start.x() + 16)
            self._animation = QPropertyAnimation(self, b"pos", self)
            self._animation.setDuration(180)
            self._animation.setStartValue(start)
            self._animation.setEndValue(self.pos())
            self._animation.start()
        self._timer.start()
