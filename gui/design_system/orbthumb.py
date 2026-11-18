"""OrbThumbnail — a live frozen frame of the real Aura orb.

Thumbnails are grabbed from a real OrbVisualizer (frozen under
reduced motion), never a schematic: what the thumbnail shows is
what the hero orb looks like. Aura-only since 2026-11-01 by owner
decision; any other style name is a programming error and raises,
never a silent fallback.
"""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

try:
    # Production + render harness load this as
    # voice_assistant.gui.design_system.orbthumb.
    from ...core.gui_facade import ORB_STYLES
except ImportError:
    # Tests load it as gui.design_system.orbthumb with the tree
    # root on sys.path.
    from core.gui_facade import ORB_STYLES

from ..style import BG
from ..visualizer import OrbVisualizer

class OrbThumbnail(QWidget):
    """A frozen live frame of one orb style at one theme.

    The default size is 140: below the visualizer's 130 px LOD
    threshold every style collapses to the same compact mark, so
    a smaller thumbnail could never tell styles apart.
    """

    def __init__(self, parent=None, style="Aura", theme="Cyan", size=140):
        super().__init__(parent)
        if style not in ORB_STYLES:
            raise ValueError(f"Unknown orb style: {style!r}")
        self._style = style
        self._theme = theme
        self._size = size
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._image = QLabel()
        layout.addWidget(self._image)
        self._render()

    def _render(self):
        visualizer = OrbVisualizer()
        visualizer.set_theme(self._theme)
        visualizer.set_orb_style(self._style)
        visualizer.set_reduced_motion(True)
        visualizer.setMinimumSize(0, 0)
        # The visualizer paints no background of its own, so a
        # parentless grab would show the default light palette.
        # Fill the window background dark first: thumbnails sit
        # on dark settings pages in production.
        visualizer.setAutoFillBackground(True)
        palette = visualizer.palette()
        palette.setColor(QPalette.Window, QColor(BG))
        visualizer.setPalette(palette)
        visualizer.resize(self._size, self._size)
        self._image.setPixmap(visualizer.grab())
        visualizer.deleteLater()

    @property
    def style_name(self):
        """The orb style this thumbnail shows."""
        return self._style

    @property
    def pixmap(self):
        """The grabbed frame (null only if the grab failed)."""
        return self._image.pixmap()
