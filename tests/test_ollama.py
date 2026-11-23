"""R11-1 ollama port — suite.

Own stdlib model management over the local Ollama server's REST
API (status / pull / delete). The lab has no Ollama server, so
every behavioural test drives a tiny fake Ollama on loopback built
here (the same four endpoints the port uses: /api/version,
/api/tags, /api/pull with stream:false, /api/delete); nothing here
claims a real Ollama install, a real model download, or Windows
behaviour. The offline tests point the module at a closed port.
"""

import json
import pathlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import ollama_models as om
from core.config import config
from core.funnel.tools_gate_egress import _LOCAL_ONLY_READS, _is_egress_read
from core.funnel.tools_gate_taint import _UNTRUSTED_PRODUCERS
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent

_START_MODELS = [
    {"name": "qwen2.5:7b", "size": 4690000000, "modified_at": "2026-11-01T10:00:00Z"},
    {"name": "nomic-embed-text:latest", "size": 274000000, "modified_at": "2026-10-15T10:00:00Z"},
]


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    models = [dict(m) for m in _START_MODELS]

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/api/version":
            self._send(200, {"version": "0.5.1"})
        elif self.path == "/api/tags":
            self._send(200, {"models": list(type(self).models)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/pull":
            name = str(self._body().get("model", ""))
            if not name:
                self._send(400, {"error": "no model name"})
                return
            if name == "missing-model:404":
                self._send(404, {"error": "model not found"})
                return
            if not any(m["name"] == name for m in type(self).models):
                type(self).models.append(
                    {"name": name, "size": 1000000000,
                     "modified_at": "2026-11-10T10:00:00Z"})
            self._send(200, {"status": "success"})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        if self.path == "/api/delete":
            name = str(self._body().get("model", ""))
            before = len(type(self).models)
            type(self).models = [m for m in type(self).models
                                 if m["name"] != name]
            if len(type(self).models) == before:
                self._send(404, {"error": "model not found"})
            else:
                self._send(200, {})
        else:
            self._send(404, {"error": "not found"})


class _FakeOllamaCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls._saved_host = config.ollama_host
        cls._saved_model = config.ollama_model

    @classmethod
    def tearDownClass(cls):
        config.ollama_host = cls._saved_host
        config.ollama_model = cls._saved_model
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _FakeOllamaHandler.models = [dict(m) for m in _START_MODELS]
        config.ollama_host = f"http://127.0.0.1:{type(self).port}"
        config.ollama_model = "qwen2.5:7b"


class TestHumanSize(unittest.TestCase):
    def test_bytes_and_kilobytes(self):
        self.assertEqual(om._human_size(0), "0 B")
        self.assertEqual(om._human_size(512), "512 B")
        self.assertEqual(om._human_size(2048), "2.0 KB")

    def test_megabytes_and_gigabytes(self):
        self.assertEqual(om._human_size(274000000), "261.3 MB")
        self.assertEqual(om._human_size(4690000000), "4.4 GB")

    def test_unknown_inputs(self):
        self.assertEqual(om._human_size(None), "unknown size")
        self.assertEqual(om._human_size("abc"), "unknown size")
        self.assertEqual(om._human_size(-5), "unknown size")


class TestNameValidation(unittest.TestCase):
    def test_good_names(self):
        for name in ("qwen2.5:7b", "llama3.2:3b", "namespace/model:tag",
                     "nomic-embed-text", "model"):
            self.assertIsNone(om._validate_model_name(name), name)

    def test_bad_names_refused(self):
        for name in ("", "   ", "not a model!!", "a b", "model\\x",
                     "../escape", "a" * 201):
            self.assertIsNotNone(om._validate_model_name(name), name)

    def test_non_string_refused(self):
        self.assertIsNotNone(om._validate_model_name(None))
        self.assertIsNotNone(om._validate_model_name(7))


class TestStatus(_FakeOllamaCase):
    def test_lists_models_and_configured_installed(self):
        answer = om.ollama_status()
        self.assertIn("0.5.1", answer)
        self.assertIn("qwen2.5:7b", answer)
        self.assertIn("nomic-embed-text:latest", answer)
        self.assertIn("Configured brain model: qwen2.5:7b — installed.", answer)
        self.assertIn("4.4 GB", answer)  # qwen2.5:7b size, not a guess

    def test_configured_missing_is_honest(self):
        config.ollama_model = "not-pulled-yet:1b"
        answer = om.ollama_status()
        self.assertIn("NOT installed", answer)
        self.assertIn("ollama_pull", answer)

    def test_empty_list_is_honest(self):
        _FakeOllamaHandler.models = []
        answer = om.ollama_status()
        self.assertIn("No models installed yet", answer)
        self.assertIn("0 models installed", answer)

    def test_offline_refuses_without_a_fabricated_list(self):
        config.ollama_host = "http://127.0.0.1:1"
        answer = om.ollama_status()
        self.assertIn("Local brain offline", answer)
        self.assertNotIn("qwen2.5:7b", answer)


class TestPull(_FakeOllamaCase):
    def test_pull_claims_via_relist(self):
        answer = om.ollama_pull("llama3.2:3b")
        self.assertIn("Pulled 'llama3.2:3b'", answer)
        self.assertIn("now in Ollama's model list", answer)
        self.assertIn("Settings -> Brain", answer)
        self.assertIn("llama3.2:3b",
                      [m["name"] for m in _FakeOllamaHandler.models])

    def test_pull_already_installed_does_not_repull(self):
        answer = om.ollama_pull("qwen2.5:7b")
        self.assertIn("already installed", answer)

    def test_pull_bad_name_refused_before_network(self):
        answer = om.ollama_pull("not a model!!")
        self.assertIn("not a model name", answer)

    def test_pull_unknown_model_reports_the_server_error(self):
        answer = om.ollama_pull("missing-model:404")
        self.assertIn("failed", answer)
        self.assertIn("404", answer)
        self.assertIn("model not found", answer)

    def test_pull_offline_is_honest(self):
        config.ollama_host = "http://127.0.0.1:1"
        answer = om.ollama_pull("llama3.2:3b")
        self.assertIn("Local brain offline", answer)


class TestDelete(_FakeOllamaCase):
    def test_delete_claims_via_relist(self):
        answer = om.ollama_delete("nomic-embed-text:latest")
        self.assertIn("Deleted 'nomic-embed-text:latest'", answer)
        self.assertIn("no longer in Ollama's model list", answer)
        self.assertNotIn("nomic-embed-text:latest",
                         [m["name"] for m in _FakeOllamaHandler.models])

    def test_delete_refuses_the_configured_model(self):
        answer = om.ollama_delete("qwen2.5:7b")
        self.assertIn("will not delete it", answer)
        self.assertIn("Settings -> Brain", answer)
        self.assertIn("qwen2.5:7b",
                      [m["name"] for m in _FakeOllamaHandler.models])

    def test_delete_missing_is_honest(self):
        answer = om.ollama_delete("never-installed:1b")
        self.assertIn("not installed", answer)

    def test_delete_bad_name_refused(self):
        self.assertIn("not a model name", om.ollama_delete("a b"))

    def test_delete_offline_is_honest(self):
        config.ollama_host = "http://127.0.0.1:1"
        answer = om.ollama_delete("nomic-embed-text:latest")
        self.assertIn("Local brain offline", answer)


class TestGates(unittest.TestCase):
    def test_tools_registered_with_schemas(self):
        decls = {d.get("name"): d for d in _registry.get_declarations()}
        for name in ("ollama_status", "ollama_pull", "ollama_delete"):
            self.assertIn(name, decls, name)
        self.assertEqual(
            len(decls["ollama_status"].get("parameters", {}).get("properties", {})), 0)
        self.assertEqual(
            len(decls["ollama_pull"].get("parameters", {}).get("properties", {})), 1)
        self.assertEqual(
            len(decls["ollama_delete"].get("parameters", {}).get("properties", {})), 1)
        self.assertIn("model", decls["ollama_pull"]["parameters"]["required"])
        self.assertIn("model", decls["ollama_delete"]["parameters"]["required"])

    def test_status_read_only_pull_delete_confirm_gated(self):
        from core.funnel.tools_gate_readonly import _READ_ONLY_TOOLS

        self.assertIn("ollama_status", _READ_ONLY_TOOLS)
        self.assertNotIn("ollama_pull", _READ_ONLY_TOOLS)
        self.assertNotIn("ollama_delete", _READ_ONLY_TOOLS)
        text = (ROOT / "core" / "funnel" / "tool_classification.py").read_text(
            encoding="utf-8")
        self.assertIn('"ollama_pull"', text)
        self.assertIn('"ollama_delete"', text)

    def test_pull_takes_the_long_deadline(self):
        from core.funnel.tools_handlers_limits import _TOOL_DEADLINE_CLASS

        self.assertEqual(_TOOL_DEADLINE_CLASS.get("ollama_pull"), "long")
        self.assertEqual(om._PULL_TIMEOUT_S, 900)

    def test_status_is_an_egress_read_with_no_taint_entry(self):
        # Loopback HTTP to the operator's own Ollama server: an
        # egress read by the file's own test, and no taint entry —
        # the answers are model-inventory metadata from the local
        # server, never web content.
        self.assertNotIn("ollama_status", _LOCAL_ONLY_READS)
        self.assertTrue(_is_egress_read("ollama_status"))
        self.assertNotIn("ollama_status", _UNTRUSTED_PRODUCERS)
        self.assertNotIn("ollama_pull", _UNTRUSTED_PRODUCERS)
        self.assertNotIn("ollama_delete", _UNTRUSTED_PRODUCERS)

    def test_tools_in_the_system_apps_group(self):
        text = (ROOT / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        for name in ("ollama_status", "ollama_pull", "ollama_delete"):
            self.assertIn(f'"{name}"', text, name)
        self.assertIn("R11-1 ollama port", text)

    def test_nothing_adopted(self):
        # No ollama requirement line (the pre-existing comment
        # names the operator flow only); the module imports stdlib
        # + .config only — no ollama client, no httpx, no pydantic.
        # The ollama check is a real import-statement regex, not a
        # substring: the module's docstring legitimately says
        # "from ollama.com" (the operator download site) and names
        # the `ollama` PyPI client it refused.
        import re

        for line in (ROOT / "requirements.txt").read_text(
                encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("ollama"):
                self.assertNotIn("==", stripped)
                self.assertNotIn(">=", stripped)
        text = (ROOT / "core" / "ollama_models.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(
            r"^\s*(import ollama\b|from ollama\s+import|"
            r"from ollama\.\w+\s+import)", text, re.M))
        for bad in ("import httpx", "import pydantic", "from pydantic"):
            self.assertNotIn(bad, text, bad)


class TestRegistryPins(unittest.TestCase):
    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 772, len(decls))  # INTENTIONAL — R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status (0 props) + ollama_pull (1) + ollama_delete (1); R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2126, total_params)  # INTENTIONAL — R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each; ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_audit_totals(self):
        text = (ROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 772', '"read": 386',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R16-9 attic pack (2026-11-11): 723/380/343/277 -> 724/380/344/278 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"effectful": 386', '"confirm_gated": 170',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R16-9 attic pack (2026-11-11): 723/380/343/277 -> 724/380/344/278 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-6 fonoster pack (2026-11-12): 157 -> 158; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"already_verified_in_handler": 320',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R16-9 attic pack (2026-11-11): 723/380/343/277 -> 724/380/344/278 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"system_apps": 90'):
            # INTENTIONAL — R11-1 ollama port (2026-11-10):
            # total 714 -> 717, read 378 -> 379 (ollama_status),
            # effectful 336 -> 338, confirm 149 -> 151,
            # already-verified 270 -> 272 (ollama_pull +
            # ollama_delete, both A5-sysreadback), system_apps
            # 87 -> 90 (effectful 44 -> 46).
            self.assertIn(needle, text, needle)

    def test_version_stays_630(self):
        text = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
        self.assertIn('VERSION = "6.3.0"', text)


if __name__ == "__main__":
    unittest.main()
