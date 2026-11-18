"""Tool dispatcher for core/desktop/cursor.py (B35 part 2 of 2):
the single cursor_control tool + register_all. The declaration
text (incl. the points items shape, the Live 1007 fix) is
verbatim from the pre-split module — see the prover.
"""

# B35 INTENTIONAL: names this family reads by bare name, imported
# from their defining modules (same objects as the facade
# re-exports). Dependency order: geometry <- input <- glide
# <- ops; control reads ops + input + geometry only.
from .cursor_geometry import (
    _capture_freshness_refusal,
    _norm1000_to_screen,
    _routed_backend,
    _screen_size,
    _to_pixels,
)
from .cursor_input import (
    _mouse_controller,
    _read_position,
)
from .cursor_ops import (
    click_cursor,
    drag_cursor,
    get_cursor_position,
    move_cursor,
    nudge_cursor,
    park_cursor,
    scroll_cursor,
    show_beacon,
)


# ---------------------------------------------------------------------------
# Tool dispatcher — ONE Gemini tool: cursor_control
# ---------------------------------------------------------------------------

_ACTIONS = ("position", "move", "move_fraction", "move_by", "nudge", "click",
            "double_click", "right_click", "middle_click", "scroll", "drag",
            "beacon", "park")


def _unverified_act(result, kind):
    """Wrap a dispatched click/scroll/drag receipt as honestly unverified.

    R1b: the cursor position after the act is not a landing
    observation — the pointer being at the point proves it moved,
    not that the click landed on anything or the drop took (see
    the rejected alternative in core/tool_outcome.py). Failure /
    refusal returns pass through untouched, as do results that do
    not read as a dispatched success.
    """
    from .. import tool_outcome as _outcome  # R1b: seam wiring

    msg = str(result)
    marks = {
        "click": ("Clicked ", "Double-clicked ", "Triple-clicked "),
        "scroll": ("Scrolled ",),
        "drag": ("Dragged ",),
    }
    prefixes = marks.get(kind, ())
    if msg.startswith(prefixes):
        caveats = {
            "click": " (Click dispatched; landing not confirmed.)",
            "scroll": " (Scroll dispatched; the page position was not re-read.)",
            "drag": " (Drag dispatched; the drop landing was not confirmed.)",
        }
        reasons = {
            "click": "no landing observation exists for a raw cursor click",
            "scroll": "no position read-back after the scroll",
            "drag": "no drop-landing observation exists for a raw cursor drag",
        }
        return _outcome.unverified(msg + caveats[kind], reasons[kind])
    return result


