"""Domain routers: the offered surface for every Live profile.

PROBLEM (owner-approved design, 2026-10-27): 640 tools as
direct Gemini declarations cost ~380 fitted bytes each and
bust the 240,000-byte budget, while the sessions that fly
never offer the full list. Trimming descriptions every wave
is a treadmill, and retiring tools to fit is a capability
loss. The domain router is the structural answer: the
resident core stays direct, and every other tool is reached
through one router declaration per catalogue domain.

SHAPE (generated, never hand-maintained): the routers are
built from the registry's own catalogue groups — the
GROUP_MEMBERS map in core/catalogue_groups_data.py, imported
directly (GAPS-23 F-V7: the tables moved out of the Qt GUI
module so no reader has to AST-parse a file anymore — the
re-parse was a second source). One router per
non-empty group, named ``route_<group slug>``. Each router
carries its tools' names as an enum plus one line per tool
(name + one-line purpose, purpose capped at 80 chars).
Purposes are the opening summary sentence of the canonical
description only — never the full description, never safety
text: safety lives in the funnel (confirm gate, taint,
validation), not in router text. Full parameter schemas stay
server-side and are fetched through use_toolbox describe
when the model needs them, or returned inside a validation
error so the model repairs in one round (the v5.50 funnel
pattern, passed through unchanged here).

COVERAGE (the invariant, tested in tests/test_domain_routers.py):
routers partition EXACTLY the tools NOT in
toolbox.CORE_TOOL_PRIORITY — every non-core registry tool in
exactly one router, no core tool in any router, nothing else
dropped, no duplicates — except the use_toolbox meta-tool
(META_TOOL_NAMES), which is offered directly in routed
sessions and must be absent in router-off sessions, so it
lives in no router. Registered tools the catalogue does
not place (today: the two plugin tools) fall into the
"More skills" router, so a catalogue gap can never silently
drop a tool. Router declarations are SYNTHETIC: they are
never registered, so the registry count never changes.

CONSEQUENCES, stated plainly (all follow from the locked
design, none of them invented here):
  * use_toolbox is itself a registered non-core tool in the
    "Mind" catalogue group, but it is EXCLUDED from routers
    by design (META_TOOL_NAMES, parent correction
    2026-10-27): a router flies in every profile including
    router-off, so a router member can never be absent in
    router-off mode — and router-off must mean no
    dispatcher. The meta-tool is offered directly in routed
    sessions only.
  * Gimmick tools (tool_profiles.GIMMICK_TOOLS) are non-core
    and therefore routed, exactly as they are already
    use_toolbox-reachable today. Curation shapes what a
    session LEADS with (direct declarations), never what it
    can reach; the gimmick filter in profile_declarations
    strips direct declarations by name and router
    declarations (named route_*) pass it by construction.
  * H6 usage adaptation (assistant profile with usage
    counts) may promote a non-core tool into the resident
    set; that tool then appears BOTH direct and in its
    router. Same precedent as resident tools being
    use_toolbox-reachable today: overlap of paths, never a
    second tool.

DISPATCH: dispatch_route() is the only entry point for a
router call. It checks membership (unknown tool refused;
wrong-router refused naming the CORRECT router), then
executes through registry.execute() — the same funnel, the
same confirm gate, taint, and validation as a direct call.
It NEVER accepts blanket confirm: a confirm-gated tool with
no handler registered fails closed with the funnel's own
not_confirmed marker, exactly as the use_toolbox gate proof.
A validation failure passes execute()'s dict through
unchanged, so its "schema" field still reaches the model.

TOOL_ROUTER REDEFINITION (see core/voice/live_client.py): the
TOOL_ROUTER flag now controls ONLY the use_toolbox
meta-tool declaration. Domain routing is structural: it is
present in every profile, including router-off mode, which
returns resident + routers WITHOUT use_toolbox. The retired
all-direct fitted list (DECL-01, owner ruling 2026-11-01)
no longer exists: no offered-set builder ever built it.

Stdlib only. No new dependencies. No tool renamed, no
resident set changed, no registry entry added or removed.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Purpose lines are capped at this many characters.
PURPOSE_CAP = 80

#: Router declarations are named with this prefix.
ROUTER_PREFIX = "route_"

#: Catalogue group whose router also receives registered tools
#: the catalogue does not place, so nothing is ever dropped.
FALLBACK_GROUP = "More skills"

#: Meta-tools that must never live inside a router: the
#: use_toolbox dispatcher is offered directly in routed
#: sessions and must be ABSENT in router-off sessions, so
#: putting it in a router (which flies in every profile)
#: would smuggle a dispatcher into router-off mode.
#: Parent correction 2026-10-27: router-off must mean no
#: dispatcher, so the meta-tool is excluded from the
#: partition by design, not dropped by accident.
META_TOOL_NAMES = frozenset({"use_toolbox"})

_GROUPS_CACHE: dict[str, tuple[str, ...]] | None = None


def _catalogue_source() -> dict[str, tuple[str, ...]]:
    """GROUP_MEMBERS from the single catalogue source, imported
    directly (test seam: tests monkeypatch this to simulate the
    source being unavailable).

    GAPS-23 (F-V7): the AST re-parse of gui/skills_catalog_data.py
    is gone — the tables live in core/catalogue_groups_data.py
    (pure data, no Qt), so this imports them. The re-parse existed
    only because the tables lived in a Qt module; importing the
    data module is the same tables with no second source.
    """
    from .. import catalogue_groups_data  # Phase 3 B1b: moved to core/brain/

    return _coerce_groups(catalogue_groups_data.GROUP_MEMBERS,
                          "core/catalogue_groups_data")


def _coerce_groups(found: Any, where: str) -> dict[str, tuple[str, ...]]:
    """GROUP_MEMBERS value -> {group: (tool, ...)}, or refuse.

    Shared by the catalogue-source import and the snapshot fallback
    so both are held to the same shape: a dict of str group
    names to lists/tuples of tool names. Anything else is a
    refusal, never a guessed grouping.
    """
    if not isinstance(found, dict):
        raise ValueError(
            f"Domain routers: GROUP_MEMBERS not found in {where} "
            "— refusing to build routers from a guessed grouping.")
    groups: dict[str, tuple[str, ...]] = {}
    for key, members in found.items():
        if not isinstance(key, str) or not isinstance(members, (list, tuple)):
            raise ValueError(
                "Domain routers: GROUP_MEMBERS has an unexpected "
                f"shape in {where} — refusing to build routers "
                "from it.")
        groups[key] = tuple(str(m) for m in members)
    return groups


def _snapshot_groups() -> dict[str, tuple[str, ...]]:
    """GROUP_MEMBERS from the embedded snapshot (test seam).

    Packaging batch (wiring audit WIR-02): the snapshot is the
    fallback ONLY when the catalogue source module cannot be
    imported. It is a generated copy of the catalogue
    (core/catalogue_snapshot.py, regenerated by
    ~/workspace/code-lab/gen_catalogue_snapshot.py); tests
    monkeypatch this function to simulate snapshot failure.
    """
    from .. import catalogue_snapshot  # Phase 3 B1b: moved to core/brain/

    return _coerce_groups(catalogue_snapshot.GROUP_MEMBERS, "catalogue snapshot")


def catalogue_groups() -> dict[str, tuple[str, ...]]:
    """GROUP_MEMBERS from the single catalogue source, imported.

    The catalogue module stays the single source of truth for
    grouping. Packaging batch (wiring audit WIR-02): when the
    source module cannot be imported — the frozen-layout
    failure, where the old code failed twice (the live-client
    fallback rebuilt routers from the same missing file) — this
    falls back to the embedded snapshot ONCE, with a loud log
    line, and never re-imports; if the snapshot is unavailable
    too, it raises naming the failed source. Imported once per
    process — the catalogue is static at runtime."""
    global _GROUPS_CACHE
    if _GROUPS_CACHE is not None:
        return _GROUPS_CACHE
    try:
        groups = _catalogue_source()
    except Exception as exc:  # noqa: BLE001 — loud fallback below
        try:
            groups = _snapshot_groups()
        except Exception as snap_exc:  # noqa: BLE001 — loud refusal below
            raise ValueError(
                "Domain routers: catalogue source unavailable "
                "(core/catalogue_groups_data: "
                f"{exc}) and the embedded snapshot is "
                f"unavailable ({snap_exc}) — refusing to build "
                "routers from a guessed grouping.") from snap_exc
        print(f"[Routers] Catalogue source unavailable ({exc}); "
              "using the embedded snapshot grouping instead — "
              "rebuild the frozen bundle with "
              "core/catalogue_groups_data.py bundled.")
        _GROUPS_CACHE = groups
        return groups
    _GROUPS_CACHE = groups
    return groups


def slugify_group(name: str) -> str:
    """A catalogue group name as a function-name-safe slug:
    lowercase, runs of non-alphanumerics to one underscore,
    never empty, never starting with a digit."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    if not slug:
        slug = "group"
    if slug[0].isdigit():
        slug = "g_" + slug
    return slug


