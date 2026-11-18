"""Pure digest/exempt helpers of the confirm gate (split B18).

No module state lives here. _is_batch_exempt_tool imports its funnel
modules function-locally, exactly as before.
"""

import hashlib
import json

def _canonical_args_digest(args: dict | None) -> str:
    """SHA-256 over a canonical JSON serialisation of the COMPLETE
    args dict (A1, P0-1). Keys sorted; non-JSON values fall back to
    repr. A digest of non-JSON-shaped values may differ between two
    equal-shaped calls (repr addresses) — that only ever MISSES a
    batch, never falsely batches two different calls.
    """
    try:
        blob = json.dumps(args, sort_keys=True, separators=(",", ":"),
                          default=repr)
    except Exception:  # noqa: BLE001 — repr fallback below
        blob = repr(args)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()



def _is_batch_exempt_tool(tool_name: str, args: dict | None) -> bool:
    """True when an ask must NEVER batch (A1, P0-1): sensitive
    operations always prompt afresh, even if an identical ask was
    just approved. The set is grounded in the tree's own
    classifications, not a new taxonomy:
    - tools_gate._CONFIRM_GATE_TOOLS: the funnel's dangerous set
      (send_email egress, run_command arbitrary shell, file_control
      incl. delete, uninstall/power/process/service control, bulk
      delete/rename, write_file + export tools, deletions, and
      extension/mission/sandbox state changes);
    - money_guard._MONEY_TOOL_NAMES / _MONEY_ACTIONS:
      financial scope.
    Fail closed: if either module cannot be imported, the tool is
    exempt (it never batches).
    """
    try:
        from .funnel import tools_gate as _tg  # Phase 3 F1: tools_gate moved to core/funnel/

        if tool_name in _tg._CONFIRM_GATE_TOOLS:
            return True
    except Exception:  # noqa: BLE001 — undecidable: exempt
        return True
    try:
        from .funnel import money_guard as _mg  # Phase 3 F2b: money_guard moved to core/funnel/

        if tool_name in _mg._MONEY_TOOL_NAMES:
            return True
        if isinstance(args, dict):
            for key in ("action", "operation", "verb"):
                value = args.get(key)
                if isinstance(value, str) and \
                        value.strip().lower() in _mg._MONEY_ACTIONS:
                    return True
    except Exception:  # noqa: BLE001 — undecidable: exempt
        return True
    return False
