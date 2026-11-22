"""R18-4 a2aproject/A2A rebuild — VYUK as an A2A client.

Fake agent only: discovery and calls are driven through an
injectable fake opener returning a recorded agent card and recorded
JSON-RPC answers, against a temp ledger DB. No real agent is ever
contacted, no real state is ever touched, and no real credential
is ever used — the 'secret' strings below are obviously fake.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import agent_call as ac
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import _needs_confirmation
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools

TREE = Path(__file__).resolve().parent.parent

CARD = {"name": "Recipe Agent",
        "description": "Helps with recipes and cooking.",
        "version": "1.0.0",
        "supportedInterfaces": [
            {"url": "https://recipes.example.com/a2a",
             "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
            {"url": "grpc.example.com:443",
             "protocolBinding": "GRPC", "protocolVersion": "1.0"}],
        "provider": {"url": "https://example.com",
                     "organization": "Example"},
        "capabilities": {"streaming": False,
                         "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{"id": "recipes", "name": "Recipes",
                    "description": "Find recipes",
                    "tags": ["cooking"],
                    "examples": ["Find a pasta recipe"]}]}

TASK = {"id": "task-1", "contextId": "ctx-1",
        "status": {"state": "TASK_STATE_COMPLETED",
                   "message": {"role": "ROLE_AGENT",
                               "messageId": "m-9",
                               "parts": [{"text": "Done!"}]}},
        "artifacts": [{"artifactId": "a-1", "name": "recipe",
                       "parts": [{"text": "Pasta: boil water."}]}]}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self, n=-1):
        return self._payload if n < 0 else self._payload[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeAgent:
    """Recorded A2A agent: GET returns the card, POST answers the
    JSON-RPC method in the request body, echoing the request id."""

    def __init__(self, card=None, error=None, task=None):
        self.card = card if card is not None else CARD
        self.error = error
        self.task = task if task is not None else TASK
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if req.method == "GET":
            if isinstance(self.card, bytes):
                return _Resp(self.card)
            return _Resp(json.dumps(self.card).encode("utf-8"))
        body = json.loads(req.data.decode("utf-8"))
        method = body.get("method")
        rid = body.get("id")
        if self.error is not None:
            return _Resp(json.dumps(
                {"jsonrpc": "2.0", "id": rid,
                 "error": self.error}).encode("utf-8"))
        if method == "SendMessage":
            result = {"task": self.task}
        elif method == "GetTask":
            result = self.task
        elif method == "ListTasks":
            result = {"tasks": [self.task], "pageSize": 1, "totalSize": 1}
        elif method == "CancelTask":
            cancelled = dict(self.task)
            cancelled["status"] = {"state": "TASK_STATE_CANCELED"}
            result = cancelled
        else:
            return _Resp(json.dumps(
                {"jsonrpc": "2.0", "id": rid,
                 "error": {"code": -32601,
                           "message": "Method not found"}}).encode("utf-8"))
        return _Resp(json.dumps(
            {"jsonrpc": "2.0", "id": rid, "result": result}).encode("utf-8"))


class AgentCallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "agent_call.sqlite3")
        self.fake = _FakeAgent()

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, *args, **kwargs):
        kwargs.setdefault("_opener", self.fake)
        kwargs.setdefault("_db_path", self.db)
        return ac.agent_call(*args, **kwargs)

    def _discover(self):
        out = self._call("discover",
                         url="https://recipes.example.com")
        self.assertTrue(out.startswith("Agent discovered: Recipe Agent"),
                        out)
        return out

    # -- discovery ------------------------------------------------------
    def test_discover_records_card_and_picks_jsonrpc(self):
        out = self._discover()
        self.assertIn("Interface: https://recipes.example.com/a2a "
                      "(JSON-RPC)", out)
        self.assertIn("Skills (1): Recipes", out)
        self.assertEqual(
            self.fake.requests[0].full_url,
            "https://recipes.example.com/.well-known/agent-card.json")
        db = ac._AgentCallDB(self.db)
        try:
            agent = db.get_agent("Recipe Agent")
        finally:
            db.close()
        self.assertEqual(agent["interface_url"],
                         "https://recipes.example.com/a2a")
        self.assertEqual(agent["protocol_version"], "1.0")
        self.assertEqual(len(agent["skills"]), 1)

    def test_discover_full_card_url(self):
        out = self._call(
            "discover",
            url="https://recipes.example.com/.well-known/agent-card.json")
        self.assertTrue(out.startswith("Agent discovered: Recipe Agent"),
                        out)

    def test_discover_refuses_non_card(self):
        fake = _FakeAgent(card={"hello": "world"})
        out = ac.agent_call("discover", url="https://x.example.com",
                            _opener=fake, _db_path=self.db)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("no usable 'name'", out)

    def test_discover_refuses_unreadable_body(self):
        fake = _FakeAgent(card=b"not json at all")
        out = ac.agent_call("discover", url="https://x.example.com",
                            _opener=fake, _db_path=self.db)
        self.assertTrue(out.startswith("refused:") or
                        "unreadable" in out, out)

    def test_discover_refuses_grpc_only(self):
        card = dict(CARD)
        card["supportedInterfaces"] = [
            {"url": "grpc.example.com:443",
             "protocolBinding": "GRPC", "protocolVersion": "1.0"}]
        fake = _FakeAgent(card=card)
        out = ac.agent_call("discover", url="https://grpc.example.com",
                            _opener=fake, _db_path=self.db)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("no JSON-RPC interface", out)

    def test_discover_refuses_authenticated_agent(self):
        card = dict(CARD)
        card["securityRequirements"] = [{"google": []}]
        card["securitySchemes"] = {
            "google": {"openIdConnectSecurityScheme": {
                "openIdConnectUrl": "https://accounts.google.com/"
                ".well-known/openid-configuration"}}}
        fake = _FakeAgent(card=card)
        out = ac.agent_call("discover", url="https://auth.example.com",
                            _opener=fake, _db_path=self.db)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("requires authentication", out)

    def test_discover_refuses_plain_http_remote(self):
        out = self._call("discover", url="http://recipes.example.com")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("plain HTTP", out)

    def test_discover_refuses_url_credentials(self):
        out = self._call(
            "discover",
            url="https://alice:s3cret@recipes.example.com")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertNotIn("s3cret", out)

    def test_discover_allows_loopback_http(self):
        out = self._call("discover", url="http://127.0.0.1:8080")
        self.assertTrue(out.startswith("Agent discovered: Recipe Agent"),
                        out)

    # -- list / remove --------------------------------------------------
    def test_list_empty_then_known(self):
        self.assertIn("No agents discovered yet",
                      self._call("list"))
        self._discover()
        out = self._call("list")
        self.assertIn("Known agents (1):", out)
        self.assertIn("Recipe Agent (v1.0.0, A2A 1.0)", out)

    def test_remove_forgets(self):
        self._discover()
        out = self._call("remove", agent="Recipe Agent")
        self.assertEqual(out, "Agent 'Recipe Agent' forgotten.")
        self.assertIn("No agents discovered yet", self._call("list"))
        out = self._call("remove", agent="Recipe Agent")
        self.assertTrue(out.startswith("refused:"), out)

    # -- call -----------------------------------------------------------
    def test_call_wire_shape_and_answer(self):
        self._discover()
        out = self._call("call", agent="Recipe Agent",
                         text="Find a pasta recipe")
        self.assertIn("task task-1: state TASK_STATE_COMPLETED", out)
        self.assertIn("artifact 'recipe':\nPasta: boil water.", out)
        self.assertIn("agent says:\nDone!", out)
        sent = json.loads(self.fake.requests[-1].data.decode("utf-8"))
        self.assertEqual(sent["jsonrpc"], "2.0")
        self.assertEqual(sent["method"], "SendMessage")
        self.assertEqual(sent["params"]["message"]["role"], "ROLE_USER")
        self.assertEqual(sent["params"]["message"]["parts"],
                         [{"text": "Find a pasta recipe"}])
        self.assertEqual(
            sent["params"]["configuration"]["acceptedOutputModes"],
            ["text/plain"])
        self.assertEqual(
            self.fake.requests[-1].full_url,
            "https://recipes.example.com/a2a")
        self.assertEqual(
            self.fake.requests[-1].headers.get("Content-type"),
            "application/json")

    def test_call_with_task_and_context(self):
        self._discover()
        out = self._call("call", agent="Recipe Agent",
                         text="More detail", task_id="task-1",
                         context_id="ctx-1", return_immediately=True)
        self.assertIn("TASK_STATE_COMPLETED", out)
        sent = json.loads(self.fake.requests[-1].data.decode("utf-8"))
        self.assertEqual(sent["params"]["message"]["taskId"], "task-1")
        self.assertEqual(sent["params"]["message"]["contextId"], "ctx-1")
        self.assertTrue(
            sent["params"]["configuration"]["returnImmediately"])

    def test_call_message_only_result(self):
        self._discover()

        def _message_only(req, timeout=None):
            if req.method == "GET":
                return self.fake(req, timeout)
            body = json.loads(req.data.decode("utf-8"))
            return _Resp(json.dumps(
                {"jsonrpc": "2.0", "id": body["id"],
                 "result": {"message": {"role": "ROLE_AGENT",
                                        "messageId": "m-2",
                                        "taskId": "task-9",
                                        "parts": [{"text": "Hello back."}]}}}
                ).encode("utf-8"))

        out = ac.agent_call("call", agent="Recipe Agent",
                            text="Hello", _opener=_message_only,
                            _db_path=self.db)
        self.assertIn("the agent answered with a message (no task):",
                      out)
        self.assertIn("Hello back.", out)

    def test_call_unknown_agent_refuses(self):
        out = self._call("call", agent="Nobody", text="hello")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("Discover it first", out)

    def test_call_empty_text_refuses(self):
        self._discover()
        out = self._call("call", agent="Recipe Agent", text="   ")
        self.assertTrue(out.startswith("refused:"), out)

    def test_call_error_names_a2a_code(self):
        self._discover()
        fake = _FakeAgent(
            error={"code": -32001, "message": "Task task-9 not found"})
        # Re-discover into the same DB through the error fake's card
        # path is not needed: the agent is already known.
        out = ac.agent_call("call", agent="Recipe Agent",
                            text="hello", _opener=fake, _db_path=self.db)
        self.assertIn("TaskNotFoundError", out)
        self.assertIn("code -32001", out)

    def test_call_id_mismatch_refused(self):
        self._discover()

        def _wrong_id(req, timeout=None):
            if req.method == "POST":
                return _Resp(json.dumps(
                    {"jsonrpc": "2.0", "id": "someone-elses-id",
                     "result": {"task": TASK}}).encode("utf-8"))
            return self.fake(req, timeout)

        out = ac.agent_call("call", agent="Recipe Agent",
                            text="hello", _opener=_wrong_id,
                            _db_path=self.db)
        self.assertIn("different request id", out)
        self.assertNotIn("TASK_STATE_COMPLETED", out)

    def test_call_non_jsonrpc_answer_refused(self):
        self._discover()

        def _no_version(req, timeout=None):
            if req.method == "POST":
                body = json.loads(req.data.decode("utf-8"))
                return _Resp(json.dumps(
                    {"id": body["id"],
                     "result": {"task": TASK}}).encode("utf-8"))
            return self.fake(req, timeout)

        out = ac.agent_call("call", agent="Recipe Agent",
                            text="hello", _opener=_no_version,
                            _db_path=self.db)
        self.assertIn("without \"jsonrpc\": \"2.0\"", out)

    def test_call_unknown_state_reported_not_trusted(self):
        self._discover()
        weird = dict(TASK)
        weird["status"] = {"state": "TASK_STATE_SPINNING"}
        fake = _FakeAgent(task=weird)
        out = ac.agent_call("call", agent="Recipe Agent",
                            text="hello", _opener=fake, _db_path=self.db)
        self.assertIn("TASK_STATE_SPINNING", out)
        self.assertIn("not an A2A task state", out)

    # -- task -----------------------------------------------------------
    def test_task_get_list_cancel(self):
        self._discover()
        out = self._call("task", agent="Recipe Agent",
                         mode="get", task_id="task-1")
        self.assertIn("task task-1: state TASK_STATE_COMPLETED", out)
        out = self._call("task", agent="Recipe Agent", mode="list")
        self.assertIn("Tasks on Recipe Agent (1):", out)
        self.assertIn("task-1: TASK_STATE_COMPLETED", out)
        out = self._call("task", agent="Recipe Agent",
                         mode="cancel", task_id="task-1")
        self.assertIn("state TASK_STATE_CANCELED", out)
        sent = json.loads(self.fake.requests[-1].data.decode("utf-8"))
        self.assertEqual(sent["method"], "CancelTask")
        self.assertEqual(sent["params"], {"id": "task-1"})

    def test_task_list_bad_status_refuses(self):
        self._discover()
        out = self._call("task", agent="Recipe Agent",
                         mode="list", status="NOPE")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("not an A2A task state", out)

    def test_task_get_needs_id(self):
        self._discover()
        out = self._call("task", agent="Recipe Agent", mode="get")
        self.assertTrue(out.startswith("refused:"), out)

    # -- own card / status / events --------------------------------------
    def test_own_card_is_a_description_not_an_endpoint(self):
        out = self._call("card")
        card = json.loads(out.split("\n", 1)[1])
        self.assertEqual(card["name"], "VYUK")
        self.assertEqual(card["supportedInterfaces"], [])
        self.assertEqual(card["version"], "6.3.0")
        self.assertIn("serves no A2A endpoint", card["description"])
        self.assertFalse(card["capabilities"]["streaming"])

    def test_status_events_and_chain(self):
        self._discover()
        self._call("call", agent="Recipe Agent", text="hi")
        out = self._call("status")
        self.assertIn("Known agents: 1.", out)
        self.assertIn("chain intact", out)
        self.assertIn("  - call: 1", out)
        self.assertIn("  - discover: 1", out)
        out = self._call("events", limit=20)
        self.assertIn("2 events, chain intact", out)
        self.assertIn("discover agent Recipe Agent", out)
        self.assertIn("call task Recipe Agent", out)

    def test_chain_tamper_detected(self):
        self._discover()
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE agent_call_events SET detail='forged' "
                     "WHERE id=1")
        conn.commit()
        conn.close()
        db = ac._AgentCallDB(self.db)
        try:
            ok, detail = db.verify_chain()
        finally:
            db.close()
        self.assertFalse(ok)
        self.assertIn("hash mismatch at event 1", detail)

    def test_unknown_action_refuses(self):
        out = self._call("bogus")
        self.assertTrue(out.startswith("refused:"), out)

    # -- wiring -----------------------------------------------------------
    def test_registry_census(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 769)  # INTENTIONAL — R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        mine = [d for d in decls if d["name"] == "agent_call"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(mine[0]["parameters"]["properties"]), 13)
        self.assertEqual(
            sum(len(d.get("parameters", {}).get("properties", {}))
                for d in decls), 2121)  # INTENTIONAL — R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_catalogue_mind_group(self):
        self.assertIn("agent_call", GROUP_MEMBERS["Mind"])
        gui_text = (TREE / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        self.assertIn('"agent_call",', gui_text)

    def test_gates(self):
        self.assertIn("agent_call", _CONFIRM_GATE_TOOLS)
        self.assertTrue(_needs_confirmation(
            "agent_call", {"action": "call"}))
        self.assertTrue(_needs_confirmation(
            "agent_call", {"action": "task", "mode": "cancel"}))
        self.assertTrue(_needs_confirmation(
            "agent_call", {"action": "remove"}))
        self.assertFalse(_needs_confirmation(
            "agent_call", {"action": "discover"}))
        self.assertFalse(_needs_confirmation(
            "agent_call", {"action": "list"}))
        self.assertFalse(_needs_confirmation(
            "agent_call", {"action": "task", "mode": "get"}))
        self.assertFalse(_needs_confirmation(
            "agent_call", {"action": "task", "mode": "list"}))
        self.assertFalse(_needs_confirmation(
            "agent_call", {"action": "card"}))
        self.assertFalse(_needs_confirmation(
            "agent_call", {"action": "status"}))
        self.assertFalse(_needs_confirmation(
            "agent_call", {"action": "events"}))
        self.assertTrue(_is_read_action(
            "agent_call", {"action": "discover"}))
        self.assertTrue(_is_read_action(
            "agent_call", {"action": "task", "mode": "get"}))
        self.assertTrue(_is_read_action(
            "agent_call", {"action": "task", "mode": "list"}))
        self.assertTrue(_is_read_action(
            "agent_call", {"action": "card"}))
        self.assertFalse(_is_read_action(
            "agent_call", {"action": "call"}))
        self.assertFalse(_is_read_action(
            "agent_call", {"action": "task", "mode": "cancel"}))
        self.assertFalse(_is_read_action(
            "agent_call", {"action": "remove"}))

    def test_licence_marker_is_proposed(self):
        src = (TREE / "core" / "agent_call.py").read_text(
            encoding="utf-8")
        # Either marker state passes: PROPOSED until the parent rules
        # the licence at verification, Parent-ruled after. (The R17-9
        # wardrobe suite asserted PROPOSED only, and the parent's
        # ruling flip red-ded it in R18-1 neighbours; the R18-4
        # parent ruling flipped this module's marker and red-ded
        # this test in R18-5 neighbours — this test does not repeat
        # that trap.)
        self.assertTrue("PROPOSED — parent rules" in src
                        or "Parent-ruled (licence" in src)
        self.assertIn("a2aproject/A2A", src)


if __name__ == "__main__":
    unittest.main()
