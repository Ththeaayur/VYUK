"""
Windows implementation of the platform-backend seam (v5.45, P-3).

This is the ONLY backend that exists today, and it is the code
the app has always run: the pre-seam modules (core/desktop/cursor.py,
core/desktop/control.py, core/desktop/uia.py, core/desktop/vision.py) are themselves
import-safe and fail-soft off-Windows, so this backend is
selected on every platform for now — on a non-Windows host its
methods degrade exactly as those modules degrade (clear
'unavailable / only on Windows' strings, None for unknown
probes). A future native macOS/Linux backend registers
alongside it in core/platform/__init__.py; see PORTING.md.

MOVED vs DELEGATED — the honest ledger:

MOVED (physically, bodies live below; the original modules
re-export the very same callables, so the legacy import paths
`core.desktop.cursor._clamp`, `core.desktop.cursor._to_pixels`,
`core.desktop.cursor._norm1000_to_pixels` and `core.desktop.vision._fit_within`
return these exact function objects):
  * _clamp, _to_pixels, _norm1000_to_pixels  (cursor geometry)
  * _fit_within                              (vision geometry)
These four are pure functions with no module-state dependencies
— the only bodies in the four source modules for which a move
is purely mechanical.

DELEGATED (everything else): every other backend method is a
one-line call into the existing function, resolved through the
module attribute at call time (so the test suites' module-level
seams — cursor._mouse_controller, uia._point_element,
control._password_block, vision.vision_manager and friends —
keep working exactly as before). The remaining bodies are NOT
moved because they are intertwined with those patchable
module globals: e.g. cursor._fence_point reads
cursor._virtual_bounds through its own module namespace, and
moving it would silently stop honouring a patched
_virtual_bounds. Delegation keeps behaviour byte-identical
while still giving callers exactly one seam: this backend.
"""

from __future__ import annotations

import sys

from .base import PlatformBackend

# ---------------------------------------------------------------------------
# MOVED — pure geometry helpers (verbatim bodies)
#
# _clamp / _to_pixels / _norm1000_to_pixels were core/cursor.py's;
# _fit_within was core/desktop/vision.py's. Both modules now import these
# names back from here, so there is still exactly ONE body each.
# ---------------------------------------------------------------------------


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _to_pixels(fx: float, fy: float, w: int, h: int):
    """Convert screen fractions (0..1) to pixel coordinates (pure)."""
    return int(round(float(fx) * (w - 1))), int(round(float(fy) * (h - 1)))


def _norm1000_to_pixels(nx, ny, w: int, h: int):
    """Convert a 0..1000 normalized point to true pixels (pure).

    The convention vision models are trained on (v5.43, E10):
    (0, 0) is the image's top-left corner, (1000, 1000) its
    bottom-right. Mapped through the same (dim - 1) scaling as
    _to_pixels, so norm1000 (500, 500) lands exactly where
    move_fraction (0.5, 0.5) does. Out-of-range values clamp to
    the nearest edge — a model overshooting by a few units still
    lands on the edge control it meant, and the bounds fence in
    _resolve_target has the final say as always."""
    fx = _clamp(float(nx), 0.0, 1000.0) / 1000.0
    fy = _clamp(float(ny), 0.0, 1000.0) / 1000.0
    return _to_pixels(fx, fy, int(w), int(h))


def _fit_within(width: int, height: int, max_dim: int) -> tuple[int, int]:
    """The size PIL's thumbnail((max_dim, max_dim)) produces for a
    width x height image (pure): never enlarges, preserves aspect
    ratio, rounds like PIL does. Mirrors the real thumbnail so
    callers can state the sent size without decoding the JPEG —
    capture_screen itself records the ACTUAL post-thumbnail size;
    this helper exists for tests and honest fallbacks."""
    w, h, cap = int(width), int(height), int(max_dim)
    if w <= 0 or h <= 0 or cap <= 0:
        return w, h
    scale = min(cap / w, cap / h, 1.0)
    if scale >= 1.0:
        return w, h
    return max(1, round(w * scale)), max(1, round(h * scale))


