"""R18-6 fonoster/fonoster rebuild — the local voice-agent /
call-flow engine + the operator-supplied SIP trunk client.

Fake SIP trunk + RTP peer only (loopback UDP, connected sockets):
no real trunk, no real phone line, no real credential — the
'secret' strings below are obviously fake, and every store is a
temp DB. The TTS renderer is a known tone; the Gemini opener is
a canned answer.
"""
import json
import math
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import voice_agent as va
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import _needs_confirmation
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools

TREE = Path(__file__).resolve().parent.parent

GOOD_FLOW = json.dumps([
    {"kind": "say", "text": "Press 1 for sales, 2 for support."},
    {"kind": "gather", "timeout_s": 5, "max_digits": 1},
    {"kind": "branch", "on": {"1": 3, "2": 5, "default": 6}},
    {"kind": "say", "text": "Sales."},
    {"kind": "hangup"},
    {"kind": "say", "text": "Support."},
    {"kind": "hangup"}])


def _fake_renderer(text):
    """A 0.2 s 440 Hz sine at 8000 Hz. Returns (rate, int16 PCM
    bytes) — the same shape as the tree's _kokoro_render."""
    samples = [int(12000 * math.sin(2 * math.pi * 440 * i / 8000))
               for i in range(1600)]
    return (8000, struct.pack("<%dh" % len(samples), *samples))


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload


def _fake_opener(request, timeout=None):
    payload = json.dumps({"candidates": [{"content": {"parts": [
        {"text": "one"}]}}]}).encode("utf-8")
    return _FakeResponse(payload)


