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
from .design_system.palette import lighten, with_alpha
import re
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
import math
import random
from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    Signal,
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



# ---------------------------------------------------------------------------
# Pure data layer (no Qt state — importable and testable headlessly)
# ---------------------------------------------------------------------------

MAX_PER_CATEGORY = 40

MAX_FACTS = 150

MAX_CROSS_DEGREE = 4


# ~50 function words and assistant-noise words that would otherwise
# link unrelated facts ("that", "with", "nova", ...). Tokens shorter
# than 4 chars never tokenize at all.
STOPWORDS = frozenset({
    "that", "this", "with", "from", "have", "your", "yours", "about",
    "when", "what", "then", "than", "them", "they", "will", "would",
    "there", "their", "been", "were", "also", "just", "like", "into",
    "over", "under", "after", "before", "while", "where", "which",
    "been", "being", "does", "done", "make", "made", "many", "much",
    "more", "most", "some", "such", "only", "very", "could", "should",
    "shall", "might", "must", "once", "again", "nova", "visor", "user",
})


# Command Deck Phase E: the category palette is derived from the
# token palette, never hardcoded. Known categories pin their role
# colour (identity is the brand cyan; learned the blue secondary;
# preferences the ok green; notes the amber); anything else hashes
# deterministically into the same family, with pale tints computed
# from the tokens for the last three slots. Categories stay
# mutually distinct — collapsing them into four roles would erase
# information the web exists to show.
KNOWN_COLORS = {
    "identity": ACCENT,
    "learned": ACCENT_2,
    "preferences": OK,
    "preference": OK,
    "note": AMBER,
    "notes": AMBER,
}

PALETTE = (
    ACCENT, ACCENT_2, OK, AMBER, DANGER,
    lighten(ACCENT, 0.45), lighten(AMBER, 0.45), lighten(OK, 0.35),
)

_WORD_RE = re.compile(r"[a-z0-9]+")

def category_color(category: str) -> str:
    """Hex colour for a category: pinned for known ones, a stable
    hash into the curated palette for everything else."""
    cat = (category or "general").strip().lower()
    if cat in KNOWN_COLORS:
        return KNOWN_COLORS[cat]
    digest = hashlib.md5(cat.encode("utf-8")).hexdigest()
    return PALETTE[int(digest[:4], 16) % len(PALETTE)]

def category_label(category: str) -> str:
    """Friendly hub name: 'learned' gets its own sentence-style
    label; everything else is de-snaked and titled."""
    cat = (category or "general").strip().lower()
    if cat == "learned":
        return "Learned from you"
    label = cat.replace("_", " ").replace("-", " ").strip()
    return label.title() if label else "General"

@dataclass
class GraphNode:
    id: str                      # "you" | "hub:<cat>" | "fact:<id>"
    kind: str                    # "you" | "hub" | "fact"
    label: str
    category: str = ""           # facts + hubs; "" for the centre
    fact_id: int | None = None
    fact: dict | None = None     # the store row, for facts
    degree: int = 0              # incident edges (membership + cross)
    overflow: int = 0            # hubs: facts shed by the caps
    count: int = 0               # hubs: fact nodes in the graph
    radius: float = 6.0

@dataclass
class GraphEdge:
    a: str
    b: str
    kind: str                    # "membership" | "cross"
    tokens: tuple = ()           # cross-links: the shared words           # cross-links: the shared words

@dataclass
class GraphData:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    by_id: dict = field(default_factory=dict)

    def node(self, node_id: str) -> GraphNode | None:
        return self.by_id.get(node_id)

def _fact_tokens(fact: dict, category_parts: set) -> set:
    text = f"{fact.get('key') or ''} {fact.get('value') or ''}".lower()
    out = set()
    for word in _WORD_RE.findall(text):
        if len(word) < 4 or word in STOPWORDS:
            continue
        if word in category_parts or word.isdigit():
            continue
        out.add(word)
    return out

