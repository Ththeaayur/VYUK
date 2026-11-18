"""
Platform-backend interface for VYUK (v5.45, P-3) — the seam.

Every OS-specific surface VYUK's hands and eyes use goes through
ONE backend object, obtained from core.platform.get_backend().
The interface covers exactly four areas:

  1. input         — keyboard typing + hotkeys, mouse move /
                     click / scroll / drag, cursor geometry.
  2. windows       — foreground window, window list / find /
                     focus / close, process-name of a window.
  3. accessibility — UIA tree read, control find / click / type,
                     focused element + its IsPassword verdict.
  4. screenshot    — screen capture + capture geometry.

Behavioural contracts (every backend, every platform — see
PORTING.md for the full text):

  * Tool-shaped methods return the SAME human-readable strings
    the pre-seam modules returned, byte-for-byte. A backend must
    never reword a receipt.
  * Unknown is unknown: a probe that cannot determine its answer
    returns None (or, for is_password_control, None) — never a
    guessed False / "" / empty success. In particular an
    unreadable IsPassword property reads UNKNOWN (None), never
    False: core/input_guard.py turns UNKNOWN into a refusal, and
    a backend that coerced it to False would silently open the
    password gate.
  * Fail soft, never raise, exactly where the pre-seam code
    failed soft. The only methods allowed to raise are the ones
    whose pre-seam originals raised (enum_windows off-Windows);
    callers of those already guard.
  * Import-safe on every OS: importing this package, and
    obtaining a backend, must work headless with none of the
    platform libraries (pywinauto, PIL) present.

This module is an ABC: a backend that does not implement every
method below cannot be instantiated. Signatures mirror the
pre-seam functions exactly (same names where the original was
public, same defaults), so delegation is a one-line call.
"""

from __future__ import annotations

import abc

# Area name -> the method names a backend must provide in it.
# (The moved pure geometry helpers — clamp / to_pixels /
# norm1000_to_pixels / fit_within — are additional attributes on
# the Windows backend, documented in PORTING.md; they are part of
# the input/screenshot geometry surface but are plain callables,
# not abstract methods, because their implementation is shared
# verbatim with the legacy modules.)
AREAS: dict[str, tuple[str, ...]] = {
    "input": (
        "type_text",
        "press_hotkey",
        "mouse_control",
        "cursor_control",
        "get_cursor_position",
        "move_cursor",
        "click_cursor",
        "scroll_cursor",
        "drag_cursor",
        "fence_point",
        "screen_size",
        "virtual_bounds",
    ),
    "windows": (
        "foreground_title",
        "get_active_window",
        "window_control",
        "enum_windows",
        "foreground_window_rect",
        "foreground_process_name",
        "close_application",
    ),
    "accessibility": (
        "uia_read_tree",
        "uia_find_control",
        "uia_click_control",
        "uia_type_text",
        "describe_point",
        "focused_element",
        "is_password_control",
        "focused_control_label",
        "desktop_snapshot",
    ),
    "screenshot": (
        "capture_screen",
        "capture_native_image",
        "last_capture_info",
        "capture_geometry_line",
        "take_screenshot",
        "virtual_screen_bounds",
    ),
}

ALL_METHODS: tuple[str, ...] = tuple(
    name for methods in AREAS.values() for name in methods
)


