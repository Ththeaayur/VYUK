# Refactor Phase 1 step 5: moved verbatim from gui/memory_web.py (split by family).
from .style import (
    ACCENT,
    ACCENT_2,
    AMBER,
    BG,
    DANGER,
    OK,
    SURFACE_3,
    TEXT,
    TEXT_MUTED,
    RADIUS_BTN,
)
from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QStackedLayout,
    QWidget,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMatrix4x4,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
    QSurfaceFormat,
    QVector3D,
    QVector4D,
    QWheelEvent,
)
from .design_system.palette import lighten, with_alpha

from .memory_web_graph import GraphData
from .memory_web_canvas2d import MemoryWeb2DCanvas
try:
    from .memory_web_gl import MemoryWeb3DWidget
except ImportError:
    MemoryWeb3DWidget = None
from .memory_web_gl import _GL_AVAILABLE
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set





# ---------------------------------------------------------------------------
# The container (v3.2): MemoryWebWidget keeps the exact public
# contract the window has used since v3.1, but now hosts two
# renderers — the 2D canvas and the 3D view — behind "2D | 3D"
# chips in its top-right corner. The choice persists in QSettings
# ("memory/dimension"); a machine without working OpenGL quietly
# stays 2D with the 3D chip disabled.
# ---------------------------------------------------------------------------


