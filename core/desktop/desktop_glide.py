# Split from core/desktop/desktop.py (Phase B batch B7) — move only,
# no behaviour change. This module owns the section named below;
# the original file is now a facade re-exporting every name.

# Cursor pilots built on core/desktop/cursor.py (extend, never
# duplicate). No backend-global reads here (verified by grep:
# no _clipboard_backend/_is_windows reads in this section).

import time

# ---------------------------------------------------------------------------
# PC-C — cursor pilots built on core/desktop/cursor.py (extend, never duplicate)
# ---------------------------------------------------------------------------
# Every pilot below calls cursor._plan_path (the seeded-Bezier
# planner) and cursor._follow_path (takeover abort + bounds fence)
# — there is exactly one path planner in the product, and it lives
# in cursor.py. These pilots are staged here for the same enum
# reason as HA-5 (cursor_control's action enum does not name them
# yet); they are plain functions, tested, and funnel-ready once
# the coordinator adds their action words to that enum.

def glide_path_metrics(x0, y0, x1, y1, duration_s: float = 0.6) -> dict:
    """Pure metrics for one seeded-Bezier glide (no input sent):
    point count, pixel distance, first/last points. Testable
    anywhere — the planner is pure math."""
    from . import cursor as _cursor  # Phase 3 D4d: cursor hub now in core/desktop/; S7a interim closed

    path = _cursor._plan_path(x0, y0, x1, y1, duration_s)
    import math

    return {
        "points": len(path),
        "distance_px": round(math.hypot(x1 - x0, y1 - y0), 1),
        "first": path[0][:2] if path else None,
        "last": path[-1][:2] if path else None,
        "duration_s": duration_s,
    }


def cursor_waypoint_glide(points, duration: float = 0.6) -> str:
    """Glide through several waypoints on seeded-Bezier legs
    (PC-C pilot: a path with intermediate points, e.g. around a
    screen obstacle, instead of one straight glide). Takeover
    abort on any leg stops the whole glide, button-free (this is
    a move, not a drag). Never raises."""
    try:
        pts = [(int(p[0]), int(p[1])) for p in points]
    except (TypeError, ValueError):
        return "A waypoint glide needs a list of (x, y) points."
    if len(pts) < 2:
        return "A waypoint glide needs at least two points."
    try:
        from . import cursor as _cursor  # Phase 3 D4d: cursor hub now in core/desktop/; S7a interim closed

        mouse, _btn, err = _cursor._mouse_controller()
        if mouse is None:
            return err
        leg_duration = max(0.05, float(duration) / (len(pts) - 1))
        for i in range(len(pts) - 1):
            start = _cursor._read_position(mouse)
            if start is None:
                start = pts[i]
            path = _cursor._plan_path(start[0], start[1],
                                      pts[i + 1][0], pts[i + 1][1],
                                      leg_duration)
            abort = _cursor._follow_path(mouse, path)
            if abort:
                return f"{abort} (Waypoint glide stopped at leg {i + 1}.)"
        return (f"Glided through {len(pts)} waypoint(s) on "
                "seeded-Bezier legs.")
    except Exception as e:  # noqa: BLE001
        return f"Waypoint glide unavailable: {e}"


def cursor_drag_with_hover_pause(x1, y1, x2, y2, pause_s: float = 0.5,
                                 duration: float = 0.8) -> str:
    """Drag with a hover pause mid-path (PC-C pilot: for targets
    that reveal themselves on hover, e.g. a submenu, before the
    drop). Button held for the whole gesture, released in a
    finally — the drag_cursor discipline, on the same planner.
    Never raises."""
    try:
        from . import cursor as _cursor  # Phase 3 D4d: cursor hub now in core/desktop/; S7a interim closed

        mouse, Button, err = _cursor._mouse_controller()
        if mouse is None:
            return err
        x1, y1, x2, y2 = (int(x1), int(y1), int(x2), int(y2))
        x1, y1 = _cursor._fence_point(x1, y1)
        x2, y2 = _cursor._fence_point(x2, y2)
        try:
            _cursor._set_position(mouse, x1, y1)
            mouse.press(Button.left)
            _cursor._note_input_pressed("button", Button.left)
        except Exception as e:  # noqa: BLE001
            return (f"Could not start the drag (no display / input "
                    f"access on this system?): {e}")
        abort = None
        try:
            path = _cursor._plan_path(x1, y1, x2, y2, duration)
            half = max(1, len(path) // 2)
            abort = _cursor._follow_path(mouse, path[:half])
            if abort is None and pause_s and float(pause_s) > 0:
                time.sleep(min(float(pause_s), 5.0))
                abort = _cursor._follow_path(mouse, path[half:])
        finally:
            try:
                mouse.release(Button.left)
            except Exception:  # noqa: BLE001
                pass
            else:
                _cursor._note_input_released("button", Button.left)
        if abort:
            return f"{abort} (Drag released where it stopped.)"
        return (f"Dragged from ({x1}, {y1}) to ({x2}, {y2}) with a "
                f"{pause_s}s hover pause mid-path.")
    except Exception as e:  # noqa: BLE001
        return f"Hover-pause drag unavailable: {e}"
