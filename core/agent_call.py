"""A2A client — VYUK calls another agent, never serves as one.

Parent-ruled (licence, 2026-11-12): a2aproject/A2A
(Apache-2.0; the LICENSE file from the default branch main was read
in full 2026-11-12 — 11,358 bytes, 202 lines by wc -l, standard
Apache-2.0 text with the patent grant, redistribution, disclaimer
and limitation sections) is INSPIRATION ONLY. No code, schema, text
or package is adopted from it; this module is written from scratch
in stdlib Python over the tree's own urllib shape
(core/security_scan.py's injectable-opener fetch, core/billing.py's
injectable-opener call) and the tree's own SQLite-store shape
(core/bookings.py, core/lodging.py, core/wardrobe.py, core/billing.py)
and the tree's own config DATA_DIR. Ideas are not copyrighted, so
no Apache-2.0 duty is triggered by a from-scratch rebuild. The
"agent_call" name here is the generic word for the capability, and
no package, fork, service or hosted offering is named after the
original.

The wire format was verified against the spec source itself before
building — the canonical Protocol Buffers definition
(specification/a2a.proto, 812 lines, main) and the specification
text (docs/specification.md, 3,616 lines, main) — not from the
README badges and not guessed:
- Discovery: GET /.well-known/agent-card.json on the agent's domain
  (spec section 6.9 / 8.6).
- Transport: JSON-RPC 2.0 over HTTP(S), Content-Type
  application/json, PascalCase method names (spec section 9.1):
  SendMessage, GetTask, ListTasks, CancelTask (SendStreamingMessage
  and SubscribeToTask are streaming/SSE and are NOT built here).
- A SendMessage request is {"jsonrpc": "2.0", "id": <uuid>,
  "method": "SendMessage", "params": {"message": {"messageId":
  <uuid>, "role": "ROLE_USER", "parts": [{"text": ...}],
  "contextId"?, "taskId"?}, "configuration":
  {"acceptedOutputModes": ["text/plain"], "historyLength"?,
  "returnImmediately"?}, "tenant"?}} and the answer is
  {"jsonrpc": "2.0", "id": <same>, "result": {"task": {...}} |
  {"message": {...}}} or {"error": {"code", "message"}}.
- Field names are camelCase and enum values are their ProtoJSON
  string names (spec section 5.5): role "ROLE_USER" / "ROLE_AGENT",
  task state "TASK_STATE_SUBMITTED" / "TASK_STATE_WORKING" /
  "TASK_STATE_COMPLETED" / "TASK_STATE_FAILED" /
  "TASK_STATE_CANCELED" / "TASK_STATE_INPUT_REQUIRED" /
  "TASK_STATE_REJECTED" / "TASK_STATE_AUTH_REQUIRED".
- An agent card carries name, description, version,
  supportedInterfaces [{url, protocolBinding ("JSONRPC" / "GRPC" /
  "HTTP+JSON"), tenant?, protocolVersion}], provider,
  documentationUrl?, capabilities {streaming?, pushNotifications?,
  extendedAgentCard?}, securitySchemes / securityRequirements,
  defaultInputModes, defaultOutputModes and skills [{id, name,
  description, tags, examples?, inputModes?, outputModes?}].
- A2A error codes (spec section 5.4): -32001 TaskNotFoundError,
  -32002 TaskNotCancelableError, -32003
  PushNotificationNotSupportedError, -32004
  UnsupportedOperationError, -32005 ContentTypeNotSupportedError,
  -32006 InvalidAgentResponseError, -32007
  ExtendedAgentCardNotConfiguredError, -32008
  ExtensionSupportRequiredError, -32009 VersionNotSupportedError.

What this is: VYUK as an A2A CLIENT. The owner names another
agent (an operator-supplied URL), VYUK fetches its public agent
card, records it in a local single-user registry, and — only after
the owner approves the call (C-14) — sends it a message and reports
the task state and the artifacts / agent message it returns. Every
call is written to a local hash-chained ledger (agent_call.sqlite3
in DATA_DIR) so the owner can audit what VYUK sent to whom; every
ledger write and every registry write is re-read before it is
claimed (A1-store) and the chain verifies over the full ledger.

What is NOT built, stated plainly:
- The SERVER side: VYUK exposes no A2A endpoint, serves no agent
  card over HTTP, and accepts no inbound calls — nothing VYUK
  serves is internet-reachable (the no-hosting ruling). The 'card'
  action returns a static document describing VYUK, not an
  endpoint: its supportedInterfaces list is empty and it says so.
- Streaming: SendStreamingMessage / SubscribeToTask (Server-Sent
  Events) are not built — a call waits for the task's answer (or
  returns immediately when the caller asks for it) instead.
- Push-notification configs: creating one needs a URL the agent
  can reach to notify VYUK, which is hosting — not built.
- The extended agent card: fetching it needs authentication, and
  authentication is out of band — not built.
- Authenticated agents: an agent whose card declares
  securityRequirements is refused at discovery AND at call time.
  VYUK stores no credentials and performs no OAuth/OIDC flow; the
  refusal names the requirement instead of guessing at it.
- The gRPC binding: only the JSON-RPC binding is spoken. An agent
  with no JSONRPC interface is refused honestly at discovery.
- Multi-user / shared agent directory: single-user by design. The
  registry is the owner's own list, in the R18-3 mcp_registry
  shape — not a shared directory, and no agent is ever added
  except by the owner's own discover call.
- No credentials are stored or echoed. A URL carrying userinfo
  (user:password@host) is stripped before it is stored or shown,
  in the R17-3 camera-credential rule. Nothing secret is ever
  written to the ledger or the registry.
- Plain HTTP is refused except on loopback (127.0.0.1 / localhost,
  where the lab fake agents live): the spec requires an absolute
  HTTPS URL for an interface in production, and VYUK does not send
  the owner's words to a remote agent over cleartext.
"""

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .config import DATA_DIR  # noqa: F401  (kept for the house import shape; the store below uses it)
from . import store_base
from .version import VERSION

