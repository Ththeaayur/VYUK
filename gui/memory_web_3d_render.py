# Refactor Phase 1 step 5: MemoryWeb3DWidget method group, moved verbatim from gui/memory_web.py.
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
import ctypes as _ctypes
from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    Signal,
)
import math

from .memory_web_graph import (_EDGE_CROSS, _LABEL_FACT_PX, _LABEL_HUB_PX, _TEXT_MAIN, _TEXT_MUTED, _draw_label_halo, _draw_label_text, _node_tooltip, _plan_label_rects, category_color)
from .memory_web_glconsts import (_GL_BLEND, _GL_COLOR_BUFFER_BIT, _GL_DEPTH_BUFFER_BIT, _GL_DEPTH_TEST, _GL_LINES, _GL_ONE_MINUS_SRC_ALPHA, _GL_POINTS, _GL_PROGRAM_POINT_SIZE, _GL_SRC_ALPHA)


class _MemoryWeb3DRenderMixin:

        def _build_gl_data(self, point_scale: float = 1.0):
            """Flatten the visible graph into vertex arrays: lines
            (7 floats/vertex) and point impostors (9 floats/vertex),
            with exactly the 2D canvas's focus/dimming alpha rules.
            Point diameters are pre-multiplied by point_scale (the
            viewport projection factor) — see the vertex shader."""
            data = self._data
            active = self._active_ids()
            focus = self._hover or self._selected
            neighbours = self._neighbours(focus) if focus else set()
            dimmed = self.search_dimmed_ids()
            matched = self.search_matched_ids()

            def node_alpha(node) -> float:
                alpha = 1.0
                if focus and node.id not in neighbours:
                    alpha *= 0.55
                if node.id in dimmed:
                    alpha *= 0.30
                return alpha

            lines: list[float] = []

            def push_line(pa, pb, colour: QColor):
                for p in (pa, pb):
                    lines.extend((p[0], p[1], p[2],
                                  colour.redF(), colour.greenF(),
                                  colour.blueF(),
                                  colour.alphaF()))

            for kind in ("cross", "membership"):
                for edge in data.edges:
                    if edge.kind != kind:
                        continue
                    if edge.a not in active or edge.b not in active:
                        continue
                    na, nb = data.node(edge.a), data.node(edge.b)
                    if na is None or nb is None:
                        continue
                    if kind == "cross":
                        colour = QColor(_EDGE_CROSS)
                        if focus and (edge.a == focus or edge.b == focus):
                            colour = QColor(255, 255, 255, 160)
                        elif focus:
                            colour = QColor(255, 255, 255, 16)
                    else:
                        hub = na if na.kind == "hub" else nb
                        colour = QColor(category_color(hub.category))
                        colour.setAlpha(90)
                        if focus and (edge.a == focus
                                      or edge.b == focus):
                            colour.setAlpha(205)
                        elif focus:
                            colour.setAlpha(36)
                    fade = min(node_alpha(na), node_alpha(nb))
                    if fade < 1.0:
                        colour = QColor(colour)
                        colour.setAlpha(int(colour.alpha() * fade))
                    push_line(self._pos[edge.a], self._pos[edge.b],
                              colour)

            points: list[float] = []

            def push_point(p, diameter, mode, colour: QColor):
                points.extend((p[0], p[1], p[2],
                               diameter * point_scale, mode,
                               colour.redF(), colour.greenF(),
                               colour.blueF(), colour.alphaF()))

            ordered = ([n for n in data.nodes if n.kind == "fact"]
                       + [n for n in data.nodes if n.kind == "hub"]
                       + [n for n in data.nodes if n.kind == "you"])
            for node in ordered:
                if node.id not in active:
                    continue
                alpha = node_alpha(node)
                base = QColor(category_color(node.category)) \
                    if node.kind != "you" else QColor(ACCENT)
                centre = self._pos[node.id]
                if node.id == self._hover or node.id == self._selected:
                    ring = QColor(base)
                    ring.setAlpha(int(210 * alpha))
                    push_point(centre, (node.radius + 3.5) * 2.0, 1.0,
                               ring)
                diameter = node.radius * 2.0
                if node.id in matched:
                    # E24: matched impostors swell a little, gain
                    # brightness, and wear the expanding pulse ring.
                    diameter *= 1.25
                    halo = QColor(base).lighter(175)
                    halo.setAlpha(int(185 * (1.0 - self._pulse_phase)
                                      * alpha))
                    push_point(centre,
                               (node.radius + 4.0
                                + self._pulse_phase * 6.0) * 2.0,
                               1.0, halo)
                body = QColor(base)
                if node.id in matched:
                    body = body.lighter(135)
                body.setAlpha(int(255 * alpha))
                push_point(centre, diameter, 0.0, body)
                if node.id in self._pinned:
                    marker = QColor(255, 255, 255, int(200 * alpha))
                    push_point([centre[0] + node.radius * 0.62,
                                centre[1] - node.radius * 0.62,
                                centre[2]], 3.4, 0.0, marker)
            return lines, points

        def _upload(vbo, floats: list):
            if not floats:
                return 0
            array = (_ctypes.c_float * len(floats))(*floats)
            vbo.bind()
            vbo.allocate(array, len(floats) * 4)
            vbo.release()
            return len(floats)

        def paintGL(self):  # noqa: N802 (Qt naming)
            if self._gl_failed or not self._gl_ready:
                return
            gl = self._gl
            painter = QPainter(self)
            try:
                painter.beginNativePainting()
                try:
                    dpr = self.devicePixelRatioF()
                    gl.glViewport(0, 0, int(self.width() * dpr),
                                  int(self.height() * dpr))
                    gl.glClearColor(0.070, 0.078, 0.098, 1.0)
                    gl.glClear(_GL_COLOR_BUFFER_BIT
                               | _GL_DEPTH_BUFFER_BIT)
                    gl.glEnable(_GL_DEPTH_TEST)
                    gl.glEnable(_GL_PROGRAM_POINT_SIZE)
                    gl.glEnable(_GL_BLEND)
                    gl.glBlendFunc(_GL_SRC_ALPHA,
                                   _GL_ONE_MINUS_SRC_ALPHA)
                    mvp, view, proj = self._mvp()
                    point_scale = self.height() * dpr \
                        * proj.data()[5] * 0.5
                    lines, points = self._build_gl_data(point_scale)
                    if lines:
                        self._prog_lines.bind()
                        self._prog_lines.setUniformValue(
                            self._u_lines_mvp, mvp)
                        self._vao_lines.bind()
                        count = self._upload(self._vbo_lines, lines)
                        self._vbo_lines.bind()
                        gl.glDrawArrays(_GL_LINES, 0, count // 7)
                        self._vbo_lines.release()
                        self._vao_lines.release()
                        self._prog_lines.release()
                    if points:
                        self._prog_points.bind()
                        self._prog_points.setUniformValue(
                            self._u_points_mvp, mvp)
                        self._prog_points.setUniformValue(
                            self._u_points_view, view)
                        self._vao_points.bind()
                        count = self._upload(self._vbo_points, points)
                        self._vbo_points.bind()
                        gl.glDrawArrays(_GL_POINTS, 0, count // 9)
                        self._vbo_points.release()
                        self._vao_points.release()
                        self._prog_points.release()
                finally:
                    painter.endNativePainting()
            except Exception as exc:  # noqa: BLE001
                # A paint failure must never take the app down with
                # an unended painter — flag it and let the container
                # fall back to the 2D canvas instead.
                self._gl_failed = True
                self._gl_error = f"paint: {exc}"
            finally:
                painter.end()
            if self._gl_failed:
                return
            # The 2D overlay (badges, labels) paints through a FRESH
            # painter, never the one that did native painting: on
            # Qt's OpenGL paint engine a painter that has been
            # through begin/endNativePainting silently drops
            # drawText calls that follow a filled shape (the label
            # halo) — bisected empirically on Mesa llvmpipe (halo
            # rendered, glyphs vanished; a fresh painter is clean).
            overlay = QPainter(self)
            try:
                self._paint_overlay(overlay)
            except Exception as exc:  # noqa: BLE001
                self._gl_failed = True
                self._gl_error = f"overlay: {exc}"
            finally:
                overlay.end()


        # ------------------------------------------------------------ overlay

        def _paint_overlay(self, painter: QPainter):
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            data = self._data
            if not data.nodes:
                return
            active = self._active_ids()
            focus = self._hover or self._selected
            neighbours = self._neighbours(focus) if focus else set()
            dimmed = self.search_dimmed_ids()
            mvp, view, proj = self._mvp()

            def node_alpha(node) -> float:
                alpha = 1.0
                if focus and node.id not in neighbours:
                    alpha *= 0.55
                if node.id in dimmed:
                    alpha *= 0.30
                return alpha

            projected: dict[str, tuple] = {}
            for node in data.nodes:
                if node.id not in active:
                    continue
                p = self._pos[node.id]
                result = self._project(p[0], p[1], p[2], mvp, view)
                if result is not None:
                    projected[node.id] = result

            # Collapsed-hub count badges, as in 2D. Shapes now, text
            # later with the label texts (same fill-then-glyph caution
            # as the halos, even on a fresh painter).
            badge_texts: list[tuple] = []
            for node in data.nodes:
                if node.kind != "hub" \
                        or node.category not in self._collapsed:
                    continue
                if node.id not in projected:
                    continue
                centre, depth = projected[node.id]
                r = self._screen_radius(node, depth, proj)
                hidden = sum(1 for n in data.nodes
                             if n.kind == "fact"
                             and n.category == node.category)
                badge_c = centre + QPointF(r * 0.85, r * 0.85)
                painter.setPen(QPen(
                    QColor(category_color(node.category)), 1.0))
                painter.setBrush(QColor(SURFACE_3))
                painter.drawEllipse(badge_c, 8.5, 8.5)
                badge_texts.append((badge_c, str(hidden)))

            # Labels: the same plan as the 2D painter (priority,
            # collision skip, hub flip, halo) over projected points.
            entries = []
            label_info: dict[str, tuple] = {}
            ordered = ([n for n in data.nodes if n.kind == "fact"]
                       + [n for n in data.nodes if n.kind == "hub"]
                       + [n for n in data.nodes if n.kind == "you"])
            fact_entries = []
            for node in ordered:
                if node.id not in projected:
                    continue
                if node.kind == "you":
                    priority, force, sticky = 0, True, False
                elif node.kind == "hub":
                    priority, force, sticky = 1, False, True
                elif node.id == self._selected:
                    priority, force, sticky = 2, True, False
                elif node.id == self._hover:
                    priority, force, sticky = 3, True, False
                elif node.kind == "fact" and self.zoom_factor >= 0.85:
                    priority, force, sticky = 4, False, False
                else:
                    continue
                centre, depth = projected[node.id]
                if centre.x() < -90 or centre.x() > self.width() + 90 \
                        or centre.y() < -40 \
                        or centre.y() > self.height() + 40:
                    continue
                r = self._screen_radius(node, depth, proj)
                label = node.label
                if node.kind == "fact" and len(label) > 20:
                    label = label[:19] + "…"
                font = QFont()
                font.setPixelSize(_LABEL_HUB_PX if node.kind != "fact"
                                  else _LABEL_FACT_PX)
                text_w = float(
                    QFontMetrics(font).horizontalAdvance(label))
                rect = QRectF(centre.x() - text_w / 2.0,
                              centre.y() + r + 3.0, text_w, 16.0)
                alt = QRectF(centre.x() - text_w / 2.0,
                             centre.y() - r - 3.0 - 16.0,
                             text_w, 16.0) if node.kind == "hub" else None
                entry = {
                    "id": node.id, "priority": priority,
                    "degree": node.degree, "rect": rect, "alt_rect": alt,
                    "force": force, "sticky": sticky,
                }
                if node.kind == "fact" and priority == 4:
                    fact_entries.append(entry)
                else:
                    entries.append(entry)
                colour = QColor(_TEXT_MAIN if node.kind != "fact"
                                else _TEXT_MUTED)
                colour.setAlpha(int(255 * node_alpha(node)))
                label_info[node.id] = (label, font, colour)
            # The collision pass is the overlay's only real cost —
            # keep it bounded on dense webs (planner sorts anyway,
            # so these are the highest-degree facts).
            fact_entries.sort(key=lambda e: -e["degree"])
            entries.extend(fact_entries[:60])
            planned = _plan_label_rects(entries)
            for node_id, rect in planned.items():
                label, font, _colour = label_info[node_id]
                _draw_label_halo(painter, rect, label, font)
            for node_id, rect in planned.items():
                label, font, colour = label_info[node_id]
                _draw_label_text(painter, rect, label, font, colour)
            if badge_texts:
                font = QFont()
                font.setPixelSize(9)
                painter.setFont(font)
                painter.setPen(QColor(TEXT))
                for badge_c, text in badge_texts:
                    painter.drawText(
                        QRectF(badge_c.x() - 8.5, badge_c.y() - 8.5,
                               17, 17),
                        Qt.AlignmentFlag.AlignCenter, text)

            if not any(n.kind == "fact" for n in data.nodes):
                painter.setPen(_TEXT_MUTED)
                font = QFont()
                font.setPixelSize(12)
                painter.setFont(font)
                painter.drawText(
                    QRectF(0, self.height() / 2.0 + 34,
                           self.width(), 20),
                    Qt.AlignmentFlag.AlignHCenter,
                    "Nothing to weave yet — facts VYUK learns will "
                    "appear here.")


        # ------------------------------------------------------------ picking

        def pick_at(self, point: QPointF) -> str | None:
            """Nearest visible node within 16 px of a screen point
            (ties break toward the camera) — hover and clicks."""
            data = self._data
            active = self._active_ids()
            mvp, view, _proj = self._mvp()
            ordered = ([n for n in data.nodes if n.kind == "you"]
                       + [n for n in data.nodes if n.kind == "hub"]
                       + [n for n in data.nodes if n.kind == "fact"])
            best = None  # (screen distance, depth, node id)
            for node in ordered:
                if node.id not in active:
                    continue
                p = self._pos[node.id]
                result = self._project(p[0], p[1], p[2], mvp, view)
                if result is None:
                    continue
                centre, depth = result
                dist = math.hypot(point.x() - centre.x(),
                                  point.y() - centre.y())
                if dist > 16.0:
                    continue
                if best is None or dist < best[0] - 1e-9 \
                        or (abs(dist - best[0]) <= 1e-9
                            and depth < best[1]):
                    best = (dist, depth, node.id)
            return best[2] if best else None

        def _set_hover(self, node_id: str | None):
            if node_id == self._hover:
                return
            self._hover = node_id
            self._note_interaction()
            self.setCursor(Qt.CursorShape.PointingHandCursor if node_id
                           else Qt.CursorShape.ArrowCursor)
            node = self._data.node(node_id) if node_id else None
            self.setToolTip(_node_tooltip(node) if node is not None
                            else "")
            self.update()


        # ------------------------------------------------------------ mouse

        def mousePressEvent(self, event: QMouseEvent):  # noqa: N802
            if self._tour_active:
                self.stop_tour()  # the user's hand always wins (E26)
            self._note_interaction()
            pos = event.position()
            self._press_pos = pos
            self._last_mouse = pos
            self._press_target = list(self._target)
            self._press_az = self._azimuth
            self._press_el = self._elevation
            shift = bool(event.modifiers()
                         & Qt.KeyboardModifier.ShiftModifier)
            if event.button() == Qt.MouseButton.RightButton or shift:
                self._panning_cam = True
                self._press_node = None
                return
            if event.button() != Qt.MouseButton.LeftButton:
                return super().mousePressEvent(event)
            hit = self.pick_at(pos)
            self._press_node = hit
            self._drag_node = None
            self._orbiting = False
            if hit:
                projected = self._project(*self._pos[hit])
                self._drag_depth = projected[1] if projected else \
                    self._distance
                self._drag_start_world = list(self._pos[hit])
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802
            pos = event.position()
            if self._press_pos is not None \
                    and (event.buttons()
                         & (Qt.MouseButton.LeftButton
                            | Qt.MouseButton.RightButton)):
                self._note_interaction()
                dx = pos.x() - self._press_pos.x()
                dy = pos.y() - self._press_pos.y()
                moved = (pos - self._press_pos).manhattanLength()
                if self._panning_cam:
                    right, up = self._camera_basis()
                    wpp = self._world_per_pixel(self._distance)
                    start = self._press_target or [0.0, 0.0, 0.0]
                    self._target = [
                        start[i] - right[i] * dx * wpp
                        + up[i] * dy * wpp
                        for i in range(3)]
                elif self._press_node:
                    if moved > 3 or self._drag_node:
                        self._drag_node = self._press_node
                        right, up = self._camera_basis()
                        wpp = self._world_per_pixel(self._drag_depth)
                        start = self._drag_start_world or [0.0, 0.0, 0.0]
                        self._pos[self._drag_node] = [
                            start[i] + right[i] * dx * wpp
                            - up[i] * dy * wpp
                            for i in range(3)]
                        self._pinned.add(self._drag_node)
                else:
                    self._orbiting = True
                    self._azimuth = self._press_az + dx * 0.0052
                    self._elevation = max(
                        -1.45, min(1.45, self._press_el + dy * 0.0052))
                self._last_mouse = pos
                self.update()
                return
            self._set_hover(self.pick_at(pos))
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802
            if event.button() in (Qt.MouseButton.LeftButton,
                                  Qt.MouseButton.RightButton) \
                    and self._press_pos is not None:
                was_click = (event.position() - self._press_pos) \
                    .manhattanLength() <= 3
                dragged = self._drag_node is not None or self._orbiting \
                    or self._panning_cam
                if was_click and not dragged \
                        and event.button() == Qt.MouseButton.LeftButton:
                    self.select_node(self.pick_at(event.position()))
                self._press_pos = None
                self._press_node = None
                self._drag_node = None
                self._orbiting = False
                self._panning_cam = False
                self._press_target = None
            super().mouseReleaseEvent(event)

        def mouseDoubleClickEvent(self, event: QMouseEvent):  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                hit = self.pick_at(event.position())
                node = self._data.node(hit) if hit else None
                if node is not None and node.kind == "hub":
                    self._note_interaction()
                    if node.category in self._collapsed:
                        self._collapsed.discard(node.category)
                    else:
                        self._collapsed.add(node.category)
                        if self._selected \
                                and self._selected.startswith("fact:"):
                            sel = self._data.node(self._selected)
                            if sel is not None \
                                    and sel.category == node.category:
                                self.clear_selection()
                    self._restart_settle(warm=True)
                    self.update()
                    return
            super().mouseDoubleClickEvent(event)

        def wheelEvent(self, event: QWheelEvent):  # noqa: N802
            if self._tour_active:
                self.stop_tour()  # the user's hand always wins (E26)
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.zoom_by(1.12 ** steps)
            event.accept()
