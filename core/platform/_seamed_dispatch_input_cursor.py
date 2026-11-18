from __future__ import annotations

from . import _common  # INTENTIONAL (B29: mixin methods resolve _common.* by bare name)
from .base import PlatformBackend  # INTENTIONAL (B29: mixin base class)
from ._seamed_helpers import _CURSOR_ACTIONS  # INTENTIONAL (B29: mixin methods resolve this by bare name)


class SeamedBackendDispatchInputCursorMixin(PlatformBackend):
    """SeamedBackend methods: seam dispatch + input/cursor (old _seamed.py L74-407)."""

    def __init__(self, requested_platform: str = "",
                 seams: dict | None = None) -> None:
        self.requested_platform = requested_platform or self.name
        self.seams: dict = dict(seams or {})
        self.clamp = _common._clamp
        self.to_pixels = _common._to_pixels
        self.norm1000_to_pixels = _common._norm1000_to_pixels
        self.fit_within = _common._fit_within
        self._cursor = (0, 0)
        self._last_capture_info = None

    # -- seam helpers ------------------------------------------------

    def _seam(self, area: str):
        seam = self.seams.get(area)
        return seam if seam is not None else None

    def _call(self, area: str, method: str, *args, **kw):
        """Call seams[area].method(*args) (object attribute or
        dict entry); returns (True, result) or (False, None)
        when the seam/method is absent. Exceptions propagate —
        a broken seam is a test bug, not a soft failure."""
        seam = self._seam(area)
        if seam is None:
            return False, None
        fn = seam.get(method) if isinstance(seam, dict) else getattr(seam, method, None)
        if not callable(fn):
            return False, None
        return True, fn(*args, **kw)

    # -- subclass OS hooks (default: nothing real is reachable) -----

    def _os_input(self, method: str, *args, **kw):
        """(handled, result) for a real-OS input attempt."""
        return False, None

    def _os_windows(self, method: str, *args, **kw):
        return False, None

    def _os_a11y(self, method: str, *args, **kw):
        return False, None

    def _os_grab(self, full_screen: bool = False):
        """(handled, PIL Image | None) for a real-OS capture."""
        return False, None

    def _session_block(self, area: str) -> str | None:
        """An honest unavailable sentence for session reasons
        (e.g. Linux/Wayland), or None when the session allows
        the area. Checked before seams? No — seams are how tests
        fake the OS, so seams win; this gates only real-OS use."""
        return None

    # ------------------------------------------------------------------
    # Area 1 — input
    # ------------------------------------------------------------------

    def type_text(self, text: str) -> str:
        if text is None:
            return "Nothing to type: no text was given."
        text = str(text)
        if not text:
            return "Nothing to type: text was empty."
        verdict = self.is_password_control(self.focused_element())
        if verdict is True:
            return ("That's a password field — passwords are yours "
                    "to type, I won't.")
        if verdict is None and self.focused_element() is not None:
            return ("I can't verify the focused field isn't a "
                    "password field, so I won't type into it.")
        handled, result = self._call("input", "type_text", text)
        if handled:
            return result
        handled, result = self._os_input("type_text", text)
        if handled:
            return result
        return self._session_block("input") or self._input_unavailable

    def press_hotkey(self, keys: str) -> str:
        if not keys or not str(keys).strip():
            return "No keys given. Example: 'ctrl+c', 'alt+tab' or 'enter'."
        parts = [p.strip().lower() for p in str(keys).split("+") if p.strip()]
        if not parts:
            return "No keys given. Example: 'ctrl+c', 'alt+tab' or 'enter'."
        handled, result = self._call("input", "press_hotkey", "+".join(parts))
        if handled:
            return result
        handled, result = self._os_input("press_hotkey", "+".join(parts))
        if handled:
            return result
        return self._session_block("input") or self._input_unavailable

    def mouse_control(self, action: str, x: int = None, y: int = None,
                      amount: int = 3) -> str:
        action = (action or "").strip().lower()
        valid = ("move", "click", "double_click", "right_click", "scroll")
        if action not in valid:
            return (f"Unknown mouse action: '{action}'. Use one of: "
                    f"{', '.join(valid)}.")
        if (x is None) != (y is None):
            return ("Mouse actions need both x and y coordinates (or "
                    f"neither) — got only {'x' if x is not None else 'y'}, "
                    "so nothing was done.")
        if x is not None and y is not None:
            x, y = self.fence_point(int(x), int(y))
            self._cursor = (x, y)
        elif action == "move":
            return "Mouse 'move' needs both x and y coordinates."
        handled, result = self._call("input", "mouse", action,
                                     x=x, y=y, amount=amount)
        if handled:
            return result
        handled, result = self._os_input("mouse", action, x=x, y=y,
                                         amount=amount)
        if handled:
            return result
        return self._session_block("input") or self._input_unavailable

    def cursor_control(self, action: str = "", x=None, y=None, fx=None,
                       fy=None, direction: str = "", pixels=None,
                       amount=None, duration=None, corner: str = "",
                       button: str = "", coords: str = None) -> str:
        act = (action or "").strip().lower()
        if act not in _CURSOR_ACTIONS:
            return (f"Unknown cursor action: '{action}'. Use one of: "
                    f"{', '.join(_CURSOR_ACTIONS)}.")
        mode = (coords or "norm1000").strip().lower()
        if act == "move_by" and not str(coords or "").strip():
            mode = "pixels"
        if mode not in ("pixels", "norm1000"):
            return (f"Unknown coords mode: '{coords}'. Use 'norm1000' "
                    f"(the default — 0..1000 normalized coordinates read "
                    f"from a screenshot) or 'pixels' (true screen pixels).")
        if mode == "norm1000" and act in (
                "move", "click", "double_click", "right_click",
                "middle_click", "scroll", "drag", "beacon") \
                and (x is not None or y is not None):
            if x is None or y is None:
                return "A norm1000 target needs both x and y (each 0..1000)."
            size = self.screen_size()
            if size is None:
                return ("Could not determine the screen size, so "
                        "norm1000 coordinates cannot be converted to "
                        "pixels here.")
            x, y = self.norm1000_to_pixels(x, y, size[0], size[1])
        if act == "position":
            return self.get_cursor_position()
        if act == "move":
            if x is None or y is None:
                return "Cursor 'move' needs both x and y (pixels)."
            return self.move_cursor(x=x, y=y,
                                    duration=0.6 if duration is None else duration)
        if act == "move_fraction":
            if fx is None or fy is None:
                return "Cursor 'move_fraction' needs both fx and fy (0..1)."
            return self.move_cursor(fx=fx, fy=fy,
                                    duration=0.6 if duration is None else duration)
        if act == "move_by":
            if x is None and y is None:
                return "Cursor 'move_by' needs x and/or y as the pixel delta (dx, dy)."
            return self.move_cursor(relative={"dx": x or 0, "dy": y or 0},
                                    duration=0.6 if duration is None else duration)
        if act == "nudge":
            if not direction:
                return ("Cursor 'nudge' needs a direction (up, down, "
                        "left, right, ne, nw, se, sw).")
            step = 40 if pixels is None else int(pixels)
            deltas = {"up": (0, -step), "down": (0, step),
                      "left": (-step, 0), "right": (step, 0),
                      "ne": (step, -step), "nw": (-step, -step),
                      "se": (step, step), "sw": (-step, step)}
            delta = deltas.get(direction.strip().lower())
            if delta is None:
                return (f"Unknown nudge direction: '{direction}'. Use up, "
                        "down, left, right, ne, nw, se or sw.")
            return self.move_cursor(relative={"dx": delta[0], "dy": delta[1]},
                                    duration=0.25)
        if act in ("click", "double_click", "right_click", "middle_click"):
            mapping = {"click": (button or "left", 1),
                       "double_click": ("left", 2),
                       "right_click": ("right", 1),
                       "middle_click": ("middle", 1)}
            btn, count = mapping[act]
            return self.click_cursor(button=btn, count=count,
                                     x=x, y=y, fx=fx, fy=fy)
        if act == "scroll":
            if amount is None:
                return "Cursor 'scroll' needs an amount (-20..20; positive scrolls up)."
            return self.scroll_cursor(amount, x=x, y=y)
        if act == "drag":
            if x is None or y is None:
                return ("Cursor 'drag' needs x and y: the point to drag "
                        "TO (the drag starts at the current cursor position).")
            return self.drag_cursor(self._cursor[0], self._cursor[1], x, y,
                                    duration=0.8 if duration is None else duration)
        if act == "beacon":
            return self._session_block("input") or self._input_unavailable
        # park: needs a screen size to pick a corner
        size = self.screen_size()
        if size is None:
            return ("Could not determine the screen size for the "
                    "park corner.")
        corners = {"top_left": (0, 0), "top_right": (size[0] - 1, 0),
                   "bottom_left": (0, size[1] - 1),
                   "bottom_right": (size[0] - 1, size[1] - 1),
                   "center": (size[0] // 2, size[1] // 2)}
        target = corners.get((corner or "bottom_right").strip().lower(),
                             corners["bottom_right"])
        return self.move_cursor(x=target[0], y=target[1], duration=0.3)

    def get_cursor_position(self) -> str:
        handled, pos = self._call("input", "cursor_position")
        if not handled:
            handled, pos = self._os_input("cursor_position")
        if handled and pos is not None:
            self._cursor = (int(pos[0]), int(pos[1]))
            size = self.screen_size()
            if size:
                return (f"Cursor is at ({self._cursor[0]}, "
                        f"{self._cursor[1]}) on a {size[0]}x{size[1]} screen.")
            return f"Cursor is at ({self._cursor[0]}, {self._cursor[1]})."
        return "Could not read the cursor position on this system."

    def move_cursor(self, x=None, y=None, fx=None, fy=None, relative=None,
                    duration: float = 0.6) -> str:
        if relative is not None:
            if x is not None or y is not None or fx is not None or fy is not None:
                return ("Ambiguous cursor target: give pixels, a fraction, "
                        "or a relative delta — not a mix.")
            handled, pos = self._call("input", "cursor_position")
            base = pos if handled and pos is not None else self._cursor
            tx = int(base[0]) + int(relative.get("dx", 0))
            ty = int(base[1]) + int(relative.get("dy", 0))
        elif fx is not None or fy is not None:
            if fx is None or fy is None or x is not None or y is not None:
                return ("Ambiguous cursor target: give pixels, a fraction, "
                        "or a relative delta — not a mix.")
            size = self.screen_size()
            if size is None:
                return "Could not determine the screen size for a fractional move."
            tx, ty = self.to_pixels(fx, fy, size[0], size[1])
        elif x is not None and y is not None:
            tx, ty = int(x), int(y)
        else:
            return "Cursor move needs a target: x/y, fx/fy, or a relative delta."
        tx, ty = self.fence_point(tx, ty)
        handled, result = self._call("input", "move", tx, ty)
        if not handled:
            handled, result = self._os_input("move", tx, ty)
        if handled:
            self._cursor = (tx, ty)
            return result
        return self._session_block("input") or self._input_unavailable

    def click_cursor(self, button: str = "left", count: int = 1, x=None,
                     y=None, fx=None, fy=None) -> str:
        if x is not None and y is not None:
            tx, ty = self.fence_point(int(x), int(y))
        elif fx is not None and fy is not None:
            size = self.screen_size()
            if size is None:
                return "Could not determine the screen size for a fractional click."
            tx, ty = self.fence_point(*self.to_pixels(fx, fy, size[0], size[1]))
        else:
            tx, ty = self._cursor
        handled, result = self._call("input", "click", button,
                                     int(count), tx, ty)
        if not handled:
            handled, result = self._os_input("click", button,
                                             int(count), tx, ty)
        if handled:
            self._cursor = (tx, ty)
            return result
        return self._session_block("input") or self._input_unavailable

    def scroll_cursor(self, amount, x=None, y=None,
                      horizontal: bool = False) -> str:
        try:
            amt = int(amount)
        except (TypeError, ValueError):
            return "Scroll amount must be a number (-20..20)."
        amt = max(-20, min(20, amt))
        if amt == 0:
            return "Scroll amount is 0 — nothing scrolled."
        if x is not None and y is not None:
            self._cursor = self.fence_point(int(x), int(y))
        handled, result = self._call("input", "scroll", amt,
                                     self._cursor[0], self._cursor[1],
                                     bool(horizontal))
        if not handled:
            handled, result = self._os_input("scroll", amt,
                                             self._cursor[0],
                                             self._cursor[1],
                                             bool(horizontal))
        if handled:
            return result
        return self._session_block("input") or self._input_unavailable

    def drag_cursor(self, x1, y1, x2, y2, duration: float = 0.8) -> str:
        sx, sy = self.fence_point(int(x1), int(y1))
        tx, ty = self.fence_point(int(x2), int(y2))
        handled, result = self._call("input", "drag", sx, sy, tx, ty)
        if not handled:
            handled, result = self._os_input("drag", sx, sy, tx, ty)
        if handled:
            self._cursor = (tx, ty)
            return result
        return self._session_block("input") or self._input_unavailable

    def fence_point(self, x, y, bounds=None):
        return _common.fence_point(x, y, bounds=bounds,
                                   virtual_bounds=self.virtual_bounds)

    def screen_size(self):
        handled, size = self._call("screenshot", "screen_size")
        if not handled:
            handled, size = self._call("input", "screen_size")
        if handled and size:
            return (int(size[0]), int(size[1]))
        handled, size = self._os_windows("screen_size")
        if handled and size:
            return (int(size[0]), int(size[1]))
        return None

    def virtual_bounds(self):
        handled, bounds = self._call("screenshot", "virtual_bounds")
        if not handled:
            handled, bounds = self._call("input", "virtual_bounds")
        if handled and bounds:
            return tuple(int(v) for v in bounds)
        size = self.screen_size()
        if size is None:
            return None
        return (0, 0, size[0] - 1, size[1] - 1)
