from __future__ import annotations

import re  # _PASSWORD_NAME_RE below

_SCREEN_W, _SCREEN_H = 1920, 1080
_PASSWORD_NAME_RE = re.compile(r"password|passcode|\bpin\b", re.IGNORECASE)
_TEXT_TYPES = ("Edit", "Document", "ComboBox")
_CURSOR_ACTIONS = ("position", "move", "move_fraction", "move_by",
                   "nudge", "click", "double_click", "right_click",
                   "middle_click", "scroll", "drag", "beacon", "park")


class _Rect:
    """Rectangle shim with the attribute shape _element_facts
    style consumers read (left/top/right/bottom)."""

    def __init__(self, rect):
        self.left, self.top, self.right, self.bottom = (
            int(v) for v in rect)


class _ElementInfo:
    def __init__(self, el: "FakeElement"):
        self._el = el
        self.name = el.name
        self.class_name = el.class_name
        self.control_type = el.control_type
        self.rectangle = _Rect(el.rect)
        self.enabled = el.enabled
        self.element = self  # GetCurrentPropertyValue lives here

    def GetCurrentPropertyValue(self, prop):  # noqa: N802 — UIA name
        # UIA property 30019 is IsPassword (core/desktop/uia.py). An
        # unscripted verdict (None) RAISES, exactly like an
        # unreadable property on a real element — callers must
        # read that as UNKNOWN, never False.
        if int(prop) == 30019:
            if self._el.is_password is None:
                raise RuntimeError("IsPassword not scripted")
            return bool(self._el.is_password)
        raise RuntimeError(f"property {prop} not scripted")


class FakeElement:
    """One scripted accessibility element. Attribute-compatible
    with the seams core/desktop/uia.py and core/input_guard.py read:
    .element_info (.name/.class_name/.control_type/.rectangle/
    .element.GetCurrentPropertyValue), .is_enabled()/.is_visible().
    """

    def __init__(self, name, control_type="Button", class_name="",
                 rect=(0, 0, 100, 30), enabled=True, visible=True,
                 is_password=None, value=""):
        self.name = str(name)
        self.control_type = str(control_type)
        self.class_name = str(class_name)
        self.rect = tuple(int(v) for v in rect)
        self.enabled = bool(enabled)
        self.visible = bool(visible)
        self.is_password = is_password  # True / False / None(unknown)
        self.value = str(value)
        self.children: list[FakeElement] = []
        self.window = None  # owning FakeWindow, set by add_control
        self.element_info = _ElementInfo(self)

    def is_enabled(self):
        return self.enabled

    def is_visible(self):
        return self.visible

    def facts(self) -> dict:
        return {"name": self.name, "class_name": self.class_name,
                "control_type": self.control_type, "rect": self.rect,
                "enabled": self.enabled, "visible": self.visible,
                "is_password": self.is_password, "value": self.value}

    def __repr__(self):  # pragma: no cover — debugging aid
        return f"<FakeElement {self.name!r} ({self.control_type})>"


class FakeWindow:
    def __init__(self, hwnd, title, rect, process):
        self.hwnd = hwnd
        self.title = str(title)
        self.rect = tuple(int(v) for v in rect)
        self.process = str(process)
        self.controls: list[FakeElement] = []
