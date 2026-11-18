"""MCP trial harness (v5.59, Wave 4 HE-6 + HE-4) — TRIAL ONLY.

These servers are EXTERNAL: they are never bundled, never
registered, never fitted, and never a shipped default.
A trial server is configured by the USER in their own
mcp_servers.json (Wave 1 bus shape) and reached ONLY
through the Wave 1 bus + toolbox + tools.execute funnel
— the same validation, failsafe, confirmation-gate,
budget, deadline and audit stages as a native call
(mcp_tool_class / mcp_needs_confirmation in
core/funnel/tools_gate.py). Trial tools never enter the native
registry, so the 480-tool count and the 229,785 B fitted
payload are untouched by any trial.

HE-6 (desktop-automation MCP): the candidate is
tanob/mcp-desktop-automation (npx, Node >= 14), whose
directory listing states MIT. Licence recorded from
that listing (2026-10-25); the trial itself does not
depend on the licence text, because nothing from the
server is shipped — it runs as an external process
under trial-only terms, and the harness below can be
exercised against stub bus entries without the server
present at all.

HE-4 (Windows-MCP, UIA toolbox): MIT (GitHub repo
page, 2026-10-25). TWO load-bearing conditions, both
checked IN CONFIG/CODE by this module, not assumed:
  (a) TELEMETRY: env ANONYMIZED_TELEMETRY must be
      exactly the string "false" (Windows-MCP README,
      Telemetry section + Environment Variables table).
      HONEST LIMIT: that proves the preset in config.
      It does NOT prove from source that the analytics
      class is never instantiated under that preset,
      and it does NOT prove zero outbound calls to
      us.i.posthog.com. The harness therefore carries a
      zero-outbound proof DESIGN (OutboundGuard: a
      socket-resolution block + log for the PostHog
      hosts, exercised in tests) — but LIVE zero-call
      proof needs the trial environment: Windows,
      Python 3.14+ (the current package's own
      requirement, per its README) and the windows-mcp
      package. NONE of those is present in this VM
      (Python 3.12.3, no windows-mcp package), so the
      live HE-4 trial does NOT proceed past harness
      design in this wave. The config is ready; the
      run is owed, and saying otherwise would be a lie.
  (b) FILESYSTEM/YAML GATED OFF: the server config
      excludes FileSystem, PowerShell and Registry via
      BOTH documented mechanisms (--exclude-tools arg
      AND WINDOWS_MCP_EXCLUDE_TOOLS env, README Tool
      Selection + Environment Variables), and the
      harness-side second lock (tools_gate.
      trial_tool_blocked) refuses any LIVE-listed tool
      whose tool-part name contains "file" or "yaml"
      before the bus is ever called — the HM-1 lesson,
      applied to the listing rather than to a name
      guessed in advance.

No trial writes to the shipped tree or to data/: the
builders return plain dicts, and any file write is the
caller's choice (tests use tmp paths). No trial touches
a password field or a CAPTCHA: trial tools are
classified by the bus's annotations, fail-closed, and
any confirm-class tool still asks through the funnel.
"""

from __future__ import annotations

import socket
from typing import Any

#: PostHog hosts the zero-outbound proof design blocks.
#: us.i.posthog.com is the documented US capture host
#: (PostHog capture docs); eu.i.posthog.com is its EU
#: counterpart, blocked too so a region flip cannot
#: quietly re-enable the lane.
POSTHOG_HOSTS = frozenset({"us.i.posthog.com", "eu.i.posthog.com"})

#: HE-4 server-side exclusions (both mechanisms, README).
HE4_EXCLUDED_TOOLS = ("FileSystem", "PowerShell", "Registry")


class BlockedHostError(OSError):
    """Raised when the outbound guard blocks a host."""


class OutboundGuard:
    """Zero-outbound proof design for HE-4 (a).

    A context manager that patches socket.getaddrinfo so
    any resolution of a PostHog host raises
    BlockedHostError and is logged (host + port, never
    payload — there is no payload at resolution time).
    Everything else resolves normally. In the live trial
    this guard wraps the whole server session: a
    telemetry attempt then FAILS LOUDLY in the trial
    log instead of silently succeeding, and an empty
    log at session end is the zero-call evidence. The
    guard is a harness instrument, not a product
    feature: it is never installed by shipped code.
    """

    def __init__(self) -> None:
        self.attempts: list[dict[str, Any]] = []
        self._original: Any = None

    def _guarded(self, host: Any, port: Any, *args: Any,
                 **kwargs: Any) -> Any:
        if isinstance(host, str) and host.lower() in POSTHOG_HOSTS:
            self.attempts.append({"host": host, "port": port})
            raise BlockedHostError(
                f"Outbound trial guard blocked {host} — "
                "telemetry host, zero-outbound trial.")
        return self._original(host, port, *args, **kwargs)

    def __enter__(self) -> "OutboundGuard":
        self._original = socket.getaddrinfo
        socket.getaddrinfo = self._guarded  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: Any) -> bool:
        socket.getaddrinfo = self._original
        self._original = None
        return False


def he6_config() -> dict[str, Any]:
    """Trial-only HE-6 config (desktop-automation MCP).

    Returned, never written: the user drops the
    mcpServers entry into their own mcp_servers.json
    if they run the trial. ``trial_only`` is a harness
    marker, not a bus key — the bus ignores unknown
    keys, and validate_trial_config checks the marker
    before the bus ever sees the entry."""
    return {
        "trial_only": True,
        "trial": "HE-6",
        "mcpServers": {
            "desktop-automation": {
                "command": "npx",
                "args": ["-y", "mcp-desktop-automation"],
                "enabled": True,
            },
        },
    }