def _you_label(facts: list) -> str:
    """The centre node's name: what she should call the user wins
    (preferred_name), then a stored name — learned or identity."""
    for wanted in ("preferred_name", "name"):
        for fact in facts:
            if str(fact.get("key") or "").strip().lower() != wanted:
                continue
            if str(fact.get("category") or "").strip().lower() \
                    not in ("learned", "identity", "general"):
                continue
            value = str(fact.get("value") or "").strip()
            if value:
                return value[:24]
    return "You"

def build_graph(facts: list) -> GraphData:
    """Store rows -> graph. Pure and deterministic: same input,
    same web (node order, caps and cross-links included)."""
    facts = [f for f in (facts or []) if isinstance(f, dict)]
    data = GraphData()
    you = GraphNode(id="you", kind="you", label=_you_label(facts),
                    radius=17.0)
    data.nodes.append(you)

    # Group by category, newest first (updated_at, then id, desc).
    groups: dict[str, list] = {}
    for fact in facts:
        cat = str(fact.get("category") or "general").strip().lower() \
            or "general"
        groups.setdefault(cat, []).append(fact)
    for cat in groups:
        groups[cat].sort(
            key=lambda f: (str(f.get("updated_at") or ""),
                           f.get("id") or 0),
            reverse=True)

    # Caps: per-category first, then the global fact budget sheds
    # the globally oldest nodes into their hubs' overflow counts.
    kept: dict[str, list] = {}
    overflow: dict[str, int] = {}
    for cat, rows in groups.items():
        kept[cat] = rows[:MAX_PER_CATEGORY]
        overflow[cat] = len(rows) - len(kept[cat])
    total = sum(len(rows) for rows in kept.values())
    if total > MAX_FACTS:
        flat = [(str(f.get("updated_at") or ""), f.get("id") or 0, cat, f)
                for cat, rows in kept.items() for f in rows]
        flat.sort(key=lambda t: (t[0], t[1]))  # oldest first
        shed = {id(f) for _ts, _fid, _c, f in flat[: total - MAX_FACTS]}
        for cat, rows in kept.items():
            survivors = []
            for f in rows:
                if id(f) in shed:
                    overflow[cat] += 1
                else:
                    survivors.append(f)
            kept[cat] = survivors

    category_parts = set()
    for cat in groups:
        category_parts.update(p for p in _WORD_RE.findall(cat) if p)

    hubs: dict[str, GraphNode] = {}
    for cat in sorted(kept):
        hub = GraphNode(id=f"hub:{cat}", kind="hub",
                        label=category_label(cat), category=cat,
                        overflow=overflow.get(cat, 0),
                        count=len(kept[cat]), radius=11.0)
        hubs[cat] = hub
        data.nodes.append(hub)
        data.edges.append(GraphEdge(a=hub.id, b="you", kind="membership"))
        for fact in kept[cat]:
            key = str(fact.get("key") or "").strip()
            value = str(fact.get("value") or "").strip()
            label = key if key else value[:24]
            node = GraphNode(id=f"fact:{fact.get('id')}", kind="fact",
                             label=label, category=cat,
                             fact_id=fact.get("id"), fact=fact)
            data.nodes.append(node)
            data.edges.append(
                GraphEdge(a=node.id, b=hub.id, kind="membership"))

    # Cross-links between facts of different categories.
    tokens_by_node: dict[str, set] = {}
    facts_by_id = {n.id: n for n in data.nodes if n.kind == "fact"}
    for node in facts_by_id.values():
        tokens_by_node[node.id] = _fact_tokens(node.fact, category_parts)
    by_token: dict[str, list] = {}
    for nid, tokens in tokens_by_node.items():
        for token in tokens:
            by_token.setdefault(token, []).append(nid)
    candidates: dict[tuple, set] = {}
    for token, nids in by_token.items():
        for i in range(len(nids)):
            for j in range(i + 1, len(nids)):
                a, b = sorted((nids[i], nids[j]))
                if facts_by_id[a].category == facts_by_id[b].category:
                    continue
                candidates.setdefault((a, b), set()).add(token)
    scored = sorted(
        candidates.items(),
        key=lambda kv: (-len(kv[1]),
                        -sum(len(t) for t in kv[1]),
                        kv[0][0], kv[0][1]))
    cross_degree: dict[str, int] = {}
    for (a, b), shared in scored:
        if cross_degree.get(a, 0) >= MAX_CROSS_DEGREE:
            continue
        if cross_degree.get(b, 0) >= MAX_CROSS_DEGREE:
            continue
        cross_degree[a] = cross_degree.get(a, 0) + 1
        cross_degree[b] = cross_degree.get(b, 0) + 1
        data.edges.append(GraphEdge(
            a=a, b=b, kind="cross",
            tokens=tuple(sorted(shared))))

    # Degrees (they size the fact nodes) and final lookup.
    degree = {n.id: 0 for n in data.nodes}
    for edge in data.edges:
        degree[edge.a] = degree.get(edge.a, 0) + 1
        degree[edge.b] = degree.get(edge.b, 0) + 1
    for node in data.nodes:
        node.degree = degree.get(node.id, 0)
        if node.kind == "fact":
            node.radius = 5.5 + min(6.0, 1.1 * node.degree)
    data.by_id = {n.id: n for n in data.nodes}
    return data