def cursor_control(action: str = "", x=None, y=None, fx=None, fy=None,
                   direction: str = "", pixels=None, amount=None,
                   duration=None, corner: str = "", button: str = "",
                   coords: str | None = None, x2=None, y2=None,
                   points=None, pause_s=None, monitor=None) -> str:
    """Dispatch the cursor_control tool's actions to the functions above.

    coords (v5.43, E10) names the space x/y are given in:
    "norm1000" (x/y in 0..1000 normalized space, as read off a
    screenshot; converted here to true pixels under the
    coordinate contract — the last capture's crop, else the
    virtual desktop — before dispatch, so every existing action
    below still receives plain pixels) or "pixels" (true screen
    pixels, exactly the pre-E10 behaviour).

    §7 consumers batch 11 — `monitor` is a funnel tool argument
    too (declared on cursor_control; batch 10 added it Python-
    only): when a
    monitor reference is given ("second monitor", "DISPLAY2",
    "left", ...), the target resolves through the item-6 engine
    against a FRESH table before dispatch, then continues with
    plain pixels. GRID WARNING: monitor-targeted norm1000 is
    VIRTUAL-DESKTOP space (item-6), NOT the capture-crop grid
    above — pass virtual-desktop norm1000 (as from
    monitor_action's virtual mapping) or true pixels with a
    monitor target, never screenshot-crop coordinates.
    monitor=None keeps every existing path byte-identical.

    v5.49 (programme item 30): norm1000 is now THE DEFAULT — an
    omitted coords (None, the sentinel that tells "not passed"
    from an explicit "pixels") resolves to norm1000 for the
    absolute-target actions, because coordinates a model supplies
    overwhelmingly come from screenshots. "pixels" stays fully
    selectable. Legacy contract change: pre-v5.49 an omitted
    coords meant pixels; suites pinning that old default are the
    coordinator's to reconcile. move_by is the one exception: its
    x/y are pixel DELTAS (the coords mode never governed them),
    so an omitted coords keeps move_by in pixels — only an
    EXPLICIT coords='norm1000' is refused there, as before.
    norm1000 applies to the absolute-target actions (move, the
    clicks, scroll, drag, beacon); nudge / move_fraction have
    their own units and are unaffected.

    Norm1000 capture freshness (2026-11-03 investigation): before
    converting, the absolute-target actions refuse when the live
    screen no longer matches the last capture's geometry — the
    virtual bounds changed since the capture, or the last capture
    was a foreground crop whose window moved, resized, or
    changed — with a re-observe instruction, instead of acting on
    a stale point.
    Absence of a refusal is NOT a freshness certificate: in-window
    content change, a same-rectangle window swap, and an
    intervening capture with a different crop are not visible to
    this check (see _capture_freshness_refusal); for a target
    where a wrong click is costly, prefer the identity-based
    tools named above."""
    act = (action or "").strip().lower()
    # §7 item-6 monitor-targeted dispatch (consumers batch 10):
    # when a monitor reference is given, resolve the target
    # through the item-6 engine against a FRESH table, then
    # continue with plain pixels through the normal flow below
    # (including the routed backend, which receives pixels).
    # monitor=None keeps every existing path byte-identical.
    # `act` is computed here (moved up from below the backend
    # delegation, same value) because this branch must run
    # before the backend takes the call.
    if monitor is not None:
        _MONITOR_ACTIONS = ("move", "click", "double_click",
                            "right_click", "middle_click", "scroll",
                            "drag", "beacon")
        if act not in _MONITOR_ACTIONS:
            return ("A monitor target applies only to absolute-target "
                    "actions (move, click, double_click, right_click, "
                    "middle_click, scroll, drag, beacon).")
        if x is None or y is None:
            return "A monitor-targeted action needs both x and y."
        if fx is not None or fy is not None:
            return ("Ambiguous target: a monitor-targeted action "
                    "takes x/y only, not fx/fy.")
        _mon_mode = (coords or "norm1000").strip().lower()
        if _mon_mode not in ("pixels", "norm1000"):
            return (f"Unknown coords mode: '{coords}'. Use 'norm1000' "
                    "(the default — 0..1000 normalized coordinates) or "
                    "'pixels' (true screen pixels).")
        try:
            from . import cursor_monitor as _cm  # Phase 3 D4d: cursor hub now in core/desktop/; S7a-2 interim closed
        except Exception:  # noqa: BLE001
            return "Monitor-targeted dispatch is unavailable here."
        if _mon_mode == "norm1000":
            try:
                _resolved, _refusal = _cm.resolve_click_point(
                    monitor, x, y)
            except (TypeError, ValueError):
                return ("x and y must be numbers in 0..1000 when "
                        "coords is 'norm1000'.")
            if _resolved is None:
                return _refusal
            x, y = _resolved["point"]
        else:
            _ok, _refusal = _cm.validate_pixel_on_monitor(
                monitor, x, y)
            if not _ok:
                return _refusal
            try:
                x, y = int(x), int(y)
            except (TypeError, ValueError):
                return "x and y must be whole numbers (pixels)."
        coords = "pixels"
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.cursor_control(
            action, x=x, y=y, fx=fx, fy=fy, direction=direction,
            pixels=pixels, amount=amount, duration=duration,
            corner=corner, button=button, coords=coords)
    # Conversion pass, Wave 4 (PC-C): pilot actions live in
    # core/w4_wiring.py (PILOT_ACTIONS), read lazily here so
    # this module never imports the wiring module at top
    # level. They are NOT added to _ACTIONS: register_all's
    # enum is list(_ACTIONS) and stays the 13 legacy actions
    # until the coordinator applies the READY declaration
    # addition (w4_wiring.CURSOR_CONTROL_DECLARATION_ADDITION)
    # — extending _ACTIONS here would grow the fitted payload
    # without a measurement. A failed lazy import reads as
    # "no pilots", exactly the pre-wire behaviour.
    _pilot_actions: tuple = ()
    _w4 = None
    try:
        from .. import w4_wiring as _w4  # Phase 3 D4d: cursor hub moved to core/desktop/; w4_wiring stays flat in core/

        _pilot_actions = _w4.PILOT_ACTIONS
    except Exception:  # noqa: BLE001 — no pilots, legacy only
        _pilot_actions = ()
        _w4 = None
    if act not in _ACTIONS and act not in _pilot_actions:
        return (f"Unknown cursor action: '{action}'. Use one of: "
                f"{', '.join(_ACTIONS)}.")
    dur = 0.6 if duration is None else duration

    explicit_mode = bool(str(coords or "").strip())
    mode = (coords or "norm1000").strip().lower()
    if act == "move_by" and not explicit_mode:
        mode = "pixels"  # deltas are pixels; see the docstring
    if mode not in ("pixels", "norm1000"):
        return (f"Unknown coords mode: '{coords}'. Use 'norm1000' "
                f"(the default — 0..1000 normalized coordinates read "
                f"from a screenshot) or 'pixels' (true screen "
                f"pixels).")
    if mode == "norm1000":
        if act == "move_by":
            return ("coords='norm1000' does not apply to move_by — its "
                    "x/y are pixel deltas. Use coords='pixels' for a "
                    "relative move, or action 'move' with norm1000 "
                    "coordinates for an absolute target.")
        if act in ("move", "click", "double_click", "right_click",
                   "middle_click", "scroll", "drag", "beacon") \
                and (x is not None or y is not None):
            if x is None or y is None:
                return ("A norm1000 target needs both x and y "
                        "(each 0..1000).")
            # Norm1000 capture-freshness check (2026-11-03): refuse
            # on positive evidence the screen moved since the last
            # capture, before any move/click executes — same action
            # set as the conversion below, so pixels / move_by /
            # fraction paths are untouched, and the monitor branch
            # above already continued in pixels.
            refusal = _capture_freshness_refusal()
            if refusal is not None:
                return refusal
            try:
                point = _norm1000_to_screen(x, y)
            except (TypeError, ValueError):
                return ("x and y must be numbers in 0..1000 when "
                        "coords is 'norm1000'.")
            if point is None:
                return ("Could not determine the screen size, so "
                        "norm1000 coordinates cannot be converted to "
                        "pixels here.")
            x, y = point

    if act in _pilot_actions:
        # Conversion pass, Wave 4 (PC-C): pilot dispatch. The
        # norm1000 conversion above covers only the legacy
        # absolute-target actions, so coords is passed through
        # and w4_wiring converts the pilot pairs itself; pilot
        # waypoint points are screen pixels by contract (see
        # w4_wiring.cursor_pilot_action). On this Linux VM the
        # platform backend above returns first, so this branch
        # runs on the Windows body (backend None) — the suite
        # pins the routing with the backend stubbed to None
        # and stub pilots, and Windows validation is owed.
        return _w4.cursor_pilot_action(
            act, x=x, y=y, x2=x2, y2=y2, points=points,
            duration=dur, pause_s=pause_s, coords=mode)
    if act == "position":
        return get_cursor_position()
    if act == "move":
        if x is None or y is None:
            return "Cursor 'move' needs both x and y (pixels)."
        return move_cursor(x=x, y=y, duration=dur)
    if act == "move_fraction":
        if fx is None or fy is None:
            return "Cursor 'move_fraction' needs both fx and fy (0..1)."
        return move_cursor(fx=fx, fy=fy, duration=dur)
    if act == "move_by":
        if x is None and y is None:
            return "Cursor 'move_by' needs x and/or y as the pixel delta (dx, dy)."
        return move_cursor(relative={"dx": x or 0, "dy": y or 0}, duration=dur)
    if act == "nudge":
        if not direction:
            return "Cursor 'nudge' needs a direction (up, down, left, right, ne, nw, se, sw)."
        return nudge_cursor(direction, pixels=40 if pixels is None else pixels,
                            duration=0.25 if duration is None else duration)
    if act in ("click", "double_click", "right_click", "middle_click"):
        mapping = {"click": (button or "left", 1), "double_click": ("left", 2),
                   "right_click": ("right", 1), "middle_click": ("middle", 1)}
        btn, count = mapping[act]
        return _unverified_act(
            click_cursor(button=btn, count=count, x=x, y=y, fx=fx, fy=fy),
            "click")
    if act == "scroll":
        if amount is None:
            return "Cursor 'scroll' needs an amount (-20..20; positive scrolls up)."
        return _unverified_act(scroll_cursor(amount, x=x, y=y), "scroll")
    if act == "drag":
        # Drags run from wherever the cursor is now to the given point —
        # that is how voice drives it: "drag this to over there".
        if x is None or y is None:
            return "Cursor 'drag' needs x and y: the point to drag TO (the drag starts at the current cursor position)."
        mouse, _btn, err = _mouse_controller()
        if mouse is None:
            return err
        start = _read_position(mouse)
        if start is None:
            return "Could not read the current cursor position to start the drag."
        return _unverified_act(
            drag_cursor(start[0], start[1], x, y,
                        duration=0.8 if duration is None else duration),
            "drag")
    if act == "beacon":
        if fx is not None and fy is not None and x is None and y is None:
            size = _screen_size()
            if size is None:
                return "Could not determine the screen size for the beacon point."
            x, y = _to_pixels(fx, fy, size[0], size[1])
        return show_beacon(x=x, y=y)
    # act == "park"
    return park_cursor(corner or "bottom_right")