class FakePeer:
    """A fake SIP trunk + RTP peer on loopback UDP, serving ONE UA
    (both sides use connected sockets — the sandbox refuses
    unconnected sendto, the qlcplus precedent)."""

    def __init__(self):
        self.sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip.bind(("127.0.0.1", 0))
        self.sip_port = self.sip.getsockname()[1]
        self.sip.settimeout(0.5)
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind(("127.0.0.1", 0))
        self.rtp_port = self.rtp.getsockname()[1]
        self.rtp.settimeout(0.5)
        self.stop = threading.Event()
        self.received = {"register": 0, "invite": 0, "ack": 0, "bye": 0}
        self.rtp_packets = []
        self.client_rtp = None
        self._lock = threading.Lock()
        self._threads = []
        self._invite = None
        self._conn = {}

    def start(self):
        for target in (self._sip_loop, self._rtp_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def close(self):
        self.stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        for s in (self.sip, self.rtp):
            try:
                s.close()
            except OSError:
                pass

    @staticmethod
    def _challenge():
        return ('Digest realm="test", nonce="abc123", qop="auth", '
                'algorithm=MD5')

    def _send(self, sock, target, data):
        if self._conn.get(id(sock)) != target:
            sock.connect(target)
            self._conn[id(sock)] = target
        sock.send(data)

    def _sip_loop(self):
        while not self.stop.is_set():
            try:
                data, addr = self.sip.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            msg = va.parse_sip(data)
            if msg is None:
                continue
            if msg.is_response:
                if msg.status == 200 and self._invite is not None:
                    answer = va.parse_sdp(msg.body)
                    if answer:
                        with self._lock:
                            self.client_rtp = (answer.get("ip"),
                                               answer.get("port"))
                    self._send_ack(addr)
                continue
            method = msg.method
            with self._lock:
                self.received[method.lower()] = \
                    self.received.get(method.lower(), 0) + 1
            if method == "REGISTER":
                if msg.header("authorization"):
                    self._send(self.sip, addr, va.build_sip_response(
                        200, "OK", msg, []))
                else:
                    self._send(self.sip, addr, va.build_sip_response(
                        401, "Unauthorized", msg,
                        [("WWW-Authenticate", self._challenge())]))
            elif method == "INVITE":
                if msg.header("authorization"):
                    offer = va.parse_sdp(msg.body)
                    if offer:
                        self.client_rtp = (offer.get("ip"),
                                           offer.get("port"))
                    answer = va.build_sdp_offer("127.0.0.1",
                                                self.rtp_port)
                    self._send(self.sip, addr, va.build_sip_response(
                        200, "OK", msg,
                        [("Content-Type", "application/sdp")],
                        answer.encode("utf-8")))
                else:
                    self._send(self.sip, addr, va.build_sip_response(
                        401, "Unauthorized", msg,
                        [("WWW-Authenticate", self._challenge())]))
            elif method == "BYE":
                self._send(self.sip, addr, va.build_sip_response(
                    200, "OK", msg, []))

    def _rtp_loop(self):
        while not self.stop.is_set():
            try:
                data, _addr = self.rtp.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            parsed = va.rtp_unpack(data)
            if parsed is None:
                continue
            pt, _seq, _ts, _ssrc, payload, _marker = parsed
            with self._lock:
                self.rtp_packets.append((pt, len(payload)))

    def audio_packets(self, payload_type=0):
        with self._lock:
            return [p for p in self.rtp_packets if p[0] == payload_type]

    def send_dtmf(self, digit):
        if not self.client_rtp or not self.client_rtp[0]:
            return False
        event = va._DTMF_EVENTS.index(digit)
        for end, dur in ((0, 160), (1, 320)):
            payload = bytes([event, (0x80 if end else 0x00) | 10,
                             dur >> 8, dur & 0xFF])
            packet = va.rtp_pack(101, 1, 160, 1234, payload)
            try:
                self._send(self.rtp, self.client_rtp, packet)
            except OSError:
                return False
        return True

    def dial(self, listener_port, number="5551234"):
        offer = va.build_sdp_offer("127.0.0.1", self.rtp_port)
        call_id = va._new_call_id("127.0.0.1")
        headers = [
            ("Via", "SIP/2.0/UDP 127.0.0.1:%d;branch=z9hG4bKpeer1"
                    % self.sip_port),
            ("Max-Forwards", "70"),
            ("From", "<sip:caller@127.0.0.1>;tag=peer1"),
            ("To", "<sip:%s@127.0.0.1>" % number),
            ("Call-ID", call_id),
            ("CSeq", "1 INVITE"),
            ("Contact", "<sip:caller@127.0.0.1:%d>" % self.sip_port),
            ("Content-Type", "application/sdp"),
        ]
        raw = va.build_sip_request(
            "INVITE", "sip:%s@127.0.0.1" % number, headers,
            offer.encode("utf-8"))
        self._invite = {"call_id": call_id, "cseq": 1,
                        "uri": "sip:%s@127.0.0.1" % number,
                        "from": "<sip:caller@127.0.0.1>;tag=peer1",
                        "to": "<sip:%s@127.0.0.1>" % number}
        self._send(self.sip, ("127.0.0.1", listener_port), raw)

    def _send_ack(self, addr):
        inv = self._invite
        if inv is None:
            return
        headers = [
            ("Via", "SIP/2.0/UDP 127.0.0.1:%d;branch=z9hG4bKpeerack"
                    % self.sip_port),
            ("Max-Forwards", "70"),
            ("From", inv["from"]),
            ("To", inv["to"]),
            ("Call-ID", inv["call_id"]),
            ("CSeq", "%d ACK" % inv["cseq"]),
            ("Contact", "<sip:caller@127.0.0.1:%d>" % self.sip_port),
        ]
        self._send(self.sip, addr,
                   va.build_sip_request("ACK", inv["uri"], headers))
        self._invite = None


def _sock_factory():
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _trunk(peer):
    return {"host": "127.0.0.1", "port": peer.sip_port,
            "user": "alice", "pass": "secret"}


class TestMuLaw(unittest.TestCase):
    def test_silence_encodes_to_0xff(self):
        self.assertEqual(va.linear_to_ulaw(0), 0xFF)

    def test_extremes(self):
        self.assertEqual(va.linear_to_ulaw(32767), 0x80)
        self.assertEqual(va.linear_to_ulaw(-32768), 0x00)

    def test_decode_known_values(self):
        self.assertEqual(va.ulaw_to_linear(0xFF), 0)
        self.assertEqual(va.ulaw_to_linear(0x80), 32124)

    def test_round_trip_within_2_percent(self):
        # mu-law is lossy companding: one step at this magnitude is 16.
        self.assertLessEqual(
            abs(va.ulaw_to_linear(va.linear_to_ulaw(1000)) - 1000), 20)


class TestSIP(unittest.TestCase):
    def test_parse_invite(self):
        raw = (b"INVITE sip:5551234@127.0.0.1 SIP/2.0\r\n"
               b"Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bK1\r\n"
               b"From: <sip:alice@127.0.0.1>;tag=a1\r\n"
               b"To: <sip:5551234@127.0.0.1>\r\n"
               b"Call-ID: c1@127.0.0.1\r\n"
               b"CSeq: 1 INVITE\r\n"
               b"Content-Length: 0\r\n\r\n")
        msg = va.parse_sip(raw)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.method, "INVITE")
        self.assertEqual(msg.header("call-id"), "c1@127.0.0.1")
        self.assertEqual(msg.header("cseq"), "1 INVITE")

    def test_parse_garbage_returns_none(self):
        self.assertIsNone(va.parse_sip(b"not sip at all"))

    def test_build_request(self):
        built = va.build_sip_request("OPTIONS", "sip:127.0.0.1",
                                     [("Via", "SIP/2.0/UDP 127.0.0.1:5060"
                                              ";branch=z9hG4bK2"),
                                      ("Call-ID", "c2@127.0.0.1")])
        self.assertTrue(built.startswith(b"OPTIONS sip:127.0.0.1 SIP/2.0"))
        self.assertIn(b"Via: SIP/2.0/UDP", built)

    def test_digest_challenge_and_response(self):
        challenge = va.parse_auth_challenge(
            'Digest realm="test", nonce="abc123", qop="auth", '
            'algorithm=MD5')
        self.assertEqual(challenge.get("realm"), "test")
        self.assertEqual(challenge.get("nonce"), "abc123")
        auth = va.digest_authorization("alice", "secret", "REGISTER",
                                       "sip:127.0.0.1", challenge)
        self.assertIsNotNone(auth)
        response = auth.split('response="')[1].split('"')[0]
        self.assertEqual(len(response), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in response))

    def test_digest_without_challenge_is_empty(self):
        # The docstring's contract: "" means "no auth".
        self.assertEqual(va.digest_authorization(
            "alice", "secret", "REGISTER", "sip:127.0.0.1", {}), "")


