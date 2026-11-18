"""Local single-user MCP server registry — the registry half of
ContextForge, rebuilt from scratch for VYUK.

Parent-ruled (licence, 2026-11-12): IBM/mcp-context-forge
(Apache License 2.0, standard text; the LICENSE file from the
default branch main was read in full 2026-11-12) is INSPIRATION
ONLY. No code, schema, text or package is adopted from it; this
module is written from scratch in stdlib Python over the tree's own
MCP client (core/mcp_client_config.py's validation + config file,
core/mcp_client_bus.py's bus, core/mcp_policy.py's review store)
and the tree's own SQLite-store shape (core/bookings.py,
core/lodging.py, core/wardrobe.py, core/billing.py) and the tree's
own config DATA_DIR. Ideas are not copyrighted, so no Apache-2.0
duty is triggered by a from-scratch rebuild. The "mcp_registry"
name here is the generic word for the capability, and no package,
fork, service or hosted offering is named after the original.

What this is: the owner manages VYUK's MCP servers from VYUK
instead of hand-editing JSON. One tool, six actions:
- 'server' (add/list/remove/enable/disable) — the server
  directory. Adds are validated by the bus's own Gate C
  validator (core/mcp_client_config.validate_server_config)
  before anything is written, and take effect immediately: the
  bus re-reads the config (MCPBus.reload_config) so a disable
  stops serving a server the owner just switched off — no
  restart, no stale tools.
- 'discover' — central discovery in one answer: every server
  (transport, enabled, live status, dead-by-config reason) and
  every tool it exposes, with each tool's operator-review class
  and whether its description changed since it was last seen.
  With connect=true it first asks the bus to connect every
  enabled server (bounded, best-effort — a dead server is a
  plain-words row, never a hang).
- 'review' — the operator reviews one tool (read / egress /
  confirm) through the tree's own policy store
  (core/mcp_policy.review_tool). The review is bound to the
  tool's current declaration digest, so a tool whose
  description changed since review is unreviewed again until
  re-reviewed — the funnel defaults an unreviewed tool to
  confirm-gated, always.
- 'policy' — the review store as it stands, with each record's
  current validity (still valid / stale digest / unseen).
- 'status' — registry health: server and tool counts, review
  counts, bus availability.
- 'events' — the hash-chained ledger of the trust decisions
  this tool made (server add/remove/enable/disable, review).

What is NOT built, stated plainly:
- A hosted or centralised gateway. Nothing VYUK serves is
  internet-reachable, so there is no endpoint for anyone to
  call, no shared registry, no multi-user anything (no VYUK
  accounts; single-user by design).
- An MCP server endpoint. VYUK is an MCP CLIENT, not a
  server — it calls other servers' tools; it does not expose
  its own tools over MCP.
- A2A / REST / gRPC federation. Those are separate protocols
  and not the finding's core for VYUK; this tool covers the
  tree's own MCP client only.
- A call proxy. Calls still flow through the existing
  'use_toolbox' find/describe/call path into the funnel's MCP
  branch, which is where validation, failsafe, confirmation,
  budget, deadline and audit live. This tool never executes an
  MCP tool, so it cannot bypass that branch — the registry is
  the front door for servers and reviews, not a second corridor
  for calls.
- No new transport. The bus (FastMCP over the official MCP
  SDK, lazy, bounded) is the only transport; this module adds
  no socket, no process, no second client.

Stores, and what lives where (nothing is duplicated):
- mcp_servers.json (the user's server config, beside .env,
  never shipped) is THE server directory. This tool edits it;
  it keeps no second copy. A server that exists only as a
  built-in default cannot be removed — removing the file entry
  would just bring the default back — so remove refuses it and
  says to disable it instead.
- data/mcp_tool_policy.json is THE review store. This tool
  writes reviews through core/mcp_policy.review_tool; it keeps
  no second copy.
- mcp_registry.sqlite3 (DATA_DIR) holds ONLY the events
  ledger — the audit trail of the trust decisions. Every
  ledger write is re-read before it is claimed (A1-store) and
  the chain verifies over the full ledger.

Secrets: none are stored or echoed here. A server's auth
secret arrives as the NAME of an environment variable
(client_secret_env), never as a literal — the Gate C validator
refuses a literal client_secret outright, refuses credentials
in a URL, and refuses credentials over plain http:// to a
non-loopback host. This module's returns name a server, its
transport and its enabled state; they never include an env
value, a header, or an auth field.
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from . import store_base
from .mcp_client_bus import get_bus
from .mcp_client_config import (
    CONFIG_FILENAME,
    DEFAULT_SERVERS,
    config_path,
    load_config,
    validate_server_config,
)
from . import mcp_policy

DEFAULT_DB_FILENAME = "mcp_registry.sqlite3"

_REVIEW_CLASSES = mcp_policy.CLASSES


class _RegistryDB:
    """Local hash-chained ledger of the registry's trust
    decisions (server add/remove/enable/disable, review). Own
    SQLite file in DATA_DIR, in the bookings/lodging/wardrobe/
    billing shape: single-writer serialisation, every write
    re-read before it is claimed, sha256(prev + payload) events
    with full-chain verification. No server secret is ever
    written here — only the server name, the action and the
    review class."""

    def __init__(self, db_path=None):
        self.path = (Path(db_path) if db_path else
                     Path(DATA_DIR) / DEFAULT_DB_FILENAME)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = store_base.connect(str(self.path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS mcp_registry_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, kind TEXT NOT NULL, "
            "subject TEXT NOT NULL, detail TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, hash TEXT NOT NULL)")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def _last_hash(self):
        row = self.conn.execute(
            "SELECT hash FROM mcp_registry_events "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else "GENESIS"

    def record(self, kind, subject, detail):
        """Insert + re-read before claiming (A1-store). Returns
        the re-read row, or None when the re-read disagrees."""
        ts = int(time.time())
        prev = self._last_hash()
        digest = store_base.chain_digest(
            "prev_payload_list", prev,
            {"fields": [ts, kind, subject, detail]})
        cur = self.conn.execute(
            "INSERT INTO mcp_registry_events "
            "(ts, kind, subject, detail, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, kind, subject, detail, prev, digest))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, ts, kind, subject, detail, hash "
            "FROM mcp_registry_events WHERE id = ?",
            (cur.lastrowid,)).fetchone()
        if not row or row[5] != digest or row[3] != subject:
            return None
        return row

    def verify_chain(self):
        rows = self.conn.execute(
            "SELECT id, ts, kind, subject, detail, prev_hash, hash "
            "FROM mcp_registry_events ORDER BY id").fetchall()
        entries = [{"fields": [ts, kind, subject, detail],
                    "prev_hash": prev_hash, "hash": digest}
                   for _id, ts, kind, subject, detail, prev_hash, digest in rows]
        result = store_base.verify_entries(
            entries, "prev_payload_list", store_base.LEGACY_GENESIS)
        if not result["ok"]:
            bad = rows[result["first_break"]["index"]]
            if result["first_break"]["reason"].startswith("link break"):
                return False, "chain broken at event %d" % bad[0]
            return False, "hash mismatch at event %d" % bad[0]
        return True, "%d events, chain intact" % len(rows)

    def list_events(self, limit=20):
        rows = self.conn.execute(
            "SELECT id, ts, kind, subject, detail "
            "FROM mcp_registry_events ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return rows

    def counts(self):
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) FROM mcp_registry_events "
            "GROUP BY kind").fetchall()
        return dict(rows)


# -------------------------------------------------------------- config file

def _read_config(cfg_path=None):
    """Read mcp_servers.json. Returns (data, error): data is the
    parsed object ({"mcpServers": {...}} when the file does not
    exist yet); error is a plain-words refusal or None."""
    path = Path(cfg_path) if cfg_path else Path(config_path())
    if not path.exists():
        return {"mcpServers": {}}, None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, ("refused: could not read the server config "
                      "(%s)." % e)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, ("refused: the server config is not valid "
                      "JSON (%s) — fix it before editing." % e)
    if not isinstance(data, dict) or not isinstance(
            data.get("mcpServers", {}), dict):
        return None, ("refused: the server config is not a JSON "
                      "object with an 'mcpServers' object.")
    return data, None


def _write_config(data, cfg_path=None):
    """Atomic write of mcp_servers.json (tmp + replace).
    Returns an error string or None."""
    path = Path(cfg_path) if cfg_path else Path(config_path())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        return "refused: could not write the server config (%s)." % e
    return None


def _file_entries(cfg_path=None):
    """The servers written in the file (not the defaults).
    Returns (entries, error)."""
    data, error = _read_config(cfg_path)
    if error:
        return None, error
    return data.get("mcpServers", {}), None


def _reload(bus):
    """Ask the bus to re-read the config. Returns the reload
    summary dict (never raises)."""
    try:
        return bus.reload_config()
    except Exception as e:  # noqa: BLE001 — a reload failure is
        return {"added": [], "removed": [], "enabled": [],  # reported, not raised
                "disabled": [], "errors": {"__bus__": str(e)}}


def _reload_note(summary):
    parts = []
    if summary.get("added"):
        parts.append("added to the bus: %s" %
                     ", ".join(summary["added"]))
    if summary.get("removed"):
        parts.append("removed from the bus: %s" %
                     ", ".join(summary["removed"]))
    if summary.get("enabled"):
        parts.append("enabled on the bus: %s" %
                     ", ".join(summary["enabled"]))
    if summary.get("disabled"):
        parts.append("disabled on the bus: %s" %
                     ", ".join(summary["disabled"]))
    errors = summary.get("errors") or {}
    for name, reason in errors.items():
        parts.append("config error on %s: %s" % (name, reason))
    return "; ".join(parts) if parts else "bus re-read, no change"


def _transport_of(entry):
    if entry.get("url"):
        return "remote"
    return "stdio"


def _describe_entry(name, entry, state, error, is_default):
    transport = _transport_of(entry)
    enabled = bool(entry.get("enabled", True))
    status = state["status"] if state else (
        "config_error" if error else "idle")
    line = "%s (%s, %s, %s" % (
        name, transport, "enabled" if enabled else "disabled", status)
    if is_default:
        line += ", built-in default"
    reason = (state.get("error") if state else None) or error
    if reason:
        line += "; %s" % reason
    if state and state.get("tool_count"):
        line += "; %d tools" % state["tool_count"]
    return line + ")"


def _entry_from_params(command, args, url, env, headers, auth, note,
                       enabled=True):
    """Build the raw server entry from the action's parameters.
    Only keys the caller supplied are included, so the file
    stays readable and the Gate C validator sees exactly what
    the owner asked for."""
    entry = {}
    if url:
        entry["url"] = url
        if headers:
            entry["headers"] = headers
        if auth:
            entry["auth"] = auth
    else:
        entry["command"] = command
        if args:
            entry["args"] = list(args)
        if env:
            entry["env"] = env
    if note:
        entry["note"] = note
    entry["enabled"] = bool(enabled)
    return entry


# -------------------------------------------------------------- server

def _raw_entry(entry):
    """The file's view of an effective entry: load_config's
    normalisation adds 'name' and 'transport', which are not
    part of the file format (the name is the key, the transport
    is derived). Strip them so a disable/enable of a default
    writes a clean file entry, not a normalised one."""
    return {key: value for key, value in entry.items()
            if key in ("command", "args", "env", "url", "headers",
                       "auth", "enabled", "note")}


def _do_server_list(bus, cfg_path=None):
    servers, errors = load_config()
    entries, file_error = _file_entries(cfg_path)
    if file_error:
        return file_error
    states = {s["name"]: s for s in bus.servers()}
    lines = []
    for name in sorted(servers):
        entry = servers[name]
        state = states.get(name)
        is_default = name in DEFAULT_SERVERS and name not in entries
        lines.append(_describe_entry(name, entry, state,
                                     errors.get(name), is_default))
    for name in sorted(errors):
        if name != "__file__" and name not in servers:
            lines.append("%s (dead-by-config: %s)" %
                         (name, errors[name]))
    if errors.get("__file__"):
        lines.append("file error: %s" % errors["__file__"])
    if not lines:
        return "No MCP servers configured."
    return "MCP servers (%d):\n%s" % (len(lines), "\n".join(lines))


def _do_server_add(db, bus, server, command, args, url, env, headers,
                   auth, note, cfg_path=None):
    if not server:
        return "refused: 'server' (the new server's name) is required."
    if not command and not url:
        return ("refused: give either 'command' (a local program to "
                "run) or 'url' (a remote server to connect to).")
    if command and url:
        return ("refused: give either 'command' or 'url', not both — "
                "a server is stdio or remote, never both.")
    entries, file_error = _file_entries(cfg_path)
    if file_error:
        return file_error
    if server in entries:
        return ("refused: '%s' is already registered — remove it "
                "first, then add it again." % server)
    entry = _entry_from_params(command, args, url, env, headers, auth,
                               note)
    ok, reason = validate_server_config(server, entry)
    if not ok:
        return "refused: %s" % reason
    data, read_error = _read_config(cfg_path)
    if read_error:
        return read_error
    data["mcpServers"][server] = entry
    write_error = _write_config(data, cfg_path)
    if write_error:
        return write_error
    _reload(bus)
    row = db.record("server_add", server,
                    "added %s server" % _transport_of(entry))
    if row is None:
        return ("refused: '%s' was written to the server config, "
                "but the ledger write could not be re-read — "
                "check the registry ledger before trusting it."
                % server)
    return ("Added the '%s' server (%s). It connects lazily on "
            "first use." % (server, _transport_of(entry)))


def _do_server_remove(db, bus, server, cfg_path=None):
    if not server:
        return "refused: 'server' (the server's name) is required."
    entries, file_error = _file_entries(cfg_path)
    if file_error:
        return file_error
    if server not in entries:
        if server in DEFAULT_SERVERS:
            return ("refused: '%s' is a built-in default — there "
                    "is no file entry to remove, and removing one "
                    "would just bring the default back. Disable it "
                    "instead." % server)
        return "refused: there is no server called '%s'." % server
    data, read_error = _read_config(cfg_path)
    if read_error:
        return read_error
    del data["mcpServers"][server]
    write_error = _write_config(data, cfg_path)
    if write_error:
        return write_error
    summary = _reload(bus)
    row = db.record("server_remove", server, "removed from the config")
    if row is None:
        return ("refused: '%s' was removed from the server config, "
                "but the ledger write could not be re-read — "
                "check the registry ledger before trusting it."
                % server)
    return ("Removed the '%s' server. %s" %
            (server, _reload_note(summary)))


def _do_server_enable(db, bus, server, enable, cfg_path=None):
    if not server:
        return "refused: 'server' (the server's name) is required."
    entries, file_error = _file_entries(cfg_path)
    if file_error:
        return file_error
    servers, errors = load_config()
    if server not in servers and server not in errors:
        return "refused: there is no server called '%s'." % server
    if server in errors:
        return ("refused: '%s' is dead-by-config — fix its config "
                "(%s) before enabling it." % (server, errors[server]))
    data, read_error = _read_config(cfg_path)
    if read_error:
        return read_error
    if server in entries:
        entry = dict(entries[server])
    else:
        entry = _raw_entry(servers[server])
    entry["enabled"] = bool(enable)
    data["mcpServers"][server] = entry
    write_error = _write_config(data, cfg_path)
    if write_error:
        return write_error
    summary = _reload(bus)
    kind = "server_enable" if enable else "server_disable"
    row = db.record(kind, server,
                    "enabled" if enable else "disabled")
    if row is None:
        return ("refused: '%s' was %s in the server config, but the "
                "ledger write could not be re-read — check the "
                "registry ledger before trusting it." %
                (server, "enabled" if enable else "disabled"))
    warn = ""
    if enable and server == "git":
        raw_args = entry.get("args") or []
        if any("mcp-server-git" in str(a) for a in raw_args):
            warn = (" Note: the built-in git server still points "
                    "at the example repo — set its args to the repo "
                    "you actually want it to serve, or the bus will "
                    "fail closed at connect.")
    verb = "Enabled" if enable else "Disabled"
    return ("%s the '%s' server. %s%s" %
            (verb, server, _reload_note(summary), warn))


# -------------------------------------------------------------- discover

def _do_discover(bus, connect, limit, _policy_store=None):
    if connect:
        bus.ensure_all_enabled()
    servers, errors = load_config()
    states = {s["name"]: s for s in bus.servers()}
    tools = bus.list_tools()
    cap = max(1, int(limit or 50))
    lines = []
    for name in sorted(servers):
        entry = servers[name]
        state = states.get(name)
        is_default = name in DEFAULT_SERVERS
        lines.append(_describe_entry(name, entry, state,
                                     errors.get(name), is_default))
    for name in sorted(errors):
        if name != "__file__" and name not in servers:
            lines.append("%s (dead-by-config: %s)" %
                         (name, errors[name]))
    if errors.get("__file__"):
        lines.append("file error: %s" % errors["__file__"])
    rows = []
    for tool in tools[:cap]:
        composed = tool.get("composed", "?")
        entry = {"name": tool.get("name", "?"),
                 "description": tool.get("description", ""),
                 "server": tool.get("server", "?"),
                 "decl": tool.get("decl", {})}
        klass = mcp_policy.policy_class(entry, store=_policy_store)
        changed = bus.tool_changed(tool.get("name", ""),
                                   tool.get("description", ""))
        rows.append("%s [%s%s]" % (composed, klass or "unreviewed",
                                   ", description changed" if changed
                                   else ""))
    out = ("MCP discovery: %d servers, %d tools%s.\n%s" %
           (len(servers), len(tools),
            " (showing %d)" % len(rows) if len(tools) > cap else "",
            "\n".join(lines)))
    if rows:
        out += "\nTools:\n" + "\n".join(rows)
    else:
        out += "\nNo tools are currently listed (connect with " \
               "connect=true to ask each enabled server)."
    return out


# -------------------------------------------------------------- review

def _do_review(db, bus, tool, klass, reviewer, _policy_store=None):
    if not tool:
        return "refused: 'tool' (the composed tool name, e.g. " \
               "'mcp__fetch__fetch') is required."
    if not klass:
        return ("refused: 'klass' is required — 'read', 'egress' "
                "or 'confirm'.")
    if klass not in _REVIEW_CLASSES:
        return ("refused: 'klass' must be 'read', 'egress' or "
                "'confirm', not '%s'." % klass)
    entry = bus.resolve(tool)
    if entry is None:
        return ("refused: I have not seen a tool called '%s' yet — "
                "run 'discover' with connect=true first, so the "
                "review is bound to the tool's current "
                "declaration." % tool)
    record = mcp_policy.review_tool(tool, klass, entry,
                                    reviewer or "operator",
                                    store=_policy_store)
    row = db.record("review", tool,
                    "reviewed as %s" % klass)
    if row is None:
        return ("refused: the review of '%s' was written to the "
                "policy store, but the ledger write could not be "
                "re-read — check the registry ledger before "
                "trusting it." % tool)
    return ("Reviewed '%s' as %s (reviewed_at %s, digest %s). "
            "It keeps that class until its declaration changes." %
            (tool, klass, record["reviewed_at"],
             record["digest"][:12]))


def _do_policy(bus, _policy_store=None):
    store = (_policy_store if _policy_store is not None
             else mcp_policy.load())
    if not store:
        return ("No operator reviews recorded yet. 'review' a tool "
                "to record one; an unreviewed tool is confirm-gated "
                "by default.")
    lines = []
    for composed in sorted(store):
        record = store[composed]
        entry = bus.resolve(composed)
        if entry is None:
            validity = "unseen (no longer listed)"
        else:
            digest = mcp_policy.declaration_digest(entry)
            validity = ("valid" if digest == record.get("digest")
                        else "stale (declaration changed — "
                             "re-review it)")
        lines.append("%s: %s, by %s at %s (%s)" %
                     (composed, record.get("class", "?"),
                      record.get("reviewer", "?"),
                      record.get("reviewed_at", "?"), validity))
    return "Operator reviews (%d):\n%s" % (len(lines), "\n".join(lines))


# -------------------------------------------------------------- status/events

def _do_status(db, bus, _policy_store=None):
    servers, errors = load_config()
    enabled = sum(1 for name, entry in servers.items()
                  if entry.get("enabled", True))
    tools = bus.list_tools()
    reviewed = 0
    changed = 0
    for tool in tools:
        entry = {"name": tool.get("name", "?"),
                 "description": tool.get("description", ""),
                 "server": tool.get("server", "?"),
                 "decl": tool.get("decl", {})}
        if mcp_policy.policy_class(entry, store=_policy_store) is not None:
            reviewed += 1
        if bus.tool_changed(tool.get("name", ""),
                            tool.get("description", "")):
            changed += 1
    store = (_policy_store if _policy_store is not None
             else mcp_policy.load())
    counts = db.counts()
    events_total = sum(counts.values())
    try:
        import anyio  # noqa: F401
        import fastmcp  # noqa: F401
        import mcp  # noqa: F401
        availability = "MCP libraries available"
    except ImportError as e:
        availability = "MCP libraries unavailable (%s)" % e
    return ("MCP registry status:\n"
            "servers: %d (%d enabled, %d disabled, %d dead-by-config)\n"
            "tools listed: %d (%d reviewed, %d unreviewed, %d "
            "description-changed)\n"
            "reviews recorded: %d\n"
            "ledger events: %d\n"
            "bus: %s" %
            (len(servers), enabled, len(servers) - enabled,
             len(errors), len(tools), reviewed,
             len(tools) - reviewed, changed, len(store),
             events_total, availability))


def _do_events(db, limit):
    rows = db.list_events(limit or 20)
    ok, chain_note = db.verify_chain()
    lines = []
    for _id, ts, kind, subject, detail in rows:
        lines.append("#%d %s %s %s — %s" %
                     (_id, time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime(ts)),
                      kind, subject, detail))
    out = "Registry ledger (%s):\n%s" % (
        chain_note, "\n".join(lines) if lines else "no events yet")
    if not ok:
        out += "\nrefused: the ledger chain is broken — do not trust it."
    return out


# -------------------------------------------------------------- entry point

def mcp_registry(action="", mode="", server="", command="", args=None,
                 url="", env=None, headers=None, auth=None, note="",
                 tool="", klass="", reviewer="", connect=None,
                 limit=None, detail=None, _bus=None, _db_path=None,
                 _config_path=None, _policy_store=None) -> str:
    """Public entry: one call, one connection, closed before
    returning.

    Every funnel call opens the registry ledger, works, and
    closes it — a leaked connection would hold a SQLite lock
    across calls and block the next write. The bus is the
    singleton (it never connects here unless 'discover' is
    asked to with connect=true), and the server config is read
    fresh for every action, so a hand-edit made outside the
    tool is never stale for long.
    """
    action = str(action or "").strip().lower()
    mode = str(mode or "").strip().lower()
    server = str(server or "").strip()
    command = str(command or "").strip()
    url = str(url or "").strip()
    note = str(note or "").strip()
    tool = str(tool or "").strip()
    klass = str(klass or "").strip().lower()
    reviewer = str(reviewer or "").strip()
    limit = limit if isinstance(limit, int) else None
    bus = _bus if _bus is not None else get_bus()
    db = _RegistryDB(_db_path)
    try:
        if action in ("status", "events"):
            if action == "status":
                return _do_status(db, bus, _policy_store)
            return _do_events(db, limit)
        if action not in ("server", "discover", "review", "policy"):
            return ("refused: action must be 'server', 'discover', "
                    "'review', 'policy', 'status' or 'events'.")
        if action == "server":
            if mode == "list":
                return _do_server_list(bus, _config_path)
            if mode == "add":
                if args is not None and not isinstance(
                        args, (list, tuple)):
                    return ("refused: 'args' must be a list of "
                            "strings.")
                for name, value in (("env", env), ("headers", headers),
                                    ("auth", auth)):
                    if value is not None and not isinstance(value, dict):
                        return ("refused: '%s' must be an object."
                                % name)
                return _do_server_add(db, bus, server, command, args,
                                      url, env, headers, auth, note,
                                      _config_path)
            if mode == "remove":
                return _do_server_remove(db, bus, server, _config_path)
            if mode == "enable":
                return _do_server_enable(db, bus, server, True,
                                         _config_path)
            if mode == "disable":
                return _do_server_enable(db, bus, server, False,
                                         _config_path)
            return ("refused: mode must be 'add', 'list', 'remove', "
                    "'enable' or 'disable'.")
        if action == "discover":
            return _do_discover(bus, bool(connect), limit, _policy_store)
        if action == "review":
            return _do_review(db, bus, tool, klass, reviewer,
                              _policy_store)
        return _do_policy(bus, _policy_store)
    finally:
        db.close()