class PlatformBackend(abc.ABC):
    """The platform-backend seam. One instance per process,
    obtained (cached) from core.platform.get_backend().

    Class attributes a backend must set:
      name               — backend id, e.g. "windows".
      requested_platform — the normalised platform the backend
                           was selected for ("windows" / "darwin"
                           / "linux" / the raw sys.platform value).
    """

    name: str = ""
    requested_platform: str = ""

    # ------------------------------------------------------------------
    # Area 1 — input (core/desktop/cursor.py + core/desktop/control.py hands)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def type_text(self, text: str) -> str:
        """Type `text` into the currently focused application."""

    @abc.abstractmethod
    def press_hotkey(self, keys: str) -> str:
        """Press a single key or '+'-separated combo, e.g. 'ctrl+c'."""

    @abc.abstractmethod
    def mouse_control(self, action: str, x: int = None, y: int = None,
                      amount: int = 3) -> str:
        """Legacy mouse hand: move/click/double_click/right_click/scroll."""

    @abc.abstractmethod
    def cursor_control(self, action: str = "", x=None, y=None, fx=None,
                       fy=None, direction: str = "", pixels=None,
                       amount=None, duration=None, corner: str = "",
                       button: str = "", coords: str = None) -> str:
        """Dispatch the cursor_control tool's actions.

        coords defaults to None (v5.49, programme item 30): the
        sentinel flows through to cursor.cursor_control, where an
        omitted coords now resolves to norm1000 for absolute
        targets — the same default the tool itself has. (Pre-v5.49
        this ABC pinned "pixels", silently defeating the flip for
        backend-routed calls.)"""

    @abc.abstractmethod
    def get_cursor_position(self) -> str:
        """Report the cursor position in true pixels + screen fraction."""

    @abc.abstractmethod
    def move_cursor(self, x=None, y=None, fx=None, fy=None, relative=None,
                    duration: float = 0.6) -> str:
        """Glide the cursor smoothly to a pixel/fraction/relative target."""

    @abc.abstractmethod
    def click_cursor(self, button: str = "left", count: int = 1, x=None,
                     y=None, fx=None, fy=None) -> str:
        """Click at the current position, or glide to a target first."""

    @abc.abstractmethod
    def scroll_cursor(self, amount, x=None, y=None,
                      horizontal: bool = False) -> str:
        """Scroll at the current position, or at a target point."""

    @abc.abstractmethod
    def drag_cursor(self, x1, y1, x2, y2, duration: float = 0.8) -> str:
        """Drag from (x1, y1) to (x2, y2), left button held."""

    @abc.abstractmethod
    def fence_point(self, x, y, bounds=None):
        """Clamp (x, y) into the virtual-screen bounds; unknown
        bounds pass the point through; non-numeric input passes
        through untouched for the caller's validation."""

    @abc.abstractmethod
    def screen_size(self):
        """(width, height) in true pixels, or None if unknown."""

    @abc.abstractmethod
    def virtual_bounds(self):
        """(min_x, min_y, max_x, max_y) inclusive virtual-screen
        bounds (secondary monitors may be negative), or None."""

    # ------------------------------------------------------------------
    # Area 2 — windows (core/desktop/control.py + core/desktop/uia.py helpers)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def foreground_title(self):
        """Title of the foreground window (str), or None when it
        cannot be determined — None is 'unknown', never ''."""

    @abc.abstractmethod
    def get_active_window(self) -> str:
        """Human-readable active-window sentence (tool-shaped)."""

    @abc.abstractmethod
    def window_control(self, action: str, title_substring: str = "") -> str:
        """list/focus/minimize/maximize/close windows by title."""

    @abc.abstractmethod
    def enum_windows(self):
        """[(hwnd, title)] for visible titled windows. This is
        the one method allowed to raise off its home platform —
        exactly as the pre-seam original did; guarded callers
        (window_control) check the platform first."""

    @abc.abstractmethod
    def foreground_window_rect(self):
        """(left, top, right, bottom) of the foreground window in
        virtual-screen pixels, or None when unknown."""

    @abc.abstractmethod
    def foreground_process_name(self):
        """Executable name of the foreground window's process
        (str), or None/''-equivalent unknown per the original —
        the Windows implementation returns whatever
        skills_window_control._foreground_process_name returns."""

    @abc.abstractmethod
    def close_application(self, name: str, confirm: bool = False) -> str:
        """Gracefully terminate processes whose name matches."""

    # ------------------------------------------------------------------
    # Area 3 — accessibility (core/desktop/uia.py)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def uia_read_tree(self, window_title: str = "", max_depth: int = 3,
                      max_nodes: int = 80) -> str:
        """Read a window's accessibility control tree (bounded)."""

    @abc.abstractmethod
    def uia_find_control(self, name: str, window_title: str = "",
                         control_type: str = "") -> str:
        """Find controls by name; report name/class/type/rect."""

    @abc.abstractmethod
    def uia_click_control(self, name: str, window_title: str = "",
                          control_type: str = "") -> str:
        """Click a control by name via the accessibility tree."""

    @abc.abstractmethod
    def uia_type_text(self, name: str, text: str, window_title: str = "",
                      clear_first: bool = True) -> str:
        """Type into a named field. The typed text is never echoed
        in the result and never logged (it may be a password)."""

    @abc.abstractmethod
    def describe_point(self, x: int, y: int):
        """Descriptor of the element at a screen point (str), or
        None when nothing can be identified. Never raises."""

    @abc.abstractmethod
    def focused_element(self):
        """The accessibility wrapper for the element holding
        keyboard focus, or None. Never raises. This is the seam
        core/input_guard.py builds the shared password rule on —
        a backend must keep it patch-compatible in behaviour."""

    @abc.abstractmethod
    def is_password_control(self, el):
        """True / False from the element's IsPassword property;
        None when the property cannot be determined. None is
        UNKNOWN — it must never be coerced to False."""

    @abc.abstractmethod
    def focused_control_label(self):
        """'"Name" (Type)' label of the focused control, or None."""

    @abc.abstractmethod
    def desktop_snapshot(self):
        """{"fg": title|None, "focus": label|None}, or None when
        nothing could be observed at all. Never raises."""

    # ------------------------------------------------------------------
    # Area 4 — screenshot (core/desktop/vision.py VisionManager + control)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def capture_screen(self, max_dim: int = 1024, quality: int = 80,
                       include_cursor: bool = False,
                       mark: tuple[int, int] | None = None,
                       full_screen: bool = False):
        """(jpeg_bytes, mime) of the screen (foreground-window
        crop by default on the Windows implementation). Records
        its geometry for last_capture_info(). May raise whatever
        the grab raises — capture failure is the caller's
        sentence to phrase, as before the seam."""

    @abc.abstractmethod
    def capture_native_image(self, full_screen: bool = False):
        """(PIL Image, meta) full-resolution frame + geometry meta."""

    @abc.abstractmethod
    def last_capture_info(self):
        """Geometry dict of the most recent capture_screen, or
        None when no capture has happened yet."""

    @abc.abstractmethod
    def capture_geometry_line(self, info: dict | None = None) -> str:
        """One sentence stating a capture's coordinate contract;
        "" when no capture geometry is known."""

    @abc.abstractmethod
    def take_screenshot(self, save_path: str = "", delay_seconds: float = 0,
                        include_cursor: bool = False) -> str:
        """Tool-shaped screenshot: capture, optionally save."""

    @abc.abstractmethod
    def virtual_screen_bounds(self):
        """(left, top, right, bottom) of the whole virtual
        desktop, or None when it cannot be determined."""