_FACT_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d",
)

def _fact_epoch(fact: dict | None) -> float | None:
    """A fact row's updated_at as a local-time epoch, or None when
    the row carries no parseable date (missing, empty, garbage).
    Timeline rule (E27): None means "we don't know when this is
    from" — such facts are NEVER hidden by a time cutoff."""
    raw = str((fact or {}).get("updated_at") or "").strip()
    if not raw:
        return None
    for fmt in _FACT_TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue
    return None



# ---------------------------------------------------------------------------
# Layout (pure): seeded start + Fruchterman-Reingold relaxation steps
# ---------------------------------------------------------------------------

_GOLDEN_ANGLE = 2.399963229728653

def seed_positions(data: GraphData, previous: dict | None = None,
                   seed: int = 1234) -> dict:
    """Deterministic starting layout. Positions from `previous`
    (keyed by node id) win, so a data refresh keeps every surviving
    node where it was; new facts spawn on a small ring at their hub,
    new hubs on the golden-angle ring around the centre."""
    rng = random.Random(seed)
    pos: dict[str, list] = {}
    previous = previous or {}
    if "you" in data.by_id:
        pos["you"] = [0.0, 0.0]
    hub_ids = [n.id for n in data.nodes if n.kind == "hub"]
    facts_by_hub: dict[str, list] = {}
    for node in data.nodes:
        if node.kind == "fact":
            facts_by_hub.setdefault(f"hub:{node.category}",
                                    []).append(node)
    total_facts = sum(len(v) for v in facts_by_hub.values())
    ring_r = 150.0 + min(60.0, 2.0 * math.sqrt(max(total_facts, 1)) * 4.0)
    for index, hub_id in enumerate(hub_ids):
        if hub_id in previous:
            pos[hub_id] = list(previous[hub_id])
            continue
        angle = index * _GOLDEN_ANGLE
        pos[hub_id] = [ring_r * math.cos(angle), ring_r * math.sin(angle)]
    for hub_id, members in facts_by_hub.items():
        hub_xy = pos.get(hub_id, [0.0, 0.0])
        hub_angle = math.atan2(hub_xy[1], hub_xy[0])
        local_r = 40.0 + 6.5 * math.sqrt(len(members))
        for j, node in enumerate(members):
            if node.id in previous:
                pos[node.id] = list(previous[node.id])
                continue
            angle = hub_angle + (2.0 * math.pi * j / max(len(members), 1)) \
                + rng.uniform(-0.15, 0.15)
            r = local_r * rng.uniform(0.8, 1.15)
            pos[node.id] = [hub_xy[0] + r * math.cos(angle),
                            hub_xy[1] + r * math.sin(angle)]
    for node in data.nodes:  # anything unforeseen still gets a spot
        if node.id not in pos:
            if node.id in previous:
                pos[node.id] = list(previous[node.id])
            else:
                pos[node.id] = [rng.uniform(-60, 60), rng.uniform(-60, 60)]
    return pos

