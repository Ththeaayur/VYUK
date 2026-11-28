"""GAPS-29 regression suite: review section 6.10, E-series
(Extensibility).

Pins: plugin tools in the page catalogue + galaxy under
"More skills" (E-V1), the cached plugin scan (E-V2), the
already-closed E-V3/E-V4/E-V5/E-V7a/E-V8 adjudications, the
MCPBus per-server failure isolation + restart state reset
(E-V6), the plugin-sandbox owed item (E-V7b, documented
future work — NOT claimed built), and the single
machine-checked tool-placement invariant (E-V9). No new tool:
census pins (755/382/373/164, params 2077, grouped 752,
closed 754).

All behavioural checks run against tempfile stores or fakes —
they never touch the tree's data/. Module hygiene: importing
core modules may construct module-level singletons that migrate
the LIVE assistant.db; data/ is snapshotted at import (before
any core import) and restored in tearDownModule, so the suite
leaves the tree exactly as it found it.

Run from the tree root with PYTHONPATH=. :
    python -m unittest tests.test_gaps29
"""

import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

TREE = Path(__file__).resolve().parent.parent

# The gui modules import voice_assistant.core.* (the shipped
# package name); plant the alias before importing them.
_pkg = types.ModuleType("voice_assistant")
_pkg.__path__ = [str(TREE)]
sys.modules.setdefault("voice_assistant", _pkg)

# Snapshot the tree's data/ BEFORE any core import (see the
# module docstring).
_DATA = TREE / "data"
_SAVED = {}
for _entry in _DATA.iterdir():
    if _entry.is_file():
        _SAVED[_entry.name] = _entry.read_bytes()


def setUpModule():
    pass


def tearDownModule():
    for _entry in list(_DATA.iterdir()):
        if _entry.is_file() and _entry.name not in _SAVED:
            _entry.unlink()
    for _name, _blob in _SAVED.items():
        (_DATA / _name).write_bytes(_blob)


try:
    from PySide6.QtCore import QSettings
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt optional, skip Qt-only tests
    QSettings = None
    _QT_AVAILABLE = False


_PLUGIN_TOOLS = ("get_crypto_price", "get_stock_quote")
# UI Phase 2 (2026-11-25): the three tools that were ungrouped
# before the Phase 2 catalogue fix — click_anywhere now lives in
# "Desktop control", the two plugin tools in "More skills".
_PRE_P2_UNGROUPED = ("click_anywhere", "get_crypto_price",
                     "get_stock_quote")


# ------------------------------------------------------------------ E-V1
@unittest.skipUnless(_QT_AVAILABLE, "Qt only: PySide6 unavailable")
class TestPluginCatalogue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _decls(self):
        from core.funnel.tools_registry import tools
        return list(tools.get_declarations())

    def test_catalogue_includes_plugin_tools(self):
        from gui.skills_catalog_widget import build_skills_catalog
        from gui.window_widgets_toolnames import _TOOL_FRIENDLY_NAMES
        widget = build_skills_catalog(self._decls(),
                                      _TOOL_FRIENDLY_NAMES)
        names = widget.tool_names()
        for name in _PRE_P2_UNGROUPED:
            self.assertIn(name, names)
        counts = widget.group_counts()
        self.assertEqual(counts.get("More skills"), 2)
        self.assertEqual(widget.total_count, 772)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_galaxy_includes_plugin_tools(self):
        from gui.skills_galaxy_data import build_galaxy
        from gui.window_widgets_toolnames import _TOOL_FRIENDLY_NAMES
        data = build_galaxy(self._decls(), _TOOL_FRIENDLY_NAMES)
        stars = {s.name: s.group for s in data.stars}
        for name in _PLUGIN_TOOLS:
            self.assertEqual(stars.get(name), "More skills")
        self.assertEqual(stars.get("click_anywhere"), "Desktop control")
        hubs = {h.group: h.count for h in data.hubs}
        self.assertEqual(hubs.get("More skills"), 2)


class TestRouterFallback(unittest.TestCase):
    def test_more_skills_router_holds_plugin_tools(self):
        from core.brain.domain_routers import domain_router_membership
        from core.funnel.tools_registry import tools
        membership = domain_router_membership(tools)
        self.assertEqual(set(membership.get("route_more_skills", ())),
                         set(_PLUGIN_TOOLS))


