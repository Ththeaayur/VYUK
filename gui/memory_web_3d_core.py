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
from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    Signal,
)
import time
import math

try:  # GL is a progressive enhancement (mirrors the original try block).
    from PySide6.QtOpenGL import (
        QOpenGLBuffer,
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLVertexArrayObject,
    )
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
except Exception:  # noqa: BLE001
    QOpenGLBuffer = QOpenGLShader = QOpenGLShaderProgram = \
        QOpenGLVertexArrayObject = None
    QOpenGLWidget = None  # type: ignore[assignment]

from .memory_web_graph import (GraphData, GraphNode, _fact_epoch, build_graph, relax_step_3d, seed_positions_3d)
from .memory_web_canvas2d import (MemoryWeb2DCanvas)
from .memory_web_glconsts import (_GL_FLOAT, _LINE_FS, _LINE_VS, _POINT_FS, _POINT_VS)


class _MemoryWeb3DCoreMixin:

        def __init__(self, parent=None):
            super().__init__(parent)
            fmt = QSurfaceFormat()
            # The shaders are explicit "#version 330" — ask for a
            # modern context with a depth buffer and MSAA, and let
            # the driver give its best. (On a machine stuck below
            # GL 3.3 the shaders fail to compile, initializeGL
            # records it, and the container falls back to 2D with
            # the 3D chip disabled — by design.)
            fmt.setDepthBufferSize(24)
            fmt.setSamples(4)
            self.setFormat(fmt)
            self.setMouseTracking(True)
            self.setMinimumSize(320, 280)
            self._data = GraphData()
            self._pos: dict[str, list] = {}
            self._pinned: set = set()
            self._collapsed: set = set()
            self._hover: str | None = None
            self._selected: str | None = None
            self._search = ""
            # v5.18 view state, mirroring the 2D canvas: category
            # filter + timeline cutoff + search pulse + auto-tour.
            # No extra timers here — the frame timer drives both.
            self._category_filter: set | None = None
            self._time_cutoff: float | None = None
            self._pulse_phase = 0.0
            self._tour_active = False
            self._tour_index = 0
            self._tour_stop_started = 0.0
            self._facts_hash: str | None = None
            self._timer = QTimer(self)
            self._timer.setInterval(16)
            self._timer.timeout.connect(self._on_tick)
            self._ticks = 0
            self._temperature = 50.0
            self._settled = True
            # Camera: orbit around a target, assistant-style orbit.
            self._azimuth = 0.65
            self._elevation = 0.42
            self._distance = self.DEFAULT_DISTANCE
            self._target = [0.0, 0.0, 0.0]
            self._last_interaction = time.monotonic()
            self._active_mode = False
            # Interaction state.
            self._drag_node: str | None = None
            self._press_pos: QPointF | None = None
            self._press_node: str | None = None
            self._press_target: list | None = None
            self._press_az = self._azimuth
            self._press_el = self._elevation
            self._drag_start_world: list | None = None
            self._drag_depth = 0.0
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
        def graph(self) -> GraphData:
            return self._data

        def set_facts(self, facts: list):
            facts = list(facts or [])
            self._facts_hash = MemoryWeb2DCanvas._hash_facts(facts)
            previous = {nid: p for nid, p in self._pos.items()}
            self._data = build_graph(facts)
            self._pos = seed_positions_3d(self._data, previous=previous)
            self._pinned = {p for p in self._pinned
                            if p in self._data.by_id}
            self._collapsed = {c for c in self._collapsed
                               if f"hub:{c}" in self._data.by_id}
            if self._selected and self._selected not in self._data.by_id:
                self._selected = None
            if self._hover and self._hover not in self._data.by_id:
                self._hover = None
            self._restart_settle(warm=bool(previous))
            self.update()

        def refresh_if_changed(self, facts: list) -> bool:
            facts = list(facts or [])
            if MemoryWeb2DCanvas._hash_facts(facts) == self._facts_hash \
                    and self._facts_hash is not None:
                return False
            self.set_facts(facts)
            return True


        # ------------------------------------------------------------ layout

        def _hidden_by_view(self, node: GraphNode) -> bool:
            """v5.18: the 2D canvas's rule, verbatim — a FACT is
            hidden by the category filter or the timeline cutoff;
            undated facts are never cutoff-hidden."""
            if node.kind != "fact":
                return False
            if self._category_filter is not None \
                    and node.category not in self._category_filter:
                return True
            if self._time_cutoff is not None:
                epoch = _fact_epoch(node.fact)
                if epoch is not None and epoch > self._time_cutoff:
                    return True
            return False

        def _active_ids(self) -> set:
            passing: dict[str, int] = {}
            totals: dict[str, int] = {}
            for node in self._data.nodes:
                if node.kind != "fact":
                    continue
                totals[node.category] = totals.get(node.category, 0) + 1
                if not self._hidden_by_view(node):
                    passing[node.category] = \
                        passing.get(node.category, 0) + 1
            active = set()
            for node in self._data.nodes:
                if node.kind == "fact":
                    if node.category in self._collapsed:
                        continue
                    if self._hidden_by_view(node):
                        continue
                    active.add(node.id)
                elif node.kind == "hub":
                    if totals.get(node.category, 0) > 0 \
                            and passing.get(node.category, 0) == 0:
                        continue
                    active.add(node.id)
                else:
                    active.add(node.id)
            return active

        def _restart_settle(self, warm: bool = False):
            self._ticks = 0
            self._temperature = 26.0 if warm else 50.0
            self._settled = False
            if self._active_mode and not self._timer.isActive():
                self._timer.start()

        def set_active(self, active: bool):
            """The container calls this as the widget is shown/hidden
            as the current renderer: the frame timer (settle +
            auto-rotate) only runs while 3D is on stage."""
            self._active_mode = bool(active)
            if self._active_mode:
                if not self._settled or True:
                    self._timer.start()
            else:
                self._timer.stop()

        def _on_tick(self):
            if not self._active_mode or not self.isVisible():
                return
            if not self._settled:
                active = self._active_ids()
                pinned = set(self._pinned) | {"you"}
                if self._drag_node:
                    pinned.add(self._drag_node)
                max_disp = 0.0
                for _ in range(self.STEPS_PER_TICK):
                    max_disp = relax_step_3d(
                        self._data, self._pos, pinned=pinned,
                        active=active, temperature=self._temperature)
                    self._temperature = max(0.05,
                                            self._temperature * 0.985)
                self._ticks += 1
                if max_disp < self.SETTLE_EPSILON \
                        or self._ticks >= self.MAX_TICKS:
                    self._settled = True
                self.update()
                return
            if self._tour_active:
                # E26: the tour owns the camera while it runs —
                # the idle auto-rotate below stays suspended.
                self._tour_tick()
                self.update()
                return
            if self._search:
                # E24: keep the match pulse breathing while a
                # search is live.
                self._pulse_phase = (self._pulse_phase + 0.011) % 1.0
                self.update()
                return
            # Settled: drift the camera only when the web is truly
            # idle — no hover, no selection, no fresh interaction.
            idle = time.monotonic() - self._last_interaction
            if not self._hover and not self._selected \
                    and self._press_pos is None \
                    and idle > self.IDLE_BEFORE_ROTATE:
                self._azimuth += self.ROTATE_STEP
                self.update()

        def is_settled(self) -> bool:
            return self._settled

        def settle_ticks(self) -> int:
            return self._ticks

        def zoom_factor(self) -> float:
            return self.DEFAULT_DISTANCE / self._distance


        # ------------------------------------------------------------ queries

        def node_positions(self) -> dict:
            return {nid: (p[0], p[1]) for nid, p in self._pos.items()}

        def node_positions_3d(self) -> dict:
            return {nid: (p[0], p[1], p[2])
                    for nid, p in self._pos.items()}

        def visible_node_ids(self) -> list:
            return sorted(self._active_ids())

        def collapsed_categories(self) -> set:
            return set(self._collapsed)

        def selected_node(self) -> str | None:
            return self._selected

        def hover_node(self) -> str | None:
            return self._hover

        def cross_links(self, node_id: str) -> list:
            out = []
            for edge in self._data.edges:
                if edge.kind != "cross":
                    continue
                if edge.a == node_id:
                    other = self._data.node(edge.b)
                elif edge.b == node_id:
                    other = self._data.node(edge.a)
                else:
                    continue
                if other is not None:
                    out.append((other.id, other.label, edge.tokens))
            return out

        def search_dimmed_ids(self) -> set:
            if not self._search:
                return set()
            needle = self._search.lower()
            dimmed = set()
            for node in self._data.nodes:
                if node.kind == "you":
                    continue
                hay = " ".join((
                    node.label, node.category,
                    str((node.fact or {}).get("key") or ""),
                    str((node.fact or {}).get("value") or ""))).lower()
                if needle not in hay:
                    dimmed.add(node.id)
            return dimmed

        def set_search_highlight(self, text: str):
            self._search = (text or "").strip()
            if not self._search:
                self._pulse_phase = 0.0
            self.update()

        def search_matched_ids(self) -> set:
            """Nodes the current search lights up (E24): everything
            that is not dimmed, except the centre. Empty when no
            search is active."""
            if not self._search:
                return set()
            dimmed = self.search_dimmed_ids()
            return {node.id for node in self._data.nodes
                    if node.kind != "you" and node.id not in dimmed}

        def search_match_count(self) -> int:
            return len(self.search_matched_ids())


        # ------------------------------------------------- filter + timeline

        def set_category_filter(self, categories):
            """E25: show only facts in these categories (None =
            all). Hubs left with nothing visible under them hide
            too. Mirrors the 2D canvas."""
            wanted = None if categories is None else {
                str(c).strip().lower() for c in categories
                if str(c).strip()}
            if wanted != self._category_filter:
                self._category_filter = wanted
                self._restart_settle(warm=True)
                self.update()

        def category_filter(self) -> set | None:
            if self._category_filter is None:
                return None
            return set(self._category_filter)

        def set_time_cutoff(self, cutoff):
            """E27: hide facts dated after this epoch (None = all
            time). Undated facts always stay visible."""
            wanted = None if cutoff is None else float(cutoff)
            if wanted != self._time_cutoff:
                self._time_cutoff = wanted
                self._restart_settle(warm=True)
                self.update()

        def time_cutoff(self) -> float | None:
            return self._time_cutoff


        # ------------------------------------------------------------ tour

        def _tour_hubs(self) -> list:
            active = self._active_ids()
            return [node for node in self._data.nodes
                    if node.kind == "hub" and node.id in active]

        def start_tour(self):
            """E26: walk the camera hub to hub. No hubs, no tour."""
            if self._tour_active or not self._tour_hubs():
                return
            self._tour_active = True
            self._tour_index = 0
            self._tour_stop_started = time.monotonic()

        def stop_tour(self):
            self._tour_active = False

        def tour_active(self) -> bool:
            return self._tour_active

        def _tour_tick(self):
            if not self._tour_active:
                return
            hubs = self._tour_hubs()
            if not hubs:
                self.stop_tour()
                return
            # Stops 0..len(hubs)-1 frame each hub's cluster; the
            # final stop is the overview, then the tour ends.
            if self._tour_index < len(hubs):
                hub = hubs[self._tour_index]
                active = self._active_ids()
                members = [self._pos[hub.id]] + [
                    self._pos[node.id] for node in self._data.nodes
                    if node.kind == "fact"
                    and node.category == hub.category
                    and node.id in active]
                centroid = [sum(p[i] for p in members) / len(members)
                            for i in range(3)]
                radius = max(
                    math.dist(centroid, p) for p in members)
                goal_target = centroid
                goal_distance = max(
                    self.MIN_DISTANCE,
                    min(self.MAX_DISTANCE,
                        max(radius * 3.0, 320.0)))
                # A slow drift while dwelling keeps it cinematic.
                self._azimuth += 0.0008
            else:
                goal_target = [0.0, 0.0, 0.0]
                goal_distance = self.DEFAULT_DISTANCE
            self._target = [
                self._target[i] + (goal_target[i] - self._target[i])
                * 0.08 for i in range(3)]
            self._distance += (goal_distance - self._distance) * 0.08
            if time.monotonic() - self._tour_stop_started \
                    >= self.TOUR_STOP_SECONDS:
                self._tour_index += 1
                self._tour_stop_started = time.monotonic()
                if self._tour_index > len(hubs):
                    self.stop_tour()


        # ------------------------------------------------------------ selection

        def select_node(self, node_id: str | None):
            if node_id is None or node_id not in self._data.by_id:
                self._selected = None
                self.selection_cleared.emit()
                self.update()
                return
            self._selected = node_id
            self._note_interaction()
            node = self._data.node(node_id)
            if node is not None and node.kind == "fact" and node.fact:
                self.fact_selected.emit(node.fact)
            else:
                self.selection_cleared.emit()
            self.update()

        def clear_selection(self):
            self.select_node(None)

        def expand_category(self, category: str):
            if category in self._collapsed:
                self._collapsed.discard(category)
                self._restart_settle(warm=True)
                self.update()

        def set_collapsed_categories(self, categories):
            wanted = {c for c in (categories or set())
                      if f"hub:{c}" in self._data.by_id}
            if wanted != self._collapsed:
                self._collapsed = wanted
                self._restart_settle(warm=True)
                self.update()


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
            """World -> (screen point in widget px, view depth), or
            None when the point sits behind the camera. The same
            matrices feed the shader, the label overlay and picking,
            so what you see is exactly what you can click."""
            if mvp is None or view is None:
                mvp, view, _proj = self._mvp()
            # NB: matrix * vector via the operator is unreliable in
            # PySide6 6.12 (it raises inside shiboken) — map() is
            # the supported spelling and does the same math.
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

        def _screen_radius(self, node: GraphNode, depth: float,
                           proj) -> float:
            """Node radius in screen px at the given view depth."""
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
            self._distance = max(self.MIN_DISTANCE,
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
                # Uniforms are addressed by location, not name: in
                # PySide6 6.12 the name+float setUniformValue
                # overload mis-resolves and raises TypeError.
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

        def gl_ready(self) -> bool:
            return self._gl_ready and not self._gl_failed


        # ------------------------------------------------------------ GL paint

        def _neighbours(self, node_id: str) -> set:
            out = {node_id}
            for edge in self._data.edges:
                if edge.a == node_id:
                    out.add(edge.b)
                elif edge.b == node_id:
                    out.add(edge.a)
            return out
