"""w8_wiring part module (skills split B14): w8_wiring_funnel."""
from __future__ import annotations

# INTENTIONAL (skills split B14): cross-part names imported from
# the modules that now own them (P3 bare-name rule).
from .w8_wiring_registry import _registry
from .w8_wiring_gates import _REFUSAL_MARKERS



def funnel_step_executor(step: dict) -> dict:
    """The funnel executor for checkpoint/teach steps.

    ``step`` is {"tool": name, "args": {...}} (CheckpointRun's
    shape). Executes ONLY via the live registry (the funnel),
    never directly. Returns CheckpointRun's outcome shape:
    {"ok", "result", "legs", "verdict", "error"}.
    """
    tool = step.get("tool", "")
    args = step.get("args") or {}
    try:
        result = _registry().execute(tool, dict(args))
    except Exception as exc:  # noqa: BLE001 — executor failures are data
        return {"ok": False, "result": None, "legs": [],
                "verdict": None, "error": str(exc)}
    if not isinstance(result, dict) or result.get("status") != "success":
        return {"ok": False, "result": None, "legs": [],
                "verdict": None,
                "error": str(result.get("message", "tool error"))
                if isinstance(result, dict) else "tool error"}
    text = str(result.get("result") or "")
    if any(marker in text for marker in _REFUSAL_MARKERS):
        return {"ok": False, "result": text, "legs": [],
                "verdict": None, "error": f"refused: {text[:120]}"}
    return {"ok": True, "result": result.get("result"), "legs": [],
            "verdict": None, "error": None}




def funnel_tool_executor(tool: str, args: dict) -> dict:
    """The funnel executor for teach replay's (tool, args) shape.

    Returns a dict with an "ok" entry, as teach.replay's
    verification expects.
    """
    outcome = funnel_step_executor({"tool": tool, "args": args})
    return {"ok": outcome["ok"], "result": outcome.get("result"),
            "error": outcome.get("error")}


