"""w8_wiring part module (skills split B14): w8_wiring_registry."""
from __future__ import annotations


# ---------------------------------------------------------------------
# Live registry seam (lazy: importing the registry pulls planner in at
# registration — see tools_handlers_pack_core2._register_mind_tools —
# so this early, widely imported module must not import it at top level)
# ---------------------------------------------------------------------


def _registry():
    from .funnel.tools_registry import tools as registry  # Phase 4 S1b INTENTIONAL: registry split to funnel

    return registry




def live_tool_names() -> set[str]:
    """The LIVE tool set, read fresh from the registry."""
    try:
        return {d["name"] for d in _registry().get_declarations()}
    except Exception:  # noqa: BLE001 — caller treats None as unknown
        return set()




_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "number": (int, float),
    "object": dict,
    "array": list,
}




def live_arg_schemas() -> dict[str, dict[str, Any]]:
    """Tool -> {arg: python type} from LIVE declarations.

    A tool with no declared properties gets an empty schema
    (args for it fail as unknown args — fail-closed). A tool
    absent from the registry gets NO schema entry (its steps
    fail validation outright).
    """
    schemas: dict[str, dict[str, Any]] = {}
    try:
        declarations = _registry().get_declarations()
    except Exception:  # noqa: BLE001
        return schemas
    for decl in declarations:
        props = (decl.get("parameters") or {}).get("properties") or {}
        schemas[decl["name"]] = {
            # A JSON Schema "type" may be a LIST (e.g. scheduled_agents
            # steps: ["array", "string"] — RET-2: the steps may be an
            # array or a JSON string). A list is unhashable as a dict
            # key, so normalize to its first element (the primary type).
            name: _JSON_TYPE_MAP.get(
                (spec.get("type")[0]
                 if isinstance(spec.get("type"), list) and spec.get("type")
                 else spec.get("type")),
                str)
            for name, spec in props.items()
        }
    return schemas