DEFAULT_DB_FILENAME = "agent_call.sqlite3"

_CARD_PATH = "/.well-known/agent-card.json"
_USER_AGENT = "VYUK-agent-call/1.0 (A2A JSON-RPC client)"
_TIMEOUT_S = 30
_MAX_BODY_BYTES = 1024 * 1024  # 1 MB cap, the security_scan precedent

# Task states, exactly as the spec's ProtoJSON names them (section
# 5.5). A state outside this set is reported as an anomaly, never
# silently normalised.
_TASK_STATES = {
    "TASK_STATE_UNSPECIFIED",
    "TASK_STATE_SUBMITTED",
    "TASK_STATE_WORKING",
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_REJECTED",
    "TASK_STATE_AUTH_REQUIRED",
}
_TERMINAL_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}

# A2A error codes (spec section 5.4).
_A2A_ERRORS = {
    -32001: "TaskNotFoundError",
    -32002: "TaskNotCancelableError",
    -32003: "PushNotificationNotSupportedError",
    -32004: "UnsupportedOperationError",
    -32005: "ContentTypeNotSupportedError",
    -32006: "InvalidAgentResponseError",
    -32007: "ExtendedAgentCardNotConfiguredError",
    -32008: "ExtensionSupportRequiredError",
    -32009: "VersionNotSupportedError",
}

# Actions that send to a third party (confirm-gated) vs reads (free).
# Kept in one place so the gate branches in
# core/funnel/tools_gate.py and core/funnel/tools_gate_readclass.py
# draw the same split.
_WRITE_ACTIONS = {
    ("call", ""),
    ("task", "cancel"),
    ("remove", ""),
}
_READ_ACTIONS = {
    ("discover", ""),
    ("list", ""),
    ("task", "get"),
    ("task", "list"),
    ("card", ""),
    ("status", ""),
    ("events", ""),
}


def _strip_url_credentials(url):
    """Strip userinfo from a URL (the R17-3 camera-credential rule).

    Returns (clean_url, had_credentials). A credential is never
    stored or echoed; the caller notes the strip in its answer.
    """
    try:
        parts = urllib.parse.urlparse(url)
    except (ValueError, AttributeError):
        return url, False
    if not parts.username and not parts.password:
        return url, False
    netloc = parts.hostname or ""
    if parts.port:
        netloc = "%s:%d" % (netloc, parts.port)
    clean = urllib.parse.urlunparse((
        parts.scheme, netloc, parts.path, parts.params,
        parts.query, parts.fragment))
    return clean, True


