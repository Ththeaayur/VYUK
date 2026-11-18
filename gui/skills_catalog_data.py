"""Skills catalogue data — re-export shim (GUI split #2, revised GAPS-23 F-V7).

The group tables (GROUP_ORDER + GROUP_MEMBERS) used to live in this
file, and this file is a Qt module — it defines the ItemDataRole
constants the catalogue widget needs. The Qt import is what forced
every non-GUI reader (core/brain/domain_routers.catalogue_groups(),
the packaging checker, the snapshot generator, the w9/w10 test
helpers) to AST-parse this file instead of importing the tables,
so the catalogue had two sources: the tables, and a re-parse of
the tables.

GAPS-23 (F-V7): the tables live in core/catalogue_groups_data.py
(pure data, no Qt, import-time invariant checks) and this file is
a thin shim that re-exports them alongside the Qt roles. The GUI
reaches the tables ONLY through the facade (GAPS-15 boundary:
a direct `from core.catalogue_groups_data import ...` here would
fail test_gui_boundary test_01) — the facade re-exports the
module, and the names below are the identical objects the
direct import would give. Every other reader imports the core
module directly.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from core.gui_facade import catalogue_groups_data

GROUP_MEMBERS = catalogue_groups_data.GROUP_MEMBERS
GROUP_ORDER = catalogue_groups_data.GROUP_ORDER

# Item-data roles (kept above the roles the app already uses).
ROLE_NAME = Qt.ItemDataRole.UserRole          # raw tool name / "" for headers
ROLE_FRIENDLY = Qt.ItemDataRole.UserRole + 1  # friendly display name
ROLE_SUMMARY = Qt.ItemDataRole.UserRole + 2   # one-line summary
ROLE_KIND = Qt.ItemDataRole.UserRole + 3      # "tool" | "header"
ROLE_GROUP = Qt.ItemDataRole.UserRole + 4     # group title the row belongs to

__all__ = [
    "GROUP_MEMBERS",
    "GROUP_ORDER",
    "ROLE_NAME",
    "ROLE_FRIENDLY",
    "ROLE_SUMMARY",
    "ROLE_KIND",
    "ROLE_GROUP",
    "_NAME_TO_GROUP",
    "_FALLBACK_GROUP",
    "_MAX_SUMMARY",
]

# Inverted once at import: raw name -> group title.
_NAME_TO_GROUP: dict[str, str] = {}
for _group, _names in GROUP_MEMBERS.items():
    for _name in _names:
        _NAME_TO_GROUP[_name] = _group

_FALLBACK_GROUP = "More skills"
_MAX_SUMMARY = 95
