"""MCP client — config + validation (split from core/mcp_client.py, Phase B B23)."""
from __future__ import annotations

import ipaddress
import json
import os
from . import config_spec
import re
import sys  # INTENTIONAL (B23 gateC seam): _facade_root reads sys.modules
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit

# --- SPLIT BODY BELOW (verbatim from core/mcp_client.py) ---

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

#: The tree root (same derivation as core/config.py: this file
#: lives in core/, so parent.parent is the package folder).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: User server config (never shipped — same rule as .env).
CONFIG_FILENAME = "mcp_servers.json"

#: Connect timeout for one server (seconds). Bounded, so a dead
#: server costs one timeout per process, not one per call.
CONNECT_TIMEOUT_S = 15.0

#: Tool-call timeout when the executor gives no deadline.
DEFAULT_CALL_TIMEOUT_S = 120.0

#: Reference servers, pinned (HE-5). Licences verified from the
#: distributed wheels, 2026-10-24: Fetch MIT, Git MIT, Time MIT
#: (METADATA licence declaration; see the ship report). Console
#: scripts, installed pinned (==2026.10.19) — never uvx @latest.
#: Git ships DISABLED: it needs a repository path, and a
#: state-changing tool must never default to an arbitrary repo.
DEFAULT_SERVERS: dict[str, dict[str, Any]] = {
    "fetch": {
        "command": "mcp-server-fetch",
        "args": [],
        "enabled": True,
    },
    "time": {
        "command": "mcp-server-time",
        "args": [],
        "enabled": True,
    },
    "git": {
        "command": "mcp-server-git",
        "args": ["--repository", "PATH-TO-YOUR-REPO"],
        "enabled": False,
        "note": ("Point this at a repository to enable it: set "
                 "args to [\"--repository\", \"C:\\\\path\\\\to\\\\repo\"] "
                 "in mcp_servers.json."),
    },
    "munim-computer-use": {
        # R7-1 (2026-11-10): the round-7 #1 pick — an open-source
        # computer-use MCP server (38 tools): accessibility-tree
        # screen reads, background clicks/types so the user's
        # mouse stays theirs, a visible agent pointer, zoom,
        # and driving the user's signed-in Chrome. Apache-2.0
        # (licence read from the repo LICENSE file, 2026-11-10).
        # The server is an EXTERNAL binary, never bundled: the
        # npm launcher below fetches the signed binary on first
        # run (needs Node.js), or point "command" at a
        # downloaded binary instead. DISABLED until the user
        # installs it and enables it — a desktop-control server
        # must never default on. Its browser_* tools need the
        # Chrome extension + native messaging host; its tools
        # are confirm-gated until operator-reviewed
        # (core.mcp_policy), like any unreviewed MCP tool.
        "command": "npx",
        "args": ["-y", "munim-computer-use"],
        "enabled": False,
        "note": ("To enable: copy this whole entry into "
                 "mcp_servers.json with \"enabled\": true (a file "
                 "entry replaces the default, so include command "
                 "and args). First run needs Node.js — the npm "
                 "launcher fetches the signed binary. Or download "
                 "the binary from the project's Releases and point "
                 "\"command\" at it. Browser tools need the "
                 "Chrome extension + native messaging host."),
    },
}

_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

#: OAuth types the bus accepts at CONFIG VALIDATION time (M2M,
#: non-interactive). Anything else — above all
#: authorization_code — is dead-by-config: the interactive
#: browser-callback flow is a later wave's item, and silently
#: downgrading to it would hang a headless call.
#: private_key_jwt is deliberately NOT here:
#: Gate C (master audit P2-7) rejects it at validation with a
#: "not implemented" error instead of accepting it here and
#: failing later at connect — the connector has no signing
#: callback a JWT assertion needs. The connect-time branch in
#: _build_client stays only as defense-in-depth for a config
#: built programmatically, bypassing validation.
_SUPPORTED_AUTH_TYPES = ("client_credentials",)


def parse_enabled_flag(name: str, raw: Any) -> tuple[bool | None, str | None]:
    """Gate C (master audit P1-9): strict "enabled" parsing for
    one server entry. Returns (enabled, None) or (None, reason).

    ONLY a genuine JSON boolean enables or disables. The old
    code did bool(raw.get("enabled", True)), so the STRING
    "false" — the single most likely typo in a JSON config —
    evaluated True and ENABLED the server. That truthiness hole
    is closed here, and the choice for every other non-bool
    value is documented, not accidental:

    * absent -> True (the historical default, unchanged);
    * True -> enabled, False -> disabled;
    * anything else — "false"/"true" in any case, 1, 0, None,
      a list, an object — -> a CONFIG ERROR, not a silent
      disable. Rationale: silently disabling a server the user
      thought they enabled (they wrote "true") would be a
      second footgun replacing the first; a loud error names
      the exact fix (write a JSON boolean)."""
    if "enabled" not in raw:
        return True, None
    value = raw["enabled"]
    if type(value) is bool:
        return value, None
    return None, (
        f"Server '{name}' sets \"enabled\" to {value!r} — enabled "
        "must be a JSON boolean (true or false), not a string or "
        "number. Write true or false without quotes.")


