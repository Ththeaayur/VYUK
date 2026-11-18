"""Tool display labels — re-export shim (GAPS-28, G-V7).

The table lives in ``core/tool_labels.py`` (data, not GUI); this
file is a thin shim so the old import path keeps working. The GUI
reaches the labels ONLY through the facade (GAPS-15 boundary: a
direct ``from core.tool_labels import ...`` here would fail
test_gui_boundary) — the facade re-exports the module, and the
names below are the identical objects the direct import would
give. Every other reader imports the core module directly.
"""

from __future__ import annotations

from core.gui_facade import tool_labels as _labels

_TOOL_FRIENDLY_NAMES = _labels.TOOL_FRIENDLY_NAMES
_TOOL_ICONS = _labels.TOOL_ICONS
_tool_friendly_name = _labels.tool_friendly_name
_tool_icon_name = _labels.tool_icon_name

__all__ = [
    "_TOOL_FRIENDLY_NAMES",
    "_TOOL_ICONS",
    "_tool_friendly_name",
    "_tool_icon_name",
]