class TestSDP(unittest.TestCase):
    def test_offer_has_pcmu_and_dtmf(self):
        offer = va.build_sdp_offer("127.0.0.1", 5004)
        self.assertIn("m=audio 5004 RTP/AVP 0 101", offer)
        self.assertIn("a=rtpmap:0 PCMU/8000", offer)
        self.assertIn("a=rtpmap:101 telephone-event/8000", offer)
        self.assertIn("c=IN IP4 127.0.0.1", offer)

    def test_parse_offer(self):
        parsed = va.parse_sdp(va.build_sdp_offer("127.0.0.1", 5004))
        self.assertEqual(parsed.get("ip"), "127.0.0.1")
        self.assertEqual(parsed.get("port"), 5004)
        self.assertIn(0, parsed.get("payloads", []))
        self.assertEqual(parsed.get("dtmf_pt"), 101)

    def test_parse_without_pcmu_is_empty(self):
        self.assertEqual(va.parse_sdp(
            "v=0\r\nm=audio 5004 RTP/AVP 8\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"), {})


class TestRTP(unittest.TestCase):
    def test_pack_unpack_round_trip(self):
        packed = va.rtp_pack(0, 42, 160, 99, b"\xff\x80")
        unpacked = va.rtp_unpack(packed)
        self.assertIsNotNone(unpacked)
        self.assertEqual(unpacked[0], 0)
        self.assertEqual(unpacked[1], 42)
        self.assertEqual(unpacked[2], 160)
        self.assertEqual(unpacked[3], 99)
        self.assertEqual(unpacked[4], b"\xff\x80")

    def test_unpack_garbage_returns_none(self):
        self.assertIsNone(va.rtp_unpack(b"short"))
        self.assertIsNone(va.rtp_unpack(b"\x00" * 12))


