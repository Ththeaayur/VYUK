from __future__ import annotations

from . import _common  # INTENTIONAL (B28: mixin methods resolve _common.* by bare name)
from .base import PlatformBackend  # INTENTIONAL (B28: mixin base class)
from .fake_helpers import (  # INTENTIONAL (B28: mixin methods resolve these by bare name)
    FakeElement,
    FakeWindow,
    _CURSOR_ACTIONS,
    _SCREEN_H,
    _SCREEN_W,
    _TEXT_TYPES,
)


class FakeBackendInputCursorMixin(PlatformBackend):
    def __init__(self, requested_platform: str = "fake",
                 script: dict | None = None) -> None:
        self.requested_platform = requested_platform or "fake"
        self.clamp = _common._clamp
        self.to_pixels = _common._to_pixels
        self.norm1000_to_pixels = _common._norm1000_to_pixels
        self.fit_within = _common._fit_within
        self.windows: list[FakeWindow] = []
        self._next_hwnd = 1000
        self._foreground_hwnd = None
        self._focused: FakeElement | None = None
        self._cursor = (0, 0)
        self.input_log: list[dict] = []
        self.state: dict = {}
        self._hotkey_reactions: dict[str, object] = {}
        self._click_reactions: dict[str, object] = {}
        self._last_capture_info = None
        if script:
            self.load_script(script)

    # ------------------------------------------------------------------
    # Scripting API (not part of the ABC)
    # ------------------------------------------------------------------

    def load_script(self, script: dict) -> None:
        """Load {"windows": [{title, rect, process, controls:
        [element-dict, ...]}], "foreground": index}."""
        for spec in script.get("windows", []):
            hwnd = self.add_window(
                spec.get("title", "Window"),
                rect=spec.get("rect", (0, 0, 800, 600)),
                process=spec.get("process", "fake.exe"))
            for ctl in spec.get("controls", []):
                self.add_control(hwnd, **ctl)
        fg = script.get("foreground")
        if fg is not None and self.windows:
            self.set_foreground(self.windows[int(fg)].hwnd)

    def add_window(self, title, rect=(0, 0, 800, 600),
                   process="fake.exe") -> int:
        hwnd = self._next_hwnd
        self._next_hwnd += 1
        win = FakeWindow(hwnd, title, rect, process)
        self.windows.append(win)
        if self._foreground_hwnd is None:
            self._foreground_hwnd = hwnd
        return hwnd

    def add_control(self, hwnd, name, control_type="Button",
                    class_name="", rect=(0, 0, 100, 30), enabled=True,
                    visible=True, is_password=None, value="",
                    parent: FakeElement | None = None) -> FakeElement:
        el = FakeElement(name, control_type, class_name, rect,
                         enabled, visible, is_password, value)
        win = self._window_by_hwnd(hwnd)
        el.window = win
        if parent is not None:
            parent.children.append(el)
        elif win is not None:
            win.controls.append(el)
        return el

    def close_window(self, hwnd) -> None:
        win = self._window_by_hwnd(hwnd)
        if win is None:
            return
        self.windows.remove(win)
        if self._focused is not None and self._focused.window is win:
            self._focused = None
        if self._foreground_hwnd == hwnd:
            self._foreground_hwnd = (
                self.windows[-1].hwnd if self.windows else None)

    def set_foreground(self, hwnd) -> None:
        if self._window_by_hwnd(hwnd) is not None:
            self._foreground_hwnd = hwnd

    def set_focus(self, element: FakeElement | None) -> None:
        self._focused = element
        if element is not None and element.window is not None:
            self._foreground_hwnd = element.window.hwnd

    def script_hotkey(self, keys: str, reaction) -> None:
        self._hotkey_reactions[str(keys).strip().lower()] = reaction

    def script_click(self, name: str, reaction) -> None:
        self._click_reactions[str(name)] = reaction

    def _window_by_hwnd(self, hwnd):
        for win in self.windows:
            if win.hwnd == hwnd:
                return win
        return None

    def _foreground_window(self):
        return self._window_by_hwnd(self._foreground_hwnd)

    def _record(self, method: str, **fields) -> None:
        entry = {"method": method}
        entry.update(fields)
        self.input_log.append(entry)

    def _all_elements(self, win: FakeWindow):
        out = []

        def _walk(el):
            out.append(el)
            for child in el.children:
                _walk(child)

        for el in win.controls:
            _walk(el)
        return out

    def _resolve_window(self, window_title: str = ""):
        """(window, description) mirroring core/desktop/uia.py's
        _uia_window contract; raises LookupError when a titled
        window cannot be found / is ambiguous."""
        title = str(window_title or "")
        if not title:
            win = self._foreground_window()
            if win is None:
                raise LookupError("no foreground window")
            return win, "the foreground window"
        matches = [w for w in self.windows
                   if title.lower() in w.title.lower()]
        if not matches:
            raise LookupError(f"no window matching '{title}'")
        if len(matches) > 1:
            raise LookupError(
                f"more than one window matches '{title}'")
        return matches[0], f"window matching '{title}'"

    def _find(self, win, name, control_type=""):
        needle = str(name).strip().lower()
        type_needle = str(control_type or "").strip().lower()
        out = []
        for el in self._all_elements(win):
            if not el.name or needle not in el.name.lower():
                continue
            if type_needle and type_needle not in el.control_type.lower():
                continue
            out.append(el)
        return out

    def _click_element(self, el: FakeElement) -> None:
        """Apply a click to a scripted element: record handled by
        the caller; focus moves to it; the built-in dialog rule
        and any scripted click reaction run."""
        self._focused = el
        if el.window is not None:
            self._foreground_hwnd = el.window.hwnd
        reaction = self._click_reactions.get(el.name)
        if reaction is not None:
            reaction(self, el)
            return
        if el.name in ("Save", "Cancel") and el.window is not None:
            if el.name == "Save":
                self.state["saved"] = True
            self.close_window(el.window.hwnd)

    # ------------------------------------------------------------------
    # Area 1 — input
    # ------------------------------------------------------------------

    def type_text(self, text: str) -> str:
        if text is None:
            return "Nothing to type: no text was given."
        text = str(text)
        if not text:
            return "Nothing to type: text was empty."
        # Password gate, mirroring core/input_guard.py's rule on
        # the scripted focused element: True refuses, UNKNOWN
        # refuses, only a scripted False proceeds.
        verdict = self.is_password_control(self._focused)
        if verdict is True:
            return ("That's a password field — passwords are yours "
                    "to type, I won't.")
        if verdict is None and self._focused is not None:
            return ("I can't verify the focused field isn't a "
                    "password field, so I won't type into it.")
        self._record("type_text", text=text)
        if self._focused is not None and \
                self._focused.control_type in _TEXT_TYPES:
            self._focused.value += text
        fg = self.foreground_title()
        result = f"Typed {len(text)} character(s) into the focused app."
        if fg:
            result += f" It went to: {fg}."
        return result

    def press_hotkey(self, keys: str) -> str:
        if not keys or not str(keys).strip():
            return "No keys given. Example: 'ctrl+c', 'alt+tab' or 'enter'."
        parts = [p.strip().lower() for p in str(keys).split("+") if p.strip()]
        if not parts:
            return "No keys given. Example: 'ctrl+c', 'alt+tab' or 'enter'."
        combo = "+".join(parts)
        self._record("press_hotkey", keys=combo)
        reaction = self._hotkey_reactions.get(combo)
        if reaction is not None:
            reaction(self)
        return f"Pressed hotkey: {combo}"

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
        self._record("mouse_control", action=action, x=x, y=y,
                     amount=amount)
        if action == "move":
            return f"Moved mouse to ({x}, {y})."
        if action == "scroll":
            return f"Scrolled {'up' if amount >= 0 else 'down'} by {abs(amount)} at ({self._cursor[0]}, {self._cursor[1]})."
        # click family: hit-test the scripted tree
        hit = self._element_at(*self._cursor)
        if hit is not None:
            self._click_element(hit)
            return (f"Clicked ({action}) at ({self._cursor[0]}, "
                    f"{self._cursor[1]}): '{hit.name}' "
                    f"({hit.control_type}).")
        return f"Clicked ({action}) at ({self._cursor[0]}, {self._cursor[1]})."

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
            return self.drag_cursor(self._cursor[0], self._cursor[1],
                                    x, y,
                                    duration=0.8 if duration is None else duration)
        if act == "beacon":
            return f"Beacon shown at ({self._cursor[0]}, {self._cursor[1]})."
        # park
        corners = {"top_left": (0, 0), "top_right": (_SCREEN_W - 1, 0),
                   "bottom_left": (0, _SCREEN_H - 1),
                   "bottom_right": (_SCREEN_W - 1, _SCREEN_H - 1),
                   "center": (_SCREEN_W // 2, _SCREEN_H // 2)}
        target = corners.get((corner or "bottom_right").strip().lower(),
                             corners["bottom_right"])
        return self.move_cursor(x=target[0], y=target[1], duration=0.3)

    def get_cursor_position(self) -> str:
        x, y = self._cursor
        return (f"Cursor is at ({x}, {y}) on a {_SCREEN_W}x{_SCREEN_H} "
                f"screen ({x / (_SCREEN_W - 1) * 100:.1f}% across, "
                f"{y / (_SCREEN_H - 1) * 100:.1f}% down).")

    def move_cursor(self, x=None, y=None, fx=None, fy=None, relative=None,
                    duration: float = 0.6) -> str:
        if relative is not None:
            if x is not None or y is not None or fx is not None or fy is not None:
                return ("Ambiguous cursor target: give pixels, a fraction, "
                        "or a relative delta — not a mix.")
            tx = self._cursor[0] + int(relative.get("dx", 0))
            ty = self._cursor[1] + int(relative.get("dy", 0))
        elif fx is not None or fy is not None:
            if fx is None or fy is None or x is not None or y is not None:
                return ("Ambiguous cursor target: give pixels, a fraction, "
                        "or a relative delta — not a mix.")
            tx, ty = self.to_pixels(fx, fy, _SCREEN_W, _SCREEN_H)
        elif x is not None and y is not None:
            tx, ty = int(x), int(y)
        else:
            return "Cursor move needs a target: x/y, fx/fy, or a relative delta."
        tx, ty = self.fence_point(tx, ty)
        self._cursor = (tx, ty)
        self._record("move_cursor", x=tx, y=ty)
        return f"Moved cursor to ({tx}, {ty})."

    def click_cursor(self, button: str = "left", count: int = 1, x=None,
                     y=None, fx=None, fy=None) -> str:
        if x is not None and y is not None:
            tx, ty = self.fence_point(int(x), int(y))
            self._cursor = (tx, ty)
        elif fx is not None and fy is not None:
            tx, ty = self.to_pixels(fx, fy, _SCREEN_W, _SCREEN_H)
            tx, ty = self.fence_point(tx, ty)
            self._cursor = (tx, ty)
        else:
            tx, ty = self._cursor
        self._record("click_cursor", button=button, count=int(count),
                     x=tx, y=ty)
        hit = self._element_at(tx, ty)
        if hit is not None:
            self._click_element(hit)
            return (f"Clicked '{hit.name}' ({hit.control_type}) at "
                    f"({tx}, {ty}) with the {button} button.")
        return f"Clicked at ({tx}, {ty}) with the {button} button."

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
        self._record("scroll_cursor", amount=amt,
                     x=self._cursor[0], y=self._cursor[1],
                     horizontal=bool(horizontal))
        direction = "up" if amt > 0 else "down"
        if horizontal:
            direction = "right" if amt > 0 else "left"
        return (f"Scrolled {direction} by {abs(amt)} at "
                f"({self._cursor[0]}, {self._cursor[1]}).")

    def drag_cursor(self, x1, y1, x2, y2, duration: float = 0.8) -> str:
        sx, sy = self.fence_point(int(x1), int(y1))
        tx, ty = self.fence_point(int(x2), int(y2))
        self._cursor = (tx, ty)
        self._record("drag_cursor", x1=sx, y1=sy, x2=tx, y2=ty)
        return f"Dragged from ({sx}, {sy}) to ({tx}, {ty})."

    def fence_point(self, x, y, bounds=None):
        return _common.fence_point(x, y, bounds=bounds,
                                    virtual_bounds=self.virtual_bounds)

    def screen_size(self):
        return (_SCREEN_W, _SCREEN_H)

    def virtual_bounds(self):
        return (0, 0, _SCREEN_W - 1, _SCREEN_H - 1)