def _check_url(url):
    """Validate an operator-supplied agent URL before any fetch.

    Returns (clean_url, refusal-or-None). Only http(s); plain HTTP
    only on loopback; userinfo is stripped, never kept.
    """
    if not isinstance(url, str) or not url.strip():
        return None, ("refused: no agent URL was supplied. The URL is "
                      "operator-supplied per call — VYUK never guesses "
                      "an agent's address.")
    url = url.strip()
    clean, had_creds = _strip_url_credentials(url)
    if had_creds:
        # Refuse rather than silently rewrite: a credential in the
        # URL is a mistake the owner should know about, and the
        # stripped form is not what they typed.
        return None, ("refused: the URL carries a username/password. "
                      "Credentials are never stored or sent by this "
                      "tool — use an agent that needs no URL "
                      "credentials, or reach it another way. "
                      "Nothing was fetched.")
    try:
        parts = urllib.parse.urlparse(clean)
    except ValueError:
        return None, "refused: that URL does not parse. Nothing was fetched."
    if parts.scheme not in ("http", "https"):
        return None, ("refused: an agent URL must be http(s). "
                      "Nothing was fetched.")
    if not parts.hostname:
        return None, "refused: that URL has no host. Nothing was fetched."
    if parts.scheme == "http" and parts.hostname not in (
            "127.0.0.1", "localhost", "::1"):
        return None, ("refused: plain HTTP to a remote host is not "
                      "allowed — the spec requires HTTPS for an agent "
                      "interface in production, and VYUK does not send "
                      "the owner's words to a remote agent over "
                      "cleartext. Use the agent's https URL. Nothing "
                      "was fetched.")
    return clean, None


def _card_url_for(base_url):
    """The agent-card URL for an operator-supplied base or card URL."""
    clean, refusal = _check_url(base_url)
    if refusal:
        return None, refusal
    if clean.rstrip("/").endswith(_CARD_PATH):
        return clean, None
    return clean.rstrip("/") + _CARD_PATH, None


