"""Path planning for core/desktop/cursor.py (B35 part 2 of 2):
seeded-Bezier glides, takeover abort, target resolution.
"""

import math
import random
import time

# B35 INTENTIONAL: geometry constants/callables + input helpers
# this family reads by bare name, imported from their defining
# modules (same objects as the facade re-exports).
from .cursor_geometry import (
    _ABORT_MSG,
    _FAILSAFE_CORNER_PX,
    _TAKEOVER_TOLERANCE_PX,
    _clamp,
    _fence_point,
    _to_pixels,
    _virtual_bounds,
)
from .cursor_input import (
    _read_position,
    _set_position,
)


# ---------------------------------------------------------------------------
# Path planning — smooth seeded-Bezier glides (pure math, testable)
# ---------------------------------------------------------------------------

def _ease_in_out(t: float) -> float:
    """Smoothstep: zero slope at both ends, fastest in the middle."""
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _plan_path(x0, y0, x1, y1, duration_s):
    """Plan a human-ish glide from (x0, y0) to (x1, y1).

    Returns a list of (x, y, delay_before) points: move to each point
    after sleeping delay_before seconds. Pure function of its inputs —
    the curve is seeded from the endpoints, so the same move always
    traces the same path.

    - Cubic Bezier; both control points are offset perpendicular to
      the direction of travel by a seeded 2-4% of the distance, so
      the glide curves slightly instead of tracking a ruler line.
    - Ease-in-out timing: every step waits the same delay_before
      (duration_s / count), while the SPATIAL steps follow the eased
      parameter — dense and slow at the ends, long and fast in the
      middle. Delays sum to duration_s.
    - Point count scales with distance (~1 point per 11 px),
      clamped to 8..90. A zero-length move returns a single point.
    """
    x0, y0, x1, y1 = float(x0), float(y0), float(x1), float(y1)
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        return [(int(round(x1)), int(round(y1)), 0.0)]

    count = int(_clamp(round(dist / 11.0), 8, 90))

    # Deterministic curve: seed only from the endpoints.
    seed = (
        (int(round(x0)) * 73856093)
        ^ (int(round(y0)) * 19349663)
        ^ (int(round(x1)) * 83492791)
        ^ (int(round(y1)) * 2971215073)
        ^ int(round(dist))
    )
    rng = random.Random(seed)
    sign = -1.0 if rng.random() < 0.5 else 1.0
    offset = sign * (0.02 + 0.02 * rng.random()) * dist  # 2-4% of distance
    # Perpendicular unit vector to the direction of travel.
    px, py = -dy / dist, dx / dist
    c1 = (x0 + dx / 3.0 + px * offset, y0 + dy / 3.0 + py * offset)
    c2 = (x0 + 2.0 * dx / 3.0 + px * offset * 0.6,
          y0 + 2.0 * dy / 3.0 + py * offset * 0.6)

    def bezier(t):
        mt = 1.0 - t
        bx = (mt ** 3 * x0 + 3 * mt * mt * t * c1[0]
              + 3 * mt * t * t * c2[0] + t ** 3 * x1)
        by = (mt ** 3 * y0 + 3 * mt * mt * t * c1[1]
              + 3 * mt * t * t * c2[1] + t ** 3 * y1)
        return bx, by

    total = max(0.0, float(duration_s or 0.0))
    step_delay = total / count  # uniform time steps; easing is spatial
    points = []
    for i in range(1, count + 1):
        bx, by = bezier(_ease_in_out(i / count))
        points.append((int(round(bx)), int(round(by)), step_delay))
    # Land exactly on the target, whatever rounding did.
    last = points[-1]
    points[-1] = (int(round(x1)), int(round(y1)), last[2])
    return points