class MemoryWebWidget(QWidget):
    """Hosts the memory-web renderers and swaps them behind the
    dimension chips. Every query/command delegates to whichever
    renderer is on stage, and set_facts feeds both, so folds,
    selection and search survive a swap."""

    fact_selected = Signal(object)       # the store row (dict)
    selection_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # UI upgrade Phase 1 (r3ef202): 280 -> 480. The
        # seed ring radius (~150-210) needs ~250 px of
        # half-height; at 280 the bottom hubs and their
        # labels fell off the canvas and were clipped.
        self.setMinimumSize(320, 480)
        # Without tracking, Qt drops no-button mouse-move events
        # addressed to this widget before its handlers ever see
        # them — and the relay below lives on those events.
        self.setMouseTracking(True)

        self._mode = "2d"
        self._mode_pending: str | None = None
        self._gl_unavailable = not _GL_AVAILABLE
        self._facts: list = []
        self._search_text = ""
        # v5.18 view state, owned here and pushed into whichever
        # renderers exist (mirrors how search text is kept).
        self._category_filter: set | None = None
        self._time_cutoff: float | None = None
        self._canvas = MemoryWeb2DCanvas(self)
        self._canvas.fact_selected.connect(self.fact_selected.emit)
        self._canvas.selection_cleared.connect(
            self.selection_cleared.emit)
        self._view3d = None
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._canvas)
        # Dimension chips, floating top-right.
        self._chip_bar = QWidget(self)
        self._chip_bar.setObjectName("memDimBar")
        self._chip_bar.setStyleSheet(
            f"#memDimBar {{ background: {with_alpha(BG, 175)};"
            f" border-radius: {RADIUS_BTN}px; }}")
        bar_layout = QHBoxLayout(self._chip_bar)
        bar_layout.setContentsMargins(2, 2, 2, 2)
        bar_layout.setSpacing(2)
        self._btn_2d = QPushButton("2D")
        self._btn_2d.setObjectName("chipBtn")
        self._btn_2d.setCheckable(True)
        self._btn_2d.setChecked(True)
        self._btn_2d.clicked.connect(lambda: self._set_mode("2d"))
        bar_layout.addWidget(self._btn_2d)
        self._btn_3d = QPushButton("3D")
        self._btn_3d.setObjectName("chipBtn")
        self._btn_3d.setCheckable(True)
        self._btn_3d.clicked.connect(lambda: self._set_mode("3d"))
        bar_layout.addWidget(self._btn_3d)
        if self._gl_unavailable:
            self._disable_3d_chip()
        self._chip_bar.adjustSize()
        persisted = str(qsettings_get("memory/dimension") or "2d").lower()
        if persisted == "3d" and not self._gl_unavailable:
            # GL needs a shown window — activate on first show.
            self._mode_pending = "3d"

    def set_theme(self, resolved: dict) -> None:
        """Recolour the dimension chip bar from the resolved theme.

        Look-fix 2026-11-24: the chip bar was built once from the
        house BG (dark), so on Light it floated as a dark
        rectangle over the light graph. The canvas already
        re-resolves per theme (M-21); the chip bar follows the
        same dict. Called from _mem_sync_canvas_theme on every
        web refresh.
        """
        try:
            bg = (resolved or {}).get("bg", BG)
            self._chip_bar.setStyleSheet(
                f"#memDimBar {{ background: {with_alpha(bg, 175)};"
                f" border-radius: {RADIUS_BTN}px; }}")
        except Exception:  # noqa: BLE001 — theme never breaks the graph
            pass

    # ------------------------------------------------------------ chips/mode

    @property
    def dimension_mode(self) -> str:
        return self._mode

    def _disable_3d_chip(self):
        self._btn_3d.setEnabled(False)
        self._btn_3d.setToolTip("3D needs OpenGL on this PC")

    def _sync_chips(self):
        for btn, value in ((self._btn_2d, "2d"), (self._btn_3d, "3d")):
            btn.blockSignals(True)
            btn.setChecked(self._mode == value)
            btn.blockSignals(False)

    def _position_chips(self):
        self._chip_bar.adjustSize()
        self._chip_bar.move(max(0, self.width()
                                - self._chip_bar.width() - 10), 10)
        self._chip_bar.raise_()

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self._mode_pending == "3d":
            self._mode_pending = None
            self._set_mode("3d", persist=False)
        self._position_chips()

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._position_chips()

    def _ensure_3d(self) -> bool:
        if self._view3d is not None:
            return True
        if not _GL_AVAILABLE:
            self._gl_unavailable = True
            return False
        try:
            widget = MemoryWeb3DWidget(self)
        except Exception:  # noqa: BLE001
            self._gl_unavailable = True
            return False
        widget.fact_selected.connect(self.fact_selected.emit)
        widget.selection_cleared.connect(self.selection_cleared.emit)
        self._view3d = widget
        self._stack.addWidget(widget)
        if self._facts:
            widget.set_facts(self._facts)
        if self._search_text:
            widget.set_search_highlight(self._search_text)
        if self._category_filter is not None:
            widget.set_category_filter(self._category_filter)
        if self._time_cutoff is not None:
            widget.set_time_cutoff(self._time_cutoff)
        widget.set_font_scale(getattr(self, "_font_scale", 1.0))
        return True

    def _set_mode(self, mode: str, persist: bool = True):
        if mode == self._mode:
            # Re-clicking the live chip is a no-op — syncing here
            # would read state back from the hidden renderer and
            # clobber whatever the user just did on stage.
            self._sync_chips()
            return
        if mode == "3d":
            if self._gl_unavailable or not self._ensure_3d():
                self._disable_3d_chip()
                mode = "2d"
        # A running tour is handed across the swap below (E26).
        was_touring = self._active().tour_active()
        if mode == "3d" and self._view3d is not None:
            view = self._view3d
            # Sync the 2D state into the incoming renderer so the
            # swap feels like the same web, re-lit — not a reset.
            view.set_collapsed_categories(
                self._canvas.collapsed_categories())
            view.set_category_filter(
                self._canvas.category_filter())
            view.set_time_cutoff(self._canvas.time_cutoff())
            if self._search_text:
                view.set_search_highlight(self._search_text)
            selected = self._canvas.selected_node()
            self._stack.setCurrentWidget(view)
            self._mode = "3d"
            view.set_active(True)
            if selected:
                view.select_node(selected)
            else:
                view.clear_selection()
            if was_touring:
                self._canvas.stop_tour()
                view.start_tour()
            view.update()
            QTimer.singleShot(450, self._verify_gl)
        else:
            if self._view3d is not None:
                view = self._view3d
                selected = view.selected_node()
                self._canvas.set_collapsed_categories(
                    view.collapsed_categories())
                self._canvas.set_category_filter(
                    view.category_filter())
                self._canvas.set_time_cutoff(view.time_cutoff())
                view.set_active(False)
                self._stack.setCurrentWidget(self._canvas)
                self._mode = "2d"
                if selected:
                    self._canvas.select_node(selected)
                else:
                    self._canvas.clear_selection()
                if was_touring:
                    view.stop_tour()
                    self._canvas.start_tour()
            else:
                self._stack.setCurrentWidget(self._canvas)
                self._mode = "2d"
        if persist:
            qsettings_set("memory/dimension", self._mode)
        self._sync_chips()
        self._position_chips()

    def _verify_gl(self):
        """First-show safety net: the 3D widget constructed fine but
        its GL context may still be unusable (VMs, remote desktop,
        ancient drivers). If so, fall back to 2D for good."""
        if self._mode != "3d" or self._view3d is None:
            return
        view = self._view3d
        context = None
        try:
            context = view.context()
        except Exception:  # noqa: BLE001
            context = None
        ok = bool(view.gl_ready) and context is not None \
            and context.isValid()
        if not ok:
            self._gl_unavailable = True
            self._disable_3d_chip()
            self._set_mode("2d", persist=True)

    # ------------------------------------------------------------ active

    def _active(self):
        if self._mode == "3d" and self._view3d is not None:
            return self._view3d
        return self._canvas

    @property
    def canvas2d(self) -> MemoryWeb2DCanvas:
        return self._canvas

    @property
    def view3d(self):
        return self._view3d

    @property
    def _hover(self) -> str | None:
        return self._active()._hover

    @_hover.setter
    def _hover(self, value):
        self._active()._hover = value

    # ------------------------------------------------------------ data

    @property
    def graph(self) -> GraphData:
        return self._active().graph

    def set_font_scale(self, scale: float) -> None:
        """Scale the painted labels on both renderers (batch 5).

        The 2D canvas always exists; the 3D view is built lazily
        on first use, so it takes the scale when it appears (and
        here when it already exists). The window drives this from
        _apply_appearance; the lazy 3D build re-applies the
        current scale through the same hook.
        """
        try:
            self._font_scale = float(scale)
        except (TypeError, ValueError):
            self._font_scale = 1.0
        self._canvas.set_font_scale(scale)
        view3d = getattr(self, "_view3d", None)
        if view3d is not None:
            setter = getattr(view3d, "set_font_scale", None)
            if setter is not None:
                setter(scale)

    def set_facts(self, facts: list):
        self._facts = list(facts or [])
        self._canvas.set_facts(self._facts)
        if self._view3d is not None:
            self._view3d.set_facts(self._facts)

    def refresh_if_changed(self, facts: list) -> bool:
        self._facts = list(facts or [])
        changed = self._canvas.refresh_if_changed(self._facts)
        if self._view3d is not None:
            self._view3d.refresh_if_changed(self._facts)
        return changed

    def set_search_highlight(self, text: str):
        self._search_text = (text or "").strip()
        self._canvas.set_search_highlight(text)
        if self._view3d is not None:
            self._view3d.set_search_highlight(text)

    def search_matched_ids(self) -> set:
        """E24: the node ids the live search highlights (delegates
        to the renderer on stage)."""
        return self._active().search_matched_ids()

    def search_match_count(self) -> int:
        return self._active().search_match_count()

    # ------------------------------------------------- filter + timeline

    def set_category_filter(self, categories):
        """E25: show only facts in these categories (None = all);
        hubs left empty by the filter hide too. Pushed into both
        renderers so a mode swap preserves it."""
        self._category_filter = None if categories is None else {
            str(c).strip().lower() for c in categories
            if str(c).strip()}
        self._canvas.set_category_filter(self._category_filter)
        if self._view3d is not None:
            self._view3d.set_category_filter(self._category_filter)

    def category_filter(self) -> set | None:
        if self._category_filter is None:
            return None
        return set(self._category_filter)

    def set_time_cutoff(self, cutoff):
        """E27: hide facts dated after this epoch (None = all
        time); undated facts always stay visible."""
        self._time_cutoff = None if cutoff is None else float(cutoff)
        self._canvas.set_time_cutoff(self._time_cutoff)
        if self._view3d is not None:
            self._view3d.set_time_cutoff(self._time_cutoff)

    def time_cutoff(self) -> float | None:
        return self._time_cutoff

    # ------------------------------------------------------------ tour

    def start_tour(self):
        """E26: start the cinematic hub tour on the renderer on
        stage; a mode swap hands a running tour across."""
        self._active().start_tour()

    def stop_tour(self):
        self._canvas.stop_tour()
        if self._view3d is not None:
            self._view3d.stop_tour()

    def tour_active(self) -> bool:
        return self._active().tour_active()

    # ------------------------------------------------------------ queries

    def is_settled(self) -> bool:
        return self._active().is_settled()

    @property
    def settle_ticks(self) -> int:
        return self._active().settle_ticks

    @property
    def zoom_factor(self) -> float:
        return self._active().zoom_factor

    def node_positions(self) -> dict:
        return self._active().node_positions()

    def visible_node_ids(self) -> list:
        return self._active().visible_node_ids()

    def collapsed_categories(self) -> set:
        return self._active().collapsed_categories()

    def selected_node(self) -> str | None:
        return self._active().selected_node()

    def hover_node(self) -> str | None:
        return self._active().hover_node()

    def cross_links(self, node_id: str) -> list:
        return self._active().cross_links(node_id)

    def search_dimmed_ids(self) -> set:
        return self._active().search_dimmed_ids()

    def world_to_screen(self, x: float, y: float) -> QPointF:
        return self._active().world_to_screen(x, y)

    def zoom_by(self, factor: float, anchor: QPointF | None = None):
        self._active().zoom_by(factor, anchor=anchor)

    # ------------------------------------------------------------ selection

    def select_node(self, node_id: str | None):
        self._active().select_node(node_id)

    def clear_selection(self):
        self._active().clear_selection()
        other = self._canvas if self._active() is self._view3d \
            else self._view3d
        if other is not None and other.selected_node() is not None:
            # Keep the hidden renderer's state honest without
            # re-emitting (its signals are not the live ones here).
            other._selected = None
            other.update()

    def expand_category(self, category: str):
        self._canvas.expand_category(category)
        if self._view3d is not None:
            self._view3d.expand_category(category)

    def set_collapsed_categories(self, categories):
        self._canvas.set_collapsed_categories(categories)
        if self._view3d is not None:
            self._view3d.set_collapsed_categories(categories)

    # ------------------------------------------------------------ event relay

    # Real pointer events land on whichever child covers the widget;
    # these handlers exist so events addressed to the container
    # itself (programmatic senders, and any chrome around the
    # renderers) still reach the active renderer unchanged. The
    # renderers fill the container exactly, so coordinates pass
    # through untouched.
    def _relay(self, event) -> bool:
        from PySide6.QtWidgets import QApplication

        target = self._active()
        if target is None or target is self:
            return False
        etype = event.type()
        # The in-flight event object itself cannot be re-sent from
        # inside its own delivery — forward a faithful clone.
        if etype in (QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonRelease,
                     QEvent.Type.MouseButtonDblClick,
                     QEvent.Type.MouseMove):
            clone = QMouseEvent(etype, event.position(),
                                event.globalPosition(), event.button(),
                                event.buttons(), event.modifiers())
            QApplication.sendEvent(target, clone)
        elif etype == QEvent.Type.Wheel:
            clone = QWheelEvent(event.position(),
                                event.globalPosition(),
                                event.pixelDelta(), event.angleDelta(),
                                event.buttons(), event.modifiers(),
                                event.phase(), event.inverted(),
                                event.source())
            QApplication.sendEvent(target, clone)
        else:
            return False
        event.accept()
        return True

    def mousePressEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().wheelEvent(event)
