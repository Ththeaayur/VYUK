"""R7-1 munim-computer-use — consume-side integration (round-7 #1).

What this pins:
* the built-in default exists, is DISABLED, and names the vendor's
  documented launch (npx -y munim-computer-use);
* a user file can enable it (merge by name) and nothing else changes;
* the doctor reports the disabled server as PASS (stays off until
  enabled) — the launcher which-check only runs once enabled;
* munim-shaped tools surface through the stub bus and are
  confirm-gated with no policy record (unreviewed -> confirm,
  never dispatched) and dispatch once operator-reviewed as read;
* no native tool is added: the tools live in the bus table only
  (INV-1), the fitted registry is untouched.

Lab limits (honest): neither lab interpreter has fastmcp/mcp
installed, so the real bus cannot start here and no real stdio
server is exercised — the stub-bus pattern is the tree's own
(test_logicL2 _FakeBus). The real-Windows field leg (real
binary, real UIA, real agent pointer) is owed.
"""

import json
import tempfile
import unittest
from pathlib import Path

import core.mcp_client as mcp_client_mod
from core.mcp_client_config import (
    DEFAULT_SERVERS,
    load_config,
    validate_server_config,
)
import core.mcp_policy as mcp_policy
from core.funnel import tools_registry as tools_registry_mod
from core.funnel import tools_handlers as tools_handlers_mod

MUNIM = "munim-computer-use"

# Two munim-shaped tools, modelled on the vendor's real surface
# (get_app_state reads the accessibility tree; click_element acts
# by element id). Schemas are the vendor's shapes, not VYUK's.
_GET_APP_STATE = {
    "server": MUNIM, "tool": "get_app_state",
    "name": f"mcp__{MUNIM}__get_app_state",
    "description": "Read the accessibility tree of the foreground app.",
    "parameters": {"type": "object", "properties": {}},
    "annotations": {},
}
_CLICK_ELEMENT = {
    "server": MUNIM, "tool": "click_element",
    "name": f"mcp__{MUNIM}__click_element",
    "description": "Click an element by its accessibility-tree id.",
    "parameters": {"type": "object",
                   "properties": {"element_id": {"type": "string"}},
                   "required": ["element_id"]},
    "annotations": {},
}

_SAVED_TIMESTAMPS = []


def setUpModule():
    global _SAVED_TIMESTAMPS
    with tools_handlers_mod._budget_lock:
        _SAVED_TIMESTAMPS = list(
            tools_handlers_mod._budget_timestamps)


def tearDownModule():
    with tools_handlers_mod._budget_lock:
        tools_handlers_mod._budget_timestamps[:] = _SAVED_TIMESTAMPS


class _FakeBus:
    """Minimal MCP bus (test_logicL2 _FakeBus pattern) with dispatch
    recording."""

    def __init__(self, entries):
        self._entries = {e["name"]: e for e in entries}
        self.dispatched = []

    def resolve(self, name):
        return self._entries.get(name)

    def tool_changed(self, name):
        return False

    def ensure_server(self, server):
        return True, None

    def call_tool_sync(self, server, tool, args, timeout=120.0):
        self.dispatched.append((server, tool))
        return True, "fake mcp result", None