def router_name_for_group(group: str) -> str:
    """The router declaration name for a catalogue group."""
    return ROUTER_PREFIX + slugify_group(group)


def _purpose(description: Any) -> str:
    """The one-line purpose for a tool: the opening summary
    sentence of its canonical description, capped at
    PURPOSE_CAP chars. The first sentence ends at a period
    followed by a capital (so "e.g. ..." does not cut the
    sentence short); over-long purposes cut at the last
    space inside the cap, with an ellipsis marking the cut.
    Empty descriptions read as "(no description)" rather
    than leaving a bare "name:" line."""
    text = " ".join(str(description or "").split())
    if not text:
        return "(no description)"
    match = re.search(r"\.\s+(?=[A-Z0-9])", text)
    if match is not None:
        text = text[:match.start() + 1]
    if len(text) <= PURPOSE_CAP:
        return text
    head = text[:PURPOSE_CAP]
    cut = head.rsplit(" ", 1)[0] if " " in head else head[:-1]
    if not cut:
        cut = head[:-1]
    return (cut + "…")[:PURPOSE_CAP]


def _registered_names(registry: Any) -> list[str]:
    """Registry tool names in registration order."""
    try:
        return [d["name"] for d in registry.get_declarations()]
    except Exception:  # noqa: BLE001 — test doubles without it
        tools_map = getattr(registry, "_tools", None) or {}
        return list(tools_map)


