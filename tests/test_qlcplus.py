"""R17-7 qlcplus rebuild — stage / venue lighting (Art-Net + sACN).
Fake nodes and fake fixtures only: a scratch SQLite file per test,
loopback UDP receivers, no real rig, no real lights, no money moving.
"""
import json
import os
import queue
import re
import socket
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import stage_lighting as sl
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import _needs_confirmation
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
from core.funnel.tools_gate_free import _FREE_BY_DESIGN
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools

TREE = Path(__file__).resolve().parent.parent


def _artnet_reply(ip="127.0.0.1", universe=17, short="TestNode",
                  long="Test Node Long"):
    """One crafted Art-Net OpPollReply (239 bytes) for the discover
    test. Universe 17 = Net 0, SubUni 1, port universe 1."""
    pkt = bytearray(239)
    pkt[0:8] = b"Art-Net\x00"
    pkt[8:10] = struct.pack("<H", 0x2100)
    pkt[10:14] = bytes(int(p) for p in ip.split("."))
    pkt[14:16] = struct.pack("<H", 6454)
    pkt[18] = 0  # NetSwitch
    pkt[19] = 0x10  # SubSwitch: subuni 1
    pkt[26:26 + len(short)] = short.encode()
    pkt[44:44 + len(long)] = long.encode()
    pkt[190] = universe & 0x0F  # SwOut[0]
    return bytes(pkt)


class _Receiver(threading.Thread):
    """A one-shot loopback UDP receiver. Binds, signals ready, takes
    one packet (or times out), then closes."""

    def __init__(self, port):
        super().__init__(daemon=True)
        self.port = port
        self.ready = threading.Event()
        self.got = queue.Queue()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", self.port))
            self.ready.set()
            sock.settimeout(5.0)
            try:
                data, _addr = sock.recvfrom(4096)
                self.got.put(data)
            except socket.timeout:
                self.got.put(None)
        finally:
            sock.close()


class _FakeArtNetNode(threading.Thread):
    """A fake Art-Net node: listens on 127.0.0.1:6454, answers one
    OpPoll with a crafted OpPollReply sent back to the poller."""

    def __init__(self, reply):
        super().__init__(daemon=True)
        self.reply = reply
        self.ready = threading.Event()
        self.saw_poll = queue.Queue()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", 6454))
            self.ready.set()
            sock.settimeout(5.0)
            try:
                data, addr = sock.recvfrom(4096)
                self.saw_poll.put(data)
                if data[:8] == b"Art-Net\x00":
                    # This sandbox blocks UDP sendto; a connected
                    # socket may send. The poller is the only peer.
                    sock.connect(addr)
                    sock.send(self.reply)
            except socket.timeout:
                self.saw_poll.put(None)
        finally:
            sock.close()


class StageLightingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "stage_lighting.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers ------------------------------------------------------

    def _node(self, name="House", protocol="artnet", host="127.0.0.1",
              universe=1):
        return sl.stage_lighting(
            "node", mode="add", node=name, protocol=protocol, host=host,
            universe=universe, _db_path=self.db)

    def _fixture(self, name="Wash", type="rgb",
                 channels="red:1;green:2;blue:3"):
        return sl.stage_lighting(
            "fixture", mode="add", fixture=name, type=type,
            channels=channels, _db_path=self.db)

    def _patch(self, fixture="Wash", universe=1, address=1):
        return sl.stage_lighting(
            "patch", mode="add", fixture=fixture, universe=universe,
            address=address, _db_path=self.db)

    def _setup_node(self):
        self.assertIn("Node 'House' added", self._node())

    def _setup_patched(self):
        self._setup_node()
        self.assertIn("Fixture 'Wash' added", self._fixture())
        self.assertIn("Patched 'Wash' on universe 1 at address 1",
                      self._patch())

    def _recv(self, port):
        rx = _Receiver(port)
        rx.start()
        self.assertTrue(rx.ready.wait(5.0), "receiver did not bind")
        return rx

    # -- wire formats (pure builders) ----------------------------------

    def test_artnet_packet_bytes(self):
        pkt = sl._artnet_dmx_packet(1, {1: 255, 2: 128}, 7)
        self.assertEqual(pkt[:8], b"Art-Net\x00")
        self.assertEqual(struct.unpack("<H", pkt[8:10])[0], 0x5000)
        self.assertEqual((pkt[10], pkt[11]), (0, 14))
        self.assertEqual(pkt[12], 7)
        self.assertEqual(struct.unpack(">H", pkt[16:18])[0], 4)
        self.assertEqual(list(pkt[18:]), [0, 255, 128, 0])
        # Universe 0x123 splits into Net 1 / SubUni 2 / Universe 3.
        pkt = sl._artnet_dmx_packet(0x123, {512: 200}, 0)
        self.assertEqual((pkt[14], pkt[15]), (2, 1))
        self.assertEqual(struct.unpack(">H", pkt[16:18])[0], 514)
        self.assertEqual(len(pkt), 18 + 514)
        # An empty level map is a minimal all-zero packet, not a crash.
        self.assertEqual(len(sl._artnet_dmx_packet(1, {}, 0)), 22)

    def test_sacn_packet_roundtrip(self):
        for levels in ({1: 255, 2: 128}, {512: 200}, {},
                       {1: 255, 300: 7, 512: 1}):
            pkt = sl._sacn_packet(1, levels, 3)
            back = sl._parse_sacn_packet(pkt)
            self.assertIsNotNone(back, levels)
            self.assertEqual(back["universe"], 1)
            self.assertEqual(back["source"], "VYUK")
            self.assertEqual(back["sequence"], 3)
            nonzero = {k: v for k, v in back["levels"].items() if v}
            self.assertEqual(nonzero, levels)
        # Truncated or garbage packets are not sACN data.
        self.assertIsNone(sl._parse_sacn_packet(
            sl._sacn_packet(1, {1: 1}, 0)[:-5]))
        self.assertIsNone(sl._parse_sacn_packet(b"junk" * 40))
        # The root length covers vector + CID + the whole framing layer.
        pkt = sl._sacn_packet(1, {1: 255, 2: 128}, 0)
        self.assertEqual(
            struct.unpack(">H", pkt[16:18])[0] & 0xFFF, len(pkt) - 18)
        self.assertEqual(pkt[:2], b"\x00\x10")
        self.assertEqual(pkt[4:16], b"ASC-E1.17\x00\x00\x00")

    def test_artnet_poll_reply_parse(self):
        parsed = sl._parse_artnet_poll_reply(_artnet_reply())
        self.assertEqual(parsed["ip"], "127.0.0.1")
        self.assertEqual(parsed["port"], 6454)
        self.assertEqual(parsed["short_name"], "TestNode")
        self.assertEqual(parsed["long_name"], "Test Node Long")
        self.assertEqual(parsed["universe"], 17)
        self.assertIsNone(sl._parse_artnet_poll_reply(b"short"))
        self.assertIsNone(sl._parse_artnet_poll_reply(
            sl._artnet_poll_packet()))  # a poll is not a reply

    # -- nodes / fixtures / patches ------------------------------------

    def test_node_add_list_remove(self):
        self.assertIn("Node 'House' added", self._node())
        self.assertIn("already exists",
                      self._node(name="House"))  # duplicate name refuses
        shown = sl.stage_lighting("node", mode="list", _db_path=self.db)
        self.assertIn("'House': artnet universe 1 via 127.0.0.1", shown)
        self.assertIn("Node 'House' removed",
                      sl.stage_lighting("node", mode="remove", node="House",
                                        _db_path=self.db))
        self.assertIn("No lighting nodes registered yet",
                      sl.stage_lighting("node", mode="list",
                                        _db_path=self.db))
        self.assertIn("does not exist",
                      sl.stage_lighting("node", mode="remove", node="House",
                                        _db_path=self.db))

    def test_node_bad_protocol_universe_refused(self):
        self.assertIn("protocol: 'artnet' or 'sacn'",
                      sl.stage_lighting("node", mode="add", node="X",
                                        protocol="dmx", universe=1,
                                        _db_path=self.db))
        self.assertIn("outside 0-32767",
                      sl.stage_lighting("node", mode="add", node="X",
                                        protocol="artnet", universe=40000,
                                        _db_path=self.db))
        self.assertIn("outside 1-63999",
                      sl.stage_lighting("node", mode="add", node="X",
                                        protocol="sacn", universe=0,
                                        _db_path=self.db))
        self.assertIn("needs a name",
                      sl.stage_lighting("node", mode="add", protocol="artnet",
                                        universe=1, _db_path=self.db))

    def test_fixture_add_list_remove(self):
        self.assertIn("Fixture 'Wash' added", self._fixture())
        self.assertIn("already exists",
                      self._fixture())  # duplicate name refuses
        shown = sl.stage_lighting("fixture", mode="list", _db_path=self.db)
        self.assertIn("'Wash' (rgb, 3 channels): red:1; green:2; blue:3",
                      shown)
        self.assertIn("Fixture 'Wash' removed",
                      sl.stage_lighting("fixture", mode="remove",
                                        fixture="Wash", _db_path=self.db))
        self.assertIn("No fixture definitions yet",
                      sl.stage_lighting("fixture", mode="list",
                                        _db_path=self.db))

    def test_fixture_bad_channels_refused(self):
        self.assertIn("name:offset",
                      sl.stage_lighting("fixture", mode="add", fixture="Bad",
                                        type="rgb", channels="red",
                                        _db_path=self.db))
        self.assertIn("listed twice",
                      sl.stage_lighting("fixture", mode="add", fixture="Bad",
                                        type="rgb",
                                        channels="red:1;red:2",
                                        _db_path=self.db))
        self.assertIn("used twice",
                      sl.stage_lighting("fixture", mode="add", fixture="Bad",
                                        type="rgb",
                                        channels="red:1;green:1",
                                        _db_path=self.db))
        self.assertIn("outside 1-512",
                      sl.stage_lighting("fixture", mode="add", fixture="Bad",
                                        type="rgb", channels="red:513",
                                        _db_path=self.db))

    def test_fixture_remove_refused_when_patched(self):
        self._setup_patched()
        self.assertIn("is patched on universe 1",
                      sl.stage_lighting("fixture", mode="remove",
                                        fixture="Wash", _db_path=self.db))

    def test_patch_add_list_remove(self):
        self._setup_patched()
        shown = sl.stage_lighting("patch", mode="list", _db_path=self.db)
        self.assertIn("'Wash' on universe 1 at address 1 (channels 1-3)",
                      shown)
        self.assertIn("Patch removed",
                      sl.stage_lighting("patch", mode="remove", universe=1,
                                        address=1, _db_path=self.db))
        self.assertIn("No fixtures patched yet",
                      sl.stage_lighting("patch", mode="list",
                                        _db_path=self.db))

    def test_patch_overlap_refused(self):
        self._setup_patched()
        # Wash occupies 1-3; a second fixture at 3 overlaps it.
        self.assertIn("Fixture 'Spot' added",
                      self._fixture(name="Spot", type="dimmer",
                                    channels="dimmer:1"))
        self.assertIn("overlaps 'Wash'",
                      self._patch(fixture="Spot", universe=1, address=3))
        # Address 4 is clear (Wash ends at 3) and patches fine.
        self.assertIn("Patched 'Spot' on universe 1 at address 4",
                      self._patch(fixture="Spot", universe=1, address=4))
        # A different universe never overlaps.
        self.assertIn("Patched 'Spot' on universe 2 at address 1",
                      self._patch(fixture="Spot", universe=2, address=1))

    def test_patch_past_channel_512_refused(self):
        self._setup_node()
        self.assertIn("Fixture 'Wide' added",
                      self._fixture(name="Wide", type="rgb",
                                    channels="red:1;green:2;blue:3"))
        self.assertIn("runs past channel 512",
                      self._patch(fixture="Wide", universe=1, address=511))
        self.assertIn("does not exist",
                      self._patch(fixture="Ghost", universe=1, address=1))

    def test_trigger_fires_on_direct_insert(self):
        self._setup_patched()
        db = sl._Store(self.db)
        conn = db._connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError) as ctx:
                conn.execute(
                    "INSERT INTO patches (fixture, universe, address,"
                    " footprint, created_at)"
                    " VALUES ('Wash', 1, 2, 3, 'x')")
            self.assertIn("overlaps", str(ctx.exception))
        finally:
            db.close()

    # -- output over loopback -------------------------------------------

    def test_output_artnet_loopback(self):
        self._setup_node()
        rx = self._recv(6454)
        out = sl.stage_lighting("output", universe=1, levels="1:255;2:128",
                                _db_path=self.db)
        self.assertIn("Sent 2 channel level(s) to universe 1", out)
        self.assertIn("via node 'House' (artnet", out)
        pkt = rx.got.get(timeout=6.0)
        self.assertIsNotNone(pkt, "no Art-Net packet arrived on loopback")
        self.assertEqual(pkt[:8], b"Art-Net\x00")
        self.assertEqual(struct.unpack("<H", pkt[8:10])[0], 0x5000)
        self.assertEqual(list(pkt[18:]), [0, 255, 128, 0])

    def test_output_sacn_loopback(self):
        self.assertIn("Node 'House' added",
                      self._node(protocol="sacn", universe=1))
        rx = self._recv(5568)
        out = sl.stage_lighting("output", universe=1, levels="1:255;2:128",
                                _db_path=self.db)
        self.assertIn("Sent 2 channel level(s) to universe 1", out)
        self.assertIn("via node 'House' (sacn", out)
        pkt = rx.got.get(timeout=6.0)
        self.assertIsNotNone(pkt, "no sACN packet arrived on loopback")
        back = sl._parse_sacn_packet(pkt)
        self.assertIsNotNone(back)
        self.assertEqual(back["universe"], 1)
        self.assertEqual({k: v for k, v in back["levels"].items() if v},
                         {1: 255, 2: 128})

    def test_blackout_zeroes_all_channels(self):
        self._setup_node()
        rx = self._recv(6454)
        out = sl.stage_lighting("blackout", universe=1, _db_path=self.db)
        self.assertIn("Blackout sent to universe 1", out)
        self.assertIn("all 512 channels zero", out)
        pkt = rx.got.get(timeout=6.0)
        self.assertIsNotNone(pkt, "no blackout packet arrived")
        self.assertEqual(struct.unpack(">H", pkt[16:18])[0], 514)
        self.assertEqual(set(pkt[18:]), {0})

    def test_output_refusals(self):
        self.assertIn("no node is registered for universe 1",
                      sl.stage_lighting("output", universe=1,
                                        levels="1:255", _db_path=self.db))
        self._setup_node()
        self.assertIn("outside 0-255",
                      sl.stage_lighting("output", universe=1,
                                        levels="1:300", _db_path=self.db))
        self.assertIn("outside 1-512",
                      sl.stage_lighting("output", universe=1,
                                        levels="513:1", _db_path=self.db))
        self.assertIn("no levels given",
                      sl.stage_lighting("output", universe=1,
                                        _db_path=self.db))
        self.assertIn("does not exist",
                      sl.stage_lighting("output", universe=1, node="Ghost",
                                        levels="1:255", _db_path=self.db))
        self.assertIn("is for universe 1, not 2",
                      sl.stage_lighting("output", universe=2, node="House",
                                        levels="1:255", _db_path=self.db))

    # -- scenes / chases over loopback -----------------------------------

    def test_scene_save_list_run_delete(self):
        self._setup_node()
        save = sl.stage_lighting("scene", mode="save", scene="Warm",
                                 universe=1, levels="1:255;2:128",
                                 _db_path=self.db)
        self.assertIn("Scene 'Warm' saved for universe 1", save)
        shown = sl.stage_lighting("scene", mode="list", _db_path=self.db)
        self.assertIn("'Warm' (universe 1): 1:255; 2:128", shown)
        rx = self._recv(6454)
        out = sl.stage_lighting("scene", mode="run", scene="Warm",
                                _db_path=self.db)
        self.assertIn("Scene 'Warm' sent to universe 1", out)
        pkt = rx.got.get(timeout=6.0)
        self.assertIsNotNone(pkt, "no scene packet arrived")
        self.assertEqual(list(pkt[18:]), [0, 255, 128, 0])
        self.assertIn("Scene 'Warm' deleted",
                      sl.stage_lighting("scene", mode="delete", scene="Warm",
                                        _db_path=self.db))
        self.assertIn("does not exist",
                      sl.stage_lighting("scene", mode="run", scene="Warm",
                                        _db_path=self.db))

    def test_chase_save_step_wraps(self):
        self._setup_node()
        sl.stage_lighting("scene", mode="save", scene="Warm", universe=1,
                          levels="1:255", _db_path=self.db)
        sl.stage_lighting("scene", mode="save", scene="Cool", universe=1,
                          levels="2:200", _db_path=self.db)
        save = sl.stage_lighting("chase", mode="save", chase="Sweep",
                                 steps="Warm:2000;Cool:2000",
                                 _db_path=self.db)
        self.assertIn("Chase 'Sweep' saved: 2 step(s)", save)
        rx = self._recv(6454)
        out = sl.stage_lighting("chase", mode="step", chase="Sweep",
                                _db_path=self.db)
        self.assertIn("step 1/2: sent scene 'Warm'", out)
        self.assertIn("next step is 'Cool'", out)
        pkt = rx.got.get(timeout=6.0)
        self.assertIsNotNone(pkt, "no chase step-1 packet arrived")
        self.assertEqual(list(pkt[18:]), [0, 255, 0, 0])
        rx = self._recv(6454)
        out = sl.stage_lighting("chase", mode="step", chase="Sweep",
                                _db_path=self.db)
        self.assertIn("step 2/2: sent scene 'Cool'", out)
        self.assertIn("next step is 'Warm'", out)
        pkt = rx.got.get(timeout=6.0)
        self.assertIsNotNone(pkt, "no chase step-2 packet arrived")
        self.assertEqual(list(pkt[18:]), [0, 0, 200, 0])
        # Unknown scene in the steps refuses at save time.
        self.assertIn("does not exist",
                      sl.stage_lighting("chase", mode="save", chase="Bad",
                                        steps="Ghost:1000",
                                        _db_path=self.db))

    # -- discovery ---------------------------------------------------------

    def test_discover_finds_fake_node(self):
        fake = _FakeArtNetNode(_artnet_reply())
        fake.start()
        self.assertTrue(fake.ready.wait(5.0), "fake node did not bind")
        out = sl.stage_lighting("discover", host="127.0.0.1",
                                _db_path=self.db)
        self.assertIn("1 Art-Net node(s) replied", out)
        self.assertIn("127.0.0.1:6454", out)
        self.assertIn("Test Node Long", out)
        self.assertIn("universe 17", out)
        poll = fake.saw_poll.get(timeout=6.0)
        self.assertIsNotNone(poll, "fake node saw no poll")
        self.assertEqual(poll, sl._artnet_poll_packet())

    def test_discover_empty_honest(self):
        out = sl.stage_lighting("discover", host="127.0.0.1",
                                _db_path=self.db)
        self.assertIn("No Art-Net nodes replied", out)
        self.assertIn("sACN has no discovery", out)

    # -- status / events / validation --------------------------------------

    def test_status_shows_state(self):
        self._setup_patched()
        out = sl.stage_lighting("status", _db_path=self.db)
        self.assertIn("1 node(s), 1 fixture(s), 1 patch(es)", out)
        self.assertIn("'House': artnet universe 1 via 127.0.0.1", out)
        self.assertIn("Nothing has been sent yet", out)
        sl.stage_lighting("output", universe=1, levels="1:255",
                          _db_path=self.db)
        out = sl.stage_lighting("status", _db_path=self.db)
        self.assertIn("Last sent", out)
        self.assertIn("output", out)

    def test_events_hash_chain(self):
        self._setup_node()
        db = sl._Store(self.db)
        try:
            ok, err = db.verify_chain()
            self.assertTrue(ok, err)
            out = sl.stage_lighting("events", limit=5, _db_path=self.db)
            self.assertIn("node_add", out)
            self.assertIn("chain OK", out)
            conn = db._connect()
            conn.execute(
                "UPDATE events SET body_json = '{\"tampered\": 1}'"
                " WHERE id = (SELECT MAX(id) FROM events)")
            conn.commit()
            ok, err = db.verify_chain()
            self.assertFalse(ok)
            out = sl.stage_lighting("events", _db_path=self.db)
            self.assertIn("fails verification", out)
        finally:
            db.close()

    def test_validation_refusals(self):
        self.assertIn("needs an action",
                      sl.stage_lighting("teleport", _db_path=self.db))
        self.assertIn("takes a mode",
                      sl.stage_lighting("node", mode="explode",
                                        _db_path=self.db))
        self.assertIn("takes a mode",
                      sl.stage_lighting("scene", mode="explode",
                                        _db_path=self.db))
        self.assertIn("takes a mode",
                      sl.stage_lighting("chase", mode="explode",
                                        _db_path=self.db))
        self.assertIn("does not exist",
                      sl.stage_lighting("scene", mode="run", scene="Ghost",
                                        _db_path=self.db))
        self.assertIn("does not exist",
                      sl.stage_lighting("chase", mode="step", chase="Ghost",
                                        _db_path=self.db))

    # -- wiring ---------------------------------------------------------

    def test_census(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 764)  # INTENTIONAL — R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2102)  # INTENTIONAL — R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        snap = json.loads((TREE / "tests" /
                           "_classification_snapshot.json").read_text())
        self.assertEqual(len(snap), 764)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(snap["stage_lighting"],
                         {"confirm": True, "free": None, "read": False})

    def test_gate_matrix(self):
        for action, args in (
                ("output", {}), ("blackout", {}),
                ("scene", {"mode": "run"}), ("chase", {"mode": "step"})):
            self.assertTrue(_needs_confirmation(
                "stage_lighting", {"action": action, **args}),
                (action, args))
        for action, args in (
                ("node", {"mode": "add"}), ("node", {"mode": "list"}),
                ("patch", {"mode": "add"}), ("fixture", {"mode": "add"}),
                ("scene", {"mode": "save"}), ("scene", {"mode": "list"}),
                ("scene", {}), ("chase", {"mode": "save"}),
                ("discover", {}), ("status", {}), ("events", {})):
            self.assertFalse(_needs_confirmation(
                "stage_lighting", {"action": action, **args}),
                (action, args))
        # Unknown or missing action gates fail-closed.
        self.assertTrue(_needs_confirmation("stage_lighting",
                                            {"action": "bogus"}))
        self.assertTrue(_needs_confirmation("stage_lighting", {"action": ""}))
        self.assertIn("stage_lighting", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("stage_lighting", _FREE_BY_DESIGN)

    def test_gate_free_and_readclass(self):
        for action in ("status", "events", "discover"):
            self.assertTrue(_is_read_action(
                "stage_lighting", {"action": action}), action)
        for action, mode in (("node", "list"), ("patch", "list"),
                             ("fixture", "list"), ("scene", "list"),
                             ("chase", "list")):
            self.assertTrue(_is_read_action(
                "stage_lighting", {"action": action, "mode": mode}),
                (action, mode))
        for action, args in (
                ("node", {"mode": "add"}), ("output", {}),
                ("blackout", {}), ("scene", {"mode": "run"}),
                ("chase", {"mode": "step"})):
            self.assertFalse(_is_read_action(
                "stage_lighting", {"action": action, **args}),
                (action, args))

    def test_catalogue_membership(self):
        self.assertIn("stage_lighting", GROUP_MEMBERS["Hands & eyes"])
        text = (TREE / "core" / "catalogue_groups_data.py").read_text()
        self.assertIn('"game_server", "stage_lighting", "drag_mouse"', text)

    def test_audit_totals(self):
        text = (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text()
        totals = json.loads(re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 764)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(totals["read"], 384)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        self.assertEqual(totals["effectful"], 380)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(totals["confirm_gated"], 169)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 157 -> 158; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        self.assertEqual(totals["already_verified_in_handler"], 314)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        counts = json.loads(re.search(
            r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(counts["desktop_control"], 67)  # INTENTIONAL — Click-anywhere (2026-11-14): 66 -> 67 — click_anywhere joins desktop_control
        row = next(line for line in text.splitlines()
                   if line.startswith("| desktop_control |"))
        self.assertIn("| 67 | 55 |", row)  # INTENTIONAL — Click-anywhere (2026-11-14): 66|54 -> 67|55 — click_anywhere joins desktop_control (verified in handler)


if __name__ == "__main__":
    unittest.main()
