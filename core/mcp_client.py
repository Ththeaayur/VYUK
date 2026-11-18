"""The MCP bus (v5.55, Wave 1 HE-1/HE-5): VYUK's client for Model
Context Protocol servers, on FastMCP over the official MCP SDK.

Design, and why each piece is shaped this way:

* ONE standard, not both. The client framework is FastMCP
  (``fastmcp.Client``), which is built on the official ``mcp``
  SDK — every tool, schema and result type below is the SDK's
  own. The single exception is the OAuth path: FastMCP's own
  client-credentials auth does not surface an ``issuer=``
  parameter (verified by reading fastmcp 4.1.3's
  client/auth/client_credentials.py — no ``issuer`` anywhere),
  and the CVE pin (CVE-2026-115563) makes ``issuer=`` the fix,
  not an option. So OAuth M2M servers drop one level to the
  SDK's own providers (``ClientCredentialsOAuthProvider`` /
  ``PrivateKeyJWTOAuthProvider``), constructed with ``issuer=``
  always. That is one protocol standard with an SDK-level
  escape hatch the pin forces — not two competing stacks.

* Servers are CONFIGURED, not hardcoded. The runtime reads
  ``mcp_servers.json`` beside ``.env`` (PROJECT_ROOT) in
  FastMCP MCPConfig shape (``{"mcpServers": {...}}``). The
  built-in defaults below (Fetch, Time enabled; Git disabled
  until the user points it at a repository; munim-computer-use
  disabled until it is installed — R7-1) apply only when no
  file exists, and a file MERGES by server name: an entry
  overrides or extends the default of the same name, and
  ``"enabled": false`` disables a default. The file is user
  state, never shipped: the ZIP carries no ``mcp_servers.json``
  (same rule as ``.env``), so extract-over updates preserve it.

* Secrets are never IN the file. stdio ``env`` values that
  start with ``$`` resolve from the process environment;
  OAuth client secrets arrive ONLY as ``client_secret_env``
  (the NAME of an environment variable). A literal
  ``client_secret`` key, a missing ``issuer``, or an
  interactive (authorization-code) auth type makes that
  server dead with a plain-words reason — fail-closed, never
  guessed. ``issuer`` is REQUIRED for every M2M OAuth server:
  without it the SDK would build token requests from whatever
  metadata the server advertises, which is exactly the
  CVE-2026-115563 hole (the pin is ``mcp`` >= 1.30.0 or
  >= 2.2.0 AND ``issuer=`` passed — the version floor alone
  is NOT the fixed behaviour).

* Untrusted by default (INV-2/3/4, safeguard S-4). MCP tools
  never enter the native registry: they live in this module's
  table, they are surfaced ONLY through ``use_toolbox``
  find/describe/call, and they execute ONLY through
  ``ToolRegistry.execute``'s MCP branch, which runs the same
  validation, failsafe, protected-path, confirmation-gate,
  budget, deadline and audit steps as a native call. The
  fitted Gemini declarations never see them (INV-1: the
  registry stays 480 tools, the fitted payload stays
  229,785 B) — the toolbox is the only door, and the funnel
  is the only corridor.

* Lazy, bounded, plain-words failure. The bus starts no
  process and opens no socket at import or at app start:
  servers connect lazily on first find/call, each connect
  bounded (15 s), each call bounded by the executor deadline.
  A dead server fails as "toolbox unavailable" in plain
  words — never a traceback, never a hang, never a silent
  retry storm: dead state is cached until an explicit
  refresh, so one dead server costs one timeout per process,
  not one per call.

* Telemetry OFF (INV-19, Q3). The MCP SDK client path
  carries no analytics (its ``telemetry`` hits are server-side
  OpenTelemetry instrumentation files, unused by the client).
  FastMCP's client telemetry is OpenTelemetry spans with NO
  exporter configured — the module's own docstring says it is
  a no-op unless the user installs an SDK and configures
  exporters. This bus configures neither, and no exporter is
  ever created here. Verified by source read of
  mcp 2.3.0 + fastmcp-slim 4.1.3 (see the ship report).

* Description-hash pinning (SE-5.12's runtime half). On
  every tool listing, each tool's (name + description +
  inputSchema) is hashed and compared with the last-seen
  hash in ``data/mcp_tool_hashes.json`` (runtime state, never
  shipped). A changed hash marks that tool CONFIRM-class for
  the rest of the session and logs a warning: a tool whose
  description changed since approval IS a new tool, and it
  re-confirms. (SE-5.12's gate half — mcp-scan over real
  server configs — stays owed behind O-24a's posture
  verification and is NOT built here.)

Qt-free, stdlib-only at import (INV-12): ``fastmcp``/``mcp``/
``anyio`` are imported lazily inside the bus thread only, so
the registry, the doctor and headless imports work with the
libraries absent — the bus then reports itself unavailable
instead of breaking the import.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import queue
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit

# INTENTIONAL re-exports (P2 facade): every top-level name of the old
# module is re-exported here so importers and dir() are unchanged.
from .mcp_client_config import (
    PROJECT_ROOT,
    CONFIG_FILENAME,
    CONNECT_TIMEOUT_S,
    DEFAULT_CALL_TIMEOUT_S,
    DEFAULT_SERVERS,
    _SERVER_NAME_RE,
    _SUPPORTED_AUTH_TYPES,
    parse_enabled_flag,
    _CREDENTIAL_HEADER_EXACT,
    _CREDENTIAL_HEADER_SUBSTRINGS,
    _header_carries_credential,
    _is_loopback_url,
    config_path,
    hashes_path,
    _resolve_env_value,
    validate_server_config,
    load_config,
)
from .mcp_client_tools import (
    compose_tool_name,
    _tool_hash,
    _load_hashes,
    _save_hashes,
    _result_to_text,
    _hint,
    _annotations_of,
    _parameters_of,
)
from .mcp_client_session import (
    _MemoryTokenStorage,
    _SDKSession,
)
from .mcp_client_bus import (
    _SessionBroken,
    _ServerState,
    MCPBus,
    _BUS,
    get_bus,
)