def _follow_path(mouse, path):
    """Drive the cursor along a planned path.

    Returns None on success, or the abort message if the human takes
    over: before each step the ACTUAL position must still match the
    last planned point (within tolerance) and must not sit in the
    top-left failsafe corner (unless the planned point is there too).
    """
    # Command Deck Phase F: the planned path is the overlay's
    # cursor trail. Emitted once, up front, through the cosmetic
    # channel (never raises, never blocks the glide). A pure move
    # emits a path but no action chip: moves alone are not enough.
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

        _prod.emit_cursor_path([(int(px), int(py)) for px, py, _d in path],
                               source="cursor")
    except Exception:  # noqa: BLE001 — a bus problem never breaks input
        pass
    prev = _read_position(mouse)
    if prev is None:
        prev = (path[0][0], path[0][1])
    for x, y, delay in path:
        if delay > 0:
            time.sleep(delay)
        actual = _read_position(mouse)
        if actual is not None:
            deviation = math.hypot(actual[0] - prev[0], actual[1] - prev[1])
            if deviation > _TAKEOVER_TOLERANCE_PX:
                return _ABORT_MSG
            near_corner = (actual[0] <= _FAILSAFE_CORNER_PX
                           and actual[1] <= _FAILSAFE_CORNER_PX)
            planned_near_corner = prev[0] <= 10 and prev[1] <= 10
            if near_corner and not planned_near_corner:
                return _ABORT_MSG
        # Bounds fence: Bezier control points can push a step a few
        # pixels off an edge — fence every dispatched point so the
        # move completes in-bounds instead of erroring at the OS.
        x, y = _fence_point(x, y)
        try:
            _set_position(mouse, x, y)
        except Exception as e:  # noqa: BLE001
            return f"Could not move the cursor (input access lost?): {e}"
        prev = (x, y)
    return None


def _glide(mouse, tx, ty, duration):
    """Smooth-move the cursor to (tx, ty). None on success, else a message."""
    start = _read_position(mouse)
    if start is None:
        return "Could not read the current cursor position on this system."
    path = _plan_path(start[0], start[1], tx, ty, duration)
    return _follow_path(mouse, path)


def _resolve_target(size, current, x=None, y=None, fx=None, fy=None,
                    relative=None):
    """Resolve a move target to clamped pixels.

    Returns (tx, ty, None) or (None, None, error_string). `current`
    is only consulted for relative moves.
    """
    if relative is not None:
        try:
            dx = int(relative.get("dx", 0))
            dy = int(relative.get("dy", 0))
        except (AttributeError, TypeError, ValueError):
            return None, None, "A relative move needs a {dx, dy} pair of numbers."
        if current is None:
            return None, None, "Could not read the current cursor position on this system."
        tx, ty = current[0] + dx, current[1] + dy
    elif fx is not None or fy is not None:
        if fx is None or fy is None:
            return None, None, "A fraction move needs both fx and fy (each 0..1)."
        try:
            fxf, fyf = float(fx), float(fy)
        except (TypeError, ValueError):
            return None, None, "fx and fy must be numbers between 0 and 1."
        if size is None:
            return None, None, ("Could not determine the screen size, so fraction "
                               "coordinates cannot be converted to pixels here.")
        tx, ty = _to_pixels(_clamp(fxf, 0.0, 1.0), _clamp(fyf, 0.0, 1.0),
                            size[0], size[1])
    elif x is not None or y is not None:
        if x is None or y is None:
            return None, None, "A pixel move needs both x and y."
        try:
            tx, ty = int(x), int(y)
        except (TypeError, ValueError):
            return None, None, "x and y must be whole numbers (pixels)."
    else:
        return None, None, ("No move target given: pass x/y pixels, fx/fy "
                            "fractions, or a relative {dx, dy}.")
    # Bounds fence against the real virtual-screen bounds (falling
    # back to the size the caller resolved with when the virtual
    # bounds can't be measured).
    bounds = _virtual_bounds()
    if bounds is None and size is not None:
        bounds = (0, 0, size[0] - 1, size[1] - 1)
    return (*_fence_point(tx, ty, bounds), None)
