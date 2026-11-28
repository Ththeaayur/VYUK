"""HudBackdrop — grid, scanlines and vignette, painted once (guide §8).

The backdrop is rendered to a cached pixmap and repainted only when
the widget resizes, so ambient layers cost nothing per frame. Grid
cells are 48 px; scanlines are drawn at low alpha.

Phase 0 HELD (UI programme Phase 1): a page background sits behind
every page over the window's existing ambience layers — wiring it
now would double-draw the ambience and needs the Phase 1
background decision first. BUILT in UI Phase 1 (2026-11-24):
the backdrop ruling (below) decides the intensity per page, and
the window applies it once per page — no double-draw.

One backdrop, three intensities (UI Phase 1 backdrop ruling):
"full" (grid + scanlines + vignette) on the hero / operations
pages, "vignette" (vignette only) on Memory, Activity and the
reading pages (Settings, Calendar, Inbox, Contacts, Calls, Notes,
Finance, Devices, Skills Centre) — look-fix 2026-11-24: the grid
read as a checkerboard on Memory + Activity and the owner
rejected it — "off" on Home and Minimal.
Profiles degrade it further: Professional drops the scanlines,
Minimal hides the backdrop entirely. ``effective_layers()`` is
the honest answer to "what is actually showing" after intensity
and profile are both applied.

The glass tile (``glass_tile``) is the backdrop's own grid,
rendered small and smooth-scaled up — a real blur — for the
HudPanel glass variant. A panel on a backdrop page paints the
tile + a translucent fill; a panel on Home (no backdrop) paints
the translucent fill alone.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from ..style import BG, LINE_SUBTLE
from .palette import parse_color, qcolor

GRID_PX = 48

_INTENSITY_LAYERS = {
    "full": (True, True, True),
    "vignette": (False, False, True),
    "off": (False, False, False),
}

_glass_tile_cache = {}


def glass_tile(resolved):
    """A blurred periodic tile of the backdrop's grid.

    Rendered at a quarter of its size and smooth-scaled up, so
    the grid lines are genuinely soft, not just faint. The tile
    is one grid cell wide in each direction (two cells across a
    96 px tile), so it repeats seamlessly. Cached per theme.
    """
    bg = resolved.get("bg", BG)
    line = resolved.get("line_subtle", LINE_SUBTLE)
    key = (bg, line)
    tile = _glass_tile_cache.get(key)
    if tile is not None:
        return tile
    size = 96
    quarter = size // 4
    small = QPixmap(quarter, quarter)
    small.fill(Qt.transparent)
    painter = QPainter(small)
    # Look-fix 2 (2026-11-24): QColor() cannot parse the CSS
    # rgba(...) spelling of the line token — it returns an invalid
    # colour, which paints as opaque black lines (the Light
    # Activity checkerboard). qcolor() parses the CSS spelling
    # (see gui/design_system/panels.py:214).
    faint = qcolor(line)
    faint.setAlpha(48)
    painter.setPen(faint)
    painter.drawLine(0, 0, 0, quarter)
    painter.drawLine(0, 0, quarter, 0)
    painter.drawLine(quarter // 2, 0, quarter // 2, quarter)
    painter.drawLine(0, quarter // 2, quarter, quarter // 2)
    painter.end()
    tile = small.scaled(size, size, Qt.IgnoreAspectRatio,
                        Qt.SmoothTransformation)
    _glass_tile_cache[key] = tile
    return tile


class HudBackdrop(QWidget):
    """A cached ambient backdrop: grid + scanlines + vignette."""

    def __init__(self, parent=None, intensity="full"):
        super().__init__(parent)
        self._intensity = (intensity if intensity in _INTENSITY_LAYERS
                           else "full")
        self._profile = "Cinematic"
        self._bg = BG
        self._line = LINE_SUBTLE
        self._cache = None

    @property
    def intensity(self):
        """The named intensity: full / vignette / off."""
        return self._intensity

    def set_intensity(self, intensity):
        """Switch the named intensity (re-renders on next paint)."""
        if intensity in _INTENSITY_LAYERS:
            self._intensity = intensity
            self._cache = None
            self.update()

    def set_profile(self, profile_name):
        """Degrade by profile: Professional drops the scanlines,
        Minimal hides the backdrop entirely, Cinematic restores."""
        self._profile = str(profile_name or "Cinematic")
        self.setHidden(self._profile == "Minimal")
        self._cache = None
        self.update()

    def set_theme(self, resolved: dict) -> None:
        """Recolour from a resolved theme (bg + line_subtle)."""
        try:
            self._bg = resolved.get("bg", BG)
            self._line = resolved.get("line_subtle", LINE_SUBTLE)
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass
        self._cache = None
        self.update()

    def effective_layers(self):
        """(grid, scanlines, vignette) actually showing now.

        The intensity's layers, with Professional's scanlines
        dropped and Minimal's everything off (the backdrop is
        hidden — nothing shows)."""
        if self._profile == "Minimal":
            return (False, False, False)
        grid, scanlines, vignette = _INTENSITY_LAYERS[self._intensity]
        if self._profile == "Professional":
            scanlines = False
        return (grid, scanlines, vignette)

    def _render(self):
        size = self.size()
        pixmap = QPixmap(size)
        pixmap.fill(qcolor(self._bg))
        if size.width() <= 0 or size.height() <= 0:
            return pixmap
        painter = QPainter(pixmap)
        grid, scanlines, vignette = self.effective_layers()
        line = qcolor(self._line)
        painter.setPen(line)
        if grid:
            x = 0
            while x <= size.width():
                painter.drawLine(x, 0, x, size.height())
                x += GRID_PX
            y = 0
            while y <= size.height():
                painter.drawLine(0, y, size.width(), y)
                y += GRID_PX
        if scanlines:
            _r, _g, _b, alpha = parse_color(self._line)
            faint = QColor(line)
            faint.setAlpha(max(0, alpha // 3))
            painter.setPen(faint)
            y = 2
            while y <= size.height():
                painter.drawLine(0, y, size.width(), y)
                y += 4
        if vignette:
            gradient = QRadialGradient(
                size.width() / 2, size.height() / 2,
                max(size.width(), size.height()) * 0.75,
            )
            clear = qcolor(self._bg)
            clear.setAlpha(0)
            dark = qcolor(self._bg)
            dark.setAlpha(90)
            gradient.setColorAt(0.0, clear)
            gradient.setColorAt(1.0, dark)
            painter.fillRect(0, 0, size.width(), size.height(), gradient)
        painter.end()
        return pixmap

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cache = self._render()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._cache is None or self._cache.size() != self.size():
            self._cache = self._render()
        painter.drawPixmap(0, 0, self._cache)
        painter.end()

    @property
    def cache_size(self):
        """The cached pixmap size (tests assert it tracks resizes)."""
        return None if self._cache is None else self._cache.size()
