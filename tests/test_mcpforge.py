"""R18-3 IBM/mcp-context-forge rebuild — local single-user MCP
server registry.

Fake bus only: the registry is driven through an injectable fake
bus, a temp config file, a temp ledger DB and an in-memory policy
store. No real MCP server is ever connected, no real config file
is ever touched (VYUK_MCP_CONFIG_PATH is pointed at the temp
config for the run), and no real secret is ever used — the
'secret' strings below are obviously fake.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import mcp_registry as mr
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import _needs_confirmation
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools
from core.mcp_client_bus import MCPBus

TREE = Path(__file__).resolve().parent.parent

_FETCH_TOOL = {"name": "fetch",
               "composed": "mcp__fetch__fetch",
               "description": "Fetch a URL",
               "server": "fetch",
               "decl": {"name": "fetch",
                        "description": "Fetch a URL",
                        "inputSchema": {"type": "object",
                                        "properties": {}}}}


def _default_states():
    return [{"name": "fetch", "status": "idle", "tool_count": 0,
             "error": None,
             "config": {"command": "mcp-server-fetch", "args": [],
                        "enabled": True}},
            {"name": "time", "status": "idle", "tool_count": 0,
             "error": None,
             "config": {"command": "mcp-server-time", "args": [],
                        "enabled": True}},
            {"name": "git", "status": "disabled", "tool_count": 0,
             "error": None,
             "config": {"command": "mcp-server-git", "args": [],
                        "enabled": False}},
            {"name": "munim-computer-use", "status": "disabled",
             "tool_count": 0, "error": None,
             "config": {"command": "npx", "args": [],
                        "enabled": False}}]


class _FakeBus:
    def __init__(self):
        self._tools = []
        self.reload_calls = 0
        self.ensure_calls = 0

    def servers(self):
        return [dict(s) for s in _default_states()]

    def list_tools(self):
        return list(self._tools)

    def resolve(self, composed):
        for t in self._tools:
            if t.get("composed") == composed:
                return t
        return None

    def tool_changed(self, name, desc):
        return False

    def ensure_all_enabled(self):
        self.ensure_calls += 1
        return None

    def reload_config(self):
        self.reload_calls += 1
        return {"added": [], "removed": [], "enabled": [],
                "disabled": [], "errors": {}}


class McpForgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "mcp_registry.sqlite3")
        self.cfg = os.path.join(self.tmp.name, "mcp_servers.json")
        self.pol = {}
        self.bus = _FakeBus()
        self._old_env = os.environ.get("VYUK_MCP_CONFIG_PATH")
        os.environ["VYUK_MCP_CONFIG_PATH"] = self.cfg

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("VYUK_MCP_CONFIG_PATH", None)
        else:
            os.environ["VYUK_MCP_CONFIG_PATH"] = self._old_env
        self.tmp.cleanup()

    def _call(self, *args, **kwargs):
        kwargs.setdefault("_bus", self.bus)
        kwargs.setdefault("_db_path", self.db)
        kwargs.setdefault("_config_path", self.cfg)
        return mr.mcp_registry(*args, **kwargs)

    # -- server directory ------------------------------------------------
    def test_server_add_list_remove(self):
        out = self._call("server", mode="add", server="demo",
                         command="demo-cmd", args=["--x"])
        self.assertTrue(out.startswith("Added the 'demo' server"),
                        out)
        out = self._call("server", mode="list")
        self.assertIn("demo (stdio, enabled, idle)", out)
        self.assertIn("fetch (stdio, enabled, idle", out)
        self.assertIn("git (stdio, disabled, disabled", out)
        out = self._call("server", mode="add", server="demo",
                         command="demo-cmd")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("already registered", out)
        out = self._call("server", mode="remove", server="demo")
        self.assertTrue(out.startswith("Removed the 'demo'"), out)
        out = self._call("server", mode="list")
        self.assertNotIn("demo (stdio", out)
        out = self._call("server", mode="remove", server="demo")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("no server called 'demo'", out)
        self.assertEqual(self.bus.reload_calls, 2)

    def test_server_add_remote(self):
        out = self._call("server", mode="add", server="remote1",
                         url="https://remote.example/mcp",
                         auth={"type": "client_credentials",
                               "issuer": "https://id.example",
                               "client_id": "cid",
                               "client_secret_env": "REMOTE_SECRET"})
        self.assertTrue(out.startswith("Added the 'remote1'"), out)
        data = json.loads(Path(self.cfg).read_text())
        entry = data["mcpServers"]["remote1"]
        self.assertEqual(entry["url"], "https://remote.example/mcp")
        self.assertEqual(entry["auth"]["client_secret_env"],
                         "REMOTE_SECRET")
        self.assertNotIn("client_secret", entry["auth"])
        out = self._call("server", mode="list")
        self.assertIn("remote1 (remote, enabled, idle)", out)

    def test_server_add_secret_gates(self):
        # A literal secret in the config is refused (Gate C).
        out = self._call("server", mode="add", server="bad1",
                         url="https://x.example",
                         auth={"type": "client_credentials",
                               "issuer": "https://i.example",
                               "client_id": "c",
                               "client_secret": "SHH"})
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("literal client_secret", out)
        self.assertNotIn("SHH", out)
        # Credentials in the URL are refused.
        out = self._call("server", mode="add", server="bad2",
                         url="https://user:pass@x.example")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("username or password", out)
        # Interactive auth is refused (dead-by-config).
        out = self._call("server", mode="add", server="bad3",
                         url="https://x.example",
                         auth={"type": "oauth"})
        self.assertTrue(out.startswith("refused:"), out)
        # command + url together, and neither, are both refused.
        out = self._call("server", mode="add", server="bad4",
                         command="x", url="https://x.example")
        self.assertTrue(out.startswith("refused:"), out)
        out = self._call("server", mode="add", server="bad5")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertFalse(Path(self.cfg).exists())

    def test_server_remove_default_refused(self):
        out = self._call("server", mode="remove", server="fetch")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("built-in default", out)
        self.assertIn("Disable it", out)

    def test_server_disable_enable_default(self):
        out = self._call("server", mode="disable", server="fetch")
        self.assertTrue(out.startswith("Disabled the 'fetch'"), out)
        data = json.loads(Path(self.cfg).read_text())
        entry = data["mcpServers"]["fetch"]
        self.assertEqual(entry["enabled"], False)
        # The file entry is the raw entry, not the normalised one.
        self.assertNotIn("name", entry)
        self.assertNotIn("transport", entry)
        self.assertEqual(entry["command"], "mcp-server-fetch")
        out = self._call("server", mode="list")
        self.assertIn("fetch (stdio, disabled, idle", out)
        out = self._call("server", mode="enable", server="fetch")
        self.assertTrue(out.startswith("Enabled the 'fetch'"), out)
        data = json.loads(Path(self.cfg).read_text())
        self.assertEqual(data["mcpServers"]["fetch"]["enabled"], True)
        out = self._call("server", mode="enable", server="nope")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("no server called 'nope'", out)

    def test_server_enable_dead_by_config_refused(self):
        data = {"mcpServers": {
            "broken": {"url": "https://x.example",
                       "auth": {"type": "oauth"}}}}
        Path(self.cfg).write_text(json.dumps(data))
        out = self._call("server", mode="enable", server="broken")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("dead-by-config", out)
        out = self._call("server", mode="list")
        self.assertIn("broken (dead-by-config:", out)

    def test_server_add_preserves_other_entries(self):
        self._call("server", mode="add", server="one",
                   command="one-cmd")
        self._call("server", mode="add", server="two",
                   url="https://two.example/mcp")
        data = json.loads(Path(self.cfg).read_text())
        self.assertEqual(sorted(data["mcpServers"]), ["one", "two"])
        self._call("server", mode="remove", server="one")
        data = json.loads(Path(self.cfg).read_text())
        self.assertEqual(list(data["mcpServers"]), ["two"])

    # -- discover / review / policy ---------------------------------------
    def test_discover(self):
        out = self._call("discover")
        self.assertTrue(out.startswith("MCP discovery: 4 servers, "
                                       "0 tools."), out)
        self.assertIn("fetch (stdio, enabled, idle", out)
        self.assertIn("No tools are currently listed", out)
        self.assertEqual(self.bus.ensure_calls, 0)
        self.bus._tools = [dict(_FETCH_TOOL)]
        out = self._call("discover", connect=True)
        self.assertEqual(self.bus.ensure_calls, 1)
        self.assertIn("mcp__fetch__fetch [unreviewed]", out)
        out = self._call("discover", connect=True, limit=1)
        self.assertIn("mcp__fetch__fetch [unreviewed]", out)

    def test_review_flow(self):
        out = self._call("review", tool="mcp__fetch__fetch",
                         klass="read", _policy_store=self.pol)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("have not seen", out)
        self.bus._tools = [dict(_FETCH_TOOL)]
        out = self._call("review", tool="mcp__fetch__fetch",
                         klass="bogus", _policy_store=self.pol)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("must be 'read', 'egress' or 'confirm'", out)
        out = self._call("review", tool="mcp__fetch__fetch",
                         klass="read", reviewer="op",
                         _policy_store=self.pol)
        self.assertTrue(out.startswith("Reviewed 'mcp__fetch__fetch' "
                                       "as read"), out)
        out = self._call("policy", _policy_store=self.pol)
        self.assertIn("mcp__fetch__fetch: read, by op", out)
        self.assertIn("(valid)", out)
        # A changed declaration makes the review stale until
        # re-reviewed.
        changed = dict(_FETCH_TOOL)
        changed["description"] = "Fetch a URL (v2)"
        changed["decl"] = dict(_FETCH_TOOL["decl"],
                               description="Fetch a URL (v2)")
        self.bus._tools = [changed]
        out = self._call("policy", _policy_store=self.pol)
        self.assertIn("stale (declaration changed", out)
        out = self._call("review", tool="mcp__fetch__fetch",
                         klass="egress", _policy_store=self.pol)
        self.assertTrue(out.startswith("Reviewed"), out)
        out = self._call("policy", _policy_store=self.pol)
        self.assertIn("mcp__fetch__fetch: egress", out)
        self.assertIn("(valid)", out)
        # Discover now shows the reviewed class.
        out = self._call("discover", _policy_store=self.pol)
        self.assertIn("mcp__fetch__fetch [egress]", out)

    def test_policy_empty(self):
        out = self._call("policy", _policy_store=self.pol)
        self.assertIn("No operator reviews recorded yet", out)

    # -- status / events / ledger -----------------------------------------
    def test_status_events_and_chain(self):
        self._call("server", mode="add", server="demo",
                   command="demo-cmd")
        self.bus._tools = [dict(_FETCH_TOOL)]
        self._call("review", tool="mcp__fetch__fetch", klass="read",
                   _policy_store=self.pol)
        out = self._call("status", _policy_store=self.pol)
        self.assertIn("servers: 5 (3 enabled, 2 disabled, "
                      "0 dead-by-config)", out)
        self.assertIn("tools listed: 1 (1 reviewed, 0 unreviewed, "
                      "0 description-changed)", out)
        self.assertIn("reviews recorded: 1", out)
        self.assertIn("ledger events: 2", out)
        out = self._call("events", limit=20)
        self.assertIn("server_add", out)
        self.assertIn("review", out)
        self.assertIn("chain intact", out)
        db = mr._RegistryDB(self.db)
        try:
            ok, detail = db.verify_chain()
            self.assertTrue(ok, detail)
            conn = sqlite3.connect(self.db)
            conn.execute("UPDATE mcp_registry_events SET detail='forged' "
                         "WHERE id=1")
            conn.commit()
            conn.close()
            ok, detail = db.verify_chain()
            self.assertFalse(ok)
        finally:
            db.close()

    def test_unknown_action(self):
        out = self._call("bogus")
        self.assertTrue(out.startswith("refused:"), out)
        out = self._call("server", mode="bogus")
        self.assertTrue(out.startswith("refused:"), out)

    # -- the bus reload -----------------------------------------------------
    def test_bus_reload_config(self):
        # The real bus, pointed at the temp config by the env var.
        data = {"mcpServers": {
            "demo1": {"command": "demo1-cmd", "args": [],
                      "enabled": True}}}
        Path(self.cfg).write_text(json.dumps(data))
        bus = MCPBus()
        names = {s["name"]: s for s in bus.servers()}
        self.assertIn("demo1", names)
        self.assertEqual(names["demo1"]["status"], "idle")
        # Disable demo1 and add demo2, then reload.
        data = {"mcpServers": {
            "demo1": {"command": "demo1-cmd", "args": [],
                      "enabled": False},
            "demo2": {"command": "demo2-cmd", "args": [],
                      "enabled": True}}}
        Path(self.cfg).write_text(json.dumps(data))
        summary = bus.reload_config()
        self.assertEqual(summary["added"], ["demo2"])
        self.assertEqual(summary["disabled"], ["demo1"])
        self.assertEqual(summary["errors"], {})
        names = {s["name"]: s for s in bus.servers()}
        self.assertEqual(names["demo1"]["status"], "disabled")
        self.assertEqual(names["demo2"]["status"], "idle")
        # Remove demo1, then reload.
        data = {"mcpServers": {
            "demo2": {"command": "demo2-cmd", "args": [],
                      "enabled": True}}}
        Path(self.cfg).write_text(json.dumps(data))
        summary = bus.reload_config()
        self.assertEqual(summary["removed"], ["demo1"])
        names = {s["name"]: s for s in bus.servers()}
        self.assertNotIn("demo1", names)
        self.assertIn("demo2", names)
        # A broken file is reported and the previous state is kept.
        Path(self.cfg).write_text("{not json")
        summary = bus.reload_config()
        self.assertIn("__file__", summary["errors"])
        self.assertEqual(summary["added"], [])
        self.assertEqual(summary["removed"], [])
        names = {s["name"]: s for s in bus.servers()}
        self.assertIn("demo2", names)

    # -- registry / classification / gates --------------------------------
    def test_registry_and_classification(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 772)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736 — mcp_registry added (effectful, confirm-gated); R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        mine = [d for d in decls if d["name"] == "mcp_registry"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(mine[0]["parameters"]["properties"]), 16)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties
        self.assertEqual(sum(len(d["parameters"]["properties"])
                           for d in decls), 2126)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        snap = json.loads((TREE / "tests" / "_classification_snapshot.json")
                          .read_text())
        self.assertEqual(snap["mcp_registry"],
                         {"confirm": True, "free": None, "read": False})
        self.assertIn("mcp_registry", _CONFIRM_GATE_TOOLS)
        self.assertIn("mcp_registry", GROUP_MEMBERS["Intelligence"])
        self.assertIn('"mcp_registry",',
                      (TREE / "core" / "catalogue_groups_data.py").read_text())
        text = (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text()
        totals = json.loads(re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 772)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["read"], 386)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["effectful"], 386)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["confirm_gated"], 170)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 157 -> 158; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["already_verified_in_handler"], 320)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        counts = json.loads(re.search(
            r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(counts["diagnostics_dev"], 69)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 66 -> 67; R18-4 a2a pack (2026-11-12): 67 -> 68 — agent_call joins diagnostics_dev; R18-5 dspy pack (2026-11-12): diagnostics_dev 68 -> 69
        self.assertEqual(sum(counts.values()), 772)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["read"] + totals["effectful"],
                         totals["total"])
        self.assertEqual(totals["seam_wired"]
                         + totals["already_verified_in_handler"]
                         + totals["honestly_unverifiable"],
                         totals["effectful"])

    def test_confirm_and_read_split(self):
        writes = [("server", "add"), ("server", "remove"),
                  ("server", "enable"), ("server", "disable"),
                  ("review", "")]
        reads = [("server", "list"), ("discover", ""), ("policy", ""),
                 ("status", ""), ("events", "")]
        for action, mode in writes:
            args = {"action": action}
            if mode:
                args["mode"] = mode
            self.assertTrue(_needs_confirmation("mcp_registry", args),
                            (action, mode))
            self.assertFalse(_is_read_action("mcp_registry", args),
                             (action, mode))
        for action, mode in reads:
            args = {"action": action}
            if mode:
                args["mode"] = mode
            self.assertFalse(_needs_confirmation("mcp_registry", args),
                             (action, mode))
            self.assertTrue(_is_read_action("mcp_registry", args),
                            (action, mode))
        self.assertTrue(_needs_confirmation("mcp_registry",
                                           {"action": "bogus"}))

    def test_licence_marker_proposed(self):
        text = (TREE / "core" / "mcp_registry.py").read_text()
        # Either marker state passes: PROPOSED until the parent rules
        # the licence at verification, Parent-ruled after. (The R17-9
        # wardrobe suite asserted PROPOSED only, and the parent's
        # ruling flip red-ded it in R18-1 neighbours — this test does
        # not repeat that trap.)
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled (licence" in text)
        self.assertIn("mcp-context-forge", text)
        self.assertIn("Apache", text)
        # No top-level fastmcp / MCP-SDK dependency: the module
        # imports cleanly on an interpreter without them (this
        # suite runs on system python3, where they are absent).
        # The only fastmcp/mcp mention is the indented import
        # probe inside _do_status, mirroring the bus's own
        # _unavailable probe — never a module-level import.
        self.assertIsNone(re.search(r"^import fastmcp|^from fastmcp",
                                    text, re.M))
        self.assertIsNone(re.search(r"^import mcp$|^from mcp import",
                                    text, re.M))


if __name__ == "__main__":
    unittest.main()
