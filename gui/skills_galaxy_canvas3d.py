"""Skills Galaxy 3D widget (guarded Qt-GL view).

Split from gui/skills_galaxy.py (GUI split #5, move only). The Qt-GL import guard
moves intact: when the GL stack is missing, the module still imports
and the container falls back to 2D only.
"""

from __future__ import annotations

import ctypes as _ctypes  # noqa: E402  (float arrays for GL buffers; moved with its sole use, GUI split #5)
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
# GUI split #5 INTENTIONAL (budget-driven): multi-name imports below are joined
# onto 88-char lines so this module stays under the 800-line budget; do not re-expand.
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QMatrix4x4, QMouseEvent,
    QPainter, QSurfaceFormat, QVector3D, QVector4D, QWheelEvent)
from PySide6.QtWidgets import QWidget

try:  # GL is a progressive enhancement — the galaxy must import
    # and run (2D) even on a machine where Qt's GL stack is missing.
    from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram,
        QOpenGLVertexArrayObject)
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    _GL_AVAILABLE = True
except Exception:  # noqa: BLE001
    QOpenGLWidget = None  # type: ignore[assignment]
    _GL_AVAILABLE = False

from .skills_galaxy_data import (GalaxyData, GalaxyNode, _BACKDROP, _LABEL_HUB_PX,
    _LABEL_STAR_PX, _TEXT_MAIN, _TEXT_MUTED, _draw_label_halo, _draw_label_text,
    _node_tooltip, _plan_label_rects, group_color, layout_positions_3d, search_sets)
from .skills_galaxy_gl import (_GL_BLEND, _GL_COLOR_BUFFER_BIT, _GL_DEPTH_BUFFER_BIT,
    _GL_DEPTH_TEST, _GL_FLOAT, _GL_LINES, _GL_ONE_MINUS_SRC_ALPHA, _GL_POINTS,
    _GL_PROGRAM_POINT_SIZE, _GL_SRC_ALPHA, _LINE_FS, _LINE_VS, _POINT_FS, _POINT_VS)