# ------------------------------------------------------------------ E-V2
class TestDiscoveryCache(unittest.TestCase):
    """The scan cache, against an isolated temp plugins dir so
    no process state leaks in (each test starts cold)."""

    _FIXTURE = (
        "# vyuk-plugin: capabilities=read\n"
        "PLUGIN = {'name': 'echo_thing', 'description': 'Echo.',\n"
        "          'parameters': {'type': 'object', 'properties': {}}}\n"
        "def run():\n    return 'echo'\n")

    def setUp(self):
        import core.plugin_loader as loader
        self.loader = loader
        self.tmp = tempfile.TemporaryDirectory()
        self.plugins_dir = Path(self.tmp.name) / "plugins"
        self.plugins_dir.mkdir()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.plugin_path = self.plugins_dir / "echo_plugin.py"
        self.plugin_path.write_text(self._FIXTURE, encoding="utf-8")
        self._write_manifest()
        self._old_plugins = loader.PLUGINS_DIR
        self._old_data = loader.DATA_DIR
        self._old_verify = loader.verify_shipped_manifest_signature
        loader.PLUGINS_DIR = self.plugins_dir
        loader.DATA_DIR = self.data_dir
        # The fixture manifest is unsigned by construction; the
        # test signs off on it (the signature path itself is
        # covered by the loader's own tests, not this one).
        loader.verify_shipped_manifest_signature = \
            lambda plugins_dir=None: (True, "")
        loader.clear_plugin_cache()

    def tearDown(self):
        loader = self.loader
        loader.verify_shipped_manifest_signature = self._old_verify
        loader.PLUGINS_DIR = self._old_plugins
        loader.DATA_DIR = self._old_data
        loader.clear_plugin_cache()
        self.tmp.cleanup()

    def _write_manifest(self):
        digest = hashlib.sha256(
            self.plugin_path.read_bytes()).hexdigest()
        (self.plugins_dir / "plugins_hashes.json").write_text(
            json.dumps({"echo_plugin.py": digest}, indent=2),
            encoding="utf-8")

    def _loaded_count(self, fn):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn()
        return buf.getvalue().count("[Plugins] Loaded:")

    def test_second_scan_no_reexec(self):
        first = self._loaded_count(
            lambda: self.loader.discover_plugins())
        second = self._loaded_count(
            lambda: self.loader.discover_plugins())
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_refresh_rescans(self):
        self.loader.discover_plugins()
        refreshed = self._loaded_count(
            lambda: self.loader.discover_plugins(refresh=True))
        self.assertEqual(refreshed, 1)

    def test_clear_cache_rescans(self):
        self.loader.discover_plugins()
        self.loader.clear_plugin_cache()
        again = self._loaded_count(
            lambda: self.loader.discover_plugins())
        self.assertEqual(again, 1)

    def test_changed_file_rescans(self):
        first = self.loader.discover_plugins()
        self.assertEqual(len(first), 1)
        # Change the plugin's bytes AND its manifest hash: the
        # fingerprint moves, so the next call re-scans and
        # re-executes (the new description proves it re-read
        # the file, not just re-listed the old entry).
        changed = self._FIXTURE.replace("'Echo.'", "'Echo two.'")
        self.plugin_path.write_text(changed, encoding="utf-8")
        self._write_manifest()
        second = self.loader.discover_plugins()
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].get("description"), "Echo two.")

    def test_different_snapshot_is_a_different_entry(self):
        # The collision verdict depends on existing_names, so
        # two callers with different snapshots hold different
        # cache entries. The discriminating assertion: a scan
        # whose snapshot contains echo_thing must refuse it —
        # if the cache ignored the snapshot, it would serve
        # the first call's cached [echo_thing] instead.
        self.loader.discover_plugins()
        result = self.loader.discover_plugins(
            existing_names={"echo_thing"})
        self.assertEqual(result, [])