class TestFlow(unittest.TestCase):
    def test_valid_flow_passes(self):
        steps, err = va.validate_flow(GOOD_FLOW)
        self.assertIsNotNone(steps)
        self.assertEqual(len(steps), 7)
        self.assertEqual(err, "")

    def test_unknown_kind_is_refused(self):
        steps, err = va.validate_flow(json.dumps([{"kind": "shout"}]))
        self.assertIsNone(steps)
        self.assertIn("kind", err)

    def test_too_many_steps_is_refused(self):
        steps, err = va.validate_flow(json.dumps(
            [{"kind": "say", "text": "x"}] * 51))
        self.assertIsNone(steps)
        self.assertIn("50", err)

    def test_bad_json_is_refused(self):
        steps, err = va.validate_flow("not json")
        self.assertIsNone(steps)

    def test_branch_on_digit_1(self):
        steps, _ = va.validate_flow(GOOD_FLOW)
        transcript = va.run_flow(steps, va.TextSession(["1"]))
        kinds = [(t["kind"], t["detail"]) for t in transcript]
        self.assertIn(("gather", "1"), kinds)
        self.assertIn(("branch", "input '1' -> step 3"), kinds)
        self.assertIn(("say", "Sales."), kinds)
        self.assertIn(("hangup", "call ended"), kinds)

    def test_branch_on_digit_2(self):
        steps, _ = va.validate_flow(GOOD_FLOW)
        transcript = va.run_flow(steps, va.TextSession(["2"]))
        kinds = [(t["kind"], t["detail"]) for t in transcript]
        self.assertIn(("say", "Support."), kinds)

    def test_transfer_ends_the_flow(self):
        steps, _ = va.validate_flow(json.dumps([
            {"kind": "gather", "timeout_s": 5, "max_digits": 1},
            {"kind": "transfer"}]))
        session = va.TextSession(["9"], transfer_to="5559999")
        transcript = va.run_flow(steps, session)
        self.assertEqual(len(transcript), 2)
        self.assertEqual(transcript[1]["kind"], "transfer")
        self.assertIn("5559999", transcript[1]["detail"])


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="fonoster_test_")
        self.store = va.VoiceAgentStore(
            os.path.join(self.tmpdir, "voice_agent.sqlite3"))

    def tearDown(self):
        self.store.close()

    def test_agent_crud(self):
        agent = self.store.add_agent("Reception", "Hello.", None,
                                     "5559999", "")
        self.assertIsNotNone(agent)
        self.assertEqual(agent["name"], "Reception")
        self.assertEqual(self.store.get_agent(agent["id"])["name"],
                         "Reception")
        self.assertEqual(len(self.store.list_agents()), 1)
        self.assertTrue(self.store.remove_agent(agent["id"]))
        self.assertIsNone(self.store.get_agent(agent["id"]))

    def test_duplicate_agent_name_is_refused(self):
        self.store.add_agent("Reception", "Hello.", None, "", "")
        self.assertIsNone(
            self.store.add_agent("Reception", "Hi.", None, "", ""))

    def test_flow_crud(self):
        flow = self.store.add_flow("Main", GOOD_FLOW)
        self.assertIsNotNone(flow)
        self.assertEqual(self.store.get_flow(flow["id"])["name"], "Main")
        self.assertEqual(len(self.store.list_flows()), 1)
        self.assertIsNone(self.store.add_flow("Main", GOOD_FLOW))
        self.assertTrue(self.store.remove_flow(flow["id"]))

    def test_record_call_and_chain(self):
        record = self.store.record_call(
            "out", "5551234", 0, time.time(), time.time() + 1.0,
            "completed", [{"step": 0, "kind": "say", "detail": "Hi."}])
        self.assertIsNotNone(record)
        self.store.append_event("test", {"x": 0})
        ok, _ = self.store.verify_chain()
        self.assertTrue(ok)

    def test_chain_catches_a_tampered_row(self):
        self.store.append_event("test", {"x": 0})
        ok, _ = self.store.verify_chain()
        self.assertTrue(ok)
        self.store.conn.execute(
            "UPDATE events SET payload_json='{\"x\": 1}' WHERE seq=1")
        self.store.conn.commit()
        ok, reason = self.store.verify_chain()
        self.assertFalse(ok)
        self.assertIn("hash", reason)

    def test_counts(self):
        self.store.add_agent("A", "", None, "", "")
        self.store.add_flow("F", GOOD_FLOW)
        counts = self.store.counts()
        self.assertEqual(counts["agents"], 1)
        self.assertEqual(counts["flows"], 1)


