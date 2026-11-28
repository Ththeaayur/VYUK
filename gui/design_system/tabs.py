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
        self._apply_sheet(TEXT_MUTED, LINE_SUBTLE, TEXT, ACCENT)

    def _apply_sheet(self, muted, line, text, accent):
        self.setStyleSheet(
            "QTabBar { background: transparent; }"
            "QTabBar::tab { background: transparent; color: %s;"
            " border: none; border-bottom: 1px solid %s;"
            " padding: 8px 16px; }"
            "QTabBar::tab:selected { color: %s;"
            " border-bottom: 2px solid %s; }"
            % (muted, line, text, accent)
        )

    def set_theme(self, resolved: dict) -> None:
        """Rebuild the tab sheet from a resolved theme (Phase 0).

        The tab bar paints nothing itself — its colours live in
        this sheet, so the sheet is the seam: unselected tabs take
        the theme muted text, the underline the theme hairline,
        the selected tab the theme text + accent. Called from the
        window's appearance pass (via _refresh_activity_theme);
        under Cyan the values are the house tokens, so nothing
        changes.
        """
        try:
            self._apply_sheet(
                resolved.get("text_muted", TEXT_MUTED),
                resolved.get("line_subtle", LINE_SUBTLE),
                resolved.get("text", TEXT),
                resolved.get("accent", ACCENT))
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass
