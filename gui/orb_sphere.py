"""R7-2 — voice-reactive particle sphere (jarvis-mark-i pattern, own code).

The round-7 #2 pick (bhavyajustchill/jarvis-mark-i, MIT, class (b)
pattern-only) is a 10,000-particle voice-reactive 3D sphere that
swells as either side speaks, plus a cinematic boot sequence. The
repo itself is a Next.js/React/Three.js app; VYUK has no WebEngine
view, so NOTHING from its stack is adopted. This module is the
pattern re-implemented as VYUK's own QPainter code:

* a shell of particles on a slowly turning sphere (Fibonacci
  lattice, orthographic projection, painter's-algorithm depth
  sort — nearer particles brighter and larger, farther dimmer
  and smaller), swelling with the SAME smoothed voice energy the
  orb core already reads (speaker level while VYUK speaks, mic
  level while listening, 0.0 muted — the _sync_audio_telemetry
  feed), and settling to a slow idle drift in silence;
* Aura blue only — every colour comes from the caller's theme
  triple (highlight/mid/edge), never a literal (the
  test_commanddeck_guards colour rule);
* no face, no Marvel marks (the fan-project disclaimer: the
  J.A.R.V.I.S./Iron Man/Stark trademarks belong to Marvel).

The math is pure (no Qt) so the lab can prove it without a
window; ``paint_particle_sphere`` is the one QPainter entry.
"""

import math

# NOTE: no Qt import at module top — the math below is pure so
# the lab can prove it on both interpreters; the one QPainter
# entry imports Qt lazily (the test_journey_gui_path pattern).

# -- tuning ---------------------------------------------------------------
# QPainter scale, not the pattern's 10,000: a few hundred discs is
# the honest QPainter budget next to the orb's 3.69 ms/frame at
# 400x400 (measured by the parent, B-R1-10). Count scales with
# the orb's level of detail: home full, home small, mini.
SPHERE_PARTICLE_COUNTS = {0: 480, 1: 320, 2: 160}
# Swell: the sphere's radius grows by this fraction at full voice
# energy (1.0 + 0.16 = 18% larger while speaking loudly).
SPHERE_SWELL_MAX = 0.16
# Idle drift: slow turn so the sphere reads 3D even in silence.
SPHERE_YAW_SPEED = 0.22  # rad/s
# Fixed tilt so the lattice never reads as a flat disc (the yaw
# turn is what makes the sphere read 3D; the pitch stays put).
SPHERE_PITCH_BASE = 0.35  # rad
# Swell spring: fast attack, graceful release — the same feel as
# the orb's energy spring (visualizer_state SPRING_ENERGY_*).
SPHERE_SWELL_ATTACK = 0.22
SPHERE_SWELL_RELEASE = 0.05


def _clamp01(value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _dt_k(k_frame: float, s: float) -> float:
    """Per-frame easing constant over ``s`` legacy 60 fps frames."""
    return 1.0 - (1.0 - max(0.0, min(1.0, k_frame))) ** max(0.0, s)


def sphere_swell_radius(energy: float) -> float:
    """The sphere's radius factor at a voice energy (0..1)."""
    return 1.0 + SPHERE_SWELL_MAX * _clamp01(energy)


class SphereState:
    """The sphere's motion state: rotation + smoothed swell.

    The paint pass only reads it; ``advance`` is called from the
    orb's own animation tick with the same dt the glass uses.
    """

    def __init__(self) -> None:
        self.yaw = 0.0
        self.pitch = SPHERE_PITCH_BASE
        self.swell = 0.0  # smoothed voice energy, 0..1

    def advance(self, dt: float, energy: float,
                *, frozen: bool = False) -> None:
        """Advance one frame: turn slowly, ease the swell toward
        the live voice energy. ``frozen`` (reduced motion / muted)
        stills the turn; the swell keeps easing, exactly like the
        glass state — silence always settles the sphere back."""
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        dt = max(0.0, min(0.25, dt))
        if dt == 0.0:
            return
        s = dt * 60.0
        if not frozen:
            # The yaw turns; the pitch stays at its fixed tilt.
            self.yaw = (self.yaw + SPHERE_YAW_SPEED * dt) % (
                2.0 * math.pi)
        energy = _clamp01(energy)
        k = _dt_k(SPHERE_SWELL_ATTACK if energy > self.swell
                  else SPHERE_SWELL_RELEASE, s)
        self.swell += (energy - self.swell) * k


# -- the lattice (pure, deterministic, cached per count) -------------------

_FIBONACCI_CACHE: dict[int, list[tuple[float, float, float]]] = {}


def fibonacci_points(count: int) -> list[tuple[float, float, float]]:
    """``count`` points spread evenly over the unit sphere (the
    golden-angle spiral). Deterministic: the same count always
    returns the same lattice, so the sphere never flickers."""
    count = max(1, int(count))
    cached = _FIBONACCI_CACHE.get(count)
    if cached is not None:
        return cached
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    points = []
    for i in range(count):
        y = 1.0 - (i / max(1, count - 1)) * 2.0
        r = math.sqrt(max(0.0, 1.0 - y * y))
        theta = golden_angle * i
        points.append((math.cos(theta) * r, y, math.sin(theta) * r))
    _FIBONACCI_CACHE[count] = points
    return points


def project_points(points, yaw: float, pitch: float,
                   swell: float) -> list[tuple[float, float, float]]:
    """Rotate the lattice and project it orthographically.

    Returns ``(x, y, depth01)`` per point, sorted far-to-near
    (painter's algorithm — the painter draws in list order, so
    near particles land on top of far ones). ``x``/``y`` are in
    units of the swollen sphere radius; ``depth01`` is 0.0 at the
    far pole, 1.0 at the near pole.
    """
    radius = sphere_swell_radius(swell)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    projected = []
    for (x, y, z) in points:
        # Yaw around Y, then pitch around X.
        x1 = x * cy + z * sy
        z1 = -x * sy + z * cy
        y2 = y * cp - z1 * sp
        z2 = y * sp + z1 * cp
        projected.append((x1 * radius, y2 * radius, (z2 + 1.0) / 2.0))
    projected.sort(key=lambda p: p[2])
    return projected


# -- the paint entry ------------------------------------------------------

def paint_particle_sphere(painter, cx: float, cy: float, radius: float,
                          state: SphereState, energy: float,
                          highlight, mid, edge, dimf: float,
                          lod: int = 0) -> int:
    """Paint the voice-reactive particle sphere inside the orb.

    The caller clips to the glass contour first (the sphere is a
    shell inside the glass, like the currents). ``energy`` is the
    orb's smoothed voice energy for this frame; the state's own
    swell is the eased copy the tick maintains. Returns the
    particle count painted. Aura blue only: near particles take
    the highlight, mid-depth the mid, far the edge — all from the
    caller's theme triple.

    Cost: particles are bucketed by depth band (3) x alpha level
    (4) and drawn with one ``drawPoints`` call per bucket — 12
    calls, not one per particle — so the sphere stays near the
    orb's own paint budget instead of doubling it.
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPen

    count = SPHERE_PARTICLE_COUNTS.get(int(lod),
                                       SPHERE_PARTICLE_COUNTS[0])
    points = fibonacci_points(count)
    projected = project_points(points, state.yaw, state.pitch,
                               state.swell)
    lift = 0.72 + 0.28 * _clamp01(energy)
    # Bucket by (depth band, alpha level): far band first so near
    # particles land on top. Representative alpha per level is the
    # bucket midpoint (0-63 -> 32, 64-127 -> 96, 128-191 -> 160,
    # 192-255 -> 224).
    buckets: dict[tuple[int, int], list] = {}
    for (x, y, depth01) in projected:
        alpha = int((36.0 + 150.0 * depth01) * lift * dimf)
        if alpha <= 0:
            continue
        band = 0 if depth01 >= 0.66 else 1 if depth01 >= 0.33 else 2
        level = min(3, alpha * 4 // 256)
        buckets.setdefault((band, level), []).append(
            QPointF(cx + x * radius, cy + y * radius))
    widths = (radius * 0.022, radius * 0.018, radius * 0.014)
    bases = (highlight, mid, edge)
    alphas = (32, 96, 160, 224)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for (band, level) in sorted(buckets, key=lambda k: -k[0]):
        base = bases[band]
        pen = QPen(QColor(base.red(), base.green(), base.blue(),
                          alphas[level]), widths[band])
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPoints(buckets[(band, level)])
    return count