class TestMunimDefault(unittest.TestCase):
    """The built-in default: present, disabled, vendor launch."""

    def test_default_entry_shape(self):
        entry = DEFAULT_SERVERS[MUNIM]
        self.assertEqual(entry["command"], "npx")
        self.assertEqual(entry["args"], ["-y", "munim-computer-use"])
        self.assertIs(entry["enabled"], False)
        self.assertIn("Node.js", entry["note"])
        self.assertIn("Chrome extension", entry["note"])
        self.assertIn("copy this whole entry", entry["note"])

    def test_default_validates_clean(self):
        cfg, err = validate_server_config(MUNIM, DEFAULT_SERVERS[MUNIM])
        self.assertIsNone(err, err)
        self.assertEqual(cfg["transport"], "stdio")
        self.assertEqual(cfg["command"], "npx")
        self.assertEqual(cfg["args"], ["-y", "munim-computer-use"])
        self.assertIs(cfg["enabled"], False)
        self.assertEqual(cfg["env"], {})

    def test_load_config_includes_it_disabled(self):
        servers, errors = load_config()
        self.assertEqual(errors, {})
        self.assertIn(MUNIM, servers)
        self.assertIs(servers[MUNIM]["enabled"], False)
        # The other defaults are untouched by the addition.
        self.assertTrue(servers["fetch"]["enabled"])
        self.assertTrue(servers["time"]["enabled"])
        self.assertIs(servers["git"]["enabled"], False)

    def test_user_file_can_enable_it(self):
        # A file entry REPLACES the default wholesale (it is
        # validated standalone), so enabling needs the full entry.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mcp_servers.json").write_text(json.dumps({
                "mcpServers": {MUNIM: {
                    "command": "npx",
                    "args": ["-y", "munim-computer-use"],
                    "enabled": True}},
            }), encoding="utf-8")
            old_root = mcp_client_mod.PROJECT_ROOT
            mcp_client_mod.PROJECT_ROOT = root
            try:
                servers, errors = load_config()
            finally:
                mcp_client_mod.PROJECT_ROOT = old_root
        self.assertEqual(errors, {})
        self.assertIs(servers[MUNIM]["enabled"], True)
        self.assertEqual(servers[MUNIM]["command"], "npx")
        self.assertEqual(servers[MUNIM]["args"],
                         ["-y", "munim-computer-use"])
        self.assertTrue(servers["fetch"]["enabled"])

    def test_partial_user_entry_is_a_loud_error_not_a_merge(self):
        # {"enabled": true} alone has no transport, so it fails
        # validation — a loud config error, and the default
        # (disabled) stands. A partial enable must never
        # silently take effect.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mcp_servers.json").write_text(json.dumps({
                "mcpServers": {MUNIM: {"enabled": True}},
            }), encoding="utf-8")
            old_root = mcp_client_mod.PROJECT_ROOT
            mcp_client_mod.PROJECT_ROOT = root
            try:
                servers, errors = load_config()
            finally:
                mcp_client_mod.PROJECT_ROOT = old_root
        self.assertIn(MUNIM, errors)
        self.assertIn("neither command nor url", errors[MUNIM])
        self.assertIs(servers[MUNIM]["enabled"], False)

    def test_doctor_reports_disabled_as_pass(self):
        import doctor as doctor_mod

        doctor_mod._register_package()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = mcp_client_mod.PROJECT_ROOT
            mcp_client_mod.PROJECT_ROOT = root
            rows = []
            old_report = doctor_mod.report
            doctor_mod.report = lambda *a, **k: rows.append(a)
            try:
                doctor_mod.check_mcp_servers()
            finally:
                doctor_mod.report = old_report
                mcp_client_mod.PROJECT_ROOT = old_root
        munim_rows = [r for r in rows
                      if len(r) > 1 and MUNIM in str(r[1])]
        self.assertTrue(munim_rows, f"no doctor row for munim in {rows}")
        self.assertEqual(munim_rows[0][0], "PASS", munim_rows)
        self.assertIn("disabled", " ".join(str(r) for r in munim_rows))


class TestMunimBoundary(unittest.TestCase):
    """Munim tools through the funnel: confirm-gated until reviewed."""

    def setUp(self):
        self._orig_get_bus = mcp_client_mod.get_bus

    def tearDown(self):
        mcp_client_mod.get_bus = self._orig_get_bus

    def _bus(self):
        return _FakeBus([_GET_APP_STATE, _CLICK_ELEMENT])

    def test_unreviewed_munim_tool_never_dispatches(self):
        bus = self._bus()
        mcp_client_mod.get_bus = lambda: bus
        reg = tools_registry_mod.ToolRegistry()
        result = reg.execute("mcp__munim-computer-use__click_element",
                             {"element_id": "42"})
        # No confirmation handler is active in the test process,
        # so the ask fails closed: refused / not_confirmed, and
        # nothing was dispatched to the server.
        self.assertEqual(result.get("status"), "refused", result)
        self.assertEqual(result.get("kind"), "not_confirmed", result)
        self.assertEqual(bus.dispatched, [])

    def test_unreviewed_munim_read_never_dispatches(self):
        bus = self._bus()
        mcp_client_mod.get_bus = lambda: bus
        reg = tools_registry_mod.ToolRegistry()
        result = reg.execute("mcp__munim-computer-use__get_app_state", {})
        self.assertEqual(result.get("status"), "refused", result)
        self.assertEqual(result.get("kind"), "not_confirmed", result)
        self.assertEqual(bus.dispatched, [])

    def test_reviewed_read_munim_tool_dispatches(self):
        entry = dict(_GET_APP_STATE)
        mcp_policy.set_store({
            mcp_policy.policy_key(entry): {
                "class": "read",
                "digest": mcp_policy.declaration_digest(entry)}})
        try:
            bus = self._bus()
            mcp_client_mod.get_bus = lambda: bus
            reg = tools_registry_mod.ToolRegistry()
            result = reg.execute(
                "mcp__munim-computer-use__get_app_state", {})
        finally:
            mcp_policy.reset_store()
        self.assertEqual(result.get("status"), "success", result)
        self.assertEqual(bus.dispatched,
                         [(MUNIM, "get_app_state")])

    def test_no_native_tool_added(self):
        reg = tools_registry_mod.ToolRegistry()
        native = [n for n in reg._tools if "munim" in n.lower()]
        self.assertEqual(native, [])
        self.assertEqual([n for n in reg._tools
                          if n.startswith("mcp__")], [])


if __name__ == "__main__":
    unittest.main()
