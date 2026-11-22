"""Skills Galaxy 2D canvas (QPainter).

Split from gui/skills_galaxy.py (GUI split #5, move only).
"""

from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from .skills_galaxy_data import (
    GalaxyData,
    GalaxyNode,
    _BACKDROP,
    _LABEL_HUB_PX,
    _LABEL_STAR_PX,
    _TEXT_MAIN,
    _TEXT_MUTED,
    _draw_label_halo,
    _draw_label_text,
    _node_tooltip,
    _plan_label_rects,
    group_color,
    layout_positions_2d,
    search_sets,
    world_extent_2d,
)


# The 2D canvas
class Galaxy2DCanvas(QWidget):
    """The skills map, painted flat: seeded starfield backdrop,
    glowing group hubs with a golden-spiral cluster of stars
    each, pan + anchored zoom, hover tooltips, click a star to
    choose it. Static layout — dragging pans the camera rather
    than moving stars (the map is a chart, not a sandbox)."""

    skill_chosen = Signal(str)           # tool name
    selection_cleared = Signal()

    LABEL_ZOOM = 1.15                    # star labels at/above this

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 280)
        self._data = GalaxyData()
        # Stage 0 features batch 5 (larger text): the painted
        # star labels + empty state scale with the appearance
        # font scale (the QSS cannot reach paintEvent text).
        # Driven by SkillsGalaxyWidget.set_font_scale.
        self._font_scale = 1.0
        self._pos: dict[str, tuple] = {}
        self._hover: str | None = None
        self._selected: str | None = None     # a star's tool name
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._fitted = False
        # D4 (golden fix): whether a real layout pass has sized
        # this widget yet. The old fit guard keyed on "sitting at
        # minimum size", which is also the widget's LEGITIMATE
        # final size at small windows (908/980) — so the fit never
        # ran there and the ring painted at zoom 1.0, far larger
        # than the widget, showing only top-edge dots.
        self._seen_resize = False
        self._search = ""
        # Starfield: two seeded layers in a world-sized box. Far
        # dots follow the camera at 0.35x, near at 0.7x — cheap
        # parallax without a second scene.
        rng = random.Random(20240214)
        self._field_far = [(rng.uniform(-1500, 1500),
                            rng.uniform(-1500, 1500),
                            rng.uniform(0.6, 1.3)) for _ in range(110)]
        self._field_near = [(rng.uniform(-1500, 1500),
                             rng.uniform(-1500, 1500),
                             rng.uniform(1.1, 2.1)) for _ in range(70)]
        self._press_pos: QPointF | None = None
        self._last_mouse = QPointF()
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(50)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)

    # ------------------------------------------------------------ data

    @property
    def galaxy(self) -> GalaxyData:
        return self._data

    def set_font_scale(self, scale: float) -> None:
        """Scale the painted star labels / empty state."""
        try:
            self._font_scale = float(scale)
        except (TypeError, ValueError):
            self._font_scale = 1.0
        self.update()

    def set_data(self, data: GalaxyData):
        self._data = data
        self._pos = layout_positions_2d(data)
        if self._selected and self._selected not in data.by_id:
            self._selected = None
        if self._hover and self._hover not in data.by_id:
            self._hover = None
        self._fitted = False
        self._fit_view()
        self._sync_pulse_timer()
        self.update()

    # ------------------------------------------------------------ view

    def _fit_view(self):
        """Frame the whole galaxy while the user has not taken
        the camera over (any pan/zoom of theirs ends the auto-fit
        for good). Data loaded before first layout is NOT a real
        fit: stay pending until a resize has sized the widget.
        That pending state must key on layout having run, NOT on
        sitting at minimum size — minimum size is also a real
        final size at small windows, and keying on it left the
        ring unfitted (zoom 1.0, top-edge dots only) at 908/980."""
        if self._fitted or not self._data.nodes:
            return
        if not self._seen_resize or self.width() <= 0 \
                or self.height() <= 0:
            return
        extent = world_extent_2d(self._data, self._pos)
        self._zoom = max(0.25, min(1.6,
                                   (min(self.width(), self.height())
                                    / 2.0) / extent))
        self._pan = QPointF(0.0, 0.0)
        self._fitted = True

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._seen_resize = True
        self._fit_view()

    @property
    def zoom_factor(self) -> float:
        return self._zoom

    def world_to_screen(self, x: float, y: float) -> QPointF:
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        return centre + self._pan + QPointF(x * self._zoom,
                                            y * self._zoom)

    def _screen_to_world(self, point: QPointF) -> QPointF:
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        rel = point - centre - self._pan
        return QPointF(rel.x() / self._zoom, rel.y() / self._zoom)

    def zoom_by(self, factor: float, anchor: QPointF | None = None):
        if anchor is None:
            anchor = QPointF(self.width() / 2.0, self.height() / 2.0)
        before = self._screen_to_world(anchor)
        self._zoom = max(0.2, min(3.0, self._zoom * factor))
        after = self.world_to_screen(before.x(), before.y())
        self._pan += anchor - after
        self._fitted = True
        self.update()

    # ------------------------------------------------------------ queries

    def node_positions(self) -> dict:
        return dict(self._pos)

    def selected_name(self) -> str | None:
        return self._selected

    def hover_node(self) -> str | None:
        return self._hover

    def search_dimmed_ids(self) -> set:
        return search_sets(self._data, self._search)[1]

    def search_matched_ids(self) -> set:
        return search_sets(self._data, self._search)[0]

    def set_search_highlight(self, text: str):
        self._search = (text or "").strip()
        self.update()

    # ------------------------------------------------------------ selection

    def select(self, name: str | None):
        """The single selection path. A valid star name selects
        and raises skill_chosen; anything else clears."""
        node = self._data.node(name) if name else None
        if node is None or node.kind != "star":
            if self._selected is not None:
                self._selected = None
                self.selection_cleared.emit()
            self._sync_pulse_timer()
            self.update()
            return
        self._selected = node.name
        self.skill_chosen.emit(node.name)
        self._sync_pulse_timer()
        self.update()

    def clear_selection(self):
        self.select(None)

    def _sync_pulse_timer(self):
        """The selection pulse is the canvas's only animation —
        run its clock only while a selection exists."""
        if self._selected is not None and self.isVisible():
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()

    def _on_pulse_tick(self):
        if self._selected is None or not self.isVisible():
            self._pulse_timer.stop()
            return
        self.update()

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._sync_pulse_timer()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        self._pulse_timer.stop()

    # ------------------------------------------------------------ painting

    def _display_radius(self, node: GalaxyNode) -> float:
        if node.kind == "hub":
            return node.radius * self._zoom
        return node.radius * max(0.8, min(1.2, self._zoom))

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.fillRect(self.rect(), _BACKDROP)
        data = self._data
        self._paint_starfield(painter)
        if not data.nodes:
            painter.setPen(_TEXT_MUTED)
            font = QFont()
            font.setPixelSize(max(1, round(12 * self._font_scale)))
            painter.setFont(font)
            painter.drawText(
                QRectF(0, self.height() / 2.0 - 10, self.width(), 20),
                Qt.AlignmentFlag.AlignHCenter,
                "No skills to chart yet.")
            return
        _matched, dimmed = search_sets(data, self._search)
        focus = self._hover or self._selected
        neighbours: set = set()
        if focus:
            node = data.node(focus)
            if node is not None and node.kind == "star":
                neighbours = {node.id, f"hub:{node.group}"}
            elif node is not None:
                neighbours = {node.id} | {
                    n.id for n in data.stars if n.group == node.group}

        def node_alpha(node) -> float:
            alpha = 1.0
            if focus and neighbours and node.id not in neighbours:
                alpha *= 0.5
            if node.id in dimmed:
                alpha *= 0.25
            return alpha

        def screen(node_id: str) -> QPointF:
            xy = self._pos[node_id]
            return self.world_to_screen(xy[0], xy[1])

        # --- spokes: hub -> star, quiet until focus passes by.
        for edge in data.edges:
            hub = data.node(edge.a)
            star = data.node(edge.b)
            if hub is None or star is None:
                continue
            colour = QColor(group_color(hub.group))
            colour.setAlpha(46)
            width = 1.0
            if focus and (edge.b == focus or edge.a == focus):
                colour.setAlpha(185)
                width = 1.5
            elif focus:
                colour.setAlpha(20)
            fade = min(node_alpha(hub), node_alpha(star))
            if fade < 1.0:
                colour.setAlpha(int(colour.alpha() * fade))
            painter.setPen(QPen(colour, width))
            painter.drawLine(screen(edge.a), screen(edge.b))

        # --- stars, then hubs on top.
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 3.2)
        ordered = data.stars + data.hubs
        for node in ordered:
            centre = screen(node.id)
            if centre.x() < -80 or centre.x() > self.width() + 80 \
                    or centre.y() < -80 \
                    or centre.y() > self.height() + 80:
                continue
            r = self._display_radius(node)
            alpha = node_alpha(node)
            base = QColor(group_color(node.group))
            if node.id == self._selected:
                ring = QColor(base)
                ring.setAlpha(int((150 + 90 * pulse) * alpha))
                painter.setPen(QPen(ring, 1.8))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(centre, r + 4.5 + 1.6 * pulse,
                                    r + 4.5 + 1.6 * pulse)
            elif node.id == self._hover:
                ring = QColor(base)
                ring.setAlpha(int(200 * alpha))
                painter.setPen(QPen(ring, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(centre, r + 3.5, r + 3.5)
            # Glow halo, then the lit core.
            glow = QRadialGradient(centre, r * 2.7)
            halo = QColor(base)
            halo.setAlpha(int((70 if node.kind == "hub" else 44) * alpha))
            glow.setColorAt(0.0, halo)
            halo0 = QColor(base)
            halo0.setAlpha(0)
            glow.setColorAt(1.0, halo0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(centre, r * 2.7, r * 2.7)
            grad = QRadialGradient(centre + QPointF(-r * 0.35, -r * 0.35),
                                   r * 1.9)
            light = QColor(base).lighter(160)
            light.setAlpha(int(255 * alpha))
            mid = QColor(base)
            mid.setAlpha(int(235 * alpha))
            deep = QColor(base).darker(135)
            deep.setAlpha(int(255 * alpha))
            grad.setColorAt(0.0, light)
            grad.setColorAt(0.55, mid)
            grad.setColorAt(1.0, deep)
            painter.setBrush(grad)
            painter.drawEllipse(centre, r, r)

        # --- labels: hubs always (planned, flip on collision),
        # stars when hovered/selected or zoomed in far enough.
        entries = []
        label_info: dict[str, tuple] = {}
        for node in ordered:
            centre = screen(node.id)
            if centre.x() < -120 or centre.x() > self.width() + 120 \
                    or centre.y() < -60 \
                    or centre.y() > self.height() + 60:
                continue
            if node.kind == "hub":
                priority, force, sticky = 1, False, True
            elif node.id == self._selected:
                priority, force, sticky = 2, True, False
            elif node.id == self._hover:
                priority, force, sticky = 3, True, False
            elif self._zoom >= self.LABEL_ZOOM:
                priority, force, sticky = 4, False, False
            else:
                continue
            r = self._display_radius(node)
            label = node.label
            if node.kind == "star" and len(label) > 24:
                label = label[:23] + "…"
            font = QFont()
            font.setPixelSize(max(1, round(
                (_LABEL_HUB_PX if node.kind == "hub"
                 else _LABEL_STAR_PX) * self._font_scale)))
            text_w = float(QFontMetrics(font).horizontalAdvance(label))
            rect = QRectF(centre.x() - text_w / 2.0,
                          centre.y() + r + 3.0, text_w, 16.0)
            alt = QRectF(centre.x() - text_w / 2.0,
                         centre.y() - r - 3.0 - 16.0, text_w, 16.0) \
                if node.kind == "hub" else None
            entries.append({
                "id": node.id, "priority": priority,
                "degree": node.count if node.kind == "hub" else 0,
                "rect": rect, "alt_rect": alt,
                "force": force, "sticky": sticky,
            })
            colour = QColor(_TEXT_MAIN if node.kind == "hub"
                            else _TEXT_MUTED)
            colour.setAlpha(int(255 * node_alpha(node)))
            label_info[node.id] = (label, font, colour)
        planned = _plan_label_rects(entries)
        for node_id, rect in planned.items():
            label, font, _c = label_info[node_id]
            _draw_label_halo(painter, rect, label, font)
        for node_id, rect in planned.items():
            label, font, colour = label_info[node_id]
            _draw_label_text(painter, rect, label, font, colour)

    def _paint_starfield(self, painter: QPainter):
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        for layer, factor, alpha in ((self._field_far, 0.35, 52),
                                     (self._field_near, 0.7, 92)):
            for x, y, r in layer:
                sx = centre.x() + self._pan.x() * factor \
                    + x * self._zoom * factor
                sy = centre.y() + self._pan.y() * factor \
                    + y * self._zoom * factor
                if sx < -4 or sx > self.width() + 4 \
                        or sy < -4 or sy > self.height() + 4:
                    continue
                dot = QColor(214, 222, 240, alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(dot)
                painter.drawEllipse(QPointF(sx, sy), r, r)

    # ------------------------------------------------------------ interaction

    def _hit_test(self, point: QPointF) -> str | None:
        data = self._data
        ordered = data.stars + data.hubs  # stars win over their hub
        best = None
        for node in ordered:
            xy = self._pos.get(node.id)
            if xy is None:
                continue
            centre = self.world_to_screen(xy[0], xy[1])
            r = self._display_radius(node) + 5.0
            dist2 = (point.x() - centre.x()) ** 2 \
                + (point.y() - centre.y()) ** 2
            if dist2 <= r * r and (best is None or dist2 < best[0]):
                best = (dist2, node.id)
        return best[1] if best else None

    def _set_hover(self, node_id: str | None):
        if node_id == self._hover:
            return
        self._hover = node_id
        self.setCursor(Qt.CursorShape.PointingHandCursor if node_id
                       else Qt.CursorShape.ArrowCursor)
        node = self._data.node(node_id) if node_id else None
        self.setToolTip(_node_tooltip(node) if node is not None else "")
        self.update()

    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._press_pos = event.position()
        self._last_mouse = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802
        pos = event.position()
        if self._press_pos is not None \
                and (event.buttons() & Qt.MouseButton.LeftButton):
            # Any drag pans — stars stay where the chart put them.
            self._pan += pos - self._last_mouse
            self._fitted = True
            self._last_mouse = pos
            self.update()
            return
        self._set_hover(self._hit_test(pos))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton \
                and self._press_pos is not None:
            was_click = (event.position() - self._press_pos) \
                .manhattanLength() <= 3
            if was_click:
                hit = self._hit_test(event.position())
                node = self._data.node(hit) if hit else None
                if node is not None and node.kind == "star":
                    self.select(node.name)
                else:
                    self.select(None)  # hub or empty sky: clear
            self._press_pos = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):  # noqa: N802
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(1.12 ** steps, anchor=event.position())
        event.accept()
