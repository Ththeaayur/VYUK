"""
Pro-grade cursor control for VYUK — the hard, correct version of
mouse movement (registered as the single `cursor_control` tool).

core/desktop/control.py's mouse_control teleports the pointer and clicks;
this module exists because real cursor work has three failure modes
that teleporting ignores:

1. DPI scaling offset — on Windows at 125%/150% display scaling, a
   process that is not DPI-aware gets VIRTUALISED coordinates, so
   clicks land offset from where OCR / UI Automation reported them.
   _ensure_dpi_aware() fixes this once, before any cursor op.
2. Bot teleport movement — instant jumps look robotic and some apps
   mis-handle them. Moves here follow a smooth seeded-Bezier path
   with ease-in-out timing (_plan_path): slow start, fast middle,
   slow end, a slight deterministic curve, never a straight jump.
3. No way to grab control back — while VYUK drives the cursor, the
   human can always take over: before every path step the ACTUAL
   cursor position is compared with the planned one; a deviation
   over 60 px (or the cursor being pushed into the top-left
   failsafe corner) aborts the move immediately.
4. Off-screen targets (v4.3) — model-supplied coordinates can be
   stale or hallucinated, so every dispatched move/click passes a
   bounds fence (_fence_point): targets are clamped into the real
   VIRTUAL-screen bounds (all monitors stitched together — secondary
   screens can live at negative coordinates) and the move completes
   at the nearest in-bounds point instead of erroring at the OS.
   On Windows, moves and clicks are dispatched through the native
   SendInput API (the input queue real hardware uses; some apps
   ignore the legacy paths other libraries used).

Same rules as core/desktop/control.py / control_plus.py: plain functions
returning human-readable strings; tkinter is imported lazily
inside the functions that need it and guarded with
try/except, so this module imports cleanly on any OS — including
headless Linux, where unavailable features return a clear fallback
string instead of crashing.

Wiring: the parent build calls register_all(registry) from core/funnel/tools_handlers_pack_core2.py (_register_cursor_tools) — this module never touches the registry itself.
"""

import math
import os
import random
import sys
import threading
import time

# B34 INTENTIONAL: geometry-freshness family re-exported from
# core/desktop/cursor_geometry.py (part 1 of 2). Every moved name
# stays available at its legacy core.desktop.cursor path, and
# facade functions resolving these names by bare name keep
# working unchanged. The three platform.windows callables are
# re-exported through geometry (same objects as the backend).
from .cursor_geometry import (
    _is_windows,
    _routed_backend,
    _ensure_dpi_aware,
    _screen_size,
    _clamp_to_screen,
    _virtual_bounds,
    _norm1000_reference,
    _norm1000_to_screen,
    _capture_freshness_refusal,
    _fence_point,
    _DPI_AWARE_DONE,
    _ABORT_MSG,
    _TAKEOVER_TOLERANCE_PX,
    _FAILSAFE_CORNER_PX,
    _clamp,
    _norm1000_to_pixels,
    _to_pixels,
)


# B35 INTENTIONAL: cursor_input family re-exported from
# core/desktop/cursor_input.py (part 2 of 2). Every moved name stays
# available at its legacy core.desktop.cursor path, and
# callers resolving these names through the facade module
# object keep working unchanged.
from .cursor_input import (
    _mouse_controller,
    _read_position,
    _read_cursor_position,
    Button,
    _WinMouse,
    _MOUSEEVENTF_MOVE,
    _MOUSEEVENTF_LEFTDOWN,
    _MOUSEEVENTF_LEFTUP,
    _MOUSEEVENTF_RIGHTDOWN,
    _MOUSEEVENTF_RIGHTUP,
    _MOUSEEVENTF_MIDDLEDOWN,
    _MOUSEEVENTF_MIDDLEUP,
    _MOUSEEVENTF_VIRTUALDESK,
    _MOUSEEVENTF_ABSOLUTE,
    _SENDINPUT_BUTTONS,
    _sendinput_events,
    _sendinput_move,
    _sendinput_click,
    _sendinput_button_down,
    _sendinput_button_up,
    _sendinput_scroll,
    _sendinput_key,
    _VK_MAP,
    _VK_CHAR_MAP,
    _NAME_BY_VK,
    _failsafe_tripped,
    _failsafe_status,
    _FAILSAFE_MSG,
    _note_input_pressed,
    _note_input_released,
    _set_position,
    _dispatch_click,
)


# B35 INTENTIONAL: cursor_glide family re-exported from
# core/desktop/cursor_glide.py (part 2 of 2). Every moved name stays
# available at its legacy core.desktop.cursor path, and
# callers resolving these names through the facade module
# object keep working unchanged.
from .cursor_glide import (
    _ease_in_out,
    _plan_path,
    _follow_path,
    _glide,
    _resolve_target,
)


# B35 INTENTIONAL: cursor_ops family re-exported from
# core/desktop/cursor_ops.py (part 2 of 2). Every moved name stays
# available at its legacy core.desktop.cursor path, and
# callers resolving these names through the facade module
# object keep working unchanged.
from .cursor_ops import (
    get_cursor_position,
    move_cursor,
    _NUDGE_VECTORS,
    nudge_cursor,
    _BUTTON_NAMES,
    _identify_click_point,
    _focus_change_note,
    click_cursor,
    scroll_cursor,
    drag_cursor,
    _BEACON_COLOUR,
    show_beacon,
    _PARK_TARGETS,
    park_cursor,
)


# B35 INTENTIONAL: cursor_control family re-exported from
# core/desktop/cursor_control.py (part 2 of 2). Every moved name stays
# available at its legacy core.desktop.cursor path, and
# callers resolving these names through the facade module
# object keep working unchanged.
from .cursor_control import (
    _ACTIONS,
    cursor_control,
    register_all,
)