# ------------------------------------------------------------------ E-V6
class TestMcpBusIsolation(unittest.TestCase):
    def _bus_with_servers(self):
        import core.mcp_client_bus as bus_mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "mcp_servers.json"
        cfg.write_text(json.dumps({"mcpServers": {
            "boom": {"command": "boom-cmd", "args": [],
                     "enabled": True},
            "ok": {"command": "ok-cmd", "args": [],
                   "enabled": True}}}))
        old = os.environ.get("VYUK_MCP_CONFIG_PATH")
        os.environ["VYUK_MCP_CONFIG_PATH"] = str(cfg)
        try:
            bus = bus_mod.MCPBus()
        finally:
            if old is None:
                os.environ.pop("VYUK_MCP_CONFIG_PATH", None)
            else:
                os.environ["VYUK_MCP_CONFIG_PATH"] = old
        return bus

    def test_server_task_contains_failure(self):
        # A failure escaping one server's task marks ONLY that
        # server dead — the other server's tools survive, and
        # the wrapper itself never raises.
        bus = self._bus_with_servers()
        st_boom = bus._states["boom"]
        st_ok = bus._states["ok"]
        bus._table["ok__tool"] = {"server": "ok", "tool": "tool"}
        st_ok.tools = [{"server": "ok", "tool": "tool"}]

        async def _boom_only(st):
            if st.name == "boom":
                raise KeyboardInterrupt("boom")

        bus._run_server_task = _boom_only
        asyncio.run(bus._server_task(st_boom))
        self.assertEqual(st_boom.status, "dead")
        self.assertIsNotNone(st_boom.error)
        self.assertEqual(st_ok.status, "idle")
        self.assertIn("ok__tool", bus._table)
        self.assertNotIn("boom", bus._wanted)

    def test_server_task_cancellation_propagates(self):
        # Cancellation is group shutdown, not a server failure:
        # it must still propagate out of the wrapper.
        bus = self._bus_with_servers()
        st_boom = bus._states["boom"]

        async def _raises_cancel(st):
            raise asyncio.CancelledError()

        bus._run_server_task = _raises_cancel
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(bus._server_task(st_boom))

    def test_start_resets_stale_state(self):
        # A (re)start after a dead thread must never report a
        # dead session as ready: states go back to idle and
        # the tool table is cleared. The MCP libraries are
        # absent in the lab, so stand in fake modules for the
        # import check (the thread then exits at once — the
        # reset happens synchronously in start(), before the
        # thread is created).
        import core.mcp_client_bus as bus_mod
        bus = self._bus_with_servers()
        fake_anyio = types.ModuleType("anyio")
        fake_anyio.run = lambda *a, **k: None
        fakes = {"anyio": fake_anyio,
                 "fastmcp": types.ModuleType("fastmcp"),
                 "mcp": types.ModuleType("mcp")}
        for name, mod in fakes.items():
            sys.modules[name] = mod
        try:
            bus._states["ok"].status = "ready"
            bus._table["ok__tool"] = {"server": "ok", "tool": "tool"}
            bus._states["ok"].tools = [{"server": "ok"}]
            bus._thread = threading.Thread()  # dead, never started
            self.assertTrue(bus.start())
            if bus._thread is not None:
                bus._thread.join(timeout=5)
            self.assertEqual(bus._states["ok"].status, "idle")
            self.assertEqual(bus._states["ok"].tools, [])
            self.assertEqual(bus._table, {})
        finally:
            for name in fakes:
                sys.modules.pop(name, None)
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001 — teardown only
                pass


# ------------------------------------------------------------------ E-V9
class TestToolPlacement(unittest.TestCase):
    def test_live_placement_ok(self):
        from core.funnel.tools_registry import tools
        from core.plugin_loader import discover_plugins
        from core.tool_placement import check_tool_placement
        plugin_names = {p["name"] for p in discover_plugins()}
        result = check_tool_placement(tools, plugin_names)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["checked"]["registered"], 772)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(result["checked"]["grouped"], 772)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 755 -> 759 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; UI Phase 2 (2026-11-25): grouped 769 -> 772 — click_anywhere + 2 plugin tools join the catalogue
        self.assertEqual(result["checked"]["ungrouped"], [])
        self.assertTrue(result["checked"]["snapshot_in_sync"])

    def test_placement_flags_unclassified_and_ungrouped(self):
        from core.tool_placement import check_tool_placement

        class _FakeRegistry:
            def get_declarations(self):
                return [
                    {"name": "get_current_time_date",
                     "description": "real", "parameters": {}},
                    {"name": "zz_no_such_tool",
                     "description": "bogus", "parameters": {}},
                ]

        result = check_tool_placement(_FakeRegistry(), set())
        self.assertFalse(result["ok"])
        joined = " ".join(result["problems"])
        self.assertIn("zz_no_such_tool", joined)
        self.assertNotIn("get_current_time_date", joined)

    def test_placement_passes_with_or_without_plugin_names(self):
        # UI Phase 2 (2026-11-25): the two plugin tools are now
        # grouped explicitly in "More skills" (and click_anywhere
        # in "Desktop control"), so the live registry passes with
        # or without the plugin names — nothing is ungrouped.
        from core.funnel.tools_registry import tools
        from core.tool_placement import check_tool_placement
        for names in ({"get_crypto_price", "get_stock_quote"}, set()):
            result = check_tool_placement(tools, names)
            self.assertTrue(result["ok"], result["problems"])
            self.assertEqual(result["checked"]["grouped"], 772)
            self.assertEqual(result["checked"]["ungrouped"], [])

    def test_placement_catches_snapshot_drift(self):
        from core import catalogue_snapshot
        from core.catalogue_groups_data import GROUP_MEMBERS
        from core.funnel.tools_registry import tools
        from core.tool_placement import check_tool_placement
        drifted = dict(GROUP_MEMBERS)
        drifted.pop("Built-in")
        with mock.patch.object(catalogue_snapshot, "GROUP_MEMBERS",
                               drifted):
            result = check_tool_placement(tools, set(_PLUGIN_TOOLS))
        self.assertFalse(result["ok"])
        self.assertTrue(any("snapshot" in p
                            for p in result["problems"]))


