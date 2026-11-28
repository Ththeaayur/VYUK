"""The ONE shared skill-detail builder (UI Phase 2, D-P2-05/06).

Two hosts render the same content from this builder: the
Galaxy's right-hand SkillDetailCard and the Centre's
ContextDrawer. One builder means one content, one pin state,
one "Ask with this" path — the two hosts can never disagree
about what a skill does, what it needs, or whether it is
pinned.

Content, in order (D-P2-06 — everything the blueprint asks
for, nothing invented):
  friendly title, group, outcome description (the registry
  description, trimmed), example request ("Try: …" — the
  first toolbox_tokens trigger phrase where one exists, else
  the "Use {friendly} to …" shape), requirements (permission
  scope from the tool's classification: read / effectful /
  confirm-gated, owner-only where the L-24 gate applies),
  connection state (only for tools that need one — a live
  read, never a green tick from a stored flag), the raw tool
  name, Pin to Home, "Ask with this" (the E22 prefill).

A field the registry or classification does not carry is
OMITTED, never guessed (D14): an unclassified tool shows no
requirements line; a tool with no connection need shows no
connection line — never a fake "ready" tick. "Unavailable"
is never rendered: no per-tool prerequisite check exists in
the tree, so no tool can honestly be called unavailable.
"""

from __future__ import annotations

from voice_assistant.core.gui_facade import (
    google_services,
    tool_classification,
    tool_labels,
    toolbox_tokens,
    tools,
    tools_gate,
)

from .skill_pins import is_pinned as _is_pinned
# NOTE: the group mapping comes from skills_catalog_data, not
# from skills_catalog_widget.group_for_name — this builder is
# imported BY the widget, so importing the widget (directly or
# through the skills_catalog shim) would be a cycle. The rule
# is the same one-liner the widget uses: the static
# _NAME_TO_GROUP table, falling back to "More skills". The
# summary rule comes from the same module, for the same reason.
from .skills_catalog_data import (
    _FALLBACK_GROUP,
    _MAX_SUMMARY as SUMMARY_CAP,
    _NAME_TO_GROUP,
    _summarise as summarise,
)

#: ``summarise`` IS the catalogue row's summary rule
#: (skills_catalog_data._summarise): the first sentence of
#: the description, whitespace-normalised, abbreviation-aware,
#: trailing period stripped, capped at 95 chars with an
#: ellipsis. The builder and the rows share this one function
#: so the drawer never shows a different summary from the row
#: that opened it. (Re-exported here so detail callers need
#: only this module.)

#: Groups whose tools need a live connection. "Your world"
#: is the Google group (email, calendar, contacts, docs) —
#: the only group with a connection seam. Every other group
#: is local and shows no connection line.
_CONNECTION_GROUPS = frozenset({"Your world"})

#: Owner-only sets (the L-24 gate): a tool in any of these
#: runs only for the owner.
_OWNER_ONLY_SETS = (
    "_OWNER_ONLY_READS",
    "_OWNER_ONLY_DISPATCHED",
    "_OWNER_ONLY_READ_ACTIONS",
)

_REQUIREMENT_LINES = {
    "confirm": "Asks for your approval before it acts",
    "read": "Read-only — it looks, never changes anything",
    "free": "Runs without asking",
}


def _is_owner_only(tool_name):
    for attr in _OWNER_ONLY_SETS:
        try:
            if tool_name in getattr(tools_gate, attr):
                return True
        except Exception:  # noqa: BLE001 — a missing set
            continue  # is not owner-only evidence.
    return False


def _connection_state(group, is_connected):
    """Live connection state for groups that need one.

    Returns "connected" / "not_connected" / "unknown", or
    None when the group needs no connection (no line is
    rendered). The read is live through the google_services
    seam; a failed read is "unknown", never a green tick.
    """
    if group not in _CONNECTION_GROUPS:
        return None
    check = is_connected
    if check is None:
        check = google_services.is_connected
    try:
        return "connected" if bool(check()) else "not_connected"
    except Exception:  # noqa: BLE001 — a failed read is
        return "unknown"  # unknown, never connected.


#: Cached registry descriptions (built once — see _descriptions).
_DESCRIPTIONS = None


def _descriptions():
    """The registry's name -> description map, built once (the
    registry is static for the life of the process). The detail
    builder is called once per row during a catalogue rebuild —
    rebuilding this map per call would be a 772 x 772 scan."""
    global _DESCRIPTIONS
    if _DESCRIPTIONS is None:
        _DESCRIPTIONS = {d.get("name", ""): d.get("description", "")
                         for d in tools.get_declarations()}
    return _DESCRIPTIONS


def build_skill_detail(tool_name, *, settings=None, is_connected=None):
    """Build the detail dict for one tool (pure except for the
    live connection read). ``settings`` is the QSettings store
    (the pin state); ``is_connected`` is the connection seam
    (tests pass a stub; the live google_services check is the
    default). Returns None for an unknown tool name."""
    name = (tool_name or "").strip()
    if not name:
        return None
    descriptions = _descriptions()
    if name not in descriptions:
        return None
    friendly = tool_labels.tool_friendly_name(name)
    group = _NAME_TO_GROUP.get(name, _FALLBACK_GROUP)
    permission = tool_classification.classify(name)
    owner_only = _is_owner_only(name)
    phrases = toolbox_tokens.TOOL_SYNONYMS.get(name) or ()
    if phrases:
        example = "Try: %s" % phrases[0]
    else:
        example = "Use %s to \u2026" % friendly
    requirements = _REQUIREMENT_LINES.get(permission)
    if requirements is not None and owner_only:
        requirements += " \u00b7 Owner only \u2014 runs only for the owner"
    return {
        "name": name,
        "friendly": friendly,
        "group": group,
        "summary": summarise(descriptions.get(name, "")),
        "description": (descriptions.get(name, "") or "").strip(),
        "example": example,
        "requirements": requirements,
        "permission": permission,
        "owner_only": owner_only,
        "connection": _connection_state(group, is_connected),
        "pinned": _is_pinned(name, settings=settings),
    }


def state_chips(detail):
    """The catalogue row's state chips for a detail dict, in
    display order. A ready tool carries no chip — the absence
    of a chip is not a claim. "Unavailable" is never returned:
    no per-tool prerequisite check exists."""
    if not detail:
        return []
    chips = []
    if detail.get("pinned"):
        chips.append("Pinned")
    if detail.get("permission") == "confirm":
        chips.append("Needs permission")
    if (detail.get("group") in _CONNECTION_GROUPS
            and detail.get("connection") == "not_connected"):
        chips.append("Needs connection")
    return chips
