"""Pinned skills (UI Phase 2, D-P2-01 / D-P2-04).

ONE store, two readers: the Home capability dock (window_home_hud)
and the Capabilities Centre's Pinned section (skills_catalog_widget)
both read this module — never their own copy of the list — so a
pin made in the Centre shows on Home and vice versa.

Storage: QSettings ``skills/pinned`` — an ordered list of tool
names, most recently pinned first. QSettings may hand a lone
string back for a one-item list, and a hand-edited conf file
may hold anything; ``_normalise`` turns every stored form into
a clean list. Unknown names are dropped by the readers (the
catalogue widget filters pins against its tools) — the store
itself keeps the list the user pinned, never rewritten.
"""

from __future__ import annotations

from voice_assistant.core.gui_facade import (
    qsettings_get,
    qsettings_set,
)

_KEY = "skills/pinned"

#: The Home dock's six defaults live in window_home_hud
#: (_build_accordion_body "tiles") and stay there: they are
#: PAGE shortcuts (Reminders -> Activity, Email -> Inbox,
#: Calendar -> Calendar; Notes / Home / Money have no target
#: yet), not tools, so they cannot be expressed as tool
#: names. The dock renders those six when this store is
#: empty; once the user pins anything, it renders the pinned
#: SKILLS instead (a tap asks with that skill). The Centre's
#: Pinned section is absent when the store is empty — no
#: defaults are shown there, because the dock already covers
#: them.


def _normalise(raw):
    """Any stored form -> a clean list of tool names."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def pinned_tools(settings=None):
    """The pinned tool names, most recently pinned first."""
    return _normalise(qsettings_get(_KEY, settings=settings))


def is_pinned(tool_name, settings=None):
    return (tool_name or "") in pinned_tools(settings=settings)


def set_pinned(tool_name, pinned, settings=None):
    """Pin or unpin one tool. Pinning moves it to the front;
    unpinning removes it. Returns the new list."""
    name = (tool_name or "").strip()
    if not name:
        return pinned_tools(settings=settings)
    current = pinned_tools(settings=settings)
    if pinned:
        current = [name] + [n for n in current if n != name]
    else:
        current = [n for n in current if n != name]
    qsettings_set(_KEY, current, settings=settings)
    return current