# ------------------------------------------------- adjudications E-V3/4/5/7/8
class TestAdjudications(unittest.TestCase):
    def test_ev3_single_source(self):
        from core.catalogue_groups_data import GROUP_MEMBERS
        from core.catalogue_snapshot import GROUP_MEMBERS as snap
        self.assertEqual(snap, GROUP_MEMBERS)

    @unittest.skipUnless(_QT_AVAILABLE, "Qt only: PySide6 unavailable")
    def test_ev3_shim_is_the_identical_object(self):
        import core.catalogue_groups_data as data
        import gui.skills_catalog_data as shim
        self.assertIs(shim.GROUP_MEMBERS, data.GROUP_MEMBERS)
        self.assertIs(shim.GROUP_ORDER, data.GROUP_ORDER)
        expected = {}
        for group, names in data.GROUP_MEMBERS.items():
            for name in names:
                expected[name] = group
        self.assertEqual(shim._NAME_TO_GROUP, expected)

    def test_ev4_no_ast_reparse(self):
        text = (TREE / "core" / "brain" / "domain_routers.py").read_text(
            encoding="utf-8")
        for frag in ("import ast", "ast.parse", "ast.literal_eval",
                     "ast.walk"):
            self.assertNotIn(frag, text)

    def test_ev5_mcp_never_in_registry(self):
        from core.funnel.tools_registry import tools
        names = [d["name"] for d in tools.get_declarations()]
        self.assertIn("use_toolbox", names)
        self.assertEqual([n for n in names if "__" in n], [])

    def test_ev7_capabilities_enforced(self):
        from core.funnel.tools_gate_egress import (
            _PLUGIN_GATE_CLASS, _plugin_gate_class)
        from core.funnel.tools_registry import tools
        from core.plugin_isolation import (
            ISOLATION_LIMITS, funnel_routing_check)
        self.assertEqual(_plugin_gate_class("get_crypto_price"),
                         "egress")
        self.assertEqual(_plugin_gate_class("get_stock_quote"),
                         "egress")
        routing = funnel_routing_check(tools._tools, _PLUGIN_GATE_CLASS)
        self.assertTrue(routing["ok"], routing["problems"])
        # The honesty text pins the limitation: a gated plugin
        # is funnel-gated, not sandboxed.
        self.assertIn("not contained at the OS level",
                      ISOLATION_LIMITS)
        self.assertIn("Full process isolation is future work",
                      ISOLATION_LIMITS)

    def test_ev7b_sandbox_owed_not_built(self):
        # The sandbox does NOT exist: no out-of-process plugin
        # host anywhere, and the model doc says so. This test
        # pins the owed state — it must be updated, not deleted,
        # when the host is built.
        from core import plugin_isolation
        self.assertIn("FUTURE WORK", plugin_isolation.__doc__)
        self.assertNotIn("subprocess",
                         (TREE / "core" / "plugin_loader.py")
                         .read_text(encoding="utf-8"))

    def test_ev8_no_server_side(self):
        for rel in ("core/mcp_client.py", "core/mcp_client_bus.py",
                    "core/mcp_client_config.py",
                    "core/mcp_client_session.py",
                    "core/mcp_client_tools.py"):
            text = (TREE / rel).read_text(encoding="utf-8")
            for frag in ("MCPServer", "FastMCP(", "create_server",
                         "StreamableHTTPSessionManager", "A2AServer",
                         "a2a.server", "AgentExecutor"):
                self.assertNotIn(frag, text, "%s in %s" % (frag, rel))
        text = (TREE / "core" / "skills_admin.py").read_text(
            encoding="utf-8")
        self.assertIn("no MCP server endpoint", text)
        self.assertIn("no A2A/REST/gRPC federation", text)


if __name__ == "__main__":
    unittest.main()
