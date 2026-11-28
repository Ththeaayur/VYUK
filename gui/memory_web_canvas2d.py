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
    TEXT_FAINT,
    TEXT_MUTED,
    RADIUS_BTN,
)
from .design_system.palette import luminance
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
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QStackedLayout,
    QWidget,
)
import hashlib
import random
import time

from .memory_web_graph import GraphData
from .memory_web_graph import GraphNode
from .memory_web_graph import _EDGE_CROSS
from .memory_web_graph import _LABEL_FACT_PX
from .memory_web_graph import _LABEL_HUB_PX
from .memory_web_graph import _TEXT_MAIN
from .memory_web_graph import _TEXT_MUTED
from .memory_web_graph import _draw_label
from .memory_web_graph import _fact_epoch
from .memory_web_graph import _node_tooltip
from .memory_web_graph import _plan_label_rects
from .memory_web_graph import build_graph
from .memory_web_graph import category_color
from .memory_web_graph import relax_step
from .memory_web_graph import seed_positions


class MemoryWeb2DCanvas(QWidget):
    """The woven memory graph, painted flat: pan, zoom, drag-to-pin,
    hover to light a neighbourhood, click a fact to inspect it,
    double-click a hub to fold its facts away. (This was v3.1's
    MemoryWebWidget; in v3.2 that name belongs to the container
    hosting this canvas and the 3D renderer.)"""

    fact_selected = Signal(object)       # the store row (dict)
    selection_cleared = Signal()

    SETTLE_EPSILON = 0.10
    MAX_TICKS = 40
    STEPS_PER_TICK = 12
    TOUR_STOP_SECONDS = 2.6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        # UI upgrade Phase 1 (r3ef202): 280 -> 480. The
        # seed ring radius (~150-210) needs ~250 px of
        # half-height; at 280 the bottom hubs and their
        # labels fell off the canvas and were clipped.
        self.setMinimumSize(320, 480)
        self._data = GraphData()
        # Stage 0 features batch 5 (larger text): the painted
        # node labels + badge + empty state scale with the
        # appearance font scale (the QSS cannot reach paintEvent
        # text). Driven by MemoryWebWidget.set_font_scale.
        self._font_scale = 1.0
        self._pos: dict[str, list] = {}
        self._pinned: set = set()
        self._collapsed: set = set()
        self._hover: str | None = None
        self._selected: str | None = None
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._search = ""
        # v5.18 view state: category filter + timeline cutoff +
        # the search-highlight pulse + the auto-tour.
        self._category_filter: set | None = None
        self._time_cutoff: float | None = None
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(40)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)
        self._tour_active = False
        self._tour_index = 0
        self._tour_stop_started = 0.0
        self._tour_timer = QTimer(self)
        self._tour_timer.setInterval(33)
        self._tour_timer.timeout.connect(self._tour_tick)
        self._facts_hash: str | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_tick)
        self._ticks = 0
        self._temperature = 50.0
        self._settled = True
        self._drag_node: str | None = None
        self._drag_offset = QPointF()
        self._press_pos: QPointF | None = None
        self._press_node: str | None = None
        self._panning = False
        self._last_mouse = QPointF()
        # UI Phase 1 (M-15, M-21, M-25): the constellation (a fixed
        # set of faint background dots, painted first), the resolved
        # theme (label / edge / halo colours), and the private-mode
        # flag (the one-hop highlight is disabled while private).
        self._theme: dict | None = None
        self._constellation_enabled = False
        self._constellation_points: list[tuple[float, float]] = []
        self._constellation_links: list[tuple[int, int]] = []
        self._private = False
        self._rebuild_constellation()

    # ------------------------------------------------------------ data

    @property
    def graph(self) -> GraphData:
        return self._data

    def set_font_scale(self, scale: float) -> None:
        """Scale the painted labels / badge / empty state."""
        try:
            self._font_scale = float(scale)
        except (TypeError, ValueError):
            self._font_scale = 1.0
        self.update()

    def set_theme(self, resolved: dict) -> None:
        """Recolour the canvas from a resolved theme (M-21).

        Label / edge / halo colours follow the theme (text,
        text_muted, text_faint, bg) instead of the dark-only
        module constants; the constellation's alpha drops on
        Light. Called from the window's appearance pass.
        """
        try:
            self._theme = dict(resolved or {})
        except Exception:  # noqa: BLE001 — theme never breaks paint
            self._theme = None
        self._rebuild_constellation()
        self.update()

    def set_private_mode(self, on: bool) -> None:
        """Private mode disables the one-hop highlight (M-25)."""
        self._private = bool(on)
        self.update()

    def set_constellation_enabled(self, enabled: bool) -> None:
        """Whether the Graph section may show the constellation.

        Set by the window from the section + profile (Graph
        section, profile not Minimal). The property below is
        the honest "is it painting now" (search hides it).
        """
        self._constellation_enabled = bool(enabled)
        self.update()

    @property
    def constellation_visible(self) -> bool:
        """True while the constellation is actually painting.

        The Graph section, a non-Minimal profile, and no search
        live. Search hides it (M-15, while search_dimmed_ids
        is non-empty — i.e. while a search string is set).
        """
        return self._constellation_enabled and not self._search

    def one_hop_ids(self) -> set:
        """The selected node's one-hop neighbours — exactly the
        cross-links (M-25). NOT the hub membership edges: a fact
        shares a hub with every fact in its category, and that
        is not a link. Empty when nothing is selected."""
        if not self._selected:
            return set()
        return {other for other, _label, _tokens
                in self.cross_links(self._selected)}

    def _theme_colour(self, key, default):
        """A resolved-theme colour, or the dark default."""
        if self._theme:
            try:
                return QColor(self._theme.get(key, default))
            except Exception:  # noqa: BLE001 — fall back to default
                pass
        return QColor(default)

    def _rebuild_constellation(self) -> None:
        """Cache the constellation's dots + nearest-neighbour
        links (M-15). A fixed set of faint dots at deterministic
        seeded positions — NOT from facts — smaller than the
        smallest fact node, with faint links between nearest
        neighbours. Rebuilt only on resize / data change / theme
        change; never animated, never on a timer."""
        width, height = self.width(), self.height()
        self._constellation_points = []
        self._constellation_links = []
        if width <= 40 or height <= 40:
            return
        rng = random.Random(20261123)
        margin = 24
        count = 48
        points = []
        for _ in range(count):
            points.append((
                margin + rng.random() * (width - 2 * margin),
                margin + rng.random() * (height - 2 * margin)))
        self._constellation_points = points
        links = set()
        for i, (x, y) in enumerate(points):
            best, best_d = None, None
            for j, (ox, oy) in enumerate(points):
                if i == j:
                    continue
                d = (x - ox) ** 2 + (y - oy) ** 2
                if best_d is None or d < best_d:
                    best, best_d = j, d
            if best is not None:
                links.add((min(i, best), max(i, best)))
        self._constellation_links = sorted(links)

    def _paint_constellation(self, painter) -> None:
        """The constellation, first in paintEvent (before edges)."""
        if not self.constellation_visible:
            return
        if not self._constellation_points:
            self._rebuild_constellation()
        if not self._constellation_points:
            return
        # Light alpha drops (M-15): the dots are fainter on a
        # light background so they stay background, not content.
        light = False
        if self._theme:
            try:
                light = luminance(self._theme.get("bg", BG)) > 0.5
            except Exception:  # noqa: BLE001 — dark alpha then
                light = False
        dot_a = 45 if light else 70
        link_a = 18 if light else 28
        faint = self._theme_colour("text_faint", TEXT_FAINT)
        link_col = QColor(faint)
        link_col.setAlpha(link_a)
        painter.setPen(QPen(link_col, 1.0))
        for i, j in self._constellation_links:
            ax, ay = self._constellation_points[i]
            bx, by = self._constellation_points[j]
            painter.drawLine(QPointF(ax, ay), QPointF(bx, by))
        dot_col = QColor(faint)
        dot_col.setAlpha(dot_a)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot_col)
        for x, y in self._constellation_points:
            painter.drawEllipse(QPointF(x, y), 2.0, 2.0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild_constellation()

    def set_facts(self, facts: list):
        """Rebuild the web from store rows, keeping every surviving
        node at (or near) its current spot, then re-settle briefly."""
        facts = list(facts or [])
        self._facts_hash = self._hash_facts(facts)
        previous = {nid: xy for nid, xy in self._pos.items()}
        self._data = build_graph(facts)
        self._pos = seed_positions(self._data, previous=previous)
        self._pinned = {p for p in self._pinned if p in self._data.by_id}
        self._collapsed = {c for c in self._collapsed
                           if f"hub:{c}" in self._data.by_id}
        if self._selected and self._selected not in self._data.by_id:
            self._selected = None
        if self._hover and self._hover not in self._data.by_id:
            self._hover = None
        self._rebuild_constellation()
        self._restart_settle(warm=bool(previous))
        self.update()

    def refresh_if_changed(self, facts: list) -> bool:
        """Cheap page-show hook: rebuild only when the (id, key,
        value, updated_at) set actually changed."""
        facts = list(facts or [])
        if self._hash_facts(facts) == self._facts_hash \
                and self._facts_hash is not None:
            return False
        self.set_facts(facts)
        return True

    @staticmethod
    def _hash_facts(facts: list) -> str:
        digest = hashlib.sha256()
        rows = sorted(
            (str(f.get("id")), str(f.get("key") or ""),
             str(f.get("value") or ""), str(f.get("updated_at") or ""))
            for f in facts if isinstance(f, dict))
        for row in rows:
            digest.update("\x1f".join(row).encode("utf-8"))
            digest.update(b"\x1e")
        return digest.hexdigest()

    # ------------------------------------------------------------ layout

    def _hidden_by_view(self, node: GraphNode) -> bool:
        """v5.18: is this FACT node hidden by the category filter
        or the timeline cutoff? (Folding is separate — see
        _active_ids.) Undated facts are never cutoff-hidden."""
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
        # A fact shows iff it is not folded away AND not hidden by
        # the filter/cutoff. A hub shows unless every fact it owns
        # is filter/cutoff-hidden (then there is nothing under it
        # to point at); a hub with no graph facts at all keeps the
        # historical behaviour and stays.
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
        if not self._timer.isActive():
            self._timer.start()

    def _on_tick(self):
        if not self.isVisible():
            return  # paused while off-page; resumes on return
        if self._settled:
            self._timer.stop()
            return
        active = self._active_ids()
        pinned = set(self._pinned) | {"you"}
        if self._drag_node:
            pinned.add(self._drag_node)
        max_disp = 0.0
        for _ in range(self.STEPS_PER_TICK):
            max_disp = relax_step(self._data, self._pos, pinned=pinned,
                                  active=active,
                                  temperature=self._temperature)
            # Cool toward a whisper, not a floor of 1.2: displacement
            # is temperature-capped, so a high floor would keep the
            # web jiggling forever and the epsilon could never land.
            self._temperature = max(0.05, self._temperature * 0.985)
        self._ticks += 1
        if max_disp < self.SETTLE_EPSILON or self._ticks >= self.MAX_TICKS:
            self._settled = True
            self._timer.stop()
        self.update()

    def is_settled(self) -> bool:
        return self._settled

    @property
    def settle_ticks(self) -> int:
        return self._ticks

    @property
    def zoom_factor(self) -> float:
        return self._zoom

    # ------------------------------------------------------------ queries

    def node_positions(self) -> dict:
        return {nid: (xy[0], xy[1]) for nid, xy in self._pos.items()}

    def visible_node_ids(self) -> list:
        return sorted(self._active_ids())

    def collapsed_categories(self) -> set:
        return set(self._collapsed)

    def selected_node(self) -> str | None:
        return self._selected

    def cross_links(self, node_id: str) -> list:
        """[(other_node_id, other_label, shared_tokens)] for a node."""
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
        # The halo pulse (E24) runs only while a search is live.
        if self._search:
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
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

    def _on_pulse_tick(self):
        self._pulse_phase = (self._pulse_phase + 0.014) % 1.0
        if self.isVisible():
            self.update()

    # ------------------------------------------------- filter + timeline

    def set_category_filter(self, categories):
        """E25: show only facts in these categories (None = all).
        Hubs left with nothing visible under them hide too."""
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
        self._tour_timer.start()

    def stop_tour(self):
        self._tour_active = False
        if self._tour_timer.isActive():
            self._tour_timer.stop()

    def tour_active(self) -> bool:
        return self._tour_active

    def _tour_tick(self):
        if not self._tour_active:
            return
        hubs = self._tour_hubs()
        if not hubs:
            self.stop_tour()
            return
        # Stops 0..len(hubs)-1 visit the hubs; the final stop is
        # the overview, after which the tour ends.
        if self._tour_index < len(hubs):
            hub = hubs[self._tour_index]
            xy = self._pos[hub.id]
            goal_zoom = 1.15
            goal_pan = QPointF(-xy[0] * goal_zoom, -xy[1] * goal_zoom)
        else:
            goal_zoom = 1.0
            goal_pan = QPointF(0.0, 0.0)
        self._zoom += (goal_zoom - self._zoom) * 0.09
        self._pan += (goal_pan - self._pan) * 0.09
        if time.monotonic() - self._tour_stop_started \
                >= self.TOUR_STOP_SECONDS:
            self._tour_index += 1
            self._tour_stop_started = time.monotonic()
            if self._tour_index > len(hubs):
                self.stop_tour()
                return
        self.update()

    # ------------------------------------------------------------ transform

    def _fit_scale(self) -> float:
        """Shrink the graph to fit the canvas (UI Phase 1).

        The seed layout's ring radius (~150–210 world units) was
        sized for the old full-page web view; in Phase 1 the Graph
        is a section whose canvas is 280 px tall (half-height
        140), so the ring's bottom hubs and their labels fall
        off the canvas and get clipped (r3ef202: "Sister's
        birthday" at y=289 on a 280 canvas). The fit scale
        shrinks the world→screen mapping just enough that the
        farthest node + its label fit inside the canvas, with a
        24 px margin. It never enlarges (≤ 1) and it multiplies
        the user's zoom, so zoom/pan keep working — the fit is
        a floor, not a replacement.
        """
        import math
        if not getattr(self, "_pos", None):
            return 1.0
        try:
            max_r = max(
                math.hypot(float(xy[0]), float(xy[1]))
                for xy in self._pos.values())
        except Exception:  # noqa: BLE001 — no positions then
            return 1.0
        if max_r <= 0:
            return 1.0
        half = min(self.width(), self.height()) / 2.0 - 24.0
        if half <= 0:
            return 1.0
        return min(1.0, half / max_r)

    def world_to_screen(self, x: float, y: float) -> QPointF:
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        scale = self._zoom * self._fit_scale()
        return centre + self._pan + QPointF(x * scale, y * scale)

    def _screen_to_world(self, point: QPointF) -> QPointF:
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        rel = point - centre - self._pan
        scale = self._zoom * self._fit_scale()
        if scale == 0:
            return QPointF(0.0, 0.0)
        return QPointF(rel.x() / scale, rel.y() / scale)

    def zoom_by(self, factor: float, anchor: QPointF | None = None):
        """Zoom about a screen anchor (the cursor, normally), so the
        world point under it stays put."""
        if anchor is None:
            anchor = QPointF(self.width() / 2.0, self.height() / 2.0)
        before = self._screen_to_world(anchor)
        self._zoom = max(0.3, min(2.5, self._zoom * factor))
        after = self.world_to_screen(before.x(), before.y())
        self._pan += anchor - after
        self.update()

    # ------------------------------------------------------------ selection

    def select_node(self, node_id: str | None):
        """Single selection path (mouse, detail-panel link rows):
        facts raise fact_selected; anything else lowers the panel."""
        if node_id is None or node_id not in self._data.by_id:
            self._selected = None
            self.selection_cleared.emit()
            self.update()
            return
        self._selected = node_id
        node = self._data.node(node_id)
        if node is not None and node.kind == "fact" and node.fact:
            self.fact_selected.emit(node.fact)
        else:
            self.selection_cleared.emit()
        self.update()

    def clear_selection(self):
        self.select_node(None)

    def expand_category(self, category: str):
        """Unfold a hub (used when a detail-panel link points into
        a collapsed category, so the target is actually visible)."""
        if category in self._collapsed:
            self._collapsed.discard(category)
            self._restart_settle(warm=True)
            self.update()

    def set_collapsed_categories(self, categories):
        """Adopt another renderer's fold state (the container syncs
        this when swapping 2D <-> 3D, so folds follow the user)."""
        wanted = {c for c in (categories or set())
                  if f"hub:{c}" in self._data.by_id}
        if wanted != self._collapsed:
            self._collapsed = wanted
            self._restart_settle(warm=True)
            self.update()

    def hover_node(self) -> str | None:
        return self._hover

    # ------------------------------------------------------------ painting

    def _display_radius(self, node: GraphNode) -> float:
        """On-screen node radius: facts breathe a little with zoom
        (clamped, so labels and dots stay proportionate); hubs and
        the centre scale plainly."""
        if node.kind == "fact":
            return node.radius * max(0.8, min(1.15, self._zoom))
        return node.radius * self._zoom

    def _neighbours(self, node_id: str) -> set:
        out = {node_id}
        for edge in self._data.edges:
            if edge.a == node_id:
                out.add(edge.b)
            elif edge.b == node_id:
                out.add(edge.a)
        return out

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        # The constellation paints FIRST, before the edges — and
        # before the empty-graph return, so the Graph section has
        # its faint starfield even with no facts yet (M-15).
        self._paint_constellation(painter)
        data = self._data
        if not data.nodes:
            return
        active = self._active_ids()
        focus = self._hover or self._selected
        # M-25: a SELECTED node's neighbourhood is its one-hop
        # cross-links only (never the hub membership — sharing a
        # category is not a link). A hovered node keeps the old
        # neighbourhood light. The linked ring is suppressed
        # while searching and in private mode.
        if focus and focus == self._selected:
            neighbours = self.one_hop_ids() | {focus}
            linked = (self.one_hop_ids()
                      if not self._search and not self._private
                      else set())
        else:
            neighbours = self._neighbours(focus) if focus else set()
            linked = set()
        dimmed = self.search_dimmed_ids()
        matched = self.search_matched_ids()

        def node_alpha(node) -> float:
            alpha = 1.0
            if focus and node.id not in neighbours:
                alpha *= 0.55
            if node.id in dimmed:
                alpha *= 0.30
            return alpha

        def screen(node_id: str) -> QPointF:
            xy = self._pos[node_id]
            return self.world_to_screen(xy[0], xy[1])

        # --- edges: cross-links first (under), then membership.
        for kind in ("cross", "membership"):
            for edge in data.edges:
                if edge.kind != kind:
                    continue
                if edge.a not in active or edge.b not in active:
                    continue
                na, nb = data.node(edge.a), data.node(edge.b)
                if na is None or nb is None:
                    continue
                pa, pb = screen(edge.a), screen(edge.b)
                if kind == "cross":
                    # M-21: the cross edge follows the theme's
                    # text_faint (a hairline), not the dark-only
                    # white constant.
                    colour = self._theme_colour("text_faint", TEXT_FAINT)
                    colour.setAlpha(40)
                    width = 1.0
                    if focus and (edge.a == focus or edge.b == focus):
                        colour = QColor(255, 255, 255, 160)
                        width = 1.4
                    elif focus:
                        colour = QColor(255, 255, 255, 16)
                else:
                    hub = na if na.kind == "hub" else nb
                    colour = QColor(category_color(hub.category))
                    colour.setAlpha(90)
                    width = 1.2
                    if focus and (edge.a == focus or edge.b == focus):
                        colour.setAlpha(205)
                        width = 1.8
                    elif focus:
                        colour.setAlpha(36)
                fade = min(node_alpha(na), node_alpha(nb))
                if fade < 1.0:
                    colour.setAlpha(int(colour.alpha() * fade))
                painter.setPen(QPen(colour, width))
                painter.drawLine(pa, pb)

        # --- nodes: facts, then hubs, then You on top.
        ordered = ([n for n in data.nodes if n.kind == "fact"]
                   + [n for n in data.nodes if n.kind == "hub"]
                   + [n for n in data.nodes if n.kind == "you"])
        for node in ordered:
            if node.id not in active:
                continue
            centre = screen(node.id)
            r = self._display_radius(node)
            alpha = node_alpha(node)
            base = QColor(category_color(node.category)) \
                if node.kind != "you" else QColor(ACCENT)
            if node.id == self._hover or node.id == self._selected:
                ring = QColor(base)
                ring.setAlpha(int(210 * alpha))
                painter.setPen(QPen(ring, 1.6))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(centre, r + 3.5, r + 3.5)
            if node.id in linked:
                # M-25: the one-hop highlight — a faint ring on
                # every fact one cross-link from the selection.
                # Suppressed while searching / in private mode
                # (see the `linked` set above).
                link_ring = QColor(base)
                link_ring.setAlpha(int(110 * alpha))
                painter.setPen(QPen(link_ring, 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(centre, r + 3.5, r + 3.5)
            if node.id in matched:
                # E24: the search pulse — two expanding rings,
                # half a cycle apart, fading as they grow.
                for k in range(2):
                    frac = (self._pulse_phase + k * 0.5) % 1.0
                    halo = QColor(base).lighter(165)
                    halo.setAlpha(int(175 * (1.0 - frac) * alpha))
                    painter.setPen(QPen(halo, 1.8))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(centre, r + 4.0 + frac * 9.0,
                                        r + 4.0 + frac * 9.0)
            grad = QRadialGradient(centre + QPointF(-r * 0.35, -r * 0.35),
                                   r * 1.9)
            light = QColor(base).lighter(155)
            light.setAlpha(int(255 * alpha))
            mid = QColor(base)
            mid.setAlpha(int(235 * alpha))
            deep = QColor(base).darker(140)
            deep.setAlpha(int(255 * alpha))
            grad.setColorAt(0.0, light)
            grad.setColorAt(0.55, mid)
            grad.setColorAt(1.0, deep)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            painter.drawEllipse(centre, r, r)
            if node.id in self._pinned:
                painter.setBrush(QColor(255, 255, 255, int(200 * alpha)))
                painter.drawEllipse(
                    centre + QPointF(r * 0.62, -r * 0.62), 1.7, 1.7)

        # --- collapsed-hub count badges.
        for node in data.nodes:
            if node.kind != "hub" or node.category not in self._collapsed:
                continue
            if node.id not in active:
                continue
            centre = screen(node.id)
            hidden = sum(1 for n in data.nodes
                         if n.kind == "fact" and n.category == node.category)
            badge_c = centre + QPointF(node.radius * 0.85,
                                       node.radius * 0.85)
            painter.setPen(QPen(QColor(category_color(node.category)), 1.0))
            painter.setBrush(QColor(SURFACE_3))
            painter.drawEllipse(badge_c, 8.5, 8.5)
            painter.setPen(QColor(TEXT))
            font = QFont()
            font.setPixelSize(max(1, round(9 * self._font_scale)))
            painter.setFont(font)
            painter.drawText(
                QRectF(badge_c.x() - 8.5, badge_c.y() - 8.5, 17, 17),
                Qt.AlignmentFlag.AlignCenter, str(hidden))

        # --- labels: planned, not sprayed (v3.2). Priority order —
        # You, hubs, the selected node, the hovered node, then facts
        # by degree. A fact label that would collide with an
        # already-placed one is skipped for this paint (it returns
        # as the zoom rises or its node is hovered/selected); hubs
        # flip to the other side of their node first. Every drawn
        # label sits on a soft halo so it reads over edges.
        entries = []
        label_info: dict[str, tuple] = {}
        for node in ordered:
            if node.id not in active:
                continue
            if node.kind == "you":
                priority, force, sticky = 0, True, False
            elif node.kind == "hub":
                priority, force, sticky = 1, False, True
            elif node.id == self._selected:
                priority, force, sticky = 2, True, False
            elif node.id == self._hover:
                priority, force, sticky = 3, True, False
            elif node.kind == "fact" and self._zoom >= 0.85:
                priority, force, sticky = 4, False, False
            else:
                continue  # fact labels wait for zoom >= 0.85
            centre = screen(node.id)
            r = self._display_radius(node)
            label = node.label
            if node.kind == "fact" and len(label) > 20:
                label = label[:19] + "…"
            font = QFont()
            font.setPixelSize(max(1, round(
                (_LABEL_HUB_PX if node.kind != "fact"
                 else _LABEL_FACT_PX) * self._font_scale)))
            # E-F202: measure the DRAWN ink, not the advance.
            # drawText clips to the rect, and the ink overhangs
            # the advance by ~1 px on the right (plus
            # antialiasing), so an advance-wide rect cuts the
            # last glyph's edge. boundingRect is the ink box;
            # +4 px (2 each side) leaves the ink clear of the
            # clip edge however drawText centres the advance.
            ink_w = float(QFontMetrics(font).boundingRect(label).width())
            text_w = ink_w + 4.0
            rect = QRectF(centre.x() - text_w / 2.0,
                          centre.y() + r + 3.0, text_w, 16.0)
            alt = QRectF(centre.x() - text_w / 2.0,
                         centre.y() - r - 3.0 - 16.0, text_w, 16.0) \
                if node.kind == "hub" else None
            entries.append({
                "id": node.id, "priority": priority,
                "degree": node.degree, "rect": rect, "alt_rect": alt,
                "force": force, "sticky": sticky,
            })
            # M-21: label colours follow the resolved theme.
            colour = self._theme_colour(
                "text" if node.kind != "fact" else "text_muted",
                TEXT if node.kind != "fact" else TEXT_MUTED)
            colour.setAlpha(int(255 * node_alpha(node)))
            label_info[node.id] = (label, font, colour)
        # Hunt fix (c) item 9: every node's sphere is an obstacle
        # for every other node's label (a fact label ~3 px from a
        # neighbouring hub's sphere reads as merged with it).
        # The planner excludes each label's own sphere.
        obstacles = [(screen(node.id), self._display_radius(node),
                      node.id)
                     for node in ordered if node.id in active]
        for node_id, rect in _plan_label_rects(
                entries, obstacles=obstacles).items():
            label, font, colour = label_info[node_id]
            # M-21: the label halo follows the theme's bg (on
            # Light a dark halo would read as a smudge).
            halo = None
            if self._theme:
                halo = self._theme_colour("bg", BG)
                halo.setAlpha(120)
            _draw_label(painter, rect, label, font, colour, halo)

        if not any(n.kind == "fact" for n in data.nodes):
            painter.setPen(_TEXT_MUTED)
            font = QFont()
            font.setPixelSize(max(1, round(12 * self._font_scale)))
            painter.setFont(font)
            painter.drawText(
                QRectF(0, self.height() / 2.0 + 34, self.width(), 20),
                Qt.AlignmentFlag.AlignHCenter,
                "Nothing to weave yet — facts VYUK learns will appear here.")

    # ------------------------------------------------------------ interaction

    def _hit_test(self, point: QPointF) -> str | None:
        data = self._data
        active = self._active_ids()
        ordered = ([n for n in data.nodes if n.kind == "you"]
                   + [n for n in data.nodes if n.kind == "hub"]
                   + [n for n in data.nodes if n.kind == "fact"])
        for node in ordered:
            if node.id not in active:
                continue
            xy = self._pos[node.id]
            centre = self.world_to_screen(xy[0], xy[1])
            r = self._display_radius(node) + 4.0
            if (point.x() - centre.x()) ** 2 \
                    + (point.y() - centre.y()) ** 2 <= r * r:
                return node.id
        return None

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
        if self._tour_active:
            self.stop_tour()  # the user's hand always wins (E26)
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position()
        self._press_pos = pos
        self._last_mouse = pos
        hit = self._hit_test(pos)
        self._press_node = hit
        if hit:
            xy = self._pos[hit]
            centre = self.world_to_screen(xy[0], xy[1])
            self._drag_offset = centre - pos
            self._drag_node = None  # becomes a drag past the threshold
        else:
            self._panning = False  # becomes a pan past the threshold
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802
        pos = event.position()
        if self._press_pos is not None \
                and (event.buttons() & Qt.MouseButton.LeftButton):
            moved = (pos - self._press_pos).manhattanLength()
            if self._press_node:
                if moved > 3 or self._drag_node:
                    self._drag_node = self._press_node
                    world = self._screen_to_world(pos + self._drag_offset)
                    self._pos[self._drag_node] = [world.x(), world.y()]
                    self._pinned.add(self._drag_node)
                    self.update()
            else:
                self._panning = True
                self._pan += pos - self._last_mouse
                self.update()
            self._last_mouse = pos
            return
        self._set_hover(self._hit_test(pos))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton \
                and self._press_pos is not None:
            was_click = (event.position() - self._press_pos) \
                .manhattanLength() <= 3
            dragged = self._drag_node is not None
            if was_click and not dragged:
                hit = self._hit_test(event.position())
                self.select_node(hit)
            self._press_pos = None
            self._press_node = None
            self._drag_node = None
            self._panning = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._hit_test(event.position())
            node = self._data.node(hit) if hit else None
            if node is not None and node.kind == "hub":
                if node.category in self._collapsed:
                    self._collapsed.discard(node.category)
                else:
                    self._collapsed.add(node.category)
                    if self._selected and self._selected.startswith("fact:"):
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
            self.zoom_by(1.12 ** steps, anchor=event.position())
        event.accept()
