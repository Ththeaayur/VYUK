"""R17-4 MCSManager rebuild — game servers. Fake servers only: no real
game server, no network beyond 127.0.0.1, no downloads. The Minecraft
Server List Ping, RCON and Steam A2S exchanges are exercised for real
against in-process fakes; process start/stop/console/send run a small
fake server (this interpreter reading stdin, echoing lines, exiting
on "stop"/"quit").
"""
import json
import os
import shlex
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import game_server as gs
from core.memory.memory import MemoryStore
from core.skills_admin_helpers import _store as _helpers_store

RCON_PASSWORD = "s3cret-rcon"
FAKE_SLP = {
    "version": {"name": "1.21.1", "protocol": 767},
    "players": {"max": 20, "online": 3,
                "sample": [{"name": "alice"}, {"name": "bob"}]},
    "description": {"text": "A test server"},
    "enforcesSecureChat": True,
}


def _cstr(text):
    return text.encode("utf-8") + b"\x00"


FAKE_A2S_INFO = (
    b"\xff\xff\xff\xffI" + b"\x11"
    + _cstr("Test Server") + _cstr("de_dust2") + _cstr("csgo")
    + _cstr("Counter-Strike: Global Offensive")
    + struct.pack("<H", 731) + bytes([3, 10, 0])
    + b"dl" + bytes([0, 1]) + _cstr("1.38.7.4") + b"\x00"
)


def _read_varint(f):
    n = 0
    shift = 0
    while True:
        raw = f.read(1)
        if not raw:
            raise EOFError("closed")
        b = raw[0]
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n
        shift += 7


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("closed")
        buf += chunk
    return bytes(buf)


class _FakeSLPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        f = self.request.makefile("rb")
        try:
            while True:
                length = _read_varint(f)
                data = f.read(length)
                if len(data) != length:
                    return
                packet_id = data[0]
                if packet_id == 0 and len(data) > 1:
                    continue  # handshake — wait for the status request
                if packet_id == 0:
                    payload = json.dumps(FAKE_SLP).encode("utf-8")
                    self.request.sendall(
                        gs._varint(len(payload) + 1) + b"\x00" + payload)
                elif packet_id == 1:
                    self.request.sendall(gs._varint(len(data)) + data)
                    return
        except (EOFError, OSError, ValueError):
            return


class _FakeSLPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _FakeRCONHandler(socketserver.BaseRequestHandler):
    def _send(self, req_id, ptype, payload):
        body = struct.pack("<ii", req_id, ptype) + payload + b"\x00\x00"
        self.request.sendall(struct.pack("<i", len(body)) + body)

    def handle(self):
        try:
            while True:
                raw = _recv_exact(self.request, 4)
                (length,) = struct.unpack("<i", raw)
                body = _recv_exact(self.request, length)
                req_id, ptype = struct.unpack("<ii", body[:8])
                payload = body[8:-2]
                if ptype == 3:
                    if payload == RCON_PASSWORD.encode():
                        self._send(req_id, 2, b"")
                    else:
                        self._send(-1, 2, b"")
                elif ptype == 2:
                    if payload == b"biglist":
                        self._send(req_id, 0, b"x" * 4096)
                        self._send(req_id, 0, b"y" * 100)
                    else:
                        self._send(req_id, 0, b"players: alice, bob")
        except (EOFError, OSError, ValueError, struct.error):
            return


class _FakeRCONServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _FakeA2S(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.5)
        self._connected = False

    def run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            # The lab sandbox blocks unconnected UDP sendto, so the
            # fake answers over a socket connected to the asker
            # (the same seam the module's client uses) — and the
            # sandbox forbids a SECOND connect on the same socket,
            # so it connects once and keeps that peer.
            if not self._connected:
                try:
                    self.sock.connect(addr)
                except OSError:
                    return
                self._connected = True
            if data == b"\xff\xff\xff\xffTSource Engine Query\x00":
                self.sock.send(b"\xff\xff\xff\xffA\x11\x22\x33\x44")
            elif data.startswith(
                    b"\xff\xff\xff\xffTSource Engine Query\x00\x11\x22\x33\x44"):
                self.sock.send(FAKE_A2S_INFO)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


_FAKE_SERVER_SCRIPT = (
    "import sys\n"
    "print('Server ready', flush=True)\n"
    "for line in sys.stdin:\n"
    "    line = line.strip()\n"
    "    print('got:' + line, flush=True)\n"
    "    if line in ('stop', 'quit'):\n"
    "        print('Stopping', flush=True)\n"
    "        break\n"
)


