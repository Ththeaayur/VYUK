"""HudTabs — square segmented tabs with a cyan underline (guide §8).

A tab bar whose selected tab is marked by a cyan underline, not a
rounded pill. Tabs are square (2 px corners) and the bar carries no
other chrome; pages own their content.
"""

from PySide6.QtWidgets import QTabBar

from ..style import ACCENT, LINE_SUBTLE, TEXT, TEXT_MUTED


class HudTabs(QTabBar):
    """A segmented tab bar: square tabs, cyan selected underline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QTabBar { background: transparent; }"
            "QTabBar::tab { background: transparent; color: %s;"
            " border: none; border-bottom: 1px solid %s;"
            " padding: 8px 16px; }"
            "QTabBar::tab:selected { color: %s;"
            " border-bottom: 2px solid %s; }"
            % (TEXT_MUTED, LINE_SUBTLE, TEXT, ACCENT)
        )
