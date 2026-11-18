"""
FakeBackend — the scripted platform backend (v5.50, programme
item 41).

A PlatformBackend that serves SCRIPTED state instead of a real
desktop, so tool code can be exercised end-to-end on any machine
(including this headless Linux VM) with no OS, no display and no
Windows APIs:

  * windows list, foreground window and focus are scripted data;
  * accessibility trees are scripted element dicts (see
    core/platform/_common.py for the shape);
  * screenshots are deterministic generated images — the same
    scripted state always encodes to the same JPEG bytes;
  * EVERY input call (typed text, clicks, hotkeys, mouse moves,
    scrolls, drags) is appended, in order, to `input_log`, an
    inspectable in-memory list of dicts. The log is a test
    surface only: nothing is persisted, and — exactly like the
    real backends — RESULT STRINGS never echo typed text.

Scripting API (tests drive these directly; they are not part of
the ABC):

  backend.add_window(title, rect=..., process=...) -> hwnd
  backend.add_control(hwnd, name, control_type=..., rect=...,
                      is_password=..., value=...) -> FakeElement
  backend.set_foreground(hwnd) / backend.set_focus(element)
  backend.script_hotkey("ctrl+s", callable)  — a reaction run
      when that hotkey is pressed (e.g. open a dialog)
  backend.script_click("Save", callable)    — a reaction run
      after a control with that name is clicked (in addition to
      the built-in dialog rule below)

Built-in scripted behaviour, so a plain "open dialog → click
Save" scenario works with no custom reactions: clicking a
control named "Save" or "Cancel" in a window closes that window
(Save also sets state["saved"] = True), and focus/foreground
fall back to the next window.

Selection: VYUK_PLATFORM=fake (core/platform/__init__.py).
The real consumers (core/desktop/uia.py, core/desktop/control.py, core/desktop/cursor.py,
core/desktop/vision.py) route through get_backend() whenever the
serving backend is not the Windows one (v5.50, item 41), so the
scripted scenario runs through ACTUAL tool code — on Windows
the Windows backend serves and those modules run their legacy
bodies exactly as before.
"""


from __future__ import annotations

import io  # INTENTIONAL (B28: leaked import re-export; old module bound io)
import re  # INTENTIONAL (B28: leaked import re-export; old module bound re)
from . import _common  # INTENTIONAL (B28: leaked import re-export; old module bound _common)
from .base import PlatformBackend  # INTENTIONAL (B28: leaked import re-export; old module bound PlatformBackend)
from .fake_helpers import (  # INTENTIONAL (B28: facade re-export; small classes + constants live in fake_helpers)
    FakeElement,
    FakeWindow,
    _CURSOR_ACTIONS,
    _ElementInfo,
    _PASSWORD_NAME_RE,
    _Rect,
    _SCREEN_H,
    _SCREEN_W,
    _TEXT_TYPES,
)
from .fake_input_cursor import FakeBackendInputCursorMixin  # INTENTIONAL (B28: P1 composed-mixin facade)
from .fake_window_uia_capture import FakeBackendWindowUiaCaptureMixin  # INTENTIONAL (B28: P1 composed-mixin facade)


class FakeBackend(FakeBackendInputCursorMixin, FakeBackendWindowUiaCaptureMixin):
    """The scripted backend. See the module docstring."""

    name = "fake"
