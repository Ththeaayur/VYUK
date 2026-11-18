# w4_wiring_pilots.py — split from core/w4_wiring.py (Phase B B12): cursor pilot dispatch closure.
# Move only; the facade in core/w4_wiring.py re-exports every name.

from __future__ import annotations  # keep def-time annotation semantics identical to the old module

import json  # runtime use: _parse_points calls json.loads


PILOT_ACTIONS: tuple[str, ...] = (
    "ole_drag_drop",
    "cursor_waypoint_glide",
    "cursor_drag_with_hover_pause",
)




# ---------------------------------------------------------------------------
# Item 4 — PC-C cursor pilot dispatch (called by core/desktop/cursor.py)
# ---------------------------------------------------------------------------

def _default_pilots() -> dict[str, Callable[..., str]]:
    """The desktop pilots, imported lazily (no top-level
    import, so cursor.py's lazy import of this module can
    never cycle)."""
    from .desktop import desktop as _d  # Phase 3 S7a: desktop.py moved to core/desktop/desktop.py

    return {
        "ole_drag_drop": _d.ole_drag_drop,
        "cursor_waypoint_glide": _d.cursor_waypoint_glide,
        "cursor_drag_with_hover_pause": _d.cursor_drag_with_hover_pause,
    }




def _parse_points(points: Any) -> list[tuple[int, int]] | None:
    """Waypoint points as a list of (x, y) pairs, or the same
    as JSON text. Returns None when unparseable."""
    try:
        if isinstance(points, str):
            points = json.loads(points)
        return [(int(p[0]), int(p[1])) for p in points]
    except (TypeError, ValueError, IndexError, KeyError):
        return None




def validate_cursor_pilot_args(action, *, x=None, y=None, x2=None,
                               y2=None, points=None):
    """Schema-time validation for cursor pilot plans (D-07):
    the per-action required fields, checked BEFORE any
    coordinate conversion or pilot dispatch. Returns an error
    string, or None when the plan is complete. Unknown actions
    are NOT this helper's job (the dispatcher's action check
    owns them) — it returns None for them. pause_s/duration are
    optional (dispatcher defaults 0.5/0.8) and never required
    here. Pure."""
    act = (action or "").strip().lower()
    if act == "ole_drag_drop":
        if None in (x, y, x2, y2):
            return (
                "Cursor 'ole_drag_drop' needs x, y (drag "
                "start) and x2, y2 (drag end) in pixels."
            )
    elif act == "cursor_waypoint_glide":
        pts = _parse_points(points)
        if pts is None or len(pts) < 2:
            return (
                "A waypoint glide needs at least two "
                "(x, y) points in pixels (list, or JSON "
                "text of a list of pairs)."
            )
    elif act == "cursor_drag_with_hover_pause":
        if None in (x, y, x2, y2):
            return (
                "Cursor 'cursor_drag_with_hover_pause' needs "
                "x, y (drag start) and x2, y2 (drag end) in "
                "pixels."
            )
    return None




def cursor_pilot_action(
    action: str,
    *,
    x: Any = None,
    y: Any = None,
    x2: Any = None,
    y2: Any = None,
    points: Any = None,
    duration: Any = None,
    pause_s: Any = None,
    coords: str | None = None,
    _pilots: dict[str, Callable[..., str]] | None = None,
    _screen_size: Callable[[], Any] | None = None,
) -> str:
    """Dispatch one PC-C pilot action to desktop.

    Coordinates: pilots take true screen pixels. When coords
    is None or "norm1000" (the cursor_control default), the
    absolute pairs (x, y) and (x2, y2) are converted from
    norm1000 space via cursor.py's own converter; "pixels"
    passes them through unchanged. Waypoint `points` are
    always screen pixels (a waypoint list has no screenshot
    frame to normalise against) — that is stated here so no
    caller silently assumes otherwise. Unknown modes and
    missing coordinates are plain-words refusals, never
    guesses. Never raises.
    """
    act = (action or "").strip().lower()
    if act not in PILOT_ACTIONS:
        return (
            f"Unknown cursor pilot action: '{action}'. Use one "
            f"of: {', '.join(PILOT_ACTIONS)}."
        )
    mode = (coords or "norm1000").strip().lower()
    if mode not in ("pixels", "norm1000"):
        return (
            f"Unknown coords mode: '{coords}'. Use 'norm1000' "
            "(the default) or 'pixels'."
        )
    # D-07: schema-time validation FIRST — an incomplete plan
    # is refused here, before any coordinate conversion or
    # pilot dispatch, with the same plain-words messages the
    # inline checks used to give (now in one place).
    incomplete = validate_cursor_pilot_args(
        act, x=x, y=y, x2=x2, y2=y2, points=points)
    if incomplete is not None:
        return incomplete
    try:
        if mode == "norm1000":
            from .desktop import cursor as _cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

            if _screen_size is not None:
                # An injected size is the test-control seam: the
                # caller owns the grid, exactly as before.
                size = _screen_size()
                if size is None and (
                    x is not None or y is not None
                    or x2 is not None or y2 is not None
                ):
                    return (
                        "Could not determine the screen size, so "
                        "norm1000 coordinates cannot be converted "
                        "to pixels here."
                    )
                if size is not None:
                    if x is not None and y is not None:
                        x, y = _cursor._norm1000_to_pixels(
                            x, y, size[0], size[1])
                    if x2 is not None and y2 is not None:
                        x2, y2 = _cursor._norm1000_to_pixels(
                            x2, y2, size[0], size[1])
            else:
                # Production path: the coordinate contract —
                # last capture's crop, else the virtual desktop.
                if x is not None and y is not None:
                    point = _cursor._norm1000_to_screen(x, y)
                    if point is None:
                        return (
                            "Could not determine the screen size, so "
                            "norm1000 coordinates cannot be converted "
                            "to pixels here."
                        )
                    x, y = point
                if x2 is not None and y2 is not None:
                    point = _cursor._norm1000_to_screen(x2, y2)
                    if point is None:
                        return (
                            "Could not determine the screen size, so "
                            "norm1000 coordinates cannot be converted "
                            "to pixels here."
                        )
                    x2, y2 = point
        pilots = _pilots if _pilots is not None else _default_pilots()
        if act == "ole_drag_drop":
            # Completeness already validated above (D-07).
            return pilots["ole_drag_drop"](
                int(x), int(y), int(x2), int(y2),
                duration=0.8 if duration is None else float(duration))
        if act == "cursor_waypoint_glide":
            pts = _parse_points(points)
            # Completeness already validated above (D-07): pts
            # is a list of at least two pairs here.
            return pilots["cursor_waypoint_glide"](
                pts, duration=0.6 if duration is None else float(duration))
        # cursor_drag_with_hover_pause — completeness already
        # validated above (D-07).
        return pilots["cursor_drag_with_hover_pause"](
            int(x), int(y), int(x2), int(y2),
            pause_s=0.5 if pause_s is None else float(pause_s),
            duration=0.8 if duration is None else float(duration))
    except (TypeError, ValueError) as exc:
        return f"Cursor pilot '{act}' got bad arguments: {exc}"
    except Exception as exc:  # noqa: BLE001 — pilots never raise
        return f"Cursor pilot '{act}' unavailable: {exc}"