def _fake_server_command():
    return (f"{shlex.quote(sys.executable)} -u -c "
            f"{shlex.quote(_FAKE_SERVER_SCRIPT)}")


class McsmanagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.slp_server = _FakeSLPServer(("127.0.0.1", 0), _FakeSLPHandler)
        threading.Thread(target=cls.slp_server.serve_forever,
                         daemon=True).start()
        cls.slp_port = cls.slp_server.server_address[1]
        cls.rcon_server = _FakeRCONServer(("127.0.0.1", 0), _FakeRCONHandler)
        threading.Thread(target=cls.rcon_server.serve_forever,
                         daemon=True).start()
        cls.rcon_port = cls.rcon_server.server_address[1]
        # The A2S fake is per-test, not class-level: it answers over
        # a socket connected once to its asker, and each test's
        # client has its own ephemeral port.

    @classmethod
    def tearDownClass(cls):
        cls.slp_server.shutdown()
        cls.slp_server.server_close()
        cls.rcon_server.shutdown()
        cls.rcon_server.server_close()

    def setUp(self):
        self.a2s = _FakeA2S()
        self.a2s.start()
        self.a2s_port = self.a2s.port
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(
            db_path=str(Path(self.tmp.name) / "facts.db"))
        self._orig_helpers_store = _helpers_store
        import core.skills_admin_helpers as helpers
        self._helpers = helpers
        helpers._store = lambda: self.store
        self._orig_gs_store = gs._store
        gs._store = lambda: self.store
        self._started = []
        self.name = "Test realm"
        out = gs.game_server(
            action="add", server=self.name, kind="minecraft",
            command=_fake_server_command(),
            working_dir=self.tmp.name, host="127.0.0.1",
            port=self.slp_port, rcon_port=self.rcon_port)
        self.assertIn("Saved server", out)

    def tearDown(self):
        for name in list(self._started):
            try:
                gs.game_server(action="stop", server=name)
            except Exception:  # noqa: BLE001 — teardown is best effort
                pass
            try:
                gs.game_server(action="remove", server=name)
            except Exception:  # noqa: BLE001 — teardown is best effort
                pass
        self.a2s.close()
        self._helpers._store = self._orig_helpers_store
        gs._store = self._orig_gs_store
        self.tmp.cleanup()

    def _start(self, name=None):
        name = name or self.name
        out = gs.game_server(action="start", server=name)
        self.assertIn("Started", out, out)
        self._started.append(name)
        return out

    def _wait_console(self, name, needle, timeout=5.0):
        """Poll the console until `needle` appears (reader-thread race)."""
        import time
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            last = gs.game_server(action="console", server=name)
            if needle in last:
                return last
            time.sleep(0.1)
        return last

    # -- unit-level: varints + protocol helpers ----------------------

    def test_varint_roundtrip(self):
        for n in (0, 1, 127, 128, 300, 767, 2147483647):
            self.assertEqual(gs._read_varint(__import__("io").BytesIO(
                gs._varint(n))), n)

    def test_varint_rejects_garbage(self):
        import io
        with self.assertRaises(ValueError):
            gs._read_varint(io.BytesIO(b"\x80" * 6))

    def test_description_plain_and_chat_dict(self):
        self.assertEqual(gs._mc_description("plain"), "plain")
        self.assertEqual(gs._mc_description(
            {"text": "A ", "extra": [{"text": "test"}]}), "A test")

    # -- Minecraft Server List Ping -----------------------------------

    def test_slp_fake_server(self):
        ok, res = gs.minecraft_status("127.0.0.1", self.slp_port)
        self.assertTrue(ok, res)
        self.assertEqual(res["version"], "1.21.1")
        self.assertEqual(res["protocol"], 767)
        self.assertEqual(res["players_online"], 3)
        self.assertEqual(res["players_max"], 20)
        self.assertEqual(res["players_sample"], ["alice", "bob"])
        self.assertEqual(res["description"], "A test server")
        self.assertTrue(res["enforces_secure_chat"])
        self.assertIsNotNone(res["latency_ms"])

    def test_slp_closed_port_is_honest(self):
        ok, res = gs.minecraft_status("127.0.0.1", 1)
        self.assertFalse(ok)
        self.assertIn("Could not reach", res)

    def test_query_action_uses_fake_slp(self):
        out = gs.game_server(action="query", server=self.name)
        self.assertIn("1.21.1", out)
        self.assertIn("players_online: 3", out)
        self.assertIn("players: alice, bob", out)

    # -- RCON ----------------------------------------------------------

    def test_rcon_fake_server(self):
        ok, res = gs.rcon_command("127.0.0.1", self.rcon_port,
                                  RCON_PASSWORD, "list")
        self.assertTrue(ok, res)
        self.assertEqual(res, "players: alice, bob")

    def test_rcon_wrong_password_refused(self):
        ok, res = gs.rcon_command("127.0.0.1", self.rcon_port,
                                  "wrong-password", "list")
        self.assertFalse(ok)
        self.assertIn("refused", res)
        self.assertNotIn("wrong-password", res)

    def test_rcon_long_response_reassembled(self):
        ok, res = gs.rcon_command("127.0.0.1", self.rcon_port,
                                  RCON_PASSWORD, "biglist")
        self.assertTrue(ok, res)
        self.assertEqual(len(res), 4196)
        self.assertTrue(res.startswith("x" * 100))
        self.assertTrue(res.endswith("y" * 100))

    def test_rcon_action_via_tool(self):
        out = gs.game_server(action="rcon", server=self.name,
                             rcon_password=RCON_PASSWORD, command="list")
        self.assertIn("players: alice, bob", out)
        self.assertNotIn(RCON_PASSWORD, out)

    def test_rcon_needs_password(self):
        out = gs.game_server(action="rcon", server=self.name, command="list")
        self.assertIn("needs a password", out)

    # -- Steam A2S ------------------------------------------------------

    def test_a2s_fake_server(self):
        ok, res = gs.steam_query("127.0.0.1", self.a2s_port)
        self.assertTrue(ok, res)
        self.assertEqual(res["name"], "Test Server")
        self.assertEqual(res["map"], "de_dust2")
        self.assertEqual(res["game"], "Counter-Strike: Global Offensive")
        self.assertEqual(res["app_id"], 731)
        self.assertEqual(res["players_online"], 3)
        self.assertEqual(res["players_max"], 10)
        self.assertEqual(res["version"], "1.38.7.4")
        self.assertFalse(res["password_protected"])

    def test_a2s_closed_port_is_honest(self):
        ok, res = gs.steam_query("127.0.0.1", 1)
        self.assertFalse(ok)
        # A connected UDP socket reports a refused port at once;
        # an unanswered one times out — either is an honest no.
        self.assertTrue("timed out" in res or "refused" in res, res)

    def test_steam_query_action(self):
        out = gs.game_server(
            action="add", server="Dusty", kind="steam",
            command=_fake_server_command(), host="127.0.0.1",
            port=self.a2s_port)
        self.assertIn("Saved server", out)
        out = gs.game_server(action="query", server="Dusty")
        self.assertIn("Test Server", out)
        self.assertIn("players_online: 3", out)
        gs.game_server(action="remove", server="Dusty")

    # -- registry -------------------------------------------------------

    def test_add_list_remove_roundtrip(self):
        listed = gs.game_server(action="list")
        self.assertIn("Test realm", listed)
        self.assertIn("minecraft", listed)
        removed = gs.game_server(action="remove", server=self.name)
        self.assertIn("Removed", removed)
        self.assertNotIn("Test realm",
                         gs.game_server(action="list"))

    def test_add_rejects_bad_kind(self):
        out = gs.game_server(action="add", server="X", kind="ark",
                             command="run.sh")
        self.assertIn("must be 'minecraft', 'steam' or 'generic'", out)

    def test_add_rejects_empty_command(self):
        out = gs.game_server(action="add", server="X", kind="generic",
                             command="  ")
        self.assertIn("never downloads", out)

    def test_add_rejects_missing_working_dir(self):
        out = gs.game_server(
            action="add", server="X", kind="generic",
            command="run.sh", working_dir="/no/such/dir/anywhere")
        self.assertIn("does not exist", out)

    def test_add_rejects_duplicate(self):
        out = gs.game_server(action="add", server=self.name,
                             kind="minecraft",
                             command=_fake_server_command())
        self.assertIn("already saved", out)

    def test_add_rejects_bad_name(self):
        out = gs.game_server(action="add", server="bad/name!",
                             kind="generic", command="run.sh")
        self.assertIn("may only use", out)

    def test_no_password_stored(self):
        gs.game_server(action="rcon", server=self.name,
                       rcon_password=RCON_PASSWORD, command="list")
        entries, _broken = gs._load_servers()
        payload = json.dumps(entries)
        self.assertNotIn(RCON_PASSWORD, payload)
        self.assertNotIn("rcon_password", payload)
        self.assertNotIn("password", payload)

    def test_unknown_server(self):
        out = gs.game_server(action="query", server="Nope")
        self.assertIn("No saved server", out)

    def test_unknown_action(self):
        out = gs.game_server(action="explode", server=self.name)
        self.assertIn("Unknown game-server action", out)

    # -- process start / stop / console / send -------------------------

    def test_start_stop_console_send(self):
        self._start()
        console = self._wait_console(self.name, "Server ready")
        self.assertIn("Server ready", console)
        sent = gs.game_server(action="send", server=self.name,
                              command="say hello")
        self.assertIn("Sent to", sent)
        console = self._wait_console(self.name, "got:say hello")
        self.assertIn("got:say hello", console)
        status = gs.game_server(action="status", server=self.name)
        self.assertIn("running", status)
        self.assertIn("3/20 players online", status)
        stopped = gs.game_server(action="stop", server=self.name)
        self.assertIn("stopped", stopped)
        self._started.remove(self.name)
        status = gs.game_server(action="status", server=self.name)
        self.assertIn("not running", status)

    def test_start_refuses_already_running(self):
        self._start()
        out = gs.game_server(action="start", server=self.name)
        self.assertIn("already running", out)

    def test_stop_refuses_not_running(self):
        out = gs.game_server(action="stop", server=self.name)
        self.assertIn("not running under VYUK", out)

    def test_console_refuses_not_running(self):
        out = gs.game_server(action="console", server=self.name)
        self.assertIn("not running under VYUK", out)

    def test_send_refuses_not_running(self):
        out = gs.game_server(action="send", server=self.name,
                             command="hi")
        self.assertIn("not running under VYUK", out)

    def test_remove_refuses_running(self):
        self._start()
        out = gs.game_server(action="remove", server=self.name)
        self.assertIn("still running", out)

    def test_restart_roundtrip(self):
        self._start()
        out = gs.game_server(action="restart", server=self.name)
        self.assertIn("Restarted", out)
        console = self._wait_console(self.name, "Server ready")
        self.assertIn("Server ready", console)

    def test_start_exits_immediately_is_honest(self):
        gs.game_server(action="add", server="Doomed", kind="generic",
                       command=f"{shlex.quote(sys.executable)} -c "
                               f"{shlex.quote('import sys; sys.exit(3)')}")
        out = gs.game_server(action="start", server="Doomed")
        self.assertIn("exited immediately", out)
        self.assertIn("code 3", out)
        gs.game_server(action="remove", server="Doomed")

    # -- gate ------------------------------------------------------------

    def test_gate_matrix(self):
        from core.funnel.tools_gate import _needs_confirmation
        from core.funnel.tools_gate_readclass import _is_read_action
        # D-06 (Phase 6): the session provenance lock (default ON,
        # core/config.py session_provenance_lock=True) gates every
        # state-changing tool in a non-user-started session — add/remove
        # are writes, so the lock gates them here and the matrix below
        # would measure the lock, not the gate. This test pins the
        # per-action gate matrix, so it runs as a user-started session
        # (the tests/test_stage0f6.py precedent) and restores the
        # provenance afterwards.
        import core.funnel.tools_gate as _tg
        _prev_prov = _tg.session_provenance()
        _prev_proven = _tg.session_owner_proven()

        def _restore():
            _tg.set_session_provenance(_prev_prov)
            if _prev_proven:
                _tg.mark_session_owner_proven("user-started")
            else:
                _tg.mark_session_owner_unproven()

        self.addCleanup(_restore)
        _tg.set_session_provenance("user")
        for action in ("query", "status", "console", "list"):
            self.assertFalse(
                _needs_confirmation("game_server", {"action": action}),
                action)
            self.assertTrue(
                _is_read_action("game_server", {"action": action}),
                action)
        # add/remove only touch VYUK's own registry, so they stay
        # free untainted — but they are writes, not read-class.
        for action in ("add", "remove"):
            self.assertFalse(
                _needs_confirmation("game_server", {"action": action}),
                action)
            self.assertFalse(
                _is_read_action("game_server", {"action": action}),
                action)
        for action in ("start", "stop", "restart", "send", "rcon"):
            self.assertTrue(
                _needs_confirmation("game_server", {"action": action}),
                action)
            self.assertFalse(
                _is_read_action("game_server", {"action": action}),
                action)
        self.assertTrue(
            _needs_confirmation("game_server", {"action": "bogus"}))
        self.assertTrue(_needs_confirmation("game_server", {}))

    # -- census pins ------------------------------------------------------

    def test_census_pins(self):
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 772)  # INTENTIONAL — R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2126)  # INTENTIONAL — R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        snap = json.load(open(os.path.join(
            os.path.dirname(__file__), "_classification_snapshot.json")))
        self.assertEqual(len(snap), 772)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(snap["game_server"],
                         {"confirm": True, "free": None,
                          "read": False})


if __name__ == "__main__":
    unittest.main()
