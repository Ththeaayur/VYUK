"""HudPanel — the shared resting surface (guide §8).

A chamfered panel with a 1 px role line, bright corner ticks at the
top-left and bottom-right, and a faint top wash. It replaces the
rounded glass cards: callers put content in content_layout() exactly
as they did with a plain frame, and the frame is painted here instead
of by a stylesheet gradient. The edge colour is a role line by
default; set_edge("danger") or set_edge("amber") re-roles it. All
colours arrive as runtime token values — this file holds no literals.
"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from ..style import (
    ACCENT,
    AMBER,
    DANGER,
    DISPLAY_FONT_STACK,
    first_font_family,
    LINE_STRONG,
    LINE_SUBTLE,
    SURFACE_2,
    TEXT_FAINT,
)
from .motion import CHAMFER_PX, TICK_PX
from .palette import lighten, luminance, qcolor, with_alpha

_EDGES = {
    "line": LINE_STRONG,
    "subtle": LINE_SUBTLE,
    "amber": AMBER,
    "danger": DANGER,
    "accent": ACCENT,
}


def chamfered_path(rect, cut):
    """A rectangle with all four corners cut by ``cut`` pixels."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath()
    path.moveTo(x + cut, y)
    path.lineTo(x + w - cut, y)
    path.lineTo(x + w, y + cut)
    path.lineTo(x + w, y + h - cut)
    path.lineTo(x + w - cut, y + h)
    path.lineTo(x + cut, y + h)
    path.lineTo(x, y + h - cut)
    path.lineTo(x, y + cut)
    path.closeSubpath()
    return path


def pill_path(rect):
    """A stadium (fully rounded) path for a pill-shaped panel."""
    radius = min(rect.width(), rect.height()) // 2
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