# ---------------------------------------------------------------------------
# Registration — called by core/funnel/tools_handlers_pack_core2.py (_register_cursor_tools; Phase 5 docs-only rename, wiring unchanged)
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register the cursor_control tool on a ToolRegistry-like object."""
    registry.register(
        name="cursor_control",
        description=(
            "Pro-grade mouse cursor control with SMOOTH, human-like gliding "
            "movement (never a teleport) in true screen pixels — coordinates "
            "are DPI-aware, so they stay accurate on 125%/150% scaled Windows "
            "displays. Coordinates default to coords='norm1000' with x/y "
            "in 0..1000 (0,0 = the image's top-left, 1000,1000 = its "
            "bottom-right): screenshots are downscaled, so image pixels "
            "are NOT screen pixels, and norm1000 is mapped to true pixels "
            "for you. Use coords='pixels' only for coordinates already in "
            "true screen pixels. If the user moves the mouse themselves mid-move, VYUK "
            "aborts instantly and reports it. Prefer click_element / "
            "uia_click_control / click_text when the target has a name or visible text; use "
            "cursor_control for precise or relative work: 'nudge it left a "
            "bit', 'click at 800, 400', 'scroll down here', 'drag from here "
            "to there'. Actions: position (report where the cursor is); move "
            "(glide to pixel x, y); move_fraction (glide to fx, fy as 0..1 "
            "screen fractions); move_by (glide by a pixel delta — x/y ARE "
            "the delta); nudge (short glide in a compass direction, "
            "'pixels' far); click / double_click / right_click / "
            "middle_click (glide to x,y or fx,fy first if given, else click "
            "where the cursor is; 'button' picks the button for action "
            "click) — every click's result identifies the control under the "
            "landing point via UI Automation when it can (e.g. 'That point "
            "is the \"Save\" (Button) in Notepad') and notes when the click "
            "moved focus to another window; scroll (amount -20..20 at x,y "
            "if given); drag (from the "
            "CURRENT cursor position to x, y, button held, abort-safe); "
            "beacon (flash a visible ring where VYUK is pointing); park "
            "(glide to a corner/edge: bottom_right, top_left, center, ...). "
            "Note on ole_drag_drop (in the action list below): it is a "
            "pointer-gesture drag on the seeded-Bezier path, NOT a COM OLE "
            "DoDragDrop — it carries no OLE data object, so a drop target "
            "that needs a real OLE data object will not receive one. "
            "Optional 'monitor' target ('second monitor', 'DISPLAY2', ...) "
            "resolves through the multi-monitor engine against a fresh "
            "table before dispatch — with a monitor target, norm1000 is "
            "virtual-desktop space (as from monitor_action's virtual "
            "mapping), never screenshot-crop space."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    # Conversion pass, Wave 4 (PC-C): the three
                    # pilot words from core/w4_wiring.py
                    # PILOT_ACTIONS, appended after the legacy
                    # actions in exactly the order the 74 B
                    # measurement assumed. _ACTIONS itself is
                    # NOT extended: the cursor.py dispatcher
                    # branch, not the legacy tuple, owns the
                    # pilots. (The W4 leaf's suggested
                    # list(_ACTIONS) + list(_PILOT_ACTIONS)
                    # cannot be used verbatim: no module-level
                    # _PILOT_ACTIONS exists in this file, so
                    # the same three words are named literally
                    # here instead of importing a name that
                    # does not exist.)
                    "enum": list(_ACTIONS) + [
                        "ole_drag_drop",
                        "cursor_waypoint_glide",
                        "cursor_drag_with_hover_pause",
                    ],
                    "description": "What the cursor should do",
                },
                "x": {"type": "integer", "description": "Target x in true pixels (for move_by: the horizontal delta). With coords='norm1000': x in 0..1000 normalized space instead (0 = left edge of the screenshot, 1000 = right edge)"},
                "y": {"type": "integer", "description": "Target y in true pixels (for move_by: the vertical delta). With coords='norm1000': y in 0..1000 normalized space instead (0 = top edge of the screenshot, 1000 = bottom edge)"},
                "x2": {"type": "integer", "description": "For ole_drag_drop / cursor_drag_with_hover_pause: drag-END x, in the same coordinate space as x/y (required for those actions; the dispatcher refuses a drag plan without it)"},
                "y2": {"type": "integer", "description": "For ole_drag_drop / cursor_drag_with_hover_pause: drag-END y, in the same coordinate space as x/y (required for those actions; the dispatcher refuses a drag plan without it)"},
                "coords": {"type": "string", "enum": ["pixels", "norm1000"], "description": "Coordinate space for x/y: 'norm1000' (the default — 0..1000 normalized; use when reading coordinates from a screenshot, whose image is downscaled) or 'pixels' (true screen pixels)"},
                "fx": {"type": "number", "description": "Target x as a screen fraction 0..1 (0 = left edge, 1 = right edge)"},
                "fy": {"type": "number", "description": "Target y as a screen fraction 0..1 (0 = top edge, 1 = bottom edge)"},
                "direction": {"type": "string", "description": "For nudge: up, down, left, right, ne, nw, se, sw"},
                "pixels": {"type": "integer", "description": "For nudge: how far to move in pixels (default 40)"},
                "amount": {"type": "integer", "description": "For scroll: steps -20..20, positive scrolls up"},
                "duration": {"type": "number", "description": "Glide time in seconds (default 0.6; nudge 0.25, drag 0.8)"},
                "points": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}, "description": "For cursor_waypoint_glide: at least two (x, y) waypoint pairs in TRUE screen pixels (a list of pairs, or JSON text of a list of pairs) — required for that action; waypoint points are never norm1000, they have no screenshot frame to normalise against"},
                "pause_s": {"type": "number", "description": "For cursor_drag_with_hover_pause: hover pause in seconds at the drag end (default 0.5; optional)"},
                "corner": {"type": "string", "description": "For park: top_left, top_right, bottom_left, bottom_right, top_center, bottom_center, left_center, right_center, center"},
                "button": {"type": "string", "description": "For click: left, right or middle (default left)"},
                "monitor": {"type": "string", "description": "Optional monitor target ('second monitor', 'DISPLAY2', 'left', ...): resolves the target through the multi-monitor engine against a FRESH monitor table before dispatch, so the click lands on the right monitor at the right physical pixels on mixed-DPI setups. GRID WARNING: with a monitor target, coords='norm1000' means VIRTUAL-DESKTOP space (0..1000 across the whole virtual desktop, as from monitor_action's virtual mapping) or coords='pixels' true pixels — NEVER screenshot-crop coordinates (the default norm1000 grid maps across the last capture's rectangle). Applies only to absolute-target actions (move, click, double_click, right_click, middle_click, scroll, drag, beacon); omitted means today's behaviour exactly."},
            },
            "required": ["action"],
        },
        handler=cursor_control,
    )