def _router_groups(registry: Any, core_names: Any = None,
                   ) -> list[tuple[str, str, tuple[str, ...]]]:
    """(router name, catalogue group label, members) in
    catalogue order. Raises on a slug collision rather than
    silently merging two domains into one router."""
    if core_names is None:
        from ..funnel.toolbox import CORE_TOOL_PRIORITY  # Phase 3 B1b: moved to core/brain/

        core_names = CORE_TOOL_PRIORITY
    core = set(core_names)
    decl_names = _registered_names(registry)
    registered = set(decl_names)
    groups = catalogue_groups()
    out: list[tuple[str, str, tuple[str, ...]]] = []
    seen_routers: set[str] = set()
    grouped: set[str] = set()
    for group, members in groups.items():
        grouped.update(m for m in members if m in registered)
        keep: list[str] = []
        for name in members:
            if name in registered and name not in core \
                    and name not in META_TOOL_NAMES \
                    and name not in keep:
                keep.append(name)
        if not keep:
            continue
        rname = router_name_for_group(group)
        if rname in seen_routers:
            raise ValueError(
                f"Domain routers: catalogue groups collide on "
                f"router name {rname!r} — refusing to merge "
                "two domains.")
        seen_routers.add(rname)
        out.append((rname, group, tuple(keep)))
    ungrouped = [n for n in decl_names
                 if n not in grouped and n not in core
                 and n not in META_TOOL_NAMES]
    if ungrouped:
        rname = router_name_for_group(FALLBACK_GROUP)
        for index, (existing, _label, members) in enumerate(out):
            if existing == rname:
                out[index] = (existing, _label,
                              tuple(members) + tuple(ungrouped))
                break
        else:
            out.append((rname, FALLBACK_GROUP, tuple(ungrouped)))
    return out


def domain_router_membership(registry: Any, core_names: Any = None,
                             ) -> dict[str, tuple[str, ...]]:
    """Router name -> member tool names, in catalogue order.
    The partition invariant lives here: every non-core
    registry tool except the use_toolbox meta-tool
    (META_TOOL_NAMES) in exactly one router, no core tool
    in any router, nothing else dropped."""
    return {rname: members
            for rname, _label, members in _router_groups(registry, core_names)}


def build_domain_routers(registry: Any, core_names: Any = None,
                         ) -> list[dict[str, Any]]:
    """The router declarations for a registry: one synthetic
    declaration per catalogue domain (see module docstring).
    Synthetic means unregistered: building routers never
    changes the registry count. Each declaration carries its
    tools' names + one-line purposes + a generic args
    object, and nothing else — full schemas stay server-side.

    ARGS CONTRACT (router-consistency batch, wiring audit
    WIR-03): the args node is a deliberate pass-through, not a
    missing schema. It carries no properties because its schema
    is the INNER tool's schema, enforced at dispatch by
    registry.execute()/validate_args — a validation failure
    returns the inner schema so the model repairs in one round.
    It is therefore exempt by design from the registry's
    empty-object rewrite (tools_registry._gemini_safe_schema),
    which applies to registered declarations only: router
    declarations never pass through that function, and the
    object shape is the field-proven calling convention
    (v5.93 A/B). The rewrite's string form is still accepted
    at dispatch as a fallback — dispatch_route parses
    JSON-text args — so either shape routes. Rejected
    alternatives, with reasons: running routers through the
    sanitizer would change the model-visible convention to
    JSON text on a proven path for no dispatch gain; giving
    args real properties is infeasible (its schema is the
    union of all member tools' schemas, which would bust the
    fitted budget the routers exist to save) and semantically
    wrong (one tool's properties do not apply to another's);
    an additionalProperties trigger would fly a key no
    production declaration has ever carried (0 of 640
    registered tools declare it), an untestable-live risk.
    """
    try:
        descriptions = {d["name"]: d.get("description", "")
                        for d in registry.get_declarations()}
    except Exception:  # noqa: BLE001 — test doubles without it
        tools_map = getattr(registry, "_tools", None) or {}
        descriptions = {name: entry.get("description", "")
                        for name, entry in tools_map.items()}
    routers: list[dict[str, Any]] = []
    for rname, group, members in _router_groups(registry, core_names):
        lines = "; ".join(f"{name}: {_purpose(descriptions.get(name, ''))}"
                          for name in members)
        routers.append({
            "name": rname,
            "description": (
                f"Domain router for {group}: call with tool set "
                "to one of the tools below and args set to that "
                "tool's arguments. Tools: " + lines),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": list(members),
                        "description": "The tool to run in this domain.",
                    },
                    "args": {
                        "type": "object",
                        "description": "Arguments for the named tool.",
                    },
                },
                "required": ["tool"],
            },
        })
    return routers