def he4_config() -> dict[str, Any]:
    """Trial-only HE-4 config (Windows-MCP, UIA toolbox).

    Telemetry preset + filesystem/YAML gating are IN
    the config (see module docstring): env
    ANONYMIZED_TELEMETRY="false", --exclude-tools AND
    WINDOWS_MCP_EXCLUDE_TOOLS both naming FileSystem,
    PowerShell, Registry. stdio transport only (local
    process, no network listener). The trial does NOT
    run in this VM — see the honest limit in the
    module docstring — but a config that fails
    validate_trial_config must never be run anywhere.
    """
    excluded = ",".join(HE4_EXCLUDED_TOOLS)
    return {
        "trial_only": True,
        "trial": "HE-4",
        "mcpServers": {
            "windows-mcp": {
                "command": "uvx",
                "args": ["windows-mcp", "serve",
                         "--exclude-tools", excluded],
                "env": {
                    "ANONYMIZED_TELEMETRY": "false",
                    "WINDOWS_MCP_EXCLUDE_TOOLS": excluded,
                },
                "enabled": True,
            },
        },
    }


def validate_trial_config(cfg: dict[str, Any]) -> list[str]:
    """Validate one trial config. Returns a list of
    problems (empty = trial-ready AS CONFIG — for HE-4
    this is necessary but NOT sufficient for a live run,
    per the module docstring's honest limit).

    Checks, all in code: trial_only marker present;
    every server entry passes the Wave 1 bus validator
    (core/mcp_client.validate_server_config); HE-4
    entries additionally carry the exact telemetry
    preset (tools_gate.windows_mcp_telemetry_preset_ok)
    AND exclude FileSystem in BOTH mechanisms AND carry
    no --tools whitelist naming a file/yaml tool."""
    from . import mcp_client as _mcp
    from .funnel.tools_gate import windows_mcp_telemetry_preset_ok  # Phase 3 F1: tools_gate moved to core/funnel/

    problems: list[str] = []
    if not isinstance(cfg, dict) or not cfg.get("trial_only"):
        problems.append("Trial config is not marked trial_only.")
    servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
    if not isinstance(servers, dict) or not servers:
        problems.append("Trial config has no mcpServers.")
        return problems
    for name, raw in servers.items():
        cleaned, error = _mcp.validate_server_config(name, raw)
        if error is not None:
            problems.append(f"Server '{name}': {error}")
            continue
        if name == "windows-mcp":
            env = (cleaned or {}).get("env") or {}
            if not windows_mcp_telemetry_preset_ok(env):
                problems.append(
                    "windows-mcp: ANONYMIZED_TELEMETRY is not "
                    "exactly 'false'.")
            excluded_env = env.get("WINDOWS_MCP_EXCLUDE_TOOLS", "")
            excluded_set = {t.strip().lower()
                            for t in str(excluded_env).split(",")
                            if t.strip()}
            if "filesystem" not in excluded_set:
                problems.append(
                    "windows-mcp: WINDOWS_MCP_EXCLUDE_TOOLS does "
                    "not exclude FileSystem.")
            args = (cleaned or {}).get("args") or []
            if "--exclude-tools" in args:
                idx = args.index("--exclude-tools")
                flag_set = set()
                if idx + 1 < len(args):
                    flag_set = {t.strip().lower()
                                for t in args[idx + 1].split(",")
                                if t.strip()}
                if "filesystem" not in flag_set:
                    problems.append(
                        "windows-mcp: --exclude-tools does not "
                        "exclude FileSystem.")
            else:
                problems.append(
                    "windows-mcp: --exclude-tools flag missing.")
            if "--tools" in args:
                idx = args.index("--tools")
                if idx + 1 < len(args):
                    for tool in args[idx + 1].split(","):
                        low = tool.strip().lower()
                        if "file" in low or "yaml" in low:
                            problems.append(
                                f"windows-mcp: --tools whitelist "
                                f"names a blocked tool '{tool}'.")
    return problems


def trial_tool_class(entry: dict[str, Any]) -> str:
    """Classify one trial MCP tool: "blocked" (never
    called — the HE-4 second lock), else the Wave 1 bus
    class ("read" / "egress" / "confirm")."""
    from .funnel.tools_gate import mcp_tool_class, trial_tool_blocked  # Phase 3 F1: tools_gate moved to core/funnel/

    if trial_tool_blocked(entry.get("composed", "")):
        return "blocked"
    return mcp_tool_class(entry)


def trial_call_allowed(entry: dict[str, Any],
                       tainted: bool = False,
                       changed: bool = False) -> tuple[bool, str]:
    """Would the funnel allow this trial call? The trial
    harness calls this BEFORE any bus call: blocked
    tools are refused here (never reaching the bus),
    and everything else is judged by the Wave 1 MCP
    gate predicate (mcp_needs_confirmation) with the
    trial's taint/changed flags. This is a harness
    pre-check — the bus call itself, if reached, still
    goes through tools.execute's own MCP branch."""
    from .funnel.tools_gate import mcp_needs_confirmation  # Phase 3 F1: tools_gate moved to core/funnel/

    if trial_tool_class(entry) == "blocked":
        return False, (f"Trial tool '{entry.get('composed')}' "
                       "is blocked (filesystem/YAML lock).")
    # mcp_needs_confirmation reads live taint state; the
    # harness passes its flags through only when they
    # are set — a changed hash or a tainted session
    # must ask, exactly as the funnel would.
    if changed or tainted:
        return False, (f"Trial tool '{entry.get('composed')}' "
                       "needs confirmation (changed/tainted) — "
                       "the trial harness does not auto-confirm.")
    if mcp_needs_confirmation(entry, {}, changed):
        return False, (f"Trial tool '{entry.get('composed')}' "
                       "needs confirmation under the MCP gate.")
    return True, ""