#: Header names that carry credentials outright.
_CREDENTIAL_HEADER_EXACT = {
    "authorization", "proxy-authorization", "cookie",
}

#: Substrings (lowercased) marking a header name credential-
#: bearing. A header like Accept or X-Client-Version matches
#: none of these and does NOT count as credentials.
_CREDENTIAL_HEADER_SUBSTRINGS = (
    "token", "apikey", "api-key", "api_key", "auth", "secret",
    "access-key", "access_key",
)


def _header_carries_credential(header_name: str) -> bool:
    """True when one request header carries credentials (an
    Authorization bearer, an API key/token header, a cookie).
    Benign headers (Accept, content types, client versions)
    return False, so a plain http:// server with only benign
    headers is unchanged by the cleartext rule."""
    low = header_name.strip().lower()
    if low in _CREDENTIAL_HEADER_EXACT:
        return True
    return any(sub in low for sub in _CREDENTIAL_HEADER_SUBSTRINGS)


def _is_loopback_url(url: str) -> bool:
    """True when a URL's host is loopback: localhost (any case,
    optional trailing dot, or a .localhost suffix) or an IP
    address ipaddress calls loopback (127.0.0.0/8, ::1). An
    unparseable host is NOT loopback — fail closed toward
    refusal for credentialed cleartext."""
    try:
        host = urlparse(url).hostname
    except Exception:  # noqa: BLE001 — unparseable, not loopback
        return False
    if not host:
        return False
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _facade_root() -> Path:
    # INTENTIONAL (B23 gateC seam, NOT a behaviour change for unpatched
    # loads): test_gateC.TestLoopbackWarningSurfacedByDoctor patches
    # PROJECT_ROOT through the FACADE module object (the
    # voice_assistant.core.mcp_client load), and doctor.check_mcp_servers
    # reaches load_config through that same facade object. A bare-name
    # PROJECT_ROOT here would resolve in THIS module's globals, so the
    # facade patch would silently miss (proven by the neighbour run:
    # gateC failed live, passed on backup). Resolving through the sibling
    # facade module object at call time makes the patch land, per load
    # (core.* and voice_assistant.core.* each pick their own sibling via
    # __package__). When no facade is loaded (direct impl import), fall
    # back to this module's own PROJECT_ROOT — identical value.
    facade = sys.modules.get(__package__ + ".mcp_client")
    if facade is not None:
        root = getattr(facade, "PROJECT_ROOT", None)
        if root is not None:
            return root
    return PROJECT_ROOT


def config_path() -> Path:
    """Where the user's server config lives (never shipped).

    Overridable via VYUK_MCP_CONFIG_PATH for tests, so suites
    never touch the real config file (the hashes_path
    VYUK_MCP_HASHES_PATH precedent).
    """
    override = config_spec.explicit("VYUK_MCP_CONFIG_PATH")
    if override:
        return Path(override)
    return _facade_root() / CONFIG_FILENAME


def hashes_path() -> Path:
    """Description-hash store (runtime state, never shipped).

    Overridable via VYUK_MCP_HASHES_PATH for tests, so suites
    never touch the real data/ directory.
    """
    override = config_spec.explicit("VYUK_MCP_HASHES_PATH")
    if override:
        return Path(override)
    return _facade_root() / "data" / "mcp_tool_hashes.json"


def _resolve_env_value(value: Any) -> Any:
    """Resolve one stdio env value: ``$NAME`` reads the process
    environment (missing => None, which kills the server with a
    plain-words reason); anything else passes through as-is."""
    if isinstance(value, str) and value.startswith("$") and len(value) > 1:
        return os.environ.get(value[1:])
    return value


