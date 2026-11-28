"""The tool-placement invariant (GAPS-29, E-V9) — one
machine-checked answer to "is this tool placed everywhere it
must be?".

A new native tool touches several hand-curated tables: its
registration, its GROUP_MEMBERS entry (or the documented
fallback), the generated catalogue snapshot, its gate-set
entry, and (optionally) its friendly-name entry. The tables
stay hand-curated — the gate sets encode deliberate
judgments, the groups encode curation — but the SYNC is now
checked by one function instead of by hand:
check_tool_placement() reports every registered tool that is
unclassified, ungrouped without a documented reason, missing
from the snapshot, or without a resolvable friendly name.
The suite pins it and the prover re-runs it; a new tool that
forgets a table fails loudly instead of silently.

The documented ungrouped set is empty: every registered tool is
now listed in GROUP_MEMBERS. The "More skills" fallback still
exists for genuinely unknown or dynamic tools (a future plugin
tool the static table cannot name lands there through
group_for_name, and the router and the GUI catalogue both
place it the same way) — but the three tools the fallback used
to carry are now placed explicitly: click_anywhere in
Desktop control and the two plugin tools in "More skills"
(UI Phase 2, D-P2-03 — the 3-tool snapshot gap is closed).
Anything ungrouped that is not a caller-declared plugin name
is a placement bug.
"""

from __future__ import annotations

from typing import Any

from . import catalogue_snapshot
from .catalogue_groups_data import GROUP_MEMBERS
from .funnel.tool_classification import classify
from .tool_labels import tool_friendly_name

#: Tools that are ungrouped BY DESIGN (see the module
#: docstring). Every other registered tool must be in
#: GROUP_MEMBERS. UI Phase 2 (D-P2-03): the set is empty —
#: the last three ungrouped tools are now listed explicitly.
DOCUMENTED_UNGROUPED = frozenset()


def _registry_names(registry: Any) -> list[str]:
    """Registered tool names, in registration order."""
    get_declarations = getattr(registry, "get_declarations", None)
    if callable(get_declarations):
        return [d["name"] for d in get_declarations()]
    tools_map = getattr(registry, "_tools", None) or {}
    return list(tools_map)


def check_tool_placement(registry: Any,
                         plugin_names: set[str] | frozenset) -> dict:
    """The placement invariant for one registry.

    ``plugin_names`` is the caller's discovered plugin-tool
    names (core.plugin_loader.discover_plugins() — cheap after
    the E-V2 cache). Returns {"ok": bool, "problems": [...],
    "checked": {...}}: every registered tool classified (never
    "unclassified"), in GROUP_MEMBERS or the documented
    ungrouped set, resolving to a non-empty underscore-free
    friendly name; every GROUP_MEMBERS member registered; the
    generated snapshot equal to the single source. Never
    raises for a bad registry — a problem is a report, and the
    caller decides what to do with it.
    """
    problems: list[str] = []
    try:
        names = _registry_names(registry)
    except Exception as exc:  # noqa: BLE001 — a broken registry
        return {"ok": False,
                "problems": ["registry unreadable: %s" % exc],
                "checked": {}}
    grouped: set[str] = set()
    for members in GROUP_MEMBERS.values():
        grouped.update(members)
    allowed_ungrouped = set(DOCUMENTED_UNGROUPED) | set(plugin_names or ())
    for name in names:
        if classify(name) == "unclassified":
            problems.append(
                f"{name!r} is unclassified (in no gate set)")
        if (name not in grouped
                and name not in allowed_ungrouped):
            problems.append(
                f"{name!r} is in no catalogue group and is not "
                "a documented ungrouped tool")
        friendly = tool_friendly_name(name)
        if not friendly or "_" in friendly:
            problems.append(
                f"{name!r} has no usable friendly name "
                f"({friendly!r})")
    for member in sorted(grouped):
        if member not in names:
            problems.append(
                f"catalogue member {member!r} is not registered")
    if catalogue_snapshot.GROUP_MEMBERS != GROUP_MEMBERS:
        problems.append(
            "catalogue snapshot drifted from GROUP_MEMBERS "
            "(regenerate with gen_catalogue_snapshot.py)")
    return {
        "ok": not problems,
        "problems": problems,
        "checked": {
            "registered": len(names),
            "grouped": len(grouped),
            "ungrouped": sorted(set(names) - grouped),
            "snapshot_in_sync":
                catalogue_snapshot.GROUP_MEMBERS == GROUP_MEMBERS,
        },
    }