if _GL_AVAILABLE:

    class Galaxy3DWidget(QOpenGLWidget):
        """The skills galaxy in 3D: group hubs on a Fibonacci
        shell, their stars in tangent-plane clusters, lit sphere-
        impostor points, thread spokes, an orbit camera with a
        slow idle drift (~0.5 deg/s) and projection picking — the
        Memory Web 3D contract, pointed at the toolset."""

        skill_chosen = Signal(str)           # tool name
        selection_cleared = Signal()

        DEFAULT_DISTANCE = 980.0
        MIN_DISTANCE = 260.0
        MAX_DISTANCE = 2400.0
        FOV_DEGREES = 42.0
        ROTATE_STEP = 0.000145     # rad per 16 ms tick ≈ 0.5°/s
        IDLE_BEFORE_ROTATE = 4.0   # seconds of stillness first

        def __init__(self, parent=None):
            super().__init__(parent)
            fmt = QSurfaceFormat()
            fmt.setDepthBufferSize(24)
            fmt.setSamples(4)
            self.setFormat(fmt)
            self.setMouseTracking(True)
            self.setMinimumSize(320, 280)
            self._data = GalaxyData()
            self._pos: dict[str, tuple] = {}
            self._hover: str | None = None
            self._selected: str | None = None
            self._search = ""
            self._timer = QTimer(self)
            self._timer.setInterval(16)
            self._timer.timeout.connect(self._on_tick)
            # Camera: orbit around a target, diorama style.
            self._azimuth = 0.65
            self._elevation = 0.42
            self._distance = self.DEFAULT_DISTANCE
            self._target = [0.0, 0.0, 0.0]
            self._last_interaction = time.monotonic()
            self._active_mode = False
            # Interaction state.
            self._press_pos: QPointF | None = None
            self._press_target: list | None = None
            self._press_az = self._azimuth
            self._press_el = self._elevation
            self._orbiting = False
            self._panning_cam = False
            self._last_mouse = QPointF()
            # GL state (built in initializeGL).
            self._gl = None
            self._gl_ready = False
            self._gl_failed = False
            self._gl_error = ""
            self._prog_points = None
            self._prog_lines = None
            self._vao_points = None
            self._vbo_points = None
            self._vao_lines = None
            self._vbo_lines = None

        # ------------------------------------------------------------ data

        @property
        def galaxy(self) -> GalaxyData:
            return self._data

        def set_data(self, data: GalaxyData):
            self._data = data
            self._pos = layout_positions_3d(data)
            if self._selected and self._selected not in data.by_id:
                self._selected = None
            if self._hover and self._hover not in data.by_id:
                self._hover = None
            # v5.57.1 (item 9): a shrunk galaxy can leave an old
            # pan target stranded in empty space — clamp it to
            # the new extent before the next frame.
            self._clamp_target()
            self.update()

        # -- v5.57.1 (item 9): camera stranding guards ------------------
        # A right-drag pan moves the orbit target unclamped, so a
        # long drag (or several accumulated drags) can push the
        # target past the whole galaxy: the 3D chip stays lit but
        # the scene renders empty (projection-level probe: a
        # >=900 px pan leaves 0/488 nodes in the viewport). The
        # GL-fallback path cannot explain that — it would have
        # switched back to 2D. These guards keep the camera
        # inside the galaxy and recover an already-stranded one
        # on re-entry, without touching deliberate framing.
        def reset_camera(self):
            """Back to the default framing (the __init__ values)."""
            self._azimuth = 0.65
            self._elevation = 0.42
            self._distance = self.DEFAULT_DISTANCE
            self._target = [0.0, 0.0, 0.0]
            self._note_interaction()
            self.update()

        def _galaxy_extent(self) -> float:
            """Farthest node distance from the origin, i.e. the
            radius the orbit target must stay inside."""
            extent = 0.0
            for p in self._pos.values():
                extent = max(extent, math.sqrt(p[0] ** 2 + p[1] ** 2
                                               + p[2] ** 2))
            return extent if extent > 0 else 460.0

        def _clamp_target(self):
            limit = self._galaxy_extent()
            tx, ty, tz = self._target
            dist = math.sqrt(tx * tx + ty * ty + tz * tz)
            if dist > limit:
                scale = limit / dist
                self._target = [tx * scale, ty * scale, tz * scale]

        def is_framing_stranded(self) -> bool:
            """True when the camera shows (almost) nothing: no
            node projects into the viewport, or at most two do
            and all of them are tiny. A deliberate close-up
            shows its node LARGE, so the radius clause protects
            it from a false reset. Unjudgeable before layout
            (width/height ~0) — returns False then."""
            if not self._pos or self.width() < 50 or self.height() < 50:
                return False
            mvp, view, proj = self._mvp()
            radii: list[float] = []
            for node in self._data.nodes:
                p = self._pos.get(node.id)
                if p is None:
                    continue
                result = self._project(p[0], p[1], p[2], mvp, view)
                if result is None:
                    continue
                centre, depth = result
                if not (0 <= centre.x() <= self.width()
                        and 0 <= centre.y() <= self.height()):
                    continue
                radii.append(self._screen_radius(node, depth, proj))
            if not radii:
                return True
            return len(radii) <= 2 and max(radii) < 12.0

        def set_active(self, active: bool):
            """The container calls this as the widget comes on/off
            stage: the frame timer (idle drift + selection pulse)
            only runs while 3D is the live renderer."""
            self._active_mode = bool(active)
            if self._active_mode:
                self._timer.start()
            else:
                self._timer.stop()

        def _on_tick(self):
            if not self._active_mode or not self.isVisible():
                return
            idle = time.monotonic() - self._last_interaction
            if not self._hover and not self._selected \
                    and self._press_pos is None \
                    and idle > self.IDLE_BEFORE_ROTATE:
                self._azimuth += self.ROTATE_STEP
                self.update()
            elif self._selected is not None:
                self.update()  # keep the selection pulse alive

        # ------------------------------------------------------------ queries

        def node_positions(self) -> dict:
            return dict(self._pos)

        @property
        def zoom_factor(self) -> float:
            return self.DEFAULT_DISTANCE / self._distance

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
            node = self._data.node(name) if name else None
            if node is None or node.kind != "star":
                if self._selected is not None:
                    self._selected = None
                    self.selection_cleared.emit()
                self.update()
                return
            self._selected = node.name
            self._note_interaction()
            self.skill_chosen.emit(node.name)
            self.update()

        def clear_selection(self):
            self.select(None)

        # ------------------------------------------------------------ camera

        def _eye(self) -> QVector3D:
            ce = math.cos(self._elevation)
            return QVector3D(
                self._target[0] + self._distance * ce
                * math.sin(self._azimuth),
                self._target[1] + self._distance
                * math.sin(self._elevation),
                self._target[2] + self._distance * ce
                * math.cos(self._azimuth))

        def _view_matrix(self) -> QMatrix4x4:
            view = QMatrix4x4()
            view.lookAt(self._eye(), QVector3D(*self._target),
                        QVector3D(0.0, 1.0, 0.0))
            return view

        def _mvp(self):
            proj = QMatrix4x4()
            aspect = max(self.width(), 1) / max(self.height(), 1)
            proj.perspective(self.FOV_DEGREES, aspect, 1.0, 6000.0)
            view = self._view_matrix()
            return proj * view, view, proj

        def _project(self, x: float, y: float, z: float,
                     mvp=None, view=None):
            """World -> (screen point, view depth), or None behind
            the camera. The same matrices feed the shader, the
            label overlay and picking."""
            if mvp is None or view is None:
                mvp, view, _proj = self._mvp()
            # NB: matrix * vector via the operator is unreliable in
            # PySide6 6.12 — map() is the supported spelling.
            clip = mvp.map(QVector4D(x, y, z, 1.0))
            w = clip.w()
            if w <= 1e-6:
                return None
            sx = (clip.x() / w * 0.5 + 0.5) * self.width()
            sy = (1.0 - (clip.y() / w * 0.5 + 0.5)) * self.height()
            view_pos = view.map(QVector4D(x, y, z, 1.0))
            return QPointF(sx, sy), -view_pos.z()

        def world_to_screen(self, x: float, y: float,
                            z: float = 0.0) -> QPointF:
            projected = self._project(x, y, z)
            if projected is None:
                return QPointF(self.width() / 2.0, self.height() / 2.0)
            return projected[0]

        def _screen_radius(self, node: GalaxyNode, depth: float,
                           proj) -> float:
            scale = self.height() * proj.data()[5] * 0.5
            return node.radius * scale / max(depth, 1.0)

        def _world_per_pixel(self, depth: float) -> float:
            return 2.0 * depth * math.tan(
                math.radians(self.FOV_DEGREES / 2.0)) \
                / max(self.height(), 1)

        def _camera_basis(self):
            data = self._view_matrix().data()  # column-major
            right = (data[0], data[4], data[8])
            up = (data[1], data[5], data[9])
            return right, up

        def zoom_by(self, factor: float, anchor: QPointF | None = None):
            self._distance = max(
                self.MIN_DISTANCE,
                min(self.MAX_DISTANCE,
                    self._distance / max(factor, 1e-6)))
            self._note_interaction()
            self.update()

        def _note_interaction(self):
            self._last_interaction = time.monotonic()

        # ------------------------------------------------------------ GL setup

        def _make_program(self, vertex_src: str, fragment_src: str):
            prog = QOpenGLShaderProgram(self)
            if not prog.addShaderFromSourceCode(
                    QOpenGLShader.ShaderTypeBit.Vertex, vertex_src):
                raise RuntimeError(f"vertex shader: {prog.log()}")
            if not prog.addShaderFromSourceCode(
                    QOpenGLShader.ShaderTypeBit.Fragment, fragment_src):
                raise RuntimeError(f"fragment shader: {prog.log()}")
            if not prog.link():
                raise RuntimeError(f"shader link: {prog.log()}")
            return prog

        def _make_buffer(self, prog, floats_per_vertex: int, attrs):
            vao = QOpenGLVertexArrayObject(self)
            if not vao.create():
                raise RuntimeError("VAO creation failed")
            vao.bind()
            vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
            if not vbo.create():
                raise RuntimeError("VBO creation failed")
            vbo.bind()
            stride = floats_per_vertex * 4
            for name, offset_floats, size in attrs:
                prog.enableAttributeArray(name)
                prog.setAttributeBuffer(name, _GL_FLOAT,
                                        offset_floats * 4, size, stride)
            vbo.release()
            vao.release()
            return vao, vbo

        def initializeGL(self):  # noqa: N802 (Qt naming)
            try:
                context = self.context()
                if context is None or not context.isValid():
                    raise RuntimeError("no valid GL context")
                self._gl = context.functions()
                self._prog_points = self._make_program(_POINT_VS,
                                                       _POINT_FS)
                self._prog_lines = self._make_program(_LINE_VS, _LINE_FS)
                self._vao_points, self._vbo_points = self._make_buffer(
                    self._prog_points, 9,
                    (("aPos", 0, 3), ("aSize", 3, 1), ("aMode", 4, 1),
                     ("aColor", 5, 4)))
                self._vao_lines, self._vbo_lines = self._make_buffer(
                    self._prog_lines, 7,
                    (("aPos", 0, 3), ("aColor", 3, 4)))
                # Uniforms by location, not name (PySide6 6.12's
                # name+float overload mis-resolves).
                self._u_lines_mvp = self._prog_lines.uniformLocation(
                    "uMVP")
                self._u_points_mvp = self._prog_points.uniformLocation(
                    "uMVP")
                self._u_points_view = \
                    self._prog_points.uniformLocation("uView")
                self._gl_ready = True
            except Exception as exc:  # noqa: BLE001
                self._gl_failed = True
                self._gl_error = str(exc)

        @property
        def gl_ready(self) -> bool:
            return self._gl_ready and not self._gl_failed

        # ------------------------------------------------------------ GL paint

        def _build_gl_data(self, point_scale: float = 1.0):
            """Flatten the chart into vertex arrays: spokes (7
            floats/vertex) and point impostors (9 floats/vertex),
            with the 2D canvas's focus/dimming alpha rules. Point
            diameters arrive pre-multiplied by point_scale (the
            viewport projection factor)."""
            data = self._data
            _matched, dimmed = search_sets(data, self._search)
            focus = self._hover or self._selected
            neighbours: set = set()
            if focus:
                node = data.node(focus)
                if node is not None and node.kind == "star":
                    neighbours = {node.id, f"hub:{node.group}"}
                elif node is not None:
                    neighbours = {node.id} | {
                        n.id for n in data.stars
                        if n.group == node.group}

            def node_alpha(node) -> float:
                alpha = 1.0
                if focus and neighbours and node.id not in neighbours:
                    alpha *= 0.5
                if node.id in dimmed:
                    alpha *= 0.25
                return alpha

            lines: list[float] = []

            def push_line(pa, pb, colour: QColor):
                for p in (pa, pb):
                    lines.extend((p[0], p[1], p[2],
                                  colour.redF(), colour.greenF(),
                                  colour.blueF(), colour.alphaF()))

            for edge in data.edges:
                hub = data.node(edge.a)
                star = data.node(edge.b)
                if hub is None or star is None:
                    continue
                colour = QColor(group_color(hub.group))
                colour.setAlpha(46)
                if focus and (edge.b == focus or edge.a == focus):
                    colour.setAlpha(185)
                elif focus:
                    colour.setAlpha(20)
                fade = min(node_alpha(hub), node_alpha(star))
                if fade < 1.0:
                    colour = QColor(colour)
                    colour.setAlpha(int(colour.alpha() * fade))
                push_line(self._pos[edge.a], self._pos[edge.b], colour)

            points: list[float] = []

            def push_point(p, diameter, mode, colour: QColor):
                points.extend((p[0], p[1], p[2],
                               diameter * point_scale, mode,
                               colour.redF(), colour.greenF(),
                               colour.blueF(), colour.alphaF()))

            pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 3.2)
            for node in data.stars + data.hubs:
                alpha = node_alpha(node)
                base = QColor(group_color(node.group))
                centre = self._pos[node.id]
                if node.id == self._selected:
                    ring = QColor(base)
                    ring.setAlpha(int((150 + 90 * pulse) * alpha))
                    push_point(centre,
                               (node.radius + 4.5 + 1.6 * pulse) * 2.0,
                               1.0, ring)
                elif node.id == self._hover:
                    ring = QColor(base)
                    ring.setAlpha(int(200 * alpha))
                    push_point(centre, (node.radius + 3.5) * 2.0, 1.0,
                               ring)
                body = QColor(base)
                body.setAlpha(int(255 * alpha))
                push_point(centre, node.radius * 2.0, 0.0, body)
            return lines, points

        @staticmethod
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
                    gl.glClearColor(0.043, 0.051, 0.070, 1.0)
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
                # an unended painter — flag it; the container falls
                # back to the 2D canvas instead.
                self._gl_failed = True
                self._gl_error = f"paint: {exc}"
            finally:
                painter.end()
            if self._gl_failed:
                return
            # The label overlay paints through a FRESH painter,
            # never the one that did native painting: on Qt's GL
            # paint engine a painter that went through
            # begin/endNativePainting silently drops drawText that
            # follows a filled shape (bisected on Mesa llvmpipe
            # during the Memory Web build; a fresh painter is
            # clean).
            overlay = QPainter(self)
            try:
                self._paint_overlay(overlay)
            except Exception as exc:  # noqa: BLE001
                self._gl_failed = True
                self._gl_error = f"overlay: {exc}"
            finally:
                overlay.end()

        # ------------------------------------------------------------ overlay

        def _label_plan_inputs(self):
            """v5.57.1 (item 8): the 3D overlay's label candidates,
            extracted so tests can call it without GL (projection
            is pure matrix math). Returns (entries, obstacles,
            label_info). Hubs get extra side/far candidates and
            every on-screen hub contributes its orb disc as an
            obstacle — the old planner only tested label-vs-label,
            so a hub label could sit on another hub's orb (the
            field report: 'Your world' over 'Files & media')."""
            data = self._data
            entries: list[dict] = []
            obstacles: list[tuple] = []
            label_info: dict[str, tuple] = {}
            if not data.nodes:
                return entries, obstacles, label_info
            _matched, dimmed = search_sets(data, self._search)
            mvp, view, proj = self._mvp()

            def node_alpha(node) -> float:
                alpha = 1.0
                if node.id in dimmed:
                    alpha *= 0.25
                return alpha

            for node in data.hubs + data.stars:
                if node.kind == "hub":
                    priority, force, sticky = 1, False, True
                elif node.id == self._selected:
                    priority, force, sticky = 2, True, False
                elif node.id == self._hover:
                    priority, force, sticky = 3, True, False
                else:
                    continue  # plain stars stay unlabelled in 3D
                p = self._pos[node.id]
                result = self._project(p[0], p[1], p[2], mvp, view)
                if result is None:
                    continue
                centre, depth = result
                if centre.x() < -90 or centre.x() > self.width() + 90 \
                        or centre.y() < -40 \
                        or centre.y() > self.height() + 40:
                    continue
                r = self._screen_radius(node, depth, proj)
                if node.kind == "hub":
                    obstacles.append((centre, r))
                label = node.label
                if node.kind == "star" and len(label) > 24:
                    label = label[:23] + "…"
                font = QFont()
                font.setPixelSize(_LABEL_HUB_PX if node.kind == "hub"
                                  else _LABEL_STAR_PX)
                text_w = float(
                    QFontMetrics(font).horizontalAdvance(label))
                rect = QRectF(centre.x() - text_w / 2.0,
                              centre.y() + r + 3.0, text_w, 16.0)
                alt = QRectF(centre.x() - text_w / 2.0,
                             centre.y() - r - 3.0 - 16.0,
                             text_w, 16.0) if node.kind == "hub" else None
                extra = None
                if node.kind == "hub":
                    # Tried in order after rect/alt, before the
                    # sticky fallback: left, right, further below,
                    # further above. Each clears its own hub by
                    # construction (gap >= r + 6 vs margin r + 2).
                    extra = [
                        QRectF(centre.x() - text_w - r - 6.0,
                               centre.y() - 8.0, text_w, 16.0),
                        QRectF(centre.x() + r + 6.0,
                               centre.y() - 8.0, text_w, 16.0),
                        QRectF(centre.x() - text_w / 2.0,
                               centre.y() + r + 23.0, text_w, 16.0),
                        QRectF(centre.x() - text_w / 2.0,
                               centre.y() - r - 39.0, text_w, 16.0),
                    ]
                entries.append({
                    "id": node.id, "priority": priority,
                    "degree": node.count if node.kind == "hub" else 0,
                    "rect": rect, "alt_rect": alt,
                    "force": force, "sticky": sticky,
                    "extra_rects": extra,
                })
                colour = QColor(_TEXT_MAIN if node.kind == "hub"
                                else _TEXT_MUTED)
                colour.setAlpha(int(255 * node_alpha(node)))
                label_info[node.id] = (label, font, colour)
            return entries, obstacles, label_info

        def _paint_overlay(self, painter: QPainter):
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            data = self._data
            if not data.nodes:
                return
            focus = self._hover or self._selected

            entries, obstacles, label_info = self._label_plan_inputs()
            planned = _plan_label_rects(entries, obstacles=obstacles)
            for node_id, rect in planned.items():
                label, font, _c = label_info[node_id]
                _draw_label_halo(painter, rect, label, font)
            for node_id, rect in planned.items():
                label, font, colour = label_info[node_id]
                _draw_label_text(painter, rect, label, font, colour)

            if focus is None and not data.stars:
                painter.setPen(_TEXT_MUTED)
                font = QFont()
                font.setPixelSize(12)
                painter.setFont(font)
                painter.drawText(
                    QRectF(0, self.height() / 2.0 + 34,
                           self.width(), 20),
                    Qt.AlignmentFlag.AlignHCenter,
                    "No skills to chart yet.")

        # ------------------------------------------------------------ picking

        def pick_at(self, point: QPointF) -> str | None:
            """Nearest visible node within reach of a screen point
            (ties break toward the camera) — hover and clicks.
            Stars are tested before hubs so a star sitting on its
            hub still wins."""
            data = self._data
            mvp, view, proj = self._mvp()
            best = None  # (screen distance, depth, node id)
            for node in data.stars + data.hubs:
                p = self._pos[node.id]
                result = self._project(p[0], p[1], p[2], mvp, view)
                if result is None:
                    continue
                centre, depth = result
                reach = max(14.0, self._screen_radius(node, depth,
                                                      proj) + 5.0)
                dist = math.hypot(point.x() - centre.x(),
                                  point.y() - centre.y())
                if dist > reach:
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
                return
            if event.button() != Qt.MouseButton.LeftButton:
                return super().mousePressEvent(event)
            self._orbiting = False
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
                if self._panning_cam:
                    right, up = self._camera_basis()
                    wpp = self._world_per_pixel(self._distance)
                    start = self._press_target or [0.0, 0.0, 0.0]
                    self._target = [
                        start[i] - right[i] * dx * wpp
                        + up[i] * dy * wpp
                        for i in range(3)]
                    # v5.57.1 (item 9): a pan may not strand the
                    # camera past the galaxy (see _clamp_target).
                    self._clamp_target()
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
                dragged = self._orbiting or self._panning_cam
                if was_click and not dragged \
                        and event.button() == Qt.MouseButton.LeftButton:
                    hit = self.pick_at(event.position())
                    node = self._data.node(hit) if hit else None
                    if node is not None and node.kind == "star":
                        self.select(node.name)
                    else:
                        self.select(None)
                self._press_pos = None
                self._orbiting = False
                self._panning_cam = False
                self._press_target = None
            super().mouseReleaseEvent(event)

        def wheelEvent(self, event: QWheelEvent):  # noqa: N802
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.zoom_by(1.12 ** steps)
            event.accept()

else:
    # GUI split #5 INTENTIONAL: placeholder so the container can import the name
    # unconditionally; only ever used behind the _GL_AVAILABLE check, exactly as
    # the original left it undefined when GL was missing.
    Galaxy3DWidget = None  # type: ignore[assignment]
