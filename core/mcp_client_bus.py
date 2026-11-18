"""MCP client — the bus (split from core/mcp_client.py, Phase B B23)."""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
from typing import Any
# INTENTIONAL cross-part imports (bare-name rule): MCPBus methods and
# get_bus resolve these by bare name; they live in the sibling modules.
from .mcp_client_config import (
    CONNECT_TIMEOUT_S,
    DEFAULT_CALL_TIMEOUT_S,
    _resolve_env_value,
    load_config,
)
from .mcp_client_session import _MemoryTokenStorage, _SDKSession
from .mcp_client_tools import (
    _annotations_of,
    _load_hashes,
    _parameters_of,
    _result_to_text,
    _save_hashes,
    _tool_hash,
    compose_tool_name,
)

# --- SPLIT BODY BELOW (verbatim from core/mcp_client.py) ---


# ---------------------------------------------------------------------------
# The bus
# ---------------------------------------------------------------------------

class _SessionBroken(Exception):
    """Marker: the transport/session broke mid-serve (distinct
    from a connect failure and from a tool's isError result)."""


class _ServerState:
    """Per-server runtime state. Plain attributes: the bus
    thread writes them, sync callers read them; the GIL makes
    single-attribute reads/writes atomic, and every decision
    that needs more than one read re-checks under the lock."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.name: str = cfg["name"]
        #: idle | connecting | ready | dead | disabled
        self.status = "disabled" if not cfg.get("enabled", True) else "idle"
        self.error: str | None = None
        self.tools: list[dict[str, Any]] = []
        self.queue: queue.Queue = queue.Queue()
        #: Set when the state leaves "connecting" (ready or dead).
        self.event = threading.Event()
        self.task_running = False


class MCPBus:
    """One bus per process. Owns server processes/sessions on a
    single background thread running an anyio loop; sync callers
    talk to it through queues and events with timeouts.

    Failure isolation (GAPS-29, E-V6): one anyio task group
    hosts every server task, so a failure escaping one task
    would cancel them all — _server_task supervises each
    server (non-cancellation failures mark only that server
    dead) and start() resets stale runtime state on
    (re)start, so a dead session is never reported ready."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._servers, self._config_errors = load_config()
        self._states: dict[str, _ServerState] = {
            name: _ServerState(cfg)
            for name, cfg in self._servers.items()
        }
        self._table: dict[str, dict[str, Any]] = {}
        self._table_lock = threading.Lock()
        #: Composed names whose description hash changed this
        #: session (SE-5.12 runtime half) — classification reads
        #: this to force CONFIRM on changed tools.
        self._changed: set[str] = set()
        self._wanted: set[str] = set()
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._unavailable: str | None = None
        self._hashes: dict[str, str] = _load_hashes()

    # -- lifecycle -----------------------------------------------------

    def _reset_runtime_state(self) -> None:
        """Clear per-server runtime state before a (re)start
        (GAPS-29, E-V6): a bus thread that died leaves states
        saying "ready" with tools in the table but no live task
        behind them — a restart must never report a dead session
        as ready. Non-disabled servers go back to idle with
        empty tools and no error; the table is cleared (servers
        re-list on reconnect). The description-hash store
        (_changed/_hashes) is session state and is kept. On a
        first start this is a no-op (states are already idle,
        the table already empty)."""
        with self._table_lock:
            self._table.clear()
        for st in self._states.values():
            st.tools = []
            if st.status != "disabled":
                st.status = "idle"
                st.error = None
                st.event.clear()

    def start(self) -> bool:
        """Start the bus thread (idempotent). Returns False with
        _unavailable set when the libraries are absent — the
        caller turns that into a plain-words message."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            if self._unavailable is not None:
                return False
            try:
                import anyio  # noqa: F401 — import check only
                import fastmcp  # noqa: F401
                import mcp  # noqa: F401
            except Exception as e:  # noqa: BLE001
                self._unavailable = (
                    "The MCP toolbox libraries are not installed, so "
                    "MCP servers can't run on this install "
                    f"({type(e).__name__}).")
                return False
            self._shutdown.clear()
            self._reset_runtime_state()
            self._thread = threading.Thread(
                target=self._thread_main, daemon=True, name="mcp-bus")
            self._thread.start()
            return True

    def shutdown(self) -> None:
        """Stop the bus thread (tests + process teardown)."""
        self._shutdown.set()
        for st in self._states.values():
            try:
                st.queue.put_nowait({"kind": "__shutdown__"})
            except Exception:  # noqa: BLE001 — best effort
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    def _thread_main(self) -> None:
        import anyio
        anyio.run(self._amain)

    async def _amain(self) -> None:
        import anyio
        async with anyio.create_task_group() as tg:
            while not self._shutdown.is_set():
                with self._lock:
                    wanted = set(self._wanted)
                for name in wanted:
                    st = self._states.get(name)
                    if (st is not None and st.status == "idle"
                            and not st.task_running):
                        st.task_running = True
                        try:
                            tg.start_soon(self._server_task, st)
                        except Exception:  # noqa: BLE001 — group closing
                            # The group is tearing down (shutdown):
                            # the server stays idle and will be
                            # (re)started by the next bus start.
                            # A non-shutdown failure here is
                            # reported, never silent.
                            st.task_running = False
                            if not self._shutdown.is_set():
                                logging.getLogger(__name__).warning(
                                    "MCP server '%s' could not be "
                                    "started: task group refused it",
                                    st.name)
                await anyio.sleep(0.5)
            for st in self._states.values():
                try:
                    st.queue.put_nowait({"kind": "__shutdown__"})
                except Exception:  # noqa: BLE001 — best effort
                    pass

    # -- server tasks --------------------------------------------------

    def _build_client(self, st: _ServerState) -> Any:
        """Build the client adapter for one server. Raises
        ValueError with a plain-words reason for every
        config-shaped failure (missing env var, unsupported
        auth, missing secret) — the task turns it into dead
        state, never a traceback to the user."""
        cfg = st.cfg
        if cfg["transport"] == "stdio":
            from fastmcp import Client
            from fastmcp.client.transports import StdioTransport

            env: dict[str, str] = {}
            for key, value in cfg.get("env", {}).items():
                resolved = _resolve_env_value(value)
                if resolved is None:
                    raise ValueError(
                        f"Environment variable '{value[1:]}' (used by "
                        f"the '{st.name}' server) is not set.")
                env[key] = resolved
            return Client(StdioTransport(
                command=cfg["command"], args=cfg["args"], env=env or None))
        # Remote.
        auth = cfg.get("auth")
        if auth is None:
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport

            return Client(StreamableHttpTransport(
                cfg["url"], headers=cfg.get("headers") or None))
        if auth["type"] == "private_key_jwt":
            # Defense-in-depth only: validation (Gate C, P2-7)
            # rejects private_key_jwt before any config reaches
            # this builder, so a validated config can never
            # arrive here with this type. A programmatically
            # built config that bypasses validation still gets
            # the plain-words refusal, never a silent downgrade.
            # The JWT assertion needs a signing callback, and a
            # JSON config file cannot express one.
            raise ValueError(
                f"The '{st.name}' server uses private_key_jwt auth, "
                "which needs a signing callback the config file "
                "cannot express yet. That auth type is not runnable "
                "in this build.")
        # client_credentials via the raw SDK, issuer= enforced
        # (CVE-2026-115563: the parameter IS the fix).
        from mcp.client.auth.extensions.client_credentials import (
            ClientCredentialsOAuthProvider,
        )

        secret = os.environ.get(auth["client_secret_env"])
        if not secret:
            raise ValueError(
                f"Environment variable '{auth['client_secret_env']}' "
                f"(the '{st.name}' server's client secret) is not set.")
        provider = ClientCredentialsOAuthProvider(
            server_url=cfg["url"],
            storage=_MemoryTokenStorage(),
            client_id=auth["client_id"],
            client_secret=secret,
            issuer=auth["issuer"],
        )
        return _SDKSession(cfg["url"], provider, cfg.get("headers") or {})

    async def _server_task(self, st: _ServerState) -> None:
        """One server's task, supervised (GAPS-29, E-V6).

        One anyio task group hosts EVERY server, so a failure
        that escapes a server task would cancel the whole group
        — every server dies because one did. This wrapper
        contains it: anything that escapes _run_server_task
        other than cancellation marks ONLY this server dead
        (tools dropped, wanted dropped, plain-words error) and
        returns normally, so the other servers keep serving.
        Cancellation (group shutdown) still propagates — a
        shutdown must be able to stop the task. The single bus
        object stays (it owns the config + the tool table); the
        fix is failure isolation, not multiple buses."""
        try:
            await self._run_server_task(st)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — containment
            st.status = "dead"
            st.error = (
                f"The '{st.name}' server failed "
                f"({type(exc).__name__}: {exc}). The toolbox "
                "can't use it right now.")
            st.event.set()
            self._drop_server_tools(st)
            with self._lock:
                self._wanted.discard(st.name)
            logging.getLogger(__name__).warning(
                "MCP server '%s' task failed: %s", st.name, exc)

    async def _run_server_task(self, st: _ServerState) -> None:
        import anyio
        import logging

        log = logging.getLogger(__name__)
        client = None
        # entered=True only after __aenter__ returns. If connect
        # times out INSIDE __aenter__, the half-open client is
        # abandoned (a stdio child then dies on EOF when its
        # pipes close at GC); the bus never spawns a second
        # instance for that server without an explicit refresh.
        entered = False
        try:
            st.status = "connecting"
            st.error = None
            st.event.clear()
            try:
                # The connect budget covers ONLY connect + first
                # list. The serve loop below lives OUTSIDE it: a
                # session that survived connect must not die at
                # 15 s of idle serving.
                # v5.55 fix: anyio 4's fail_after is a SYNC
                # context manager (plain `with`, not `async
                # with`) — the first version used `async with`
                # and every connect died with
                # "'_GeneratorContextManager' object does not
                # support the asynchronous context manager
                # protocol". The call path below already used
                # plain `with` and was correct.
                with anyio.fail_after(CONNECT_TIMEOUT_S):
                    client = self._build_client(st)
                    await client.__aenter__()
                    entered = True
                    tools = await client.list_tools()
                self._publish_tools(st, tools)
                st.status = "ready"
                st.event.set()
                while True:
                    req = await anyio.to_thread.run_sync(st.queue.get)
                    if req.get("kind") == "__shutdown__":
                        break
                    try:
                        if req["kind"] == "list":
                            tools = await client.list_tools()
                            self._publish_tools(st, tools)
                            req["result"] = (True, tools, None)
                        elif req["kind"] == "call":
                            try:
                                with anyio.fail_after(req["timeout"]):
                                    result = await client.call_tool(
                                        req["tool"], req["args"])
                            except TimeoutError:
                                # A slow tool, not a dead session:
                                # fail the call, keep the session.
                                req["result"] = (
                                    False,
                                    f"The '{st.name}' server did not "
                                    "answer in time.",
                                    "timeout")
                            else:
                                text, is_error = _result_to_text(result)
                                req["result"] = (
                                    not is_error, text,
                                    "tool_error" if is_error else None)
                        else:
                            req["result"] = (
                                False, "Unknown toolbox request.",
                                "unavailable")
                    except Exception as e:  # noqa: BLE001
                        # Transport/session broke (tool errors
                        # arrive as isError results, not
                        # exceptions) — fail this request AND
                        # kill the session.
                        req["result"] = (
                            False,
                            f"The '{st.name}' server connection broke "
                            f"({type(e).__name__}: {e}).",
                            "unavailable")
                        raise _SessionBroken(str(e) or type(e).__name__)
                    finally:
                        req["event"].set()
            except _SessionBroken as e:
                detail = str(e)
                st.status = "dead"
                st.error = (
                    f"The '{st.name}' server connection broke "
                    f"({detail}). The toolbox can't use it right now.")
                st.event.set()
                self._drop_server_tools(st)
                with self._lock:
                    # Dead stays dead until an explicit refresh:
                    # no respawn storm, one timeout per process.
                    self._wanted.discard(st.name)
                log.warning("MCP server '%s' session broke: %s",
                            st.name, detail)
            except Exception as e:  # noqa: BLE001 — connect/list dead
                detail = str(e) or type(e).__name__
                st.status = "dead"
                st.error = (
                    f"The '{st.name}' server could not be reached "
                    f"({detail}). The toolbox can't use it right now.")
                st.event.set()
                self._drop_server_tools(st)
                with self._lock:
                    self._wanted.discard(st.name)
                log.warning("MCP server '%s' dead: %s", st.name, detail)
        finally:
            st.task_running = False
            if client is not None and entered:
                try:
                    await client.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001 — teardown, best effort
                    pass

    def _drop_server_tools(self, st: _ServerState) -> None:
        """Remove one server's tools from the table (dead servers
        must not leave stale tools discoverable)."""
        with self._table_lock:
            for key in [k for k, t in self._table.items()
                        if t["server"] == st.name]:
                del self._table[key]
        st.tools = []

    def _publish_tools(self, st: _ServerState, tools: list[Any]) -> None:
        """Normalise one listing into the table + hash store.
        A changed description hash marks the tool CONFIRM-class
        for the rest of the session (SE-5.12 runtime half)."""
        import logging

        log = logging.getLogger(__name__)
        normalised: list[dict[str, Any]] = []
        new_hashes = dict(self._hashes)
        for tool in tools:
            name = getattr(tool, "name", None) or ""
            entry = {
                "server": st.name,
                "tool": name,
                "composed": compose_tool_name(st.name, name),
                "description": getattr(tool, "description", None) or "",
                "parameters": _parameters_of(tool),
                "annotations": _annotations_of(tool),
            }
            digest = _tool_hash(entry)
            if (entry["composed"] in self._hashes
                    and self._hashes[entry["composed"]] != digest):
                self._changed.add(entry["composed"])
                log.warning(
                    "MCP tool '%s' description changed since last "
                    "seen — forcing confirmation for this session.",
                    entry["composed"])
            new_hashes[entry["composed"]] = digest
            normalised.append(entry)
        with self._table_lock:
            for key in [k for k, t in self._table.items()
                        if t["server"] == st.name]:
                del self._table[key]
            for entry in normalised:
                self._table[entry["composed"]] = entry
        st.tools = normalised
        self._hashes = new_hashes
        _save_hashes(new_hashes)

    # -- sync API (registry / toolbox / doctor) ------------------------

    def servers(self) -> list[dict[str, Any]]:
        """Cached per-server state for the doctor. NEVER connects
        — the doctor reports, it does not block on a dead server."""
        rows: list[dict[str, Any]] = []
        for name, st in self._states.items():
            rows.append({
                "name": name,
                "enabled": st.cfg.get("enabled", True),
                "transport": st.cfg.get("transport"),
                "status": st.status,
                "error": st.error,
                "tool_count": len(st.tools),
            })
        for name, reason in self._config_errors.items():
            if name == "__file__":
                rows.append({
                    "name": "__file__", "enabled": None, "transport": None,
                    "status": "config_error", "error": reason,
                    "tool_count": 0,
                })
            elif name not in self._states:
                rows.append({
                    "name": name, "enabled": None, "transport": None,
                    "status": "config_error", "error": reason,
                    "tool_count": 0,
                })
        return rows

    def ensure_server(self, name: str,
                      timeout: float = CONNECT_TIMEOUT_S) -> tuple[bool, str]:
        """Connect one server (bounded). Returns (ok, reason);
        reason is plain words on failure."""
        if name in self._config_errors:
            return False, self._config_errors[name]
        st = self._states.get(name)
        if st is None:
            return False, (f"There is no MCP server called '{name}' "
                            "configured.")
        if st.status == "disabled":
            return False, (f"The MCP server '{name}' is disabled in "
                            "mcp_servers.json.")
        if st.status == "ready":
            return True, ""
        if st.status == "dead":
            return False, st.error or "The server is unavailable."
        if not self.start():
            return False, self._unavailable or (
                "The MCP toolbox libraries are not installed.")
        with self._lock:
            self._wanted.add(name)
        st.event.wait(timeout)
        if st.status == "ready":
            return True, ""
        if st.status == "dead":
            return False, st.error or "The server is unavailable."
        return False, (f"The '{name}' server did not start within "
                       f"{timeout:g} seconds.")

    def ensure_all_enabled(self,
                           timeout: float = CONNECT_TIMEOUT_S) -> None:
        """Best-effort connect of every enabled server (toolbox
        discovery path). Failures are cached dead state, not
        raised — find proceeds with whatever connected."""
        for name, st in self._states.items():
            if st.cfg.get("enabled", True) and st.status in ("idle", "connecting"):
                self.ensure_server(name, timeout=timeout)

    def refresh(self, name: str) -> tuple[bool, str]:
        """Clear dead state so the next ensure reconnects, or
        re-list a ready server (best effort)."""
        if name in self._config_errors:
            return False, self._config_errors[name]
        st = self._states.get(name)
        if st is None:
            return False, (f"There is no MCP server called '{name}' "
                            "configured.")
        if st.status == "dead":
            st.status = "idle"
            st.error = None
            st.event.clear()
            with self._lock:
                self._wanted.add(name)
            return True, ""
        if st.status == "ready":
            st.queue.put({"kind": "list", "event": threading.Event(),
                          "result": None})
            return True, ""
        return False, f"The '{name}' server is {st.status}."

    def reload_config(self) -> dict[str, Any]:
        """Re-read mcp_servers.json and reconcile the live bus.

        R18-3 (IBM/mcp-context-forge inspiration): the registry
        tool edits the server config, and the bus must reflect
        the edit without a restart — otherwise a disable would
        keep serving a server the owner just switched off.

        Added servers get a fresh idle state (never connected
        here — lazy, like the rest of the bus). Removed servers
        have their tools dropped, their task stopped, and their
        state deleted. Disabled servers have their tools
        dropped, their task stopped, and status set to
        "disabled". Re-enabled servers go idle and connect on
        first use. A server that turned dead-by-config is
        dropped the same way a removed one is (it reappears as
        a config_error row in servers()). A broken config file
        is reported in "errors" and the previous state is kept
        for the file entries (the defaults still apply) — the
        reload never raises, and a bad edit never silently
        wipes the live table.
        """
        try:
            servers, errors = load_config()
        except Exception as e:  # noqa: BLE001 — a broken loader
            return {"added": [], "removed": [], "enabled": [],  # keeps state
                    "disabled": [], "errors": {"__file__": str(e)}}
        if "__file__" in errors:
            # The file is broken: load_config already dropped
            # every file entry (defaults only). Do not apply
            # that as a removal — keep the previous live state
            # and report the file error.
            return {"added": [], "removed": [], "enabled": [],
                    "disabled": [], "errors": dict(errors)}
        added: list[str] = []
        removed: list[str] = []
        enabled: list[str] = []
        disabled: list[str] = []
        stop: list[_ServerState] = []
        with self._lock:
            old_names = set(self._states)
            new_names = set(servers)
            for name in sorted(new_names - old_names):
                self._states[name] = _ServerState(servers[name])
                added.append(name)
            for name in sorted(old_names - new_names):
                st = self._states.pop(name)
                self._wanted.discard(name)
                removed.append(name)
                if st.task_running:
                    stop.append(st)
            for name in sorted(new_names & old_names):
                st = self._states[name]
                was_enabled = st.cfg.get("enabled", True)
                st.cfg = servers[name]
                now_enabled = servers[name].get("enabled", True)
                if was_enabled and not now_enabled:
                    self._wanted.discard(name)
                    disabled.append(name)
                    if st.task_running:
                        stop.append(st)
                    st.status = "disabled"
                    st.error = None
                elif not was_enabled and now_enabled:
                    enabled.append(name)
                    if st.status == "disabled":
                        st.status = "idle"
                        st.error = None
                        st.event.clear()
            # Servers that turned dead-by-config: drop them the
            # same way a removed server is dropped, so servers()
            # reports them as config_error rows.
            for name in sorted(set(self._states) & set(errors)):
                if name == "__file__":
                    continue
                st = self._states.pop(name)
                self._wanted.discard(name)
                removed.append(name)
                if st.task_running:
                    stop.append(st)
            self._servers = servers
            self._config_errors = errors
        for st in stop:
            self._drop_server_tools(st)
            try:
                st.queue.put_nowait({"kind": "__shutdown__"})
            except Exception:  # noqa: BLE001 — teardown, best effort
                pass
        return {"added": added, "removed": sorted(set(removed)),
                "enabled": enabled, "disabled": disabled,
                "errors": dict(errors)}

    def list_tools(self) -> list[dict[str, Any]]:
        """Snapshot of the current MCP tool table (no connect)."""
        with self._table_lock:
            return list(self._table.values())

    def resolve(self, composed: str) -> dict[str, Any] | None:
        """One toolbox-visible MCP name -> its table entry."""
        with self._table_lock:
            return self._table.get(composed)

    def tool_changed(self, composed: str) -> bool:
        """True when this tool's description hash changed since
        the last-seen hash (SE-5.12 runtime half)."""
        return composed in self._changed

    def call_tool_sync(self, server: str, tool: str,
                       args: dict[str, Any],
                       timeout: float = DEFAULT_CALL_TIMEOUT_S
                       ) -> tuple[bool, str, str | None]:
        """Call one MCP tool, bounded. Returns (ok, text,
        failure_kind); text is the tool's text on success, a
        plain-words reason on failure; failure_kind is None on
        success, else "unavailable" (server unreachable),
        "timeout" (bounded wait expired), or "tool_error"
        (the tool ran and reported its own error). Never raises
        for a server-side failure."""
        ok, err = self.ensure_server(server)
        if not ok:
            return False, err, "unavailable"
        st = self._states[server]
        req: dict[str, Any] = {
            "kind": "call", "tool": tool, "args": args,
            "timeout": timeout, "event": threading.Event(),
            "result": None,
        }
        st.queue.put(req)
        if not req["event"].wait(timeout + 10.0):
            return False, f"The '{server}' server did not answer in time.", "timeout"
        result = req["result"]
        if result is None:
            return False, f"The '{server}' server did not answer.", "unavailable"
        return result


_BUS: MCPBus | None = None


def get_bus() -> MCPBus:
    """The process-wide bus singleton."""
    global _BUS
    if _BUS is None:
        _BUS = MCPBus()
    return _BUS