def router_for_tool(membership: dict[str, tuple[str, ...]],
                    tool: str) -> str | None:
    """The router offering `tool`, or None when no router does."""
    for rname, members in membership.items():
        if tool in members:
            return rname
    return None


def dispatch_route(registry: Any, router_name: str, tool: Any,
                   args: Any) -> dict[str, Any]:
    """Execute one domain-router call through the funnel.

    Returns an execute()-shaped dict in every case, so the
    Live adapter delivers it unchanged. Unknown tools and
    wrong-router calls are refusals, never executions; a
    validation failure passes execute()'s dict through
    untouched, so its "schema" field still reaches the model
    for a one-round repair. Confirm-gated tools go through
    execute() with no handler of their own, so with no
    confirmation handler registered they fail closed with
    the funnel's not_confirmed marker — blanket confirm is
    never accepted here."""
    tool_name = str(tool or "").strip()
    if not tool_name:
        return {"status": "failure", "kind": "unavailable",
                "message": "Domain router call needs a tool name."}
    membership = domain_router_membership(registry)
    if router_name not in membership:
        return {"status": "failure", "kind": "unavailable",
                "message": f"Unknown domain router: {router_name}."}
    if tool_name not in membership[router_name]:
        correct = router_for_tool(membership, tool_name)
        if correct is None:
            if tool_name in META_TOOL_NAMES:
                return {"status": "failure", "kind": "unavailable",
                        "message": f"Tool '{tool_name}' is a meta-tool "
                                   "offered directly in routed sessions, "
                                   "not through a domain router. Call it "
                                   "directly."}
            return {"status": "failure", "kind": "unavailable",
                    "message": f"Unknown tool: {tool_name}. No "
                               "domain router offers this tool."}
        return {"status": "refused", "kind": "refused",
                "message": f"Tool '{tool_name}' belongs to domain "
                           f"router '{correct}', not '{router_name}'. "
                           f"Call it through '{correct}'."}
    # Audit fix A4 (wiring audit WIR-06): distinguish 'argument
    # omitted' from 'provided but malformed'. The Live adapter
    # passes {} when the model omits args, so {} is the omitted
    # case here and keeps today's behaviour; a provided bundle
    # that is not a dict — an unparseable JSON string, a parsed
    # non-object, a list, an int, or an explicit null — is
    # REJECTED with a structured validation error that keeps
    # the router (outer dispatcher) and the tool (inner call)
    # separate, and preserves the received bundle. It must
    # never silently become {} and dispatch a no-argument call.
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError as exc:
            received = repr(args)
            if len(received) > 1000:
                received = (f"{received[:1000]}... (truncated, "
                            f"{len(repr(args))} chars total)")
            return {"status": "refused", "kind": "validation",
                    "router": router_name, "tool": tool_name,
                    "malformed_args": received,
                    "message": (
                        f"Domain router '{router_name}' call to "
                        f"'{tool_name}' did not run: arguments are "
                        f"not valid JSON ({exc}); received: {received}")}
        if not isinstance(parsed, dict):
            return {"status": "refused", "kind": "validation",
                    "router": router_name, "tool": tool_name,
                    "malformed_args": repr(args),
                    "message": (
                        f"Domain router '{router_name}' call to "
                        f"'{tool_name}' did not run: arguments must "
                        "be an object "
                        f"(parsed JSON is {type(parsed).__name__}); "
                        f"received: {repr(args)}")}
        args = parsed
    if not isinstance(args, dict):
        received = repr(args)
        if len(received) > 1000:
            received = (f"{received[:1000]}... (truncated, "
                        f"{len(repr(args))} chars total)")
        return {"status": "refused", "kind": "validation",
                "router": router_name, "tool": tool_name,
                "malformed_args": received,
                "message": (
                    f"Domain router '{router_name}' call to "
                    f"'{tool_name}' did not run: arguments must be "
                    f"an object (got {type(args).__name__}); "
                    f"received: {received}")}
    return registry.execute(tool_name, args)