class HudPanel(QFrame):
    """A resting surface with a chamfered role-line frame.

    Drop-in for a plain frame: content goes in content_layout().
    An optional eyebrow micro-label is rendered first, in tracked
    mono capitals, when ``eyebrow`` is given.

    The glass variant (``glass=True``) is the frosted-glass
    surface of the UI Phase 1 glass ruling: a blurred periodic
    tile from the backdrop pixmap (set by the window per page —
    a panel on Home, which has no backdrop, paints the
    translucent fill alone) under a translucent fill. Minimal
    falls back to the opaque fill. ``pill=True`` makes the panel
    a stadium shape (the composer and search pills).
    """

    def __init__(self, parent=None, eyebrow=None, fill=None, edge="line",
                 glass=False, pill=False):
        super().__init__(parent)
        self.setFrameStyle(QFrame.NoFrame)
        self._fill = fill or SURFACE_2
        self._edge_kind = edge
        self._edge = _EDGES.get(edge, LINE_STRONG)
        self.glass = bool(glass)
        self.pill = bool(pill)
        # The glass is live while the variant is on and the
        # profile allows it (Minimal turns it off — see
        # set_profile). ``glass`` is the variant; ``_glass_live``
        # is whether it paints as glass right now.
        self._glass_live = self.glass
        self._glass_tile = None
        self._glass_fill = None
        # The faint top wash's colour. On dark it is the fill
        # lightened; on Light the theme's highlight token (H-28).
        self._wash_top = lighten(self._fill, 0.12)
        # Stage 0 features batch 5 (Light theme, render-found):
        # the corner ticks paint the house ACCENT and the eyebrow
        # the house TEXT_FAINT — both need the theme when the
        # fill follows it (see set_theme).
        self._tick = ACCENT
        self._eyebrow_label = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(6)
        if eyebrow:
            label = QLabel(eyebrow)
            font = QFont(first_font_family(DISPLAY_FONT_STACK), 11)
            font.setLetterSpacing(QFont.PercentageSpacing, 120)
            label.setFont(font)
            label.setStyleSheet("color: %s;" % TEXT_FAINT)
            self._layout.addWidget(label)
            self._eyebrow_label = label

    def set_theme(self, resolved: dict) -> None:
        """Recolour the panel from a resolved theme (batch 5).

        The panel paints its own fill, so the QSS cannot reach
        it: on Light the house SURFACE_2 stayed black while the
        content took the theme-dark text — the Settings cards
        were unreadable. The fill follows the theme's surface_2,
        the corner ticks the theme accent, the eyebrow the theme
        text_faint. The edge is NOT re-resolved here: the line /
        subtle edges are the house cyan hairline, the same
        language the window sheet's borders use on every theme,
        and the amber / danger edges are semantic role colours
        shared across themes. Called from the window's appearance
        pass (it walks every HudPanel); under Cyan the values
        are the house tokens, so nothing changes.

        The top wash (H-28): on Light (bg luminance above one
        half) it is the theme's ``highlight`` token; on dark it
        stays the fill lightened. The glass fill is the theme's
        surface_2 made translucent.
        """
        try:
            self._fill = resolved.get("surface_2", SURFACE_2)
            self._tick = resolved.get("accent", ACCENT)
            # An accent edge follows the theme accent too.
            if getattr(self, "_edge_kind", None) == "accent":
                self._edge = self._tick
            if self._eyebrow_label is not None:
                self._eyebrow_label.setStyleSheet(
                    "color: %s;" % resolved.get("text_faint", TEXT_FAINT))
            bg = resolved.get("bg")
            if bg is not None and luminance(bg) > 0.5:
                self._wash_top = resolved.get("highlight",
                                              lighten(self._fill, 0.12))
            else:
                self._wash_top = lighten(self._fill, 0.12)
            self._glass_fill = with_alpha(self._fill, 140)
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass
        self.update()

    def set_glass_tile(self, tile):
        """The blurred backdrop tile the glass paints (or None).

        Set by the window per page: a panel on a backdrop page
        gets the tile, a panel on Home (no backdrop) gets None
        and paints the translucent fill alone.
        """
        self._glass_tile = tile
        self.update()

    def set_glass_enabled(self, enabled):
        """Whether the glass variant paints as glass right now."""
        self._glass_live = bool(enabled) and self.glass
        self.update()

    def set_profile(self, profile_name):
        """Degrade by profile: Minimal falls back to the opaque
        fill (no glass); Cinematic / Professional keep the glass."""
        self.set_glass_enabled(str(profile_name or "Cinematic")
                               != "Minimal")

    def content_layout(self):
        """The layout callers add content to."""
        return self._layout

    def set_edge(self, kind):
        """Re-role the frame line: line, subtle, amber, danger or
        accent (the accent edge follows the theme accent — it is
        re-resolved by set_theme, so a profile/theme change keeps
        the live rail host on the theme accent)."""
        self._edge_kind = kind
        if kind == "accent":
            self._edge = getattr(self, "_tick", ACCENT)
        else:
            self._edge = _EDGES.get(kind, LINE_STRONG)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = (pill_path(rect) if self.pill
                else chamfered_path(rect, CHAMFER_PX))
        if self.glass and self._glass_live:
            # Frosted glass: the translucent fill first, then the
            # blurred backdrop tile over it (a panel on Home has
            # no tile — the translucent fill alone, over the solid
            # room fill). No top wash: the glass IS the treatment.
            fill = self._glass_fill or with_alpha(self._fill, 140)
            # qcolor, not QColor(str): QColor does not parse the
            # css rgba() form — it returns invalid (black).
            painter.fillPath(path, QBrush(qcolor(fill)))
            if self._glass_tile is not None:
                painter.save()
                painter.setClipPath(path)
                painter.fillRect(rect, QBrush(self._glass_tile))
                painter.restore()
        else:
            painter.fillPath(path, QBrush(qcolor(self._fill)))
            # Faint top wash: the wash colour at the top edge,
            # fading back to the fill over the top strip.
            wash = QLinearGradient(0, rect.top(), 0, rect.top() + 28)
            wash.setColorAt(0.0, qcolor(self._wash_top))
            wash.setColorAt(1.0, qcolor(self._fill))
            painter.save()
            painter.setClipPath(path)
            painter.fillRect(rect.x(), rect.top(), rect.width(), 28, wash)
            painter.restore()
        painter.setPen(QPen(qcolor(self._edge), 1))
        painter.drawPath(path)
        # Bright corner ticks, top-left and bottom-right.
        # Batch 5: the tick colour follows the theme (set_theme);
        # under Cyan it is the house ACCENT, so nothing changes.
        tick_pen = QPen(qcolor(getattr(self, "_tick", ACCENT)), 2)
        painter.setPen(tick_pen)
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
