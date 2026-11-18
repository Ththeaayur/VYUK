"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

from typing import Any



# HE-4 / HE-6 MCP trial classification (see core/mcp_trials.py).
# Trial tools ride the Wave 1 bus/toolbox ONLY — they are
# never fitted, never registered, and a trial call is still
# judged by mcp_tool_class / mcp_needs_confirmation above.
# These two predicates are the trial harness's load-bearing
# config checks, in code rather than in prose.
#: Tool-name fragments that are trial-BLOCKED for HE-4: any
#: MCP tool whose name carries one of these is never called
#: by the trial, whatever the server advertises. FileSystem
#: is Windows-MCP's documented tool family (its README's
#: desktop-gate table lists FileSystem among the tools);
#: the YAML fragment covers YAML-editing tools by name.
#: PowerShell and Registry are excluded in the server
#: config itself (see core/mcp_trials.py), not here —
#: this set is the harness-side second lock, applied to
#: the LIVE tool listing, so a renamed or added tool with
#: one of these fragments still cannot slip through.
TRIAL_BLOCKED_TOOL_FRAGMENTS = frozenset({"file", "yaml"})


def trial_tool_blocked(composed_name: str) -> bool:
    """True when a trial MCP tool name is blocked: its
    tool part (after the last "__") contains a blocked
    fragment (case-insensitive). Server names are not
    judged — a server called "file-server" is not a file
    tool; only the tool part counts."""
    tool = str(composed_name or "").rsplit("__", 1)[-1].lower()
    return any(frag in tool for frag in TRIAL_BLOCKED_TOOL_FRAGMENTS)


def windows_mcp_telemetry_preset_ok(env: Any) -> bool:
    """True only when the trial config's env sets the
    documented telemetry preset EXACTLY: the string
    "false" for ANONYMIZED_TELEMETRY (Windows-MCP README,
    Telemetry section + Environment Variables table,
    2026-10-25). Any other value — including a boolean
    False, "0", or a missing key — is NOT ok: the bus
    passes env values through as strings, and a mistyped
    preset that silently re-enables telemetry is exactly
    the failure this check exists to catch. NOTE (honest
    limit, Wave 4): this proves the PRESET in config. It
    does NOT prove from source that the analytics class is
    never instantiated under that preset, nor does it
    prove zero outbound calls to us.i.posthog.com — both
    need the live trial environment (Windows, Python
    3.14+, the windows-mcp package — none present in this
    VM), so HE-4 stays at harness design until then."""
    if not isinstance(env, dict):
        return False
    return env.get("ANONYMIZED_TELEMETRY") == "false"
