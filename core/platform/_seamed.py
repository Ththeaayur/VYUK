"""
SeamedBackend — the shared body of the EXPERIMENTAL native
backends (v5.50, programme item 42): core/platform/macos.py
(MacBackend) and core/platform/linux.py (LinuxBackend).

Design (the restraint item 42 demands): every OS surface is a
SEAM — an optional provider object injected at construction
(`seams={...}`) or assigned later (`backend.seams = {...}`).
The four seam keys mirror the ABC's four areas:

  "input"         — type_text(text), press_hotkey(keys),
                    mouse(action, x, y, amount), move(x, y),
                    click(button, count, x, y), scroll(amount, x, y,
                    horizontal), drag(x1, y1, x2, y2),
                    cursor_position() -> (x, y),
                    screen_size() -> (w, h) | None,
                    virtual_bounds() -> (l, t, r, b) | None
  "windows"       — list_windows() -> [(hwnd, title)],
                    foreground() -> {"hwnd", "title", "rect",
                                     "process"} | None,
                    focus(hwnd), minimize(hwnd), maximize(hwnd),
                    close(hwnd),
                    processes() -> [process-name, ...],
                    terminate(name) -> int (count closed)
  "accessibility" — tree(window_title) -> element dict | None,
                    find(window_title, name, control_type)
                        -> [element dict, ...],
                    click(element dict), set_value(element, text),
                    focused() -> element dict | None,
                    element_at(x, y) -> element dict | None
                    (element dicts use the _common.py shape)
  "screenshot"    — grab(full_screen, rect) -> PIL Image,
                    screen_size() -> (w, h) | None,
                    virtual_bounds() -> (l, t, r, b) | None

With a seam present, methods operate against it and return the
same tool-shaped receipts the other backends return. With NO
seam, each method tries the real OS lazily (subclass hooks —
guarded imports only) and, when the OS cannot answer, fails
soft and honest with the subclass's unavailable sentences:
never an invented success, never a raise (except enum_windows,
whose pre-seam original raised off its home platform, and
capture_screen, whose contract lets the grab's failure surface
to the caller — both documented in PORTING.md).

Subclasses set: name, _a11y_unavailable (str), _input_unavailable,
_windows_unavailable, and may override the _os_* hooks.
"""

from __future__ import annotations

import io  # INTENTIONAL (B29: facade re-export; moved code lives in _seamed_* modules)
import re  # INTENTIONAL (B29: facade re-export; moved code lives in _seamed_* modules)
from . import _common  # INTENTIONAL (B29: facade re-export)
from .base import PlatformBackend  # INTENTIONAL (B29: facade re-export)
from ._seamed_helpers import (  # INTENTIONAL (B29: facade re-export)
    _CURSOR_ACTIONS,
    _PASSWORD_NAME_RE,
    _TEXT_TYPES,
)
from ._seamed_dispatch_input_cursor import SeamedBackendDispatchInputCursorMixin  # INTENTIONAL (B29: P1 composed-mixin facade)
from ._seamed_window_uia_capture import SeamedBackendWindowUiaCaptureMixin  # INTENTIONAL (B29: P1 composed-mixin facade)


class SeamedBackend(SeamedBackendDispatchInputCursorMixin, SeamedBackendWindowUiaCaptureMixin):
    """Common implementation for the seamed native backends."""

    name = ""
    _a11y_unavailable = "Accessibility isn't available on this system."
    _input_unavailable = "Input isn't available on this system."
    _windows_unavailable = "Window control isn't available on this system."
