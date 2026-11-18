"""B-D6 (2026-11-09) voice/video calls via aiortc.

Runs against a FAKE aiortc backend + a LOOPBACK signalling
transport + a FAKE CLOCK (no real network, no aiortc on this
Linux lab machine, no real waiting). The engine-availability
tests assert the honest engine_unavailable degradation on
this machine instead of skipping: a suite that silently
skips the call checks on an interpreter without the library
is a lie, not a pass.

The real-Windows leg — a real call between two machines —
is a field leg, owed, not this batch.
"""

import unittest

from core.desktop import call_control as cc


class _FakeClock:
    """monotonic()/sleep() with no real waiting."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class _LoopbackSignalling:
    """A signalling transport that never leaves the machine.

    encrypted=False but endpoint loopback:// — accepted by
    the wrapper as loopback. send() of an offer immediately
    stages a matching answer in the inbox (the peer answers);
    recv() pops it. Every message sent is recorded.
    """

    def __init__(self):
        self.encrypted = False
        self.endpoint = "loopback://peer"
        self.sent = []
        self._inbox = []

    def send(self, msg):
        self.sent.append(dict(msg))
        if msg.get("type") == "offer":
            self._inbox.append({
                "type": "answer", "call_id": msg.get("call_id"),
                "peer": msg.get("peer"),
                "sdp": "v=0 fake answer", "sdp_type": "answer"})

    def recv(self, timeout_s):
        if self._inbox:
            return self._inbox.pop(0)
        return None


class _PlainSignalling:
    """An UNENCRYPTED, non-loopback transport: refused."""

    def __init__(self):
        self.encrypted = False
        self.endpoint = "ws://signal.example.com/room"
        self.sent = []

    def send(self, msg):
        self.sent.append(dict(msg))

    def recv(self, timeout_s):
        return None


class _EncryptedSignalling:
    """An encrypted transport: accepted. Its inbox is
    pre-staged with an answer by the test."""

    def __init__(self):
        self.encrypted = True
        self.endpoint = "wss://signal.example.com/room"
        self.sent = []
        self._inbox = []

    def stage_answer(self, call_id, peer):
        self._inbox.append({
            "type": "answer", "call_id": call_id, "peer": peer,
            "sdp": "v=0 fake answer", "sdp_type": "answer"})

    def send(self, msg):
        self.sent.append(dict(msg))

    def recv(self, timeout_s):
        if self._inbox:
            return self._inbox.pop(0)
        return None


class _FakeBackend:
    """The aiortc backend, faked. Models exactly what the
    wrapper relies on: place/answer exchange through the
    signalling transport, frame counters for the media
    check, mic_live False before acceptance and after
    hangup, close-on-end asserted by the wrapper's
    verification (is_live)."""

    def __init__(self, clock, *, media=True):
        self.clock = clock
        self.media = media
        self.calls = {}      # key -> record
        self.closed = set()  # keys ended
        self.counter = 0
        self.close_calls = 0

    def _new_key(self, peer, invitation):
        self.counter += 1
        key = object()
        self.calls[key] = {
            "peer": peer, "call_id": invitation["call_id"],
            "kind": invitation.get("kind", "audio"),
            "mic_live": True, "state": "connected"}
        return key

    def place(self, peer, invitation, signalling):
        signalling.send({"type": "offer",
                         "call_id": invitation["call_id"],
                         "peer": peer, "sdp": "v=0 fake offer",
                         "sdp_type": "offer"})
        answer = signalling.recv(5.0)
        if answer is None or answer.get("type") != "answer":
            raise cc.CallFailed(
                f"no answer from the peer ({peer!r})")
        return self._new_key(peer, invitation)

    def answer(self, invitation, signalling):
        signalling.send({"type": "answer",
                         "call_id": invitation["call_id"],
                         "peer": invitation["peer"],
                         "sdp": "v=0 fake answer",
                         "sdp_type": "answer"})
        return self._new_key(invitation["peer"], invitation)

    def call_media(self, key):
        rec = self.calls.get(key)
        if rec is None:
            return {"state": "ended", "media_in": False,
                    "media_out": False, "mic_live": False}
        return {"state": rec["state"],
                "media_in": self.media, "media_out": self.media,
                "mic_live": rec["mic_live"]}

    def end(self, key):
        if key not in self.calls:
            raise cc.CallDead("the call is already ended")
        self.calls[key]["mic_live"] = False
        self.calls[key]["state"] = "ended"
        del self.calls[key]
        self.closed.add(key)

    def is_live(self, key):
        return key in self.calls

    def close(self):
        self.close_calls += 1


class _EngineGate(unittest.TestCase):
    """The engine gate, stated once: on an interpreter
    without aiortc, the real-backend path returns
    engine_unavailable honestly. Tests that need a call
    pass an injected fake backend."""

    def setUp(self):
        self.clock = _FakeClock()
        self.backend = _FakeBackend(self.clock)
        self.sig = _LoopbackSignalling()
        # The module-level call registry is shared: a test
        # that leaves a call live would leak into the next.
        for call_id in [c["call_id"] for c in
                        cc.list_calls()["calls"]]:
            cc.end_call(call_id, backend=self.backend)

    def tearDown(self):
        for call_id in [c["call_id"] for c in
                        cc.list_calls()["calls"]]:
            cc.end_call(call_id, backend=self.backend)


class TestEngineAvailability(_EngineGate):
    def test_engine_unavailable_on_this_machine(self):
        self.assertFalse(cc.engine_available())
        status = cc.engine_status()
        self.assertFalse(status["available"])
        self.assertIsNone(status["backend"])
        self.assertIn("aiortc", status["error"])

    def test_start_without_backend_is_engine_unavailable(self):
        result = cc.start_call("peer", signalling=self.sig,
                               backend=None, clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("engine_unavailable", result["reason"])


class TestStartCall(_EngineGate):
    def test_call_reports_id_peer_and_media(self):
        result = cc.start_call("alice", signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        self.assertTrue(result["ok"], result["reason"])
        self.assertTrue(result["call_id"])
        self.assertEqual(result["peer"], "alice")
        self.assertEqual(result["state"], "connected")
        self.assertTrue(result["media"]["in"])
        self.assertTrue(result["media"]["out"])
        self.assertTrue(result["media"]["mic_live"])
        self.assertEqual(len(self.sig.sent), 1)
        self.assertEqual(self.sig.sent[0]["type"], "offer")

    def test_call_is_registered_and_listed(self):
        result = cc.start_call("alice", signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        listed = cc.list_calls()
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["calls"]), 1)
        self.assertEqual(listed["calls"][0]["call_id"],
                         result["call_id"])
        self.assertEqual(listed["calls"][0]["peer"], "alice")

    def test_call_state_reports_media(self):
        result = cc.start_call("alice", signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        state = cc.call_state(result["call_id"],
                              backend=self.backend)
        self.assertTrue(state["ok"])
        self.assertEqual(state["state"], "connected")
        self.assertTrue(state["media"]["mic_live"])

    def test_empty_peer_refused(self):
        result = cc.start_call("   ", signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("peer", result["reason"])

    def test_unknown_kind_refused(self):
        result = cc.start_call("alice", kind="sms",
                               signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("audio", result["reason"])

    def test_video_kind_carried(self):
        result = cc.start_call("alice", kind="video",
                               signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["kind"], "video")


class TestSignalling(_EngineGate):
    def test_unencrypted_transport_refused_before_any_send(self):
        plain = _PlainSignalling()
        result = cc.start_call("alice", signalling=plain,
                               backend=self.backend,
                               clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("not encrypted", result["reason"])
        self.assertEqual(plain.sent, [])

    def test_encrypted_transport_accepted(self):
        enc = _EncryptedSignalling()
        # The fake backend's place() sends an offer then
        # reads the answer: pre-stage one (its call_id is
        # not checked by the fake — only its type).
        enc.stage_answer("any", "alice")
        result = cc.start_call("alice", signalling=enc,
                               backend=self.backend,
                               clock=self.clock)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(enc.sent[0]["type"], "offer")

    def test_no_signalling_refused(self):
        result = cc.start_call("alice", signalling=None,
                               backend=self.backend,
                               clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("signalling transport", result["reason"])

    def test_no_answer_is_a_failure(self):
        class _Silent(_LoopbackSignalling):
            def send(self, msg):
                self.sent.append(dict(msg))
                # Never answer.

        silent = _Silent()
        result = cc.start_call("alice", signalling=silent,
                               backend=self.backend,
                               clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("no answer", result["reason"])
        self.assertEqual(cc.list_calls()["calls"], [])


class TestAnswerCall(_EngineGate):
    def test_answer_before_expiry(self):
        invite = cc.make_invitation("bob", clock=self.clock)
        result = cc.answer_call(invite, signalling=self.sig,
                                backend=self.backend,
                                clock=self.clock)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["call_id"], invite["call_id"])
        self.assertEqual(result["peer"], "bob")
        self.assertTrue(result["media"]["mic_live"])

    def test_expired_invitation_refuses_and_never_answers(self):
        invite = cc.make_invitation("bob", ttl_s=60.0,
                                    clock=self.clock)
        self.clock.t += 61.0
        result = cc.answer_call(invite, signalling=self.sig,
                                backend=self.backend,
                                clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("expired", result["reason"])
        self.assertEqual(self.sig.sent, [])
        self.assertEqual(len(self.backend.calls), 0)

    def test_invitation_without_expiry_refused(self):
        result = cc.answer_call({"call_id": "x", "peer": "bob"},
                                signalling=self.sig,
                                backend=self.backend,
                                clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("expiry", result["reason"])

    def test_invitation_without_peer_refused(self):
        invite = cc.make_invitation("bob", clock=self.clock)
        invite["peer"] = "  "
        result = cc.answer_call(invite, signalling=self.sig,
                                backend=self.backend,
                                clock=self.clock)
        self.assertFalse(result["ok"])

    def test_make_invitation_validation(self):
        with self.assertRaises(ValueError):
            cc.make_invitation("   ", clock=self.clock)
        with self.assertRaises(ValueError):
            cc.make_invitation("bob", ttl_s=0, clock=self.clock)
        with self.assertRaises(ValueError):
            cc.make_invitation("bob", kind="sms",
                               clock=self.clock)


class TestMediaCheck(_EngineGate):
    def test_connected_without_audio_is_a_failure(self):
        silent = _FakeBackend(self.clock, media=False)
        result = cc.start_call("alice", signalling=self.sig,
                               backend=silent,
                               clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("no audio flowed", result["reason"])
        self.assertEqual(cc.list_calls()["calls"], [])
        self.assertEqual(len(silent.closed), 1)

    def test_answer_without_audio_is_a_failure(self):
        silent = _FakeBackend(self.clock, media=False)
        invite = cc.make_invitation("bob", clock=self.clock)
        result = cc.answer_call(invite, signalling=self.sig,
                                backend=silent,
                                clock=self.clock)
        self.assertFalse(result["ok"])
        self.assertIn("no audio flowed", result["reason"])
        self.assertEqual(cc.list_calls()["calls"], [])


class TestHangup(_EngineGate):
    def test_hangup_closes_and_releases(self):
        result = cc.start_call("alice", signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        call_id = result["call_id"]
        key = next(iter(self.backend.calls))
        ended = cc.end_call(call_id, backend=self.backend)
        self.assertTrue(ended["ok"], ended["reason"])
        self.assertFalse(self.backend.is_live(key))
        self.assertIn(key, self.backend.closed)
        self.assertEqual(cc.list_calls()["calls"], [])
        state = cc.call_state(call_id, backend=self.backend)
        self.assertFalse(state["ok"])

    def test_mic_off_after_hangup(self):
        result = cc.start_call("alice", signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        key = next(iter(self.backend.calls))
        self.assertTrue(self.backend.call_media(key)["mic_live"])
        cc.end_call(result["call_id"], backend=self.backend)
        self.assertFalse(
            self.backend.call_media(key)["mic_live"])

    def test_double_hangup_is_an_honest_failure(self):
        result = cc.start_call("alice", signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        self.assertTrue(cc.end_call(result["call_id"],
                                    backend=self.backend)["ok"])
        again = cc.end_call(result["call_id"],
                            backend=self.backend)
        self.assertFalse(again["ok"])
        self.assertIn("already ended", again["reason"])

    def test_hangup_unknown_call_is_an_honest_failure(self):
        result = cc.end_call("no-such-call",
                             backend=self.backend)
        self.assertFalse(result["ok"])
        self.assertIn("no live call", result["reason"])

    def test_failed_hangup_is_reported(self):
        result = cc.start_call("alice", signalling=self.sig,
                               backend=self.backend,
                               clock=self.clock)
        key = next(iter(self.backend.calls))

        class _Leaky(_FakeBackend):
            def end(self, k):
                # Pretend to close but stay live: the wrapper
                # must verify the close and report it.
                pass

        leaky = _Leaky(self.clock)
        leaky.calls = self.backend.calls
        ended = cc.end_call(result["call_id"], backend=leaky)
        self.assertFalse(ended["ok"])
        self.assertIn("still live", ended["reason"])
        self.assertTrue(self.backend.is_live(key))


if __name__ == "__main__":
    unittest.main()
