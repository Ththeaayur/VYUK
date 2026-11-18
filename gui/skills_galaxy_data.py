"""Skills Galaxy data layer: pure data, no Qt state.

Split from gui/skills_galaxy.py (GUI split #5, move only): constants,
GalaxyNode/GalaxyEdge/GalaxyData, build/layout/search/label functions.
Importable and testable headlessly.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter

from voice_assistant.gui.skills_catalog import GROUP_ORDER, group_for_name

from .design_system.palette import lighten
from .style import ACCENT, ACCENT_2, AMBER, DANGER, OK, TEXT, TEXT_MUTED


# Pure data layer (no Qt state — importable and testable headlessly)
_MAX_DESCRIPTION = 140

# One colour per catalogue group, in GROUP_ORDER order — eleven
# distinguishable hues on the app's dark ground, all derived from
# the token palette (Command Deck Phase E; the old indigo/violet/
# teal/pink/sky/orange/magenta set is gone). An unknown group
# (never in GROUP_ORDER) hashes into the same table.
GROUP_PALETTE = (
    ACCENT,  # Hands & eyes — brand cyan
    ACCENT_2,  # Mind — blue secondary
    OK,  # Web — ok green
    AMBER,  # Your world — amber
    lighten(OK, 0.35),  # Everyday skills — pale green
    lighten(DANGER, 0.45),  # Play & create — pale red
    lighten(ACCENT, 0.45),  # System & apps — pale cyan
    lighten(AMBER, 0.45),  # Files & media — pale amber
    lighten(ACCENT_2, 0.45),  # Network & display — pale blue
    DANGER,  # Built-in — danger red
    TEXT_MUTED,  # More skills — muted silver
)

_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


def group_color(group: str) -> str:
    """Hex colour for a catalogue group: palette slot by
    GROUP_ORDER index, stable hash for anything else."""
    if group in GROUP_ORDER:
        return GROUP_PALETTE[GROUP_ORDER.index(group)
                             % len(GROUP_PALETTE)]
    digest = hashlib.md5((group or "").encode("utf-8")).hexdigest()
    return GROUP_PALETTE[int(digest[:8], 16) % len(GROUP_PALETTE)]


def _prettify(name: str) -> str:
    """Friendly-name fallback for a tool missing from the map
    (mirrors the catalogue's humaniser)."""
    text = (name or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else (name or "")


_ABBREVIATIONS = {"eg", "ie", "etc", "vs", "dr", "mr", "mrs", "st"}


def trim_description(description: str) -> str:
    """First sentence of a tool description, capped at ~140 chars.
    Abbreviations ("e.g.", "Dr.") do not end a sentence; the
    trailing full stop is dropped (the catalogue's convention)."""
    text = " ".join((description or "").split())
    if not text:
        return ""
    sentence = text
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        before = re.search(r"([A-Za-z]+)$", text[: match.start()])
        if before and (len(before.group(1)) == 1
                       or before.group(1).lower() in _ABBREVIATIONS):
            continue
        sentence = text[: match.end()]
        break
    sentence = sentence.rstrip()
    if sentence.endswith("."):
        sentence = sentence[:-1].rstrip()
    if len(sentence) > _MAX_DESCRIPTION:
        sentence = sentence[: _MAX_DESCRIPTION - 1].rstrip() + "…"
    return sentence


def _seed_floats(key: str, count: int) -> list[float]:
    """Deterministic pseudo-random floats in [0, 1) from a name —
    hashlib-seeded, so layouts never depend on run order."""
    out: list[float] = []
    counter = 0
    while len(out) < count:
        digest = hashlib.md5(f"{key}#{counter}".encode("utf-8")).digest()
        for i in range(0, len(digest) - 3, 4):
            out.append(int.from_bytes(digest[i: i + 4], "little")
                       / 4294967296.0)
            if len(out) >= count:
                break
        counter += 1
    return out


@dataclass
class GalaxyNode:
    id: str                      # "hub:<group>" | the tool name
    kind: str                    # "hub" | "star"
    label: str                   # hub: group title; star: friendly name
    group: str                   # catalogue group (hubs: their own)
    name: str = ""               # stars: the raw tool name
    description: str = ""        # stars: trimmed one-liner
    count: int = 0               # hubs: member stars
    radius: float = 4.2


@dataclass
class GalaxyEdge:
    a: str                       # hub id
    b: str                       # star id


@dataclass
class GalaxyData:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    by_id: dict = field(default_factory=dict)

    def node(self, node_id: str) -> GalaxyNode | None:
        return self.by_id.get(node_id)

    @property
    def hubs(self) -> list:
        return [n for n in self.nodes if n.kind == "hub"]

    @property
    def stars(self) -> list:
        return [n for n in self.nodes if n.kind == "star"]


def build_galaxy(declarations: list, friendly_names: dict) -> GalaxyData:
    """Declarations -> galaxy. Pure and deterministic: buckets by
    catalogue group (GROUP_ORDER first, unknown groups after),
    stars name-sorted inside a group, one hub per non-empty
    group, one spoke per star."""
    friendly_names = friendly_names or {}
    buckets: dict[str, list] = {}
    for decl in declarations or []:
        if not isinstance(decl, dict):
            continue
        name = str(decl.get("name") or "").strip()
        if not name:
            continue
        group = group_for_name(name)
        buckets.setdefault(group, []).append((name, decl))

    def group_key(group: str):
        return (GROUP_ORDER.index(group) if group in GROUP_ORDER
                else len(GROUP_ORDER), group)

    data = GalaxyData()
    for group in sorted(buckets, key=group_key):
        members = sorted(buckets[group], key=lambda item: item[0])
        hub = GalaxyNode(
            id=f"hub:{group}", kind="hub", label=group, group=group,
            count=len(members),
            radius=10.0 + min(9.0, math.sqrt(len(members)) * 1.5))
        data.nodes.append(hub)
        data.by_id[hub.id] = hub
        for name, decl in members:
            star = GalaxyNode(
                id=name, kind="star",
                label=str(friendly_names.get(name) or _prettify(name)),
                group=group, name=name,
                description=trim_description(
                    str(decl.get("description") or "")),
                radius=4.2)
            data.nodes.append(star)
            data.by_id[star.id] = star
            data.edges.append(GalaxyEdge(a=hub.id, b=star.id))
    return data


def _cluster_radius(count: int) -> float:
    """2D spiral radius for a group of `count` stars — big groups
    spread wider, but never so wide that neighbour clusters on
    the hub ring bleed into each other at the default fit."""
    return 34.0 + 6.0 * math.sqrt(max(count, 1))


def layout_positions_2d(data: GalaxyData) -> dict:
    """{node id: (x, y)} in world units. Hubs sit on a ring (first
    hub at the top, then clockwise in group order); each hub's
    stars fill a golden-angle spiral disc around it, phase-seeded
    by the group so no two clusters share a rotation."""
    pos: dict[str, tuple] = {}
    hubs = data.hubs
    if not hubs:
        return pos
    ring_r = max(250.0, len(hubs) * 26.0)
    for i, hub in enumerate(hubs):
        angle = -math.pi / 2.0 + i * (2.0 * math.pi / len(hubs))
        hx, hy = ring_r * math.cos(angle), ring_r * math.sin(angle)
        pos[hub.id] = (hx, hy)
        stars = [n for n in data.nodes
                 if n.kind == "star" and n.group == hub.group]
        cluster_r = _cluster_radius(len(stars))
        phase = _seed_floats(f"phase:{hub.group}", 1)[0] \
            * 2.0 * math.pi
        for j, star in enumerate(stars):
            frac = (j + 0.5) / max(len(stars), 1)
            r = cluster_r * math.sqrt(frac)
            theta = phase + j * _GOLDEN_ANGLE
            pos[star.id] = (hx + r * math.cos(theta),
                            hy + r * math.sin(theta))
    return pos


def _fibonacci_sphere(count: int, radius: float) -> list:
    """Evenly spread points on a sphere (deterministic)."""
    points = []
    for i in range(count):
        y = 1.0 - (2.0 * i + 1.0) / max(count, 1)
        ring = math.sqrt(max(0.0, 1.0 - y * y))
        theta = i * _GOLDEN_ANGLE
        points.append((radius * ring * math.cos(theta),
                       radius * y,
                       radius * ring * math.sin(theta)))
    return points


def layout_positions_3d(data: GalaxyData) -> dict:
    """{node id: (x, y, z)}. Hubs spread over a Fibonacci shell;
    each hub's stars fill a golden spiral in the plane tangent to
    the shell at the hub, with a small seeded radial jitter so
    the cluster has depth instead of being a flat sticker."""
    pos: dict[str, tuple] = {}
    hubs = data.hubs
    if not hubs:
        return pos
    shell_r = 300.0
    centres = _fibonacci_sphere(len(hubs), shell_r)
    for hub, centre in zip(hubs, centres):
        hx, hy, hz = centre
        pos[hub.id] = centre
        # Tangent frame at the hub: `normal` points away from the
        # shell centre; u/v span the cluster plane.
        normal = (hx / shell_r, hy / shell_r, hz / shell_r)
        seed_axis = (0.0, 1.0, 0.0) if abs(normal[1]) < 0.9 \
            else (1.0, 0.0, 0.0)
        ux = seed_axis[1] * normal[2] - seed_axis[2] * normal[1]
        uy = seed_axis[2] * normal[0] - seed_axis[0] * normal[2]
        uz = seed_axis[0] * normal[1] - seed_axis[1] * normal[0]
        ulen = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
        u = (ux / ulen, uy / ulen, uz / ulen)
        v = (normal[1] * u[2] - normal[2] * u[1],
             normal[2] * u[0] - normal[0] * u[2],
             normal[0] * u[1] - normal[1] * u[0])
        stars = [n for n in data.nodes
                 if n.kind == "star" and n.group == hub.group]
        cluster_r = 52.0 + 9.0 * math.sqrt(max(len(stars), 1))
        phase = _seed_floats(f"phase3:{hub.group}", 1)[0] \
            * 2.0 * math.pi
        for j, star in enumerate(stars):
            frac = (j + 0.5) / max(len(stars), 1)
            r = cluster_r * math.sqrt(frac)
            theta = phase + j * _GOLDEN_ANGLE
            jitter = (_seed_floats(f"j3:{star.name}", 1)[0] - 0.5) \
                * 30.0
            cu, cv = r * math.cos(theta), r * math.sin(theta)
            pos[star.id] = (
                hx + u[0] * cu + v[0] * cv + normal[0] * jitter,
                hy + u[1] * cu + v[1] * cv + normal[1] * jitter,
                hz + u[2] * cu + v[2] * cv + normal[2] * jitter)
    return pos


def world_extent_2d(data: GalaxyData, pos: dict) -> float:
    """Half-width of the 2D map (for fit-to-view math)."""
    extent = 60.0
    for hub in data.hubs:
        hx, hy = pos.get(hub.id, (0.0, 0.0))
        stars = sum(1 for n in data.nodes
                    if n.kind == "star" and n.group == hub.group)
        extent = max(extent, math.hypot(hx, hy)
                     + _cluster_radius(stars) + 26.0)
    return extent


def search_sets(data: GalaxyData, text: str):
    """(matched ids, dimmed ids) for a search string. A star
    matches on friendly label, raw name, group or description; a
    hub matches when its group matches or any member star does.
    No search -> two empty sets. The pair always partitions the
    node set when a search is active (the suite asserts this)."""
    needle = (text or "").strip().lower()
    if not needle:
        return set(), set()
    matched: set = set()
    for node in data.nodes:
        if node.kind != "star":
            continue
        hay = " ".join((node.label, node.name, node.group,
                        node.description)).lower()
        if needle in hay:
            matched.add(node.id)
    for hub in data.hubs:
        if needle in hub.group.lower() \
                or any(n.group == hub.group and n.id in matched
                       for n in data.stars):
            matched.add(hub.id)
    dimmed = {n.id for n in data.nodes} - matched
    return matched, dimmed


# ---------------------------------------------------------------------------
# Label planning (mirrors the Memory Web's: priority wins space,
# collisions skip, hubs may flip side, halos keep text legible)
# ---------------------------------------------------------------------------

def _plan_label_rects(entries: list, obstacles: list | None = None) -> dict:
    """Each entry: {id, priority (lower wins), degree (tiebreak),
    rect, alt_rect, force, sticky, extra_rects (optional list of
    further candidates, tried in order after rect/alt)}.
    obstacles: optional [(centre QPointF, radius float)] discs a
    label must clear as well (3D hub orbs). Returns {id: QRectF}.

    Without obstacles/extra_rects the behaviour is exactly the
    old one (the 2D canvas calls it that way, unchanged)."""
    placed: dict[str, QRectF] = {}
    placed_rects: list[QRectF] = []

    def collides(rect: QRectF) -> bool:
        probe = rect.adjusted(-2.0, -2.0, 2.0, 2.0)
        if any(probe.intersects(other) for other in placed_rects):
            return True
        for centre, radius in (obstacles or []):
            cx = min(max(centre.x(), rect.left()), rect.right())
            cy = min(max(centre.y(), rect.top()), rect.bottom())
            if ((centre.x() - cx) ** 2 + (centre.y() - cy) ** 2
                    < (radius + 2.0) ** 2):
                return True
        return False

    ordered = sorted(entries,
                     key=lambda e: (e["priority"], -e.get("degree", 0),
                                    e["id"]))
    for entry in ordered:
        rect = entry["rect"]
        alt = entry.get("alt_rect")
        if entry.get("force"):
            chosen = rect
        elif not collides(rect):
            chosen = rect
        elif alt is not None and not collides(alt):
            chosen = alt
        else:
            chosen = None
            for cand in entry.get("extra_rects") or []:
                if not collides(cand):
                    chosen = cand
                    break
            if chosen is None:
                if alt is not None and entry.get("sticky"):
                    chosen = alt
                elif entry.get("sticky"):
                    chosen = rect
                else:
                    continue
        placed[entry["id"]] = QRectF(chosen)
        placed_rects.append(QRectF(chosen))
    return placed


_LABEL_HALO = QColor(10, 12, 17, 130)


def _label_halo_rect(rect: QRectF, text: str, font: QFont) -> QRectF:
    metrics = QFontMetrics(font)
    text_w = metrics.horizontalAdvance(text)
    text_h = metrics.height()
    return QRectF(rect.center().x() - text_w / 2.0 - 4.0,
                  rect.center().y() - text_h / 2.0 - 1.0,
                  text_w + 8.0, text_h + 2.0)


def _draw_label_halo(painter: QPainter, rect: QRectF, text: str,
                     font: QFont):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_LABEL_HALO)
    painter.drawRoundedRect(_label_halo_rect(rect, text, font), 3.0, 3.0)


def _draw_label_text(painter: QPainter, rect: QRectF, text: str,
                     font: QFont, colour: QColor):
    painter.setFont(font)
    painter.setPen(colour)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def _draw_label(painter: QPainter, rect: QRectF, text: str, font: QFont,
                colour: QColor):
    """Halo first, glyphs second — the order matters on some
    paint engines (see the 3D overlay note)."""
    _draw_label_halo(painter, rect, text, font)
    _draw_label_text(painter, rect, text, font, colour)


_LABEL_STAR_PX = 11
_LABEL_HUB_PX = 12
_TEXT_MAIN = QColor(TEXT)
_TEXT_MUTED = QColor(TEXT_MUTED)
_BACKDROP = QColor(11, 13, 18)


def _node_tooltip(node: GalaxyNode) -> str:
    if node.kind == "hub":
        return f"{node.label} — {node.count} skill(s). Click a star " \
            "to see what it does."
    tip = node.label
    if node.description:
        tip += f" — {node.description}"
    return tip[:140]
