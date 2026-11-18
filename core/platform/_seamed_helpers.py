from __future__ import annotations

import re  # INTENTIONAL (B29: _PASSWORD_NAME_RE is compiled here)

_PASSWORD_NAME_RE = re.compile(r"password|passcode|\bpin\b", re.IGNORECASE)
_TEXT_TYPES = ("Edit", "Document", "ComboBox", "TextField",
               "SecureTextField", "password text", "text")
_CURSOR_ACTIONS = ("position", "move", "move_fraction", "move_by",
                    "nudge", "click", "double_click", "right_click",
                    "middle_click", "scroll", "drag", "beacon", "park")
