"""State-mirrors wiring tests (ruled 2026-11-05: display-only,
device-gated, terms-gated, freshness-stamped).

Covers the previously untested mirror module end to end: the
freshness stamp on every snapshot, the product read path
(read_home_mirrors) with its PF-18 hook gate and device
gating, the structural display-only property, the Hue terms
gate, and the build-once map rule. No device exists in the
lab: readers are caller-supplied callables, and the
nothing-connected path is the honest default. Stdlib only;
no Qt needed, but the suite runs on both interpreters per
the batch contract.
"""

import json
import pathlib
import tempfile
import time
import unittest

try:
    from core import w9_wiring as _facade
    _FACADE_AVAILABLE = True
except Exception:  # noqa: BLE001 — PySide6 absent on this interpreter
    _facade = None
    _FACADE_AVAILABLE = False


class MirrorWiringTests(unittest.TestCase):
    """Freshness, device gating, PF-18 blinding, display-only."""

    def setUp(self):
        from core import permission_grants as pg
        from core import state_mirrors as mirrors
        from core import w9_wiring_lan as lan
        self.pg = pg
        self.mirrors = mirrors
        self.lan = lan
        self._tmp = tempfile.TemporaryDirectory()
        # Default: unknown grant (empty grants map) — the hook
        # passes unknown, so reads proceed unless a test
        # rewrites the store to denied/granted.
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        self._store.write_text(json.dumps({
            "version": 1, "grants": {}, "changes": []}),
            encoding="utf-8")
        pg.set_path_override(self._store)

    def tearDown(self):
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def _deny(self):
        self._store.write_text(json.dumps({
            "version": 1,
            "grants": {"home_devices": {"state": "denied"}},
            "changes": []}), encoding="utf-8")

    def _grant(self):
        self._store.write_text(json.dumps({
            "version": 1,
            "grants": {"home_devices": {"state": "granted"}},
            "changes": []}), encoding="utf-8")

    def test_snapshot_carries_freshness(self):
        # INTENTIONAL (state-mirrors wiring, ruled 2026-11-05):
        # every snapshot carries freshness — the stamp is taken
        # at read time, so it must be a float near now.
        before = time.time()
        snap = self.mirrors.OpenRGBMirror(
            "desk", reader=lambda: {"color": "blue"}).snapshot()
        after = time.time()
        self.assertIsInstance(snap.freshness, float)
        self.assertGreaterEqual(snap.freshness, before)
        self.assertLessEqual(snap.freshness, after)
        self.assertEqual(snap.state, {"color": "blue"})
        self.assertTrue(snap.reachable)

    def test_state_map_entries_carry_freshness(self):
        # INTENTIONAL (state-mirrors wiring): the map carries
        # each snapshot's freshness through, not just state.
        built = self.lan.build_lan_state_map([
            {"source": "wled", "device": "strip",
             "reader": lambda: {"on": True}}])
        entry = built["wled:strip"]
        self.assertEqual(entry["state"], {"on": True})
        self.assertTrue(entry["reachable"])
        self.assertIsInstance(entry["freshness"], float)
        self.assertLessEqual(abs(entry["freshness"] - time.time()), 60)

    def test_read_no_specs_nothing_connected(self):
        # INTENTIONAL (state-mirrors wiring, ruled 2026-11-05):
        # no configured mirrors reads as nothing connected —
        # the Permissions-page wording, not an error, and never
        # an invented device.
        result = self.lan.read_home_mirrors()
        self.assertFalse(result["connected"])
        self.assertFalse(result["blinded"])
        self.assertEqual(result["reason"], "Nothing connected yet — "
                         "no home devices have been connected.")
        self.assertEqual(result["snapshots"], {})
        self.assertEqual(self.lan.read_home_mirrors([])["connected"],
                         False)

    def test_read_with_specs_connected(self):
        # INTENTIONAL (state-mirrors wiring): caller-supplied
        # specs with real readers produce stamped snapshots.
        self._grant()
        result = self.lan.read_home_mirrors([
            {"source": "openrgb", "device": "desk",
             "reader": lambda: {"color": "blue"}},
            {"source": "busylight", "device": "lamp",
             "reader": lambda: {"busy": False}}])
        self.assertTrue(result["connected"])
        self.assertFalse(result["blinded"])
        snaps = result["snapshots"]
        self.assertEqual(snaps["openrgb:desk"]["state"],
                         {"color": "blue"})
        self.assertEqual(snaps["busylight:lamp"]["state"],
                         {"busy": False})
        for entry in snaps.values():
            self.assertTrue(entry["reachable"])
            self.assertIsInstance(entry["freshness"], float)

    def test_denied_blinds_and_never_calls_reader(self):
        # INTENTIONAL (state-mirrors wiring, ruled 2026-11-05):
        # a denied home_devices grant blinds mirrors BEFORE any
        # reader runs — no device is contacted, no device state
        # leaks into the result (no names, no states, no error
        # carrying state).
        calls = []
        self._deny()
        result = self.lan.read_home_mirrors([
            {"source": "openrgb", "device": "desk",
             "reader": lambda: calls.append(1) or {"color": "blue"}}])
        self.assertFalse(result["connected"])
        self.assertTrue(result["blinded"])
        self.assertIn("Settings", result["reason"])
        self.assertEqual(result["snapshots"], {})
        self.assertEqual(calls, [])
        self.assertNotIn("desk", result["reason"])
        self.assertNotIn("blue", result["reason"])

    def test_unknown_grant_passes(self):
        # INTENTIONAL (state-mirrors wiring): the PF-18 hook
        # passes unknown (only denied blinds), matching the
        # hook's own contract for a store with no record.
        result = self.lan.read_home_mirrors([
            {"source": "wled", "device": "strip",
             "reader": lambda: {"on": True}}])
        self.assertTrue(result["connected"])
        self.assertFalse(result["blinded"])

    def test_display_only_surface(self):
        # INTENTIONAL (state-mirrors wiring, ruled 2026-11-05):
        # direction is structural — no mirror or map object
        # exposes a set/control/write/toggle/drive callable.
        # snapshot() is the only public method on mirrors.
        classes = [self.mirrors.OpenRGBMirror,
                   self.mirrors.WLEDMirror,
                   self.mirrors.BusylightMirror,
                   self.mirrors.HueMirror]
        banned = ("set_", "control", "write", "toggle", "drive")
        for cls in classes:
            callables = [n for n in dir(cls)
                         if not n.startswith("_")
                         and callable(getattr(cls, n, None))]
            self.assertEqual(callables, ["snapshot"],
                             f"{cls.__name__} exposes {callables}")
            for name in dir(cls):
                self.assertFalse(
                    any(b in name for b in banned),
                    f"{cls.__name__}.{name} looks like a write path")
        map_callables = [n for n in dir(self.mirrors.StateMap)
                         if not n.startswith("_")
                         and callable(getattr(self.mirrors.StateMap,
                                              n, None))]
        self.assertEqual(map_callables, ["build"])

    def test_hue_gate_raises_without_terms(self):
        # INTENTIONAL (state-mirrors wiring): Hue terms are
        # OWED and NOT cleared by this batch — construction
        # without terms_cleared=True still raises, directly
        # and through the product read path.
        with self.assertRaises(self.mirrors.HueTermsNotCleared):
            self.mirrors.HueMirror("living", "localhost")
        with self.assertRaises(self.mirrors.HueTermsNotCleared):
            self.lan.read_home_mirrors([
                {"source": "hue", "device": "living",
                 "host": "localhost"}])

    def test_hue_remote_rejected(self):
        # INTENTIONAL (state-mirrors wiring): remote mode is
        # explicitly NOT implemented — a non-LAN host raises
        # instead of being contacted.
        with self.assertRaises(self.mirrors.RemoteHueRejected):
            self.mirrors.HueMirror("living", "203.0.113.7",
                                   terms_cleared=True)
        with self.assertRaises(self.mirrors.RemoteHueRejected):
            self.lan.read_home_mirrors([
                {"source": "hue", "device": "living",
                 "host": "203.0.113.7", "terms_cleared": True}])

    def test_hue_lan_with_terms_works(self):
        # INTENTIONAL (state-mirrors wiring): LAN + explicit
        # terms clearance is the only Hue path that reads.
        snap = self.mirrors.HueMirror(
            "living", "localhost", reader=lambda: {"on": True},
            terms_cleared=True).snapshot()
        self.assertEqual(snap.state, {"on": True})
        self.assertTrue(snap.reachable)
        self.assertIsInstance(snap.freshness, float)

    def test_build_once(self):
        # INTENTIONAL (state-mirrors wiring): the shared map
        # is built once — a second build raises so two maps
        # can never disagree about what devices reported.
        state_map = self.mirrors.StateMap()
        state_map.build([])
        with self.assertRaises(self.mirrors.StateMapAlreadyBuilt):
            state_map.build([])

    def test_unreachable_reader(self):
        # INTENTIONAL (state-mirrors wiring): an unreadable
        # device is offline, not fatal — reachable False with
        # an empty state, still freshness-stamped.
        def _boom():
            raise RuntimeError("down")

        snap = self.mirrors.WLEDMirror("strip",
                                       reader=_boom).snapshot()
        self.assertFalse(snap.reachable)
        self.assertEqual(snap.state, {})
        self.assertIsInstance(snap.freshness, float)

    @unittest.skipUnless(_FACADE_AVAILABLE,
                         "w9_wiring needs PySide6 (Qt interpreter)")
    def test_facade_reexport(self):
        # INTENTIONAL (state-mirrors wiring, B31 facade rule):
        # the product read path is re-exported by the facade.
        self.assertIs(_facade.read_home_mirrors,
                      self.lan.read_home_mirrors)


if __name__ == "__main__":
    unittest.main()
