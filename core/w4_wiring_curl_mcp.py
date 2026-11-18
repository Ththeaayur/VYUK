# w4_wiring_curl_mcp.py — split from core/w4_wiring.py (Phase B B12): curl_cffi read lane + MCP trial calls.
# Move only; the facade in core/w4_wiring.py re-exports every name.

from __future__ import annotations  # keep def-time annotation semantics identical to the old module



# ---------------------------------------------------------------------------
# Item 7 — curl_cffi read lane (live read path through the gate)
# ---------------------------------------------------------------------------

def curl_read(
    url: str,
    *,
    cookies: list[dict[str, Any]] | None = None,
    cookie_source: str | None = None,
    impersonate: str = "chrome",
    timeout: float = 30.0,
    _transport: Any = None,
) -> dict[str, Any]:
    """One live read through the curl_cffi lane, gated twice:
    this wrapper checks the tools_gate classification first,
    then the lane module re-checks everything (HTTPS only,
    cookie provenance, impersonation allowlist, taint
    refusal) before any network I/O.

    Cookies MUST come from her own Playwright browser
    context: the caller supplies them plus the provenance
    stamp. With no cookies supplied this refuses in plain
    words — the lane never reads a cookie file, never takes
    a cookie path, and this wrapper invents no provenance
    stamp for cookies it did not receive from that context.
    Cookie values are never logged or returned.
    """
    from .funnel.tools_gate import (
        CURL_CFFI_COOKIE_SOURCE,
        curl_cffi_lane_class,
    )  # Phase 3 F1: tools_gate moved to core/funnel/
    from . import curl_cffi_lane as _lane

    if curl_cffi_lane_class() != "egress":
        return {
            "status": "error", "kind": "refused",
            "message": "The curl_cffi read lane is not classified "
                       "as egress — refused.",
        }
    if cookies is None:
        return {
            "status": "error", "kind": "refused",
            "message": (
                "No Playwright-context cookies were supplied "
                "for the read lane. The lane accepts cookies "
                "only from her own Playwright browser context "
                "and never reads a cookie file, so no read "
                "is attempted without them."
            ),
        }
    if cookie_source != CURL_CFFI_COOKIE_SOURCE:
        return {
            "status": "error", "kind": "refused",
            "message": (
                "Cookies for the read lane must come from her "
                "own Playwright context "
                "(cookie_source='playwright_context'). No other "
                "cookie source is accepted."
            ),
        }
    return _lane.fetch_read(
        url, cookies, cookie_source=CURL_CFFI_COOKIE_SOURCE,
        impersonate=impersonate, timeout=timeout,
        _transport=_transport,
    )




def curl_cffi_read_tool(args: dict[str, Any],
                        _transport: Any = None) -> dict[str, Any]:
    """Tool-shaped handler for the READY curl_cffi_read
    declaration (coordinator dispatch target). The funnel
    supplies url/impersonate/timeout; cookies come from the
    dispatching handler's live Playwright context — until
    the coordinator wires that caller, cookies are absent
    and curl_read() refuses honestly (see above)."""
    if not isinstance(args, dict):
        return {"status": "error", "kind": "validation",
                "message": "curl_cffi_read needs an arguments object."}
    return curl_read(
        args.get("url", ""),
        cookies=args.get("cookies"),
        cookie_source=args.get("cookie_source"),
        impersonate=args.get("impersonate", "chrome"),
        timeout=float(args.get("timeout", 30.0)),
        _transport=_transport,
    )




# ---------------------------------------------------------------------------
# Item 8 — MCP trial calls through ToolRegistry.execute only
# ---------------------------------------------------------------------------

def mcp_trial_configs() -> dict[str, Any]:
    """The HE-4 / HE-6 trial configs as data for the trial
    runner (no server is launched, no bus is touched)."""
    from . import mcp_trials as _trials

    return {"he4": _trials.he4_config(), "he6": _trials.he6_config()}




def mcp_trial_call(
    entry: dict[str, Any],
    executor: Callable[[str, dict[str, Any]], dict[str, Any]],
    *,
    approved_entries: Any = None,
    tainted: bool = False,
    changed: bool = False,
) -> dict[str, Any]:
    """Call ONE MCP trial tool through the funnel executor
    (in the shipped path, ToolRegistry.execute, which routes
    mcp__<server>__<tool> names to its MCP branch with the
    Wave 1 gate, failsafe, confirmation, budget and audit
    stages intact).

    Refusal order, all before any bus call: (1) the entry
    must be on the caller-supplied approved list — with no
    list supplied (the state today: no approval exists) the
    call is refused; (2) the harness gate predicate
    (trial_call_allowed) must pass — blocked file/YAML
    fragments and confirm-class calls without confirmation
    are refused here and never reach the bus. A non-dict
    executor result is wrapped, never trusted as success.
    """
    from . import mcp_trials as _trials

    if not isinstance(entry, dict):
        return {"status": "error", "kind": "validation",
                "message": "MCP trial entry must be an object."}
    composed = entry.get("composed", "")
    if approved_entries is None or composed not in approved_entries:
        return {
            "status": "error", "kind": "refused",
            "message": (
                f"Trial tool '{composed}' is not on an approved "
                "trial list — no approved MCP trial list exists "
                "yet (decision owed), so the trial call is "
                "refused before the funnel."
            ),
        }
    allowed, reason = _trials.trial_call_allowed(
        entry, tainted=tainted, changed=changed)
    if not allowed:
        return {"status": "error", "kind": "refused", "message": reason}
    result = executor(composed, dict(entry.get("args") or {}))
    if not isinstance(result, dict):
        return {"status": "error", "kind": "handler_error",
                "message": "Trial executor returned a non-dict result."}
    return result