def validate_server_config(name: str, raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one server entry, fail-closed.

    Returns (cleaned_config, None) or (None, plain-words reason).
    Nothing here raises: a bad entry makes that server dead,
    never the bus.

    Gate C rules enforced here (validate_server_config is the
    ONLY table builder — load_config is its only caller — so
    every server a connect can reach has passed this gate):
    * P1-9: "enabled" must be a genuine JSON boolean (see
      parse_enabled_flag); the string "false" no longer enables.
    * P1-8: credentials over plain http:// to a non-loopback
      host are refused; loopback http with credentials is
      allowed ONLY with cfg["cleartext_loopback_warning"] set
      (the doctor surfaces it as a WARN).
    * L1 (LOG-01/LOG-02): the url is parsed ONCE with
      urlsplit and every decision is taken from the parsed
      object. URL userinfo (username/password in the url) is
      refused outright on every host, including loopback —
      it is credentials, and it is never echoed in an error.
      The scheme is case-insensitive (normalized to lower
      case); a hostname is required; an invalid port or a
      malformed authority is refused here, not at connect.
    * P2-7: private_key_jwt is rejected here as not
      implemented, never accepted-then-failed at connect.
    """
    if not isinstance(raw, dict):
        return None, f"Server '{name}' is not an object in {CONFIG_FILENAME}."
    if not _SERVER_NAME_RE.match(name):
        return None, (f"Server name '{name}' is invalid — use letters, "
                       "digits, '_', '.' or '-'.")
    if "__" in name:
        return None, (f"Server name '{name}' contains '__' — the "
                       "toolbox namespace separator — so the server "
                       "cannot be addressed. Rename it.")
    cfg: dict[str, Any] = {"name": name}
    enabled, enabled_err = parse_enabled_flag(name, raw)
    if enabled_err is not None:
        return None, enabled_err
    cfg["enabled"] = enabled
    command = raw.get("command")
    url = raw.get("url")
    if command and url:
        return None, (f"Server '{name}' sets both command and url — "
                       "pick one transport.")
    if command:
        if not isinstance(command, str) or not command.strip():
            return None, f"Server '{name}' has an empty command."
        args = raw.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            return None, f"Server '{name}' args must be a list of strings."
        cfg["transport"] = "stdio"
        cfg["command"] = command
        cfg["args"] = list(args)
        env = raw.get("env", {})
        if not isinstance(env, dict):
            return None, f"Server '{name}' env must be an object."
        cfg["env"] = dict(env)
        if raw.get("auth") is not None:
            return None, (f"Server '{name}' is a stdio server — auth "
                           "belongs on remote (url) servers, not here.")
    elif url:
        # L1 (LOG-01/LOG-02): parse the URL ONCE and decide
        # everything from the parsed object — never from string
        # prefixes. The old prefix check accepted
        # "https:///missing-host" (no host at all), rejected a
        # valid uppercase scheme, and never looked at URL
        # userinfo, so credentials embedded in the URL slipped
        # past the Gate C cleartext rule entirely.
        if not isinstance(url, str):
            return None, (f"Server '{name}' url must be a valid "
                           "http:// or https:// URL.")
        if any(ch.isspace() for ch in url):
            return None, (f"Server '{name}' url must not contain "
                           "whitespace.")
        try:
            parsed = urlsplit(url)
            parsed.port  # raises ValueError on an invalid port
        except ValueError:
            return None, (f"Server '{name}' url is malformed — it "
                           "must be a valid http:// or https:// "
                           "URL with a hostname.")
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return None, (f"Server '{name}' url must start with "
                           "http:// or https://.")
        if not parsed.hostname:
            return None, (f"Server '{name}' url must have a "
                           "hostname.")
        if parsed.username or parsed.password:
            # LOG-01: URL userinfo IS credentials, on every host
            # including loopback — refused outright, not routed
            # through the loopback-warning path. Use the
            # supported auth fields instead. The raw URL is
            # NEVER echoed here: userinfo would leak into logs
            # and diagnostics through this very message.
            return None, (
                f"Server '{name}' puts a username or password in "
                "the url itself — remove it from the url and use "
                "the supported auth fields instead.")
        # Schemes are case-insensitive: store the normalized
        # form so every downstream consumer (including the
        # loopback check below) sees the same URL.
        url = parsed._replace(scheme=scheme).geturl()
        cfg["transport"] = "remote"
        cfg["url"] = url
        headers = raw.get("headers", {})
        if not isinstance(headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            return None, f"Server '{name}' headers must be string pairs."
        cfg["headers"] = dict(headers)
        # Gate C (P1-8): credentials must never travel over
        # cleartext. An auth object counts as credentials (its
        # token reaches this URL), as does any credential-
        # bearing header; benign headers do not trigger this.
        # URL userinfo is refused outright above, so it never
        # reaches this check. Non-loopback http + credentials
        # is a HARD refusal. Loopback http + credentials is
        # allowed ONLY with an explicit warning recorded on
        # the config (the doctor surfaces it as WARN): local
        # traffic is still unencrypted, and https is preferred
        # even locally. https + credentials, and http without
        # credentials, are unchanged by this rule.
        _has_credentials = raw.get("auth") is not None or any(
            _header_carries_credential(k) for k in headers)
        if scheme == "http" and _has_credentials:
            if not _is_loopback_url(url):
                return None, (
                    f"Server '{name}' sends credentials over plain "
                    "http:// to a non-loopback host — credentials "
                    "must never travel over cleartext. Use "
                    "https:// for this server.")
            cfg["cleartext_loopback_warning"] = (
                f"Server '{name}' sends credentials over plain "
                "http:// to a loopback address — that traffic is "
                "unencrypted on this machine. Prefer https:// "
                "even locally.")
        auth = raw.get("auth")
        if auth is not None:
            if not isinstance(auth, dict):
                return None, f"Server '{name}' auth must be an object."
            auth_type = auth.get("type")
            if auth_type == "private_key_jwt":
                # Gate C (P2-7): rejected HERE as not
                # implemented. The connector has no signing
                # callback a JWT assertion needs, so accepting
                # this at validation and refusing only at
                # connect (the old behaviour) was accept-then-
                # fail-later. Use client_credentials until a
                # signing-callback registry exists.
                return None, (
                    f"Server '{name}' uses private_key_jwt auth, "
                    "which is NOT IMPLEMENTED in this build — "
                    "the connector has no signing callback a JWT "
                    "assertion needs, so the server cannot "
                    "authenticate. Use client_credentials "
                    "instead until a signing-callback registry "
                    "exists.")
            if auth_type not in _SUPPORTED_AUTH_TYPES:
                return None, (
                    f"Server '{name}' uses auth '{auth_type}', which this "
                    "bus does not support yet — only machine-to-machine "
                    "client_credentials is supported; "
                    "interactive sign-in is not. The server stays "
                    "unavailable until its auth is one of those.")
            # CVE-2026-115563: issuer= is the fix. Missing issuer
            # is a dead server, not a warning.
            issuer = auth.get("issuer")
            if not isinstance(issuer, str) or not issuer.strip():
                return None, (
                    f"Server '{name}' uses {auth_type} auth but sets no "
                    "issuer — the authorization server's issuer is "
                    "required, so token requests are built only from "
                    "that issuer's metadata. Add \"issuer\" to its auth.")
            if "client_secret" in auth:
                return None, (
                    f"Server '{name}' puts a literal client_secret in "
                    f"{CONFIG_FILENAME} — never store secrets in the "
                    "config file. Use \"client_secret_env\" with the "
                    "NAME of an environment variable instead.")
            if not isinstance(auth.get("client_id"), str) or not auth["client_id"].strip():
                return None, f"Server '{name}' auth needs a client_id."
            # v5.55 fix, superseded by Gate C (P2-7):
            # client_secret_env is a client_credentials
            # requirement only. (The first version demanded it
            # for private_key_jwt too; that auth type is now
            # rejected outright above as not implemented, so
            # this branch is client_credentials-only by
            # construction, not by a connect-time refusal.)
            # Invariant after the checks above: auth_type is
            # client_credentials here (private_key_jwt was
            # rejected outright; anything else was rejected as
            # unsupported). No else branch: a future auth type
            # must pass the checks above first, never fall
            # through into a config dict by accident.
            if not isinstance(auth.get("client_secret_env"), str) \
                    or not auth["client_secret_env"].strip():
                return None, (f"Server '{name}' auth needs "
                               "\"client_secret_env\" (an environment "
                               "variable name holding the secret).")
            cfg["auth"] = {
                "type": auth_type,
                "issuer": issuer.strip(),
                "client_id": auth["client_id"].strip(),
                "client_secret_env": auth["client_secret_env"].strip(),
            }
    else:
        return None, (f"Server '{name}' sets neither command nor url — "
                       "a server needs one transport.")
    note = raw.get("note")
    if isinstance(note, str):
        cfg["note"] = note
    return cfg, None


def load_config() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Load the effective server table: defaults merged with the
    user's file by name (file entries override/extend; enabled
    false disables a default). Returns (servers, config_errors)
    where config_errors maps a dead-by-config server name to its
    plain-words reason. Never raises; a broken file disables
    every file entry (defaults still apply) and reports itself.
    """
    servers: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for name, raw in DEFAULT_SERVERS.items():
        cfg, err = validate_server_config(name, raw)
        if cfg is not None:
            servers[name] = cfg
        elif err:
            errors[name] = err
    path = config_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
                errors["__file__"] = (
                    f"{CONFIG_FILENAME} must be an object with an "
                    "\"mcpServers\" object inside.")
            else:
                for name, raw in data["mcpServers"].items():
                    cfg, err = validate_server_config(name, raw)
                    if cfg is not None:
                        servers[name] = cfg
                    elif err:
                        errors[name] = err
    except Exception as e:  # noqa: BLE001 — a broken file is a
        errors["__file__"] = (  # plain-words report, never a crash
            f"{CONFIG_FILENAME} could not be read ({type(e).__name__}: "
            f"{e}) — using the built-in defaults.")
    return servers, errors