def _fetch_json(url, _opener=None):
    """One GET returning (parsed-dict-or-None, error-or-None).

    The opener is injectable for tests (a fake returning recorded
    bytes); the default is urllib.request.urlopen. Bodies over the
    1 MB cap are refused unread.
    """
    opener = _opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }, method="GET")
    try:
        with opener(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read(_MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return None, ("the agent answered HTTP %d — no card was read."
                      % exc.code)
    except urllib.error.URLError as exc:
        return None, ("could not reach %s (%s) — nothing was read."
                      % (urllib.parse.urlparse(url).hostname or url,
                         exc.reason or exc))
    except (ValueError, OSError) as exc:
        return None, "could not fetch the card (%s) — nothing was read." % exc
    if len(raw) > _MAX_BODY_BYTES:
        return None, ("the agent answered with more than 1 MB — refused "
                      "unread, nothing was stored.")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, ("the agent answered something unreadable (%s) — "
                      "nothing was claimed." % exc)
    if not isinstance(parsed, dict):
        return None, ("the agent answered something unreadable (not a "
                      "JSON object) — nothing was claimed.")
    return parsed, None


def _jsonrpc(url, method, params, _opener=None):
    """One JSON-RPC 2.0 call. Returns (result-or-None, error-or-None).

    The request id is a fresh uuid and the answer must carry the
    same id back with exactly one of "result" / "error" (spec
    section 9.3). A mismatched id, a missing result AND error, or
    both at once is an InvalidAgentResponseError, never a success.
    """
    request_id = uuid.uuid4().hex
    payload = {"jsonrpc": "2.0", "id": request_id,
               "method": method, "params": params}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }, method="POST")
    opener = _opener or urllib.request.urlopen
    try:
        with opener(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read(_MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read(_MAX_BODY_BYTES + 1).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, ("the agent answered HTTP %d with an unreadable "
                          "body — nothing was claimed." % exc.code)
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            return None, _jsonrpc_error_text(parsed["error"])
        return None, ("the agent answered HTTP %d — nothing was claimed."
                      % exc.code)
    except urllib.error.URLError as exc:
        return None, ("could not reach %s (%s) — nothing was sent, and "
                      "this is not a success."
                      % (urllib.parse.urlparse(url).hostname or url,
                         exc.reason or exc))
    except (ValueError, OSError) as exc:
        return None, ("could not call the agent (%s) — nothing was sent."
                      % exc)
    if len(raw) > _MAX_BODY_BYTES:
        return None, ("the agent answered with more than 1 MB — refused "
                      "unread, nothing was claimed.")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, ("the agent answered something unreadable (%s) — "
                      "nothing was claimed." % exc)
    if not isinstance(parsed, dict):
        return None, ("the agent answered something unreadable (not a "
                      "JSON object) — nothing was claimed.")
    if parsed.get("jsonrpc") != "2.0":
        return None, ("the agent answered without \"jsonrpc\": \"2.0\" — "
                      "not an A2A JSON-RPC answer, nothing was claimed.")
    if parsed.get("id") != request_id:
        return None, ("the agent answered with a different request id "
                      "than the one sent — not our answer, nothing was "
                      "claimed.")
    has_result = "result" in parsed
    has_error = "error" in parsed
    if has_result == has_error:
        return None, ("the agent answered with neither a result nor an "
                      "error (or both) — not a valid JSON-RPC answer, "
                      "nothing was claimed.")
    if has_error:
        err = parsed["error"]
        if not isinstance(err, dict):
            return None, ("the agent answered with a malformed error — "
                          "nothing was claimed.")
        return None, _jsonrpc_error_text(err)
    return parsed["result"], None


def _jsonrpc_error_text(err):
    """Render a JSON-RPC error object, naming the A2A error type."""
    code = err.get("code")
    message = (err.get("message") or "").strip()
    name = _A2A_ERRORS.get(code) if isinstance(code, int) else None
    if name:
        return "the agent refused: %s%s" % (
            message or name,
            (" [%s, code %d]" % (name, code)) if message else
            (" [code %d]" % code))
    if message:
        return "the agent refused: %s%s" % (
            message, (" [code %s]" % code) if code is not None else "")
    return "the agent refused the call (no message in the answer)."


def _validate_card(card):
    """Validate a fetched agent card against the spec (section 3.2).

    Returns (interface-or-None, refusal-or-None). The interface is
    the first declared JSONRPC interface; the card is refused when
    a required field is missing, when no JSONRPC interface exists,
    or when the card declares security requirements (auth is out
    of band — no credentials are stored, no OAuth flow is built).
    """
    for field in ("name", "description", "version"):
        value = card.get(field)
        if not isinstance(value, str) or not value.strip():
            return None, ("refused: the agent card has no usable '%s' — "
                          "not an A2A agent card, nothing was stored."
                          % field)
    interfaces = card.get("supportedInterfaces")
    if not isinstance(interfaces, list) or not interfaces:
        return None, ("refused: the agent card declares no interfaces — "
                      "not an A2A agent card, nothing was stored.")
    chosen = None
    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        url = iface.get("url")
        binding = iface.get("protocolBinding")
        version = iface.get("protocolVersion")
        if (isinstance(url, str) and url.strip()
                and isinstance(binding, str)
                and binding.strip().upper() == "JSONRPC"
                and isinstance(version, str) and version.strip()):
            chosen = {"url": url.strip(),
                      "protocol_version": version.strip(),
                      "tenant": iface.get("tenant")
                      if isinstance(iface.get("tenant"), str) else ""}
            break
    if chosen is None:
        return None, ("refused: the agent card declares no JSON-RPC "
                      "interface (only %s) — VYUK speaks the JSON-RPC "
                      "binding only, so this agent cannot be called. "
                      "Nothing was stored."
                      % ", ".join(sorted({
                          str(i.get("protocolBinding"))
                          for i in interfaces
                          if isinstance(i, dict)})))
    url_ok, refusal = _check_url(chosen["url"])
    if refusal:
        return None, ("refused: the card's interface URL is unusable — "
                      "%s Nothing was stored." % refusal)
    chosen["url"] = url_ok
    for field in ("capabilities",):
        if not isinstance(card.get(field), dict):
            return None, ("refused: the agent card has no '%s' object — "
                          "not an A2A agent card, nothing was stored."
                          % field)
    for field in ("defaultInputModes", "defaultOutputModes"):
        if not isinstance(card.get(field), list):
            return None, ("refused: the agent card has no '%s' list — "
                          "not an A2A agent card, nothing was stored."
                          % field)
    skills = card.get("skills")
    if not isinstance(skills, list):
        return None, ("refused: the agent card has no 'skills' list — "
                      "not an A2A agent card, nothing was stored.")
    requirements = card.get("securityRequirements")
    if isinstance(requirements, list) and requirements:
        schemes = card.get("securitySchemes")
        names = []
        if isinstance(schemes, dict):
            names = sorted(schemes.keys())
        return None, ("refused: this agent requires authentication "
                      "(%s) — VYUK stores no credentials and performs "
                      "no OAuth/OIDC flow, so an authenticated agent "
                      "cannot be called. Nothing was stored."
                      % (", ".join(names) if names else
                         "security requirements declared"))
    return chosen, None


def _parts_text(parts):
    """The text of a part list, in order; files/data are named, not read."""
    lines = []
    if not isinstance(parts, list):
        return ""
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            lines.append(text)
            continue
        if "data" in part:
            lines.append("[structured data part]")
        elif part.get("url") or part.get("filename"):
            lines.append("[file part: %s]"
                         % (part.get("filename") or part.get("url") or "?"))
    return "\n".join(lines)


def _summarise_task(task):
    """A task object as plain lines: state, artifacts, status message."""
    if not isinstance(task, dict):
        return "the agent answered with a task that is not an object — nothing was claimed."
    task_id = task.get("id") or "?"
    state = task.get("status", {}).get("state") if isinstance(
        task.get("status"), dict) else None
    lines = ["task %s: state %s" % (task_id, state or "?")]
    if state is not None and state not in _TASK_STATES:
        lines.append("WARNING: '%s' is not an A2A task state — the "
                     "agent's answer is reported, not trusted." % state)
    artifacts = task.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        for art in artifacts:
            if not isinstance(art, dict):
                continue
            text = _parts_text(art.get("parts"))
            name = art.get("name") or art.get("artifactId") or "artifact"
            if text:
                lines.append("artifact '%s':\n%s" % (name, text))
            else:
                lines.append("artifact '%s': (no text parts)" % name)
    status = task.get("status")
    if isinstance(status, dict):
        message = status.get("message")
        if isinstance(message, dict):
            text = _parts_text(message.get("parts"))
            if text:
                lines.append("agent says:\n%s" % text)
    return "\n".join(lines)


# ------------------------------------------------------------------ store

class _AgentCallDB:
    """Local single-user registry of discovered agents + hash-chained
    ledger of what VYUK sent to whom, in the bookings/lodging/
    wardrobe/billing shape: single-writer serialisation, every
    write re-read before it is claimed, sha256(prev + payload)
    events with full-chain verification. No credential is ever
    written here — only the agent name, its card URL and interface
    URL, and the task ids VYUK exchanged.
    """

    def __init__(self, db_path=None):
        self.path = (Path(db_path) if db_path else
                     Path(DATA_DIR) / DEFAULT_DB_FILENAME)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = store_base.connect(str(self.path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_call_agents ("
            "name TEXT PRIMARY KEY, card_url TEXT NOT NULL, "
            "interface_url TEXT NOT NULL, protocol_version TEXT NOT NULL, "
            "tenant TEXT NOT NULL, description TEXT NOT NULL, "
            "agent_version TEXT NOT NULL, skills_json TEXT NOT NULL, "
            "discovered_at INTEGER NOT NULL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_call_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, kind TEXT NOT NULL, "
            "agent TEXT NOT NULL, object TEXT NOT NULL, "
            "detail TEXT NOT NULL, prev_hash TEXT NOT NULL, "
            "hash TEXT NOT NULL)")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def upsert_agent(self, name, card_url, interface_url, protocol_version,
                     tenant, description, agent_version, skills):
        """Insert-or-replace an agent, then re-read it (A1-store).

        Returns the re-read row, or None when the re-read disagrees.
        """
        now = int(time.time())
        skills_json = json.dumps(skills, sort_keys=True)
        self.conn.execute(
            "INSERT INTO agent_call_agents (name, card_url, "
            "interface_url, protocol_version, tenant, description, "
            "agent_version, skills_json, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET card_url=excluded.card_url, "
            "interface_url=excluded.interface_url, "
            "protocol_version=excluded.protocol_version, "
            "tenant=excluded.tenant, description=excluded.description, "
            "agent_version=excluded.agent_version, "
            "skills_json=excluded.skills_json, "
            "discovered_at=excluded.discovered_at",
            (name, card_url, interface_url, protocol_version, tenant,
             description, agent_version, skills_json, now))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT name, card_url, interface_url, protocol_version, "
            "tenant, description, agent_version, skills_json "
            "FROM agent_call_agents WHERE name = ?", (name,)).fetchone()
        if not row or row[1] != card_url or row[2] != interface_url:
            return None
        return row

    def get_agent(self, name):
        row = self.conn.execute(
            "SELECT name, card_url, interface_url, protocol_version, "
            "tenant, description, agent_version, skills_json "
            "FROM agent_call_agents WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        return {"name": row[0], "card_url": row[1],
                "interface_url": row[2], "protocol_version": row[3],
                "tenant": row[4], "description": row[5],
                "agent_version": row[6],
                "skills": json.loads(row[7])}

    def list_agents(self):
        rows = self.conn.execute(
            "SELECT name, card_url, interface_url, protocol_version, "
            "description, agent_version FROM agent_call_agents "
            "ORDER BY name").fetchall()
        return [{"name": r[0], "card_url": r[1], "interface_url": r[2],
                 "protocol_version": r[3], "description": r[4],
                 "agent_version": r[5]} for r in rows]

    def remove_agent(self, name):
        self.conn.execute(
            "DELETE FROM agent_call_agents WHERE name = ?", (name,))
        self.conn.commit()
        return self.get_agent(name) is None

    def _last_hash(self):
        row = self.conn.execute(
            "SELECT hash FROM agent_call_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else "GENESIS"

    def record(self, kind, agent, obj, detail):
        """Insert + re-read before claiming (A1-store). Returns the
        re-read row, or None when the re-read disagrees."""
        ts = int(time.time())
        prev = self._last_hash()
        digest = store_base.chain_digest(
            "prev_payload_list", prev,
            {"fields": [ts, kind, agent, obj, detail]})
        cur = self.conn.execute(
            "INSERT INTO agent_call_events "
            "(ts, kind, agent, object, detail, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, kind, agent, obj, detail, prev, digest))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, ts, kind, agent, object, detail, hash "
            "FROM agent_call_events WHERE id = ?",
            (cur.lastrowid,)).fetchone()
        if not row or row[6] != digest or row[3] != agent:
            return None
        return row

    def verify_chain(self):
        rows = self.conn.execute(
            "SELECT id, ts, kind, agent, object, detail, "
            "prev_hash, hash FROM agent_call_events ORDER BY id"
            ).fetchall()
        entries = [{"fields": [ts, kind, agent, obj, detail],
                    "prev_hash": prev_hash, "hash": digest}
                   for _id, ts, kind, agent, obj, detail, prev_hash, digest in rows]
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
            "SELECT id, ts, kind, agent, object, detail "
            "FROM agent_call_events ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return rows

    def counts(self):
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) FROM agent_call_events GROUP BY kind"
            ).fetchall()
        return dict(rows)


# -------------------------------------------------------------- actions

def _do_discover(db, url, _opener):
    card_url, refusal = _card_url_for(url)
    if refusal:
        return refusal
    card, err = _fetch_json(card_url, _opener=_opener)
    if err:
        return err
    iface, refusal = _validate_card(card)
    if refusal:
        return refusal
    name = card["name"].strip()
    row = db.upsert_agent(
        name, card_url, iface["url"], iface["protocol_version"],
        iface["tenant"], card["description"].strip(),
        card["version"].strip(), card["skills"])
    if row is None:
        return ("the agent card was fetched (%s) but the local registry "
                "re-read disagreed — the discovery is reported, not "
                "hidden, and the registry needs a look." % name)
    db.record("discover", name, "agent",
              "%s via %s (protocol %s)"
              % (name, iface["url"], iface["protocol_version"]))
    skills = card["skills"]
    lines = ["Agent discovered: %s (version %s, A2A protocol %s)."
             % (name, card["version"].strip(),
                iface["protocol_version"]),
             "  %s" % card["description"].strip(),
             "  Interface: %s (JSON-RPC)" % iface["url"]]
    if iface["tenant"]:
        lines.append("  Tenant: %s (sent with every call)" % iface["tenant"])
    caps = card["capabilities"]
    lines.append("  Capabilities: streaming=%s, push notifications=%s."
                 % (bool(caps.get("streaming")),
                    bool(caps.get("pushNotifications"))))
    if skills:
        lines.append("  Skills (%d): %s" % (
            len(skills),
            ", ".join(s.get("name", "?") for s in skills
                      if isinstance(s, dict))))
    else:
        lines.append("  Skills: none declared.")
    lines.append("No authentication is required by this agent's card; "
                 "calls to it ask first (C-14).")
    return "\n".join(lines)


def _do_list(db):
    agents = db.list_agents()
    if not agents:
        return ("No agents discovered yet. Discover one with action "
                "'discover' and the agent's URL first.")
    lines = ["Known agents (%d):" % len(agents)]
    for agent in agents:
        lines.append("  - %s (v%s, A2A %s): %s — %s"
                     % (agent["name"], agent["agent_version"],
                        agent["protocol_version"], agent["description"],
                        agent["interface_url"]))
    return "\n".join(lines)


def _do_call(db, agent_name, text, task_id, context_id, history_length,
             return_immediately, _opener):
    if not text or not str(text).strip():
        return ("refused: 'call' needs the text to send. Nothing was "
                "sent.")
    agent = db.get_agent(agent_name) if agent_name else None
    if agent is None:
        return ("refused: no agent named '%s' is known. Discover it "
                "first with action 'discover' and its URL — VYUK "
                "never calls an agent it has not seen a card for."
                % (agent_name or ""))
    message = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER",
               "parts": [{"text": str(text)}]}
    if context_id:
        message["contextId"] = str(context_id)
    if task_id:
        message["taskId"] = str(task_id)
    params = {"message": message,
              "configuration": {"acceptedOutputModes": ["text/plain"]}}
    if history_length is not None:
        params["configuration"]["historyLength"] = int(history_length)
    if return_immediately:
        params["configuration"]["returnImmediately"] = True
    if agent["tenant"]:
        params["tenant"] = agent["tenant"]
    result, err = _jsonrpc(agent["interface_url"], "SendMessage", params,
                           _opener=_opener)
    if err:
        return err
    if not isinstance(result, dict):
        return ("the agent answered with a result that is not an object "
                "— nothing was claimed.")
    detail = ""
    if isinstance(result.get("task"), dict):
        detail = _summarise_task(result["task"])
        task_id_out = result["task"].get("id") or "?"
        state = (result["task"].get("status") or {}).get("state") \
            if isinstance(result["task"].get("status"), dict) else None
    elif isinstance(result.get("message"), dict):
        text_out = _parts_text(result["message"].get("parts"))
        detail = ("the agent answered with a message (no task):\n%s"
                  % (text_out or "(no text parts)"))
        task_id_out = result["message"].get("taskId") or "?"
        state = None
    else:
        return ("the agent answered with neither a task nor a message "
                "— nothing was claimed.")
    row = db.record("call", agent["name"], "task",
                    "task %s state %s" % (task_id_out, state or "?"))
    if row is None:
        detail += ("\n(The call reached the agent, but the local ledger "
                   "re-read disagreed — the answer is reported, not "
                   "hidden, and the ledger needs a look.)")
    return detail


def _do_task(db, agent_name, mode, task_id, context_id, status, page_size,
             _opener):
    agent = db.get_agent(agent_name) if agent_name else None
    if agent is None:
        return ("refused: no agent named '%s' is known. Discover it "
                "first with action 'discover' and its URL."
                % (agent_name or ""))
    url = agent["interface_url"]
    tenant = agent["tenant"]
    if mode == "get":
        if not task_id:
            return ("refused: 'task' get needs a task id. Nothing was "
                    "sent.")
        params = {"id": str(task_id)}
        if tenant:
            params["tenant"] = tenant
        result, err = _jsonrpc(url, "GetTask", params, _opener=_opener)
        if err:
            return err
        return _summarise_task(result)
    if mode == "list":
        params = {}
        if context_id:
            params["contextId"] = str(context_id)
        if status:
            state = str(status).strip().upper()
            if not state.startswith("TASK_STATE_"):
                state = "TASK_STATE_" + state
            if state not in _TASK_STATES:
                return ("refused: '%s' is not an A2A task state. "
                        "Nothing was sent." % status)
            params["status"] = state
        if page_size is not None:
            params["pageSize"] = max(1, min(100, int(page_size)))
        if tenant:
            params["tenant"] = tenant
        result, err = _jsonrpc(url, "ListTasks", params, _opener=_opener)
        if err:
            return err
        tasks = (result or {}).get("tasks")
        if not isinstance(tasks, list):
            return ("the agent answered with no task list — nothing was "
                    "claimed.")
        if not tasks:
            return "Tasks: none listed by %s." % agent["name"]
        lines = ["Tasks on %s (%d):" % (agent["name"], len(tasks))]
        for task in tasks:
            if not isinstance(task, dict):
                continue
            state = (task.get("status") or {}).get("state") \
                if isinstance(task.get("status"), dict) else "?"
            lines.append("  - %s: %s" % (task.get("id") or "?", state))
        return "\n".join(lines)
    if mode == "cancel":
        if not task_id:
            return ("refused: 'task' cancel needs a task id. Nothing "
                    "was sent.")
        params = {"id": str(task_id)}
        if tenant:
            params["tenant"] = tenant
        result, err = _jsonrpc(url, "CancelTask", params, _opener=_opener)
        if err:
            return err
        row = db.record("task.cancel", agent["name"], "task",
                        "task %s cancelled" % task_id)
        detail = _summarise_task(result)
        if row is None:
            detail += ("\n(The cancel reached the agent, but the local "
                       "ledger re-read disagreed — the answer is "
                       "reported, not hidden, and the ledger needs a "
                       "look.)")
        return detail
    return ("refused: 'task' mode must be 'get', 'list' or 'cancel'.")


def _do_remove(db, agent_name):
    if not agent_name:
        return "refused: 'remove' needs the agent's name."
    if db.get_agent(agent_name) is None:
        return ("refused: no agent named '%s' is known — nothing was "
                "removed." % agent_name)
    if not db.remove_agent(agent_name):
        return ("the agent '%s' could not be removed from the registry "
                "— the re-read still finds it." % agent_name)
    db.record("remove", agent_name, "agent", "forgotten")
    return "Agent '%s' forgotten." % agent_name


def _do_card():
    """VYUK's own agent card — a static document, not an endpoint.

    Honest about the limit: VYUK is an A2A client only. It serves
    no endpoint, so supportedInterfaces is empty and the card says
    so; another agent cannot call VYUK through this card.
    """
    card = {
        "name": "VYUK",
        "description": ("VYUK — a local, single-user voice assistant "
                        "for the owner's own computer. This card is a "
                        "description only: VYUK is an A2A client, it "
                        "serves no A2A endpoint and accepts no inbound "
                        "calls."),
        "version": VERSION,
        "supportedInterfaces": [],
        "capabilities": {"streaming": False,
                         "pushNotifications": False,
                         "extendedAgentCard": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [],
        "note": ("No endpoint: nothing VYUK serves is "
                 "internet-reachable. This card describes VYUK; it "
                 "cannot be used to reach it."),
    }
    return ("VYUK's own agent card (a description, NOT an endpoint — "
            "VYUK serves nothing and accepts no inbound A2A calls):\n"
            + json.dumps(card, indent=2, sort_keys=True))


def _do_status(db):
    counts = db.counts()
    total = sum(counts.values())
    ok, chain = db.verify_chain()
    agents = db.list_agents()
    lines = ["A2A client — VYUK calls other agents; it serves no "
             "endpoint.",
             "Known agents: %d." % len(agents),
             "Ledger: %d recorded actions; chain %s."
             % (total, chain)]
    for kind in sorted(counts):
        lines.append("  - %s: %d" % (kind, counts[kind]))
    if not ok:
        lines.append("WARNING: the ledger chain does not verify — %s."
                     % chain)
    lines.append("Authenticated agents are refused (no credentials are "
                 "stored); streaming and push notifications are not "
                 "built.")
    return "\n".join(lines)


def _do_events(db, limit):
    rows = db.list_events(limit if limit is not None else 20)
    ok, chain = db.verify_chain()
    if not rows:
        return ("No A2A actions recorded yet. Chain: %s." % chain)
    lines = ["A2A ledger (%d shown; chain: %s):"
             % (len(rows), chain)]
    for row in rows:
        _id, _ts, kind, agent, obj, detail = row
        lines.append("  #%d %s %s %s — %s"
                     % (_id, kind, obj, agent, detail))
    return "\n".join(lines)


def agent_call(action="", mode="", url="", agent="", text="", task_id="",
               context_id="", history_length=None, return_immediately=False,
               status="", page_size=None, limit=None, detail=None,
               _opener=None, _db_path=None) -> str:
    """Public entry: one call, one connection, closed before returning.

    Every funnel call opens the agent registry + ledger, works, and
    closes it — a leaked connection would hold a SQLite lock across
    calls and block the next write.
    """
    action = str(action or "").strip().lower()
    mode = str(mode or "").strip().lower()
    db = _AgentCallDB(_db_path)
    try:
        if action in ("status", "events"):
            if action == "status":
                return _do_status(db)
            return _do_events(db, limit)
        if action == "card":
            return _do_card()
        if action == "discover":
            return _do_discover(db, url, _opener)
        if action == "list":
            return _do_list(db)
        if action == "call":
            return _do_call(db, agent, text, task_id, context_id,
                            history_length, return_immediately, _opener)
        if action == "task":
            return _do_task(db, agent, mode, task_id, context_id, status,
                            page_size, _opener)
        if action == "remove":
            return _do_remove(db, agent)
        return ("refused: action must be 'discover', 'list', 'call', "
                "'task', 'remove', 'card', 'status' or 'events'.")
    finally:
        db.close()