_EDGE_WEIGHTS = {"membership": 1.0, "cross": 0.4}

def relax_step(data: GraphData, pos: dict, pinned=frozenset(),
               active: set | None = None, temperature: float = 40.0,
               k: float = 58.0) -> float:
    """One Fruchterman-Reingold iteration over `pos` (mutated).
    Returns the largest single-node displacement — the widget's
    settle signal. Pinned and inactive nodes neither move nor pull."""
    nodes = [n for n in data.nodes if active is None or n.id in active]
    ids = [n.id for n in nodes]
    index = {nid: i for i, nid in enumerate(ids)}
    xs = [pos[nid][0] for nid in ids]
    ys = [pos[nid][1] for nid in ids]
    fx = [0.0] * len(ids)
    fy = [0.0] * len(ids)
    cutoff2 = (3.0 * k) ** 2
    kk = k * k
    # Repulsion with an early cutoff, grid-bucketed: pairs within
    # the cutoff can only sit in the same or neighbouring cells, so
    # a 170-node web costs local pairs, not all pairs.
    cell = 3.0 * k
    grid: dict = {}
    for i in range(len(ids)):
        key = (int(xs[i] // cell), int(ys[i] // cell))
        grid.setdefault(key, []).append(i)
    for i in range(len(ids)):
        xi, yi = xs[i], ys[i]
        cx, cy = int(xi // cell), int(yi // cell)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in grid.get((gx, gy), ()):
                    if j <= i:
                        continue
                    dx = xi - xs[j]
                    dy = yi - ys[j]
                    d2 = dx * dx + dy * dy
                    if d2 > cutoff2:
                        continue
                    if d2 < 0.01:
                        angle = math.radians((i * 37 + j * 17) % 360)
                        dx, dy, d2 = math.cos(angle), math.sin(angle), 1.0
                    d = math.sqrt(d2)
                    force = kk / d
                    ux, uy = dx / d, dy / d
                    fx[i] += ux * force
                    fy[i] += uy * force
                    fx[j] -= ux * force
                    fy[j] -= uy * force
    # Attraction along edges (hub->you pulls hardest, cross-links
    # whisper — they should bend the web, not own it).
    for edge in data.edges:
        if edge.a not in index or edge.b not in index:
            continue
        ia, ib = index[edge.a], index[edge.b]
        weight = _EDGE_WEIGHTS.get(edge.kind, 1.0)
        if edge.b == "you" or edge.a == "you":
            weight = 1.4
        dx = xs[ia] - xs[ib]
        dy = ys[ia] - ys[ib]
        d = math.sqrt(dx * dx + dy * dy)
        if d < 0.01:
            continue
        force = (d * d / k) * weight
        ux, uy = dx / d, dy / d
        fx[ia] -= ux * force
        fy[ia] -= uy * force
        fx[ib] += ux * force
        fy[ib] += uy * force
    max_disp = 0.0
    for i, nid in enumerate(ids):
        if nid in pinned:
            continue
        f = math.sqrt(fx[i] * fx[i] + fy[i] * fy[i])
        if f < 1e-9:
            continue
        step = min(f, temperature)
        pos[nid][0] = xs[i] + fx[i] / f * step
        pos[nid][1] = ys[i] + fy[i] / f * step
        if step > max_disp:
            max_disp = step
    return max_disp



# ---------------------------------------------------------------------------
# 3D layout (pure siblings of the 2D pair — the 2D code above is
# untouched, so v3.1 layouts are bit-identical; here positions are
# [x, y, z] and the same FR recipe runs in three dimensions)
# ---------------------------------------------------------------------------


def seed_positions_3d(data: GraphData, previous: dict | None = None,
                      seed: int = 1234) -> dict:
    """Deterministic 3D start: You at the origin, hubs on the same
    golden-angle ring as 2D with a low-discrepancy z in
    [-0.35R, 0.35R], facts on Fibonacci-sphere shells around their
    hub. `previous` positions win, exactly like the 2D seeding."""
    rng = random.Random(seed)
    pos: dict[str, list] = {}
    previous = previous or {}
    if "you" in data.by_id:
        pos["you"] = [0.0, 0.0, 0.0]
    hub_ids = [n.id for n in data.nodes if n.kind == "hub"]
    facts_by_hub: dict[str, list] = {}
    for node in data.nodes:
        if node.kind == "fact":
            facts_by_hub.setdefault(f"hub:{node.category}",
                                    []).append(node)
    total_facts = sum(len(v) for v in facts_by_hub.values())
    ring_r = 150.0 + min(60.0, 2.0 * math.sqrt(max(total_facts, 1)) * 4.0)
    for index, hub_id in enumerate(hub_ids):
        if hub_id in previous:
            pos[hub_id] = list(previous[hub_id])
            continue
        angle = index * _GOLDEN_ANGLE
        frac = (index * 0.6180339887498949) % 1.0
        z = 0.35 * ring_r * (2.0 * frac - 1.0)
        pos[hub_id] = [ring_r * math.cos(angle),
                       ring_r * math.sin(angle), z]
    for hub_id, members in facts_by_hub.items():
        hub_xyz = pos.get(hub_id, [0.0, 0.0, 0.0])
        local_r = 40.0 + 6.5 * math.sqrt(len(members))
        count = len(members)
        for j, node in enumerate(members):
            if node.id in previous:
                pos[node.id] = list(previous[node.id])
                continue
            # Fibonacci sphere: near-uniform directions, deterministic.
            y = 1.0 - (2.0 * j + 1.0) / max(count, 1)
            ring = math.sqrt(max(0.0, 1.0 - y * y))
            theta = j * _GOLDEN_ANGLE
            r = local_r * rng.uniform(0.8, 1.15)
            pos[node.id] = [
                hub_xyz[0] + r * ring * math.cos(theta),
                hub_xyz[1] + r * y,
                hub_xyz[2] + r * ring * math.sin(theta),
            ]
    for node in data.nodes:
        if node.id not in pos:
            if node.id in previous:
                pos[node.id] = list(previous[node.id])
            else:
                pos[node.id] = [rng.uniform(-60, 60), rng.uniform(-60, 60),
                                rng.uniform(-60, 60)]
    return pos

def relax_step_3d(data: GraphData, pos: dict, pinned=frozenset(),
                  active: set | None = None, temperature: float = 40.0,
                  k: float = 58.0) -> float:
    """One 3D Fruchterman-Reingold iteration over [x, y, z]
    positions (mutated); returns the largest displacement. Same
    weights, cutoff and grid-bucket repulsion as relax_step."""
    nodes = [n for n in data.nodes if active is None or n.id in active]
    ids = [n.id for n in nodes]
    index = {nid: i for i, nid in enumerate(ids)}
    xs = [pos[nid][0] for nid in ids]
    ys = [pos[nid][1] for nid in ids]
    zs = [pos[nid][2] for nid in ids]
    fx = [0.0] * len(ids)
    fy = [0.0] * len(ids)
    fz = [0.0] * len(ids)
    cutoff2 = (3.0 * k) ** 2
    kk = k * k
    cell = 3.0 * k
    grid: dict = {}
    for i in range(len(ids)):
        key = (int(xs[i] // cell), int(ys[i] // cell), int(zs[i] // cell))
        grid.setdefault(key, []).append(i)
    for i in range(len(ids)):
        xi, yi, zi = xs[i], ys[i], zs[i]
        cx, cy, cz = int(xi // cell), int(yi // cell), int(zi // cell)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for gz in (cz - 1, cz, cz + 1):
                    for j in grid.get((gx, gy, gz), ()):
                        if j <= i:
                            continue
                        dx = xi - xs[j]
                        dy = yi - ys[j]
                        dz = zi - zs[j]
                        d2 = dx * dx + dy * dy + dz * dz
                        if d2 > cutoff2:
                            continue
                        if d2 < 0.01:
                            angle = math.radians((i * 37 + j * 17) % 360)
                            dx, dy, dz, d2 = math.cos(angle), 0.0, \
                                math.sin(angle), 1.0
                        d = math.sqrt(d2)
                        force = kk / d
                        ux, uy, uz = dx / d, dy / d, dz / d
                        fx[i] += ux * force
                        fy[i] += uy * force
                        fz[i] += uz * force
                        fx[j] -= ux * force
                        fy[j] -= uy * force
                        fz[j] -= uz * force
    for edge in data.edges:
        if edge.a not in index or edge.b not in index:
            continue
        ia, ib = index[edge.a], index[edge.b]
        weight = _EDGE_WEIGHTS.get(edge.kind, 1.0)
        if edge.b == "you" or edge.a == "you":
            weight = 1.4
        dx = xs[ia] - xs[ib]
        dy = ys[ia] - ys[ib]
        dz = zs[ia] - zs[ib]
        d = math.sqrt(dx * dx + dy * dy + dz * dz)
        if d < 0.01:
            continue
        force = (d * d / k) * weight
        ux, uy, uz = dx / d, dy / d, dz / d
        fx[ia] -= ux * force
        fy[ia] -= uy * force
        fz[ia] -= uz * force
        fx[ib] += ux * force
        fy[ib] += uy * force
        fz[ib] += uz * force
    max_disp = 0.0
    for i, nid in enumerate(ids):
        if nid in pinned:
            continue
        f = math.sqrt(fx[i] * fx[i] + fy[i] * fy[i] + fz[i] * fz[i])
        if f < 1e-9:
            continue
        step = min(f, temperature)
        pos[nid][0] = xs[i] + fx[i] / f * step
        pos[nid][1] = ys[i] + fy[i] / f * step
        pos[nid][2] = zs[i] + fz[i] / f * step
        if step > max_disp:
            max_disp = step
    return max_disp



# ---------------------------------------------------------------------------
# Label planning (shared by both painters): priority order, collision
# skipping, hub side-flipping, halo-backed drawing
# ---------------------------------------------------------------------------


def _plan_label_rects(entries: list, obstacles: list | None = None) -> dict:
    """Decide which labels get drawn, and where.

    Each entry: {id, priority (lower draws first / wins space),
    degree (tiebreak, higher first), rect (QRectF below the node),
    alt_rect (QRectF above the node, hubs only), force (draw even
    on collision — You / selected / hovered), sticky (hubs: if both
    sides collide, still draw at the alt side rather than vanish)}.
    A non-forced label whose rect — inflated by 2 px — intersects
    any already-placed rect is skipped (or flipped, for hubs).
    obstacles: optional [(centre QPointF, radius float, owner_id)]
    discs a label must clear as well (node spheres). A label
    clears every NON-OWN disc by >= 6 px — the own disc is
    excluded (the label sits 3 px below it by construction, so a
    6 px clearance would collide with itself). Without obstacles
    the behaviour is exactly the old one.
    Returns {id: QRectF}.

    Hunt fix (c) item 9: a fact label whose rect sits ~3 px from
    a neighbouring hub's sphere reads as merged with it (the
    "repro_quokka_alpha" label against the General hub). A label
    whose rect and alt both collide is SLID along its row —
    smallest shift first — instead of vanishing or landing on
    the sphere; the slide is tried for every entry (a fact label
    has no alt and is not sticky, so without it the quokka label
    would simply disappear)."""
    placed: dict[str, QRectF] = {}
    placed_rects: list[QRectF] = []

    def collides(rect: QRectF, entry: dict) -> bool:
        probe = rect.adjusted(-2.0, -2.0, 2.0, 2.0)
        if any(probe.intersects(other) for other in placed_rects):
            return True
        for centre, radius, owner in (obstacles or []):
            if owner == entry["id"]:
                continue  # the label's own sphere, by construction
            cx = min(max(centre.x(), rect.left()), rect.right())
            cy = min(max(centre.y(), rect.top()), rect.bottom())
            if ((centre.x() - cx) ** 2 + (centre.y() - cy) ** 2
                    < (radius + 6.0) ** 2):
                return True
        return False

    def slide(base: QRectF, entry: dict) -> QRectF | None:
        # Smallest shift first, left then right, along the row.
        for k in range(1, 11):
            for shift in (-4.0 * k, 4.0 * k):
                cand = QRectF(base)
                cand.translate(shift, 0.0)
                if not collides(cand, entry):
                    return cand
        return None

    ordered = sorted(entries,
                     key=lambda e: (e["priority"], -e.get("degree", 0),
                                    e["id"]))
    for entry in ordered:
        rect = entry["rect"]
        alt = entry.get("alt_rect")
        if entry.get("force"):
            chosen = rect
        elif not collides(rect, entry):
            chosen = rect
        elif alt is not None and not collides(alt, entry):
            chosen = alt
        else:
            chosen = slide(rect, entry)
            if chosen is None and alt is not None:
                chosen = slide(alt, entry)
            if chosen is None:
                if alt is not None and entry.get("sticky"):
                    chosen = alt  # both sides busy — hubs stay labelled
                elif entry.get("sticky"):
                    chosen = rect
                else:
                    continue
        placed[entry["id"]] = QRectF(chosen)
        placed_rects.append(QRectF(chosen))
    return placed

_LABEL_HALO = QColor(13, 15, 19, 120)

def _label_halo_rect(rect: QRectF, text: str, font: QFont) -> QRectF:
    """The halo box behind a label: text size + 4x1 padding."""
    metrics = QFontMetrics(font)
    text_w = metrics.horizontalAdvance(text)
    text_h = metrics.height()
    return QRectF(rect.center().x() - text_w / 2.0 - 4.0,
                  rect.center().y() - text_h / 2.0 - 1.0,
                  text_w + 8.0, text_h + 2.0)

def _draw_label_halo(painter: QPainter, rect: QRectF, text: str,
                     font: QFont, halo: QColor | None = None):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(halo if halo is not None else _LABEL_HALO)
    painter.drawRoundedRect(_label_halo_rect(rect, text, font), 3.0, 3.0)

def _draw_label_text(painter: QPainter, rect: QRectF, text: str,
                     font: QFont, colour: QColor):
    painter.setFont(font)
    painter.setPen(colour)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

def _draw_label(painter: QPainter, rect: QRectF, text: str, font: QFont,
                colour: QColor, halo: QColor | None = None):
    """One halo-backed label: a soft rounded rect hugging the text
    (padding 4×1) so words stay legible over edges and orbs, then
    the text centred in its band. ``halo`` overrides the dark
    default (M-21: on Light the halo is the theme's bg)."""
    _draw_label_halo(painter, rect, text, font, halo)
    _draw_label_text(painter, rect, text, font, colour)

def _node_tooltip(node: GraphNode) -> str:
    """Hover tooltip text for a node (shared by both renderers)."""
    if node.kind == "you":
        return (f"{node.label} — everything VYUK knows, "
                "woven around you.")
    if node.kind == "hub":
        tip = f"{node.label} — {node.count} fact(s)"
        if node.overflow:
            tip += f" (+{node.overflow} more in List view)"
        tip += ". Double-click to fold or unfold."
        return tip
    fact = node.fact or {}
    tip = f"{fact.get('key') or node.label}: {fact.get('value') or ''}"
    return tip[:110]



# ---------------------------------------------------------------------------
# The widgets
# ---------------------------------------------------------------------------

_LABEL_FACT_PX = 11

_LABEL_HUB_PX = 12

_TEXT_MAIN = QColor(TEXT)

_TEXT_MUTED = QColor(TEXT_MUTED)

_EDGE_CROSS = QColor(255, 255, 255, 40)
