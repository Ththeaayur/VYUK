"""
Hands skills pack 3 for VYUK (v5.30) — the window-geometry and
second-chance hands: snapping the active window to halves and
quarters, moving it between monitors, typing the clipboard into
fields that block paste, dragging through waypoints, scrolling
until text shows up, diffing the screen against a saved baseline,
finding a window by what's written inside it, a shortcut cheat
sheet for the focused app, unpinning every always-on-top window at
once, and dragging a file onto an app.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise and never fake a
result. Acting tools follow the house discipline — identify the
target before touching it (an exact title match beats substring
matches; several matches refuse and list), verify the outcome by
reading it back, and stop when the failsafe has tripped. All OS
surface sits behind the small module-level helpers at the top, so
tests can monkeypatch a single seam instead of the OS.

Seams reused, not reimplemented: window enumeration, titles and
the failsafe check from core/desktop/control.py; the OCR word-box pattern
from core/desktop/control_plus.click_text; the cursor's path planner,
bounds fence and takeover abort from core/desktop/cursor.py; the monitor
enumeration shape from core/skills_plus.py.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

# SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL (pure facade): the hands3
# implementations live in the three impl modules (window-manip,
# window-focus, capture-scroll); this file re-exports every name of the
# original module so existing importers — including the five live
# importers of private names (core/platform/windows,
# core/desktop/uia_probe, core/desktop/screen_watch,
# core/desktop/vision_capture,
# core/funnel/tools_handlers_capture_launch) and tools_handlers' capture seam
# (_resolve_capture_monitor, _monitor_rect) — keep working unchanged,
# and register_all below resolves its handlers. No implementation lives
# here anymore.
# B-D1 (2026-11-09): the PyWinCtl window_place handler lives in
# the wrapper (core/desktop/pywinctl_wrapper.py) — the only new
# tool this batch adds. The wrapper is additive; the ctypes
# handlers above stay exactly as-is.
from .desktop.pywinctl_wrapper import window_place
from .skills_hands3_window_manip import (
    _is_windows,
    _user32,
    _foreground_hwnd,
    _title_of,
    _window_rect,
    _centre,
    _monitors,
    _monitor_for_window,
    _pick_window,
    _SNAP_POSITIONS,
    _snap_rect,
    window_snap,
    _resolve_monitor,
    move_window_to_monitor,
    _GWL_EXSTYLE,
    _WS_EX_TOPMOST,
    _HWND_NOTOPMOST,
    window_pin_toggle_all,
    _root_window_at,
    _find_item_centre,
    _pick_explorer_window,
    drag_file_onto,
)

from .skills_hands3_window_focus import (
    _MAX_FOCUS_WINDOWS,
    focus_window_by_text,
    _GENERIC_SHORTCUTS,
    _APP_SHORTCUTS,
    _TITLE_HINTS,
    _CHEATSHEET_APPS_LINE,
    _foreground_process_name,
    _format_shortcuts,
    hotkey_cheatsheet,
)

from .skills_hands3_capture_scroll import (
    _NOT_WINDOWS,
    _DATA_DIR,
    _BASELINE_NAME,
    _TYPE_CLIPBOARD_CAP,
    _MAX_WAYPOINTS,
    _input_blocked,
    _ocr_words,
    _inspected_scroll_point,
    _phrase_centres,
    _drag_hold,
    type_clipboard,
    _parse_points,
    click_and_drag_path,
    scroll_until_found,
    _DIFF_REGIONS,
    _DIFF_THRESHOLD,
    _DIFF_REGION_MIN,
    _DIFF_QUIET,
    _virtual_origin,
    _monitor_rect,
    _resolve_capture_monitor,
    _grab_screen_image,
    _BASELINE_SIDECAR_NAME,
    _baseline_sidecar_path,
    _write_baseline_sidecar,
    _read_baseline_sidecar,
    _monitor_sentence,
    _pct,
    screen_diff,
    _grab_desktop_frame,
    _ocr_words_in_rect,
    _restore_foreground,
)


# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Hands-3 pack tool on a ToolRegistry-like object."""
    registry.register(
        name="window_snap",
        description="Snap the window you're using to one half or quarter of its screen (left, right, top-left, top-right, bottom-left, bottom-right) or maximise it, with exact positioning — including the corners, which the Win+arrow keys can't reach. The placement is read back before the result claims it. Use when the user asks to move or split the current window to part of the screen. Windows only.",
        parameters={
            "type": "object",
            "properties": {
                "position": {"type": "string", "enum": ["left", "right", "top-left", "top-right", "bottom-left", "bottom-right", "maximise"], "description": "Where on the screen the active window should go"},
            },
            "required": ["position"],
        },
        handler=window_snap,
    )
    registry.register(
        name="window_place",
        description="Place a named window where you want it: snap it to one half, third or corner of its own screen (left, right, top, bottom, left-third, center-third, right-third, top-left, top-right, bottom-left, bottom-right) or maximise it — or move/resize it to exact pixels. The window is found by title (an exact match wins; several matches refuse and list), identified by its handle, and the placement is read back before the result claims it. Use when the user asks to put a specific window somewhere. Windows only.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The window to place, by its title (an exact title wins; if several windows share it, the action refuses and lists them)"},
                "position": {"type": "string", "enum": ["left", "right", "top", "bottom", "left-third", "center-third", "right-third", "top-left", "top-right", "bottom-left", "bottom-right", "maximise"], "description": "Snap position on the window's own screen — give either a position or explicit geometry, not both"},
                "left": {"type": "integer", "description": "Left edge in screen pixels (a move needs both left and top)"},
                "top": {"type": "integer", "description": "Top edge in screen pixels (a move needs both left and top)"},
                "width": {"type": "integer", "description": "Width in pixels (a resize needs both width and height)"},
                "height": {"type": "integer", "description": "Height in pixels (a resize needs both width and height)"},
            },
            "required": ["title"],
        },
        handler=window_place,
    )
    registry.register(
        name="move_window_to_monitor",
        description="Move the window you're using to another screen: 'next' (the default) cycles to the following monitor, or give a monitor number (1, 2…) or part of its name. The window keeps its relative place and size on the new screen, and the move is verified by reading its position back. Use when the user wants the current window on the other monitor. Windows only.",
        parameters={
            "type": "object",
            "properties": {
                "monitor": {"type": "string", "description": "'next' (default), a monitor number like '2', 'main', or part of the monitor's name"},
            },
            "required": [],
        },
        handler=move_window_to_monitor,
    )
    registry.register(
        name="type_clipboard",
        description="Type the clipboard's text into the focused app as real keystrokes — for fields that block pasting, like some password-style or remote-desktop fields. At most 2000 characters are typed; if the clipboard holds more, only the first 2000 go in and the result says so. Use when paste (Ctrl+V) won't work somewhere.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=type_clipboard,
    )
    registry.register(
        name="click_and_drag_path",
        description="Press the left mouse button and drag through a list of waypoints ('x,y;x,y;…', up to 12), easing between them — for sliders, drawing apps and map pans that one straight drag can't follow. The button is always released, and the drag stops if the user grabs the mouse. Use when a drag must bend or pass through specific points.",
        parameters={
            "type": "object",
            "properties": {
                "points": {"type": "string", "description": "Waypoints as 'x,y;x,y;…' in screen pixels, e.g. '100,200;400,200;400,500'"},
                "duration_seconds": {"type": "number", "description": "Total drag time in seconds (default 2, clamped to 0.2–20)"},
            },
            "required": ["points"],
        },
        handler=click_and_drag_path,
    )
    registry.register(
        name="scroll_until_found",
        description="Scroll the focused window down step by step, reading the screen with OCR after each scroll, until a word or phrase appears — then report where it is. Stops after the chosen number of tries or when the page stops moving, and says so honestly if the text never shows up. Use to find something further down a page or document.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The word or phrase to look for on screen"},
                "max_attempts": {"type": "integer", "description": "How many times to scroll before giving up (default 6, max 10)"},
                "scroll_amount": {"type": "integer", "description": "Wheel steps per scroll (default 5, max 10)"},
            },
            "required": ["text"],
        },
        handler=scroll_until_found,
    )
    registry.register(
        name="screen_diff",
        description="Compare the screen now against a saved baseline picture and describe what changed in words: how much of the screen changed and in which areas (top-left, centre, bottom-right…). First call with action 'save_baseline' to store the picture; the check is coarse, so tiny text edits may not register. Use to notice a dialog appearing or a page changing. Pass monitor (an index, a device name, 'main', or 'next') to scope both the baseline and the compare to one monitor; without it the primary screen is compared, and a baseline is never compared against a different monitor's picture.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["compare", "save_baseline"], "description": "'compare' (default) describes what changed; 'save_baseline' stores the current screen as the reference picture"},
                "monitor": {"type": "string", "description": "One monitor to compare (index like '2', device name, 'main'/'primary', or 'next' from the focused window's monitor). The baseline and the compare must name the same monitor; a mismatch refuses honestly instead of comparing unlike pictures"},
            },
            "required": [],
        },
        handler=screen_diff,
    )
    registry.register(
        name="focus_window_by_text",
        description="Bring forward the window whose on-screen contents contain a phrase — it looks inside windows by reading each one's screen with OCR, not just at titles, without bringing any window forward while searching. Windows are checked front to back, at most 12; exactly one matching window is focused and verified, and several matching windows are never silently resolved to the first — the result names them all and asks which one. Pass title_hint to narrow the search to windows with that text in the title. Use when the user describes a window by what's written in it rather than its title. Windows only.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The word or phrase that appears inside the wanted window"},
                "title_hint": {"type": "string", "description": "Optional: only windows with this text in the title are searched — use it to disambiguate when several windows contain the phrase"},
            },
            "required": ["text"],
        },
        handler=focus_window_by_text,
    )
    registry.register(
        name="hotkey_cheatsheet",
        description="Show the most useful keyboard shortcuts for the app currently in focus, from a built-in table covering about 15 common apps (browsers, VS Code, Office, File Explorer, Slack, Zoom and more). An app that isn't in the table gets the general Windows shortcut set, and the result says which set you're seeing. Use when the user asks what shortcuts an app has.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=hotkey_cheatsheet,
    )
    registry.register(
        name="window_pin_toggle_all",
        description="Unpin every always-on-top window at once, so nothing floats above the other windows any more — the recovery move when a pinned window traps the screen. Each window's state is read back after unpinning, and the result names the windows that were unpinned. Use when something is stuck on top of everything else. Windows only.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=window_pin_toggle_all,
    )
    registry.register(
        name="drag_file_onto",
        description="Drag a file from its folder onto another window or app: opens the file's folder in Explorer, finds the file's icon by name with UI Automation, and drags it to the centre of the named window. Use to open a file in an app that has no Open dialog. Whether the app accepts the drop is up to the app, and the result says so. Windows only.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the file to drag"},
                "window": {"type": "string", "description": "Part of the title of the window to drop the file onto"},
            },
            "required": ["path", "window"],
        },
        handler=drag_file_onto,
    )