class _StoreCase(unittest.TestCase):
    """A temp store + a flow, for the handler tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="fonoster_test_")
        self.store = va.VoiceAgentStore(
            os.path.join(self.tmpdir, "voice_agent.sqlite3"))
        self.flow = self.store.add_flow("Main", GOOD_FLOW)
        self.agent = self.store.add_agent(
            "Reception", "Hello.", self.flow["id"], "5559999", "")

    def tearDown(self):
        try:
            va.stop_listener()
        except Exception:
            pass
        self.store.close()


class TestHandler(_StoreCase):
    def test_agent_add_list_get_remove(self):
        out = va.voice_agent(action="agent", mode="add", name="Night", greeting="Hi.", flow_id=self.flow["id"], transfer_to="5559999", _store=self.store)
        self.assertIn("Added voice agent", out)
        out = va.voice_agent(action="agent", mode="list",
                             _store=self.store)
        self.assertIn("Reception", out)
        self.assertIn("Night", out)
        out = va.voice_agent(action="agent", mode="get", agent_id=self.agent["id"], _store=self.store)
        self.assertIn("Reception", out)
        out = va.voice_agent(action="agent", mode="remove", agent_id=self.agent["id"], _store=self.store)
        self.assertIn("Removed", out)

    def test_agent_add_without_name_is_refused(self):
        out = va.voice_agent(action="agent", mode="add",
                             _store=self.store)
        self.assertIn("refused", out)

    def test_flow_add_list_get_remove(self):
        out = va.voice_agent(action="flow", mode="add", name="Second", steps=GOOD_FLOW, _store=self.store)
        self.assertIn("Added call flow", out)
        out = va.voice_agent(action="flow", mode="list",
                             _store=self.store)
        self.assertIn("Main", out)
        self.assertIn("Second", out)

    def test_flow_add_with_bad_steps_is_refused(self):
        out = va.voice_agent(action="flow", mode="add", name="Bad", steps='[{"kind": "shout"}]', _store=self.store)
        self.assertIn("refused", out)

    def test_run_follows_the_branch(self):
        out = va.voice_agent(action="run", flow_id=self.flow["id"], inputs='["1"]', _store=self.store)
        self.assertIn("Sales.", out)
        self.assertIn("gather", out)

    def test_run_with_bad_inputs_is_refused(self):
        out = va.voice_agent(action="run", flow_id=self.flow["id"], inputs="not json", _store=self.store)
        self.assertIn("refused", out)

    def test_status_and_events(self):
        out = va.voice_agent(action="status", _store=self.store)
        self.assertIn("agents:", out)
        self.assertIn("ledger chain: valid", out)
        out = va.voice_agent(action="events", limit=5,
                             _store=self.store)
        self.assertIn("No voice-agent events yet", out)

    def test_unknown_action_is_refused(self):
        out = va.voice_agent(action="nonsense", _store=self.store)
        self.assertIn("refused", out)

    def test_call_with_bad_number_is_refused(self):
        out = va.voice_agent(action="call", agent_id=self.agent["id"], number="abc", trunk=json.dumps({"host": "127.0.0.1", "port": 5060,
                                  "user": "a", "pass": "b"}),
            _store=self.store)
        self.assertIn("refused", out)

    def test_call_with_bad_trunk_is_refused(self):
        out = va.voice_agent(action="call", agent_id=self.agent["id"], number="5551234", trunk="not json",
            _store=self.store)
        self.assertIn("refused", out)

    def test_call_with_unknown_agent_is_refused(self):
        out = va.voice_agent(action="call", agent_id=99999, number="5551234", trunk=json.dumps({"host": "127.0.0.1", "port": 5060,
                                  "user": "a", "pass": "b"}),
            _store=self.store)
        self.assertIn("refused", out)

    def test_serve_with_unknown_agent_is_refused(self):
        out = va.voice_agent(action="serve", agent_id=99999, trunk=json.dumps({"host": "127.0.0.1", "port": 5060,
                                  "user": "a", "pass": "b"}),
            _store=self.store)
        self.assertIn("refused", out)


class TestSIPLive(_StoreCase):
    """The real SIP/RTP code against a fake peer on loopback UDP."""

    def setUp(self):
        super().setUp()
        self.peer = FakePeer()
        self.peer.start()

    def tearDown(self):
        self.peer.close()
        super().tearDown()

    def test_register_invite_audio_dtmf_bye(self):
        trunk = _trunk(self.peer)
        ua = va.SIPUA(sock_factory=_sock_factory)
        try:
            ok, reason = ua.register(trunk)
            self.assertTrue(ok, reason)
            offer = va.build_sdp_offer(va._local_ip_for(trunk["host"]),
                                       ua.rtp.local_port)
            dialog, reason = ua.invite("5551234", trunk, offer)
            self.assertIsNotNone(dialog, reason)
            self.assertEqual(ua.rtp.peer,
                             ("127.0.0.1", self.peer.rtp_port))
            session = va.SIPSession(ua, dialog, transfer_to="5559999",
                                    _renderer=_fake_renderer,
                                    _opener=_fake_opener, pace=False)
            session.say("Hello, this is a test.")
            time.sleep(0.3)
            self.assertGreaterEqual(
                len(self.peer.audio_packets(0)), 5)
            self.assertTrue(self.peer.send_dtmf("1"))
            got = session.gather(5, 1, "dtmf")
            self.assertEqual(got[0], "1")
            session.hangup()
            time.sleep(0.3)
            self.assertGreaterEqual(self.peer.received.get("bye", 0), 1)
        finally:
            ua.close()

    def test_handler_call_through_the_fake_peer(self):
        peerB = FakePeer()
        peerB.start()
        try:
            trunkB = _trunk(peerB)
            result = {}

            def _do_call():
                result["out"] = va.voice_agent(action="call", agent_id=self.agent["id"], number="5551234", trunk=json.dumps(trunkB), timeout_s=60,
                    _store=self.store, _renderer=_fake_renderer,
                    _opener=_fake_opener, _sock_factory=_sock_factory)

            thread = threading.Thread(target=_do_call, daemon=True)
            thread.start()
            deadline = time.time() + 15
            sent = False
            while time.time() < deadline and thread.is_alive():
                if len(peerB.audio_packets(0)) >= 5 and not sent:
                    peerB.send_dtmf("1")
                    sent = True
                time.sleep(0.1)
            thread.join(timeout=15)
            out = result.get("out", "")
            self.assertIn("Called 5551234", out)
            self.assertIn("Sales.", out)
            self.assertGreaterEqual(self.store.counts()["calls"], 1)
            events = self.store.list_events(5)
            self.assertTrue(any(e["kind"] == "call_out"
                                for e in events))
        finally:
            peerB.close()

    def test_listener_answers_an_inbound_call(self):
        peerC = FakePeer()
        peerC.start()
        try:
            trunkC = _trunk(peerC)
            ok, reason = va.start_listener(
                trunkC, self.agent["id"], self.store,
                _sock_factory=_sock_factory, _renderer=_fake_renderer,
                _opener=_fake_opener)
            self.assertTrue(ok, reason)
            listener_port = va._LISTENER["ua"].local_port
            self.assertGreater(listener_port, 0)
            peerC.dial(listener_port)
            deadline = time.time() + 15
            sent = False
            recorded = 0
            while time.time() < deadline:
                if len(peerC.audio_packets(0)) >= 5 and not sent:
                    peerC.send_dtmf("1")
                    sent = True
                recorded = self.store.counts()["calls"]
                if recorded >= 1:
                    break
                time.sleep(0.2)
            self.assertGreaterEqual(recorded, 1)
            self.assertGreaterEqual(len(peerC.audio_packets(0)), 5)
        finally:
            va.stop_listener()
            peerC.close()


class TestWiring(unittest.TestCase):
    def test_registry_has_voice_agent_with_14_properties(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 756)  # INTENTIONAL — R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        decl = [d for d in decls if d.get("name") == "voice_agent"]
        self.assertEqual(len(decl), 1)
        props = decl[0].get("parameters", {}).get("properties", {})
        self.assertEqual(len(props), 14)
        for name in ("action", "mode", "agent_id", "name", "greeting",
                     "flow_id", "steps", "inputs", "number", "trunk",
                     "transfer_to", "timeout_s", "limit", "detail"):
            self.assertIn(name, props)

    def test_call_and_serve_gate_but_reads_are_free(self):
        self.assertTrue(_needs_confirmation(
            "voice_agent", {"action": "call"}))
        self.assertTrue(_needs_confirmation(
            "voice_agent", {"action": "serve"}))
        self.assertFalse(_needs_confirmation(
            "voice_agent", {"action": "agent", "mode": "add"}))
        self.assertFalse(_needs_confirmation(
            "voice_agent", {"action": "run"}))
        self.assertFalse(_needs_confirmation(
            "voice_agent", {"action": "status"}))
        self.assertFalse(_needs_confirmation(
            "voice_agent", {"action": "events"}))
        # Unknown action gates fail-closed.
        self.assertTrue(_needs_confirmation(
            "voice_agent", {"action": "nonsense"}))

    def test_read_class(self):
        self.assertTrue(_is_read_action(
            "voice_agent", {"action": "run"}))
        self.assertTrue(_is_read_action(
            "voice_agent", {"action": "status"}))
        self.assertTrue(_is_read_action(
            "voice_agent", {"action": "events"}))
        self.assertTrue(_is_read_action(
            "voice_agent", {"action": "agent", "mode": "list"}))
        self.assertTrue(_is_read_action(
            "voice_agent", {"action": "flow", "mode": "get"}))
        self.assertFalse(_is_read_action(
            "voice_agent", {"action": "call"}))
        self.assertFalse(_is_read_action(
            "voice_agent", {"action": "serve"}))
        self.assertFalse(_is_read_action(
            "voice_agent", {"action": "agent", "mode": "add"}))
        self.assertFalse(_is_read_action(
            "voice_agent", {"action": "stop"}))

    def test_confirm_set_and_snapshot(self):
        self.assertIn("voice_agent", _CONFIRM_GATE_TOOLS)
        snapshot = json.loads(
            (TREE / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(snapshot["voice_agent"],
                         {"confirm": True, "free": None, "read": False})

    def test_catalogue_has_voice_agent_in_your_world(self):
        self.assertIn("voice_agent", GROUP_MEMBERS["Your world"])

    def test_licence_marker_is_proposed_or_parent_ruled(self):
        # Hardened (the R18-1/R18-5 trap): the parent's ruling edit
        # flips the marker after this run, so either state passes.
        text = (TREE / "core" / "voice_agent.py").read_text(
            encoding="utf-8")
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled" in text)


if __name__ == "__main__":
    unittest.main()