class WindowsBackend(PlatformBackend):
    """The Windows backend — today, the delegating front for the
    existing cursor/control/uia/vision implementations.

    `requested_platform` is the normalised platform this instance
    was selected for; it is informational only. Behaviour is
    decided by the delegated code itself, which checks the real
    sys.platform the way it always has — so running this backend
    on Linux (as the suites do) degrades exactly like the
    pre-seam app on Linux.
    """

    name = "windows"

    def __init__(self, requested_platform: str = "") -> None:
        self.requested_platform = requested_platform or (
            "windows" if sys.platform.startswith("win") else sys.platform
        )
        # The MOVED pure geometry helpers, exposed on the backend
        # as the very same callables the legacy modules re-export.
        self.clamp = _clamp
        self.to_pixels = _to_pixels
        self.norm1000_to_pixels = _norm1000_to_pixels
        self.fit_within = _fit_within

    # ------------------------------------------------------------------
    # Area 1 — input (delegated: core/desktop/control.py + core/desktop/cursor.py)
    # ------------------------------------------------------------------

    def type_text(self, text: str) -> str:
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        return control.type_text(text)

    def press_hotkey(self, keys: str) -> str:
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        return control.press_hotkey(keys)

    def mouse_control(self, action: str, x: int = None, y: int = None,
                      amount: int = 3) -> str:
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        return control.mouse_control(action, x=x, y=y, amount=amount)

    def cursor_control(self, action: str = "", x=None, y=None, fx=None,
                       fy=None, direction: str = "", pixels=None,
                       amount=None, duration=None, corner: str = "",
                       button: str = "", coords: str = None) -> str:
        # v5.49 (programme item 30): coords=None sentinel — see
        # core/platform/base.py; an explicit "pixels" still passes
        # through untouched.
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor.cursor_control(
            action, x=x, y=y, fx=fx, fy=fy, direction=direction,
            pixels=pixels, amount=amount, duration=duration,
            corner=corner, button=button, coords=coords)

    def get_cursor_position(self) -> str:
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor.get_cursor_position()

    def move_cursor(self, x=None, y=None, fx=None, fy=None, relative=None,
                    duration: float = 0.6) -> str:
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor.move_cursor(x=x, y=y, fx=fx, fy=fy,
                                  relative=relative, duration=duration)

    def click_cursor(self, button: str = "left", count: int = 1, x=None,
                     y=None, fx=None, fy=None) -> str:
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor.click_cursor(button=button, count=count, x=x, y=y,
                                   fx=fx, fy=fy)

    def scroll_cursor(self, amount, x=None, y=None,
                      horizontal: bool = False) -> str:
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor.scroll_cursor(amount, x=x, y=y, horizontal=horizontal)

    def drag_cursor(self, x1, y1, x2, y2, duration: float = 0.8) -> str:
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor.drag_cursor(x1, y1, x2, y2, duration=duration)

    def fence_point(self, x, y, bounds=None):
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor._fence_point(x, y, bounds=bounds)

    def screen_size(self):
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor._screen_size()

    def virtual_bounds(self):
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        return cursor._virtual_bounds()

    # ------------------------------------------------------------------
    # Area 2 — windows (delegated: core/desktop/control.py, core/desktop/uia.py,
    # core/desktop/vision.py rect, core/skills_window_control.py process name)
    # ------------------------------------------------------------------

    def foreground_title(self):
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia.foreground_title()

    def get_active_window(self) -> str:
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        return control.get_active_window()

    def window_control(self, action: str, title_substring: str = "") -> str:
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        return control.window_control(action, title_substring=title_substring)

    def enum_windows(self):
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        return control._enum_windows()

    def foreground_window_rect(self):
        from ..desktop import vision  # Phase 3 D4c: vision hub moved to core/desktop/

        return vision.foreground_window_rect()

    def foreground_process_name(self):
        from .. import skills_window_control

        return skills_window_control._foreground_process_name()

    def close_application(self, name: str, confirm: bool = False) -> str:
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        return control.close_application(name, confirm=confirm)

    # ------------------------------------------------------------------
    # Area 3 — accessibility (delegated: core/desktop/uia.py)
    #
    # focused_element / is_password_control are the exact seams
    # core/input_guard.py consumes (via core.desktop.uia's attributes);
    # they are delegated through the module attribute at call
    # time, so input_guard's view and this backend's view can
    # never diverge.
    # ------------------------------------------------------------------

    def uia_read_tree(self, window_title: str = "", max_depth: int = 3,
                      max_nodes: int = 80) -> str:
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia.uia_read_tree(window_title=window_title,
                                 max_depth=max_depth, max_nodes=max_nodes)

    def uia_find_control(self, name: str, window_title: str = "",
                         control_type: str = "") -> str:
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia.uia_find_control(name, window_title=window_title,
                                     control_type=control_type)

    def uia_click_control(self, name: str, window_title: str = "",
                          control_type: str = "") -> str:
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia.uia_click_control(name, window_title=window_title,
                                     control_type=control_type)

    def uia_type_text(self, name: str, text: str, window_title: str = "",
                      clear_first: bool = True) -> str:
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia.uia_type_text(name, text, window_title=window_title,
                                 clear_first=clear_first)

    def describe_point(self, x: int, y: int):
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia.describe_point(x, y)

    def focused_element(self):
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia._focused_element()

    def is_password_control(self, el):
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia._is_password_control(el)

    def focused_control_label(self):
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia._focused_control_label()

    def desktop_snapshot(self):
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia.desktop_snapshot()

    # ------------------------------------------------------------------
    # Area 4 — screenshot (delegated: core/desktop/vision.py + control.py)
    # ------------------------------------------------------------------

    def capture_screen(self, max_dim: int = 1024, quality: int = 80,
                       include_cursor: bool = False,
                       mark: tuple[int, int] | None = None,
                       full_screen: bool = False):
        from ..desktop import vision  # Phase 3 D4c: vision hub moved to core/desktop/

        return vision.vision_manager.capture_screen(
            max_dim=max_dim, quality=quality,
            include_cursor=include_cursor, mark=mark,
            full_screen=full_screen)

    def capture_native_image(self, full_screen: bool = False):
        from ..desktop import vision  # Phase 3 D4c: vision hub moved to core/desktop/

        return vision.capture_native_image(full_screen=full_screen)

    def last_capture_info(self):
        from ..desktop import vision  # Phase 3 D4c: vision hub moved to core/desktop/

        return vision.last_capture_info()

    def capture_geometry_line(self, info: dict | None = None) -> str:
        from ..desktop import vision  # Phase 3 D4c: vision hub moved to core/desktop/

        return vision.capture_geometry_line(info)

    def take_screenshot(self, save_path: str = "", delay_seconds: float = 0,
                        include_cursor: bool = False) -> str:
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/

        return control.take_screenshot(
            save_path=save_path, delay_seconds=delay_seconds,
            include_cursor=include_cursor)

    def virtual_screen_bounds(self):
        from ..desktop import vision  # Phase 3 D4c: vision hub moved to core/desktop/

        return vision._virtual_screen_bounds()
