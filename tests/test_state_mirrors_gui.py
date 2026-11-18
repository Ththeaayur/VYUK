"""State-mirrors GUI display tests (ruled 2026-11-05: display-only,
device-gated, terms-gated, freshness-stamped).

The Permissions page's home_devices section gains a device-state
row fed ONLY through the read_home_mirrors product entry (PF-18
hook gate FIRST inside it) — never through the builder directly,
never past a denied grant. The row shows state and freshness age,
never a set/control/write/toggle/drive affordance, and degrades
to the honest-empty message for blinded / not-connected /
failed reads. No device exists in the lab: tests inject a
mirror_reader returning read_home_mirrors-shaped results, and
the default production path (real entry, no specs) reads
honest-empty. Qt tests skip on interpreters without PySide6,
following the programme's standing optional-dependency skips.
"""

import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import permission_grants as pg  # noqa: E402

try:
    from gui.pf_permissions import (  # noqa: E402
        _device_lines,
        _format_device_age,
        _summarise_device_state,
        _EMPTY_DEVICES_MESSAGE,
    )
    from PySide6.QtWidgets import QApplication
    from gui.pf_permissions import PermissionsWidget
    _QT = True
except ImportError:
    _QT = False
    # Fallback only so module level binds on Qt-less
    # interpreters; every test class below is skipped there.
    _EMPTY_DEVICES_MESSAGE = ("Nothing connected yet — no home devices "
                              "have been connected.")

EMPTY = _EMPTY_DEVICES_MESSAGE


def _connected(snapshots):
    return {"connected": True, "blinded": False, "reason": "",
            "snapshots": snapshots}


@unittest.skipUnless(_QT, "Qt unavailable")
class DeviceLineTests(unittest.TestCase):
    """Pure formatting: age, summary, degradation. No Qt needed
    to reason about, but the helpers live in the Qt page module,
    so this class follows the same Qt-optional skip as the
    widget tests below."""

    def test_age_seconds_minutes_hours(self):
        # INTENTIONAL (mirrors GUI): a stamp always shows as
        # an age, so staleness is visible, never implied live.
        now = 1_000_000.0
        self.assertEqual(_format_device_age(now - 5, now=now),
                         "read 5s ago")
        self.assertEqual(_format_device_age(now - 90, now=now),
                         "read 2m ago")
        self.assertEqual(_format_device_age(now - 7200, now=now),
                         "read 2.0h ago")

    def test_age_missing_or_unusable_is_none(self):
        # INTENTIONAL (mirrors GUI): an unstamped snapshot is
        # labelled unknown downstream, never given a fake age.
        self.assertIsNone(_format_device_age(None))
        self.assertIsNone(_format_device_age("not-a-time"))
        self.assertIsNone(_format_device_age(float("nan")))

    def test_age_future_clamps_to_zero(self):
        # INTENTIONAL (mirrors GUI): clock skew must not make
        # a future stamp read as live-forever; it reads now.
        now = 1_000_000.0
        self.assertEqual(_format_device_age(now + 500, now=now),
                         "read 0s ago")

    def test_state_summary_sorted_and_truncated(self):
        # INTENTIONAL (mirrors GUI): keys sorted for a stable
        # row; truncation marked with an ellipsis, never silent.
        self.assertEqual(
            _summarise_device_state({"b": 2, "a": 1}), "a: 1, b: 2")
        self.assertEqual(_summarise_device_state({}), "")
        self.assertEqual(_summarise_device_state(None), "")
        long_state = {"k": "x" * 300}
        summary = _summarise_device_state(long_state)
        self.assertTrue(summary.endswith("…"))
        self.assertLessEqual(len(summary), 200)

    def test_connected_lines_carry_state_and_age(self):
        # INTENTIONAL (mirrors GUI): a connected device shows
        # its state AND its freshness age on the same row.
        now = time.time()
        lines = _device_lines(_connected({
            "wled:desk": {"state": {"on": True}, "reachable": True,
                          "freshness": now - 5},
        }))
        self.assertEqual(len(lines), 1)
        self.assertIn("wled:desk", lines[0])
        self.assertIn("on: True", lines[0])
        self.assertIn("ago", lines[0])

    def test_unreachable_never_presents_state_as_current(self):
        # INTENTIONAL (mirrors GUI): an unreachable device is
        # labelled unreachable; its state is never shown as
        # current, even if the snapshot carries stale values.
        lines = _device_lines(_connected({
            "wled:desk": {"state": {"on": True}, "reachable": False,
                          "freshness": time.time()},
        }))
        self.assertEqual(len(lines), 1)
        self.assertIn("unreachable", lines[0])
        self.assertNotIn("on: True", lines[0])

    def test_missing_freshness_labelled_not_live(self):
        # INTENTIONAL (mirrors GUI): no stamp means the row
        # says freshness unknown, never presents the state
        # as a live reading.
        lines = _device_lines(_connected({
            "openrgb:case": {"state": {"mode": "static"},
                             "reachable": True},
        }))
        self.assertEqual(len(lines), 1)
        self.assertIn("freshness unknown", lines[0])
        self.assertIn("not shown as live", lines[0])

    def test_blinded_not_connected_malformed_all_degrade_empty(self):
        # INTENTIONAL (mirrors GUI): blinded, not-connected,
        # malformed, and empty results all degrade to the
        # honest-empty message — never a fabricated list.
        cases = [
            {"connected": False, "blinded": True, "reason": "off",
             "snapshots": {}},
            {"connected": False, "blinded": False,
             "reason": "nothing", "snapshots": {}},
            {"connected": True, "blinded": False, "reason": "",
             "snapshots": {}},
            None,
            "not-a-result",
        ]
        for result in cases:
            with self.subTest(result=result):
                self.assertEqual(_device_lines(result), [EMPTY])

    def test_blinded_result_with_snapshots_still_shows_nothing(self):
        # INTENTIONAL (mirrors GUI): the gate wins over the
        # payload — even a blinded result carrying snapshots
        # (which the real entry never returns) shows no state.
        lines = _device_lines({
            "connected": False, "blinded": True, "reason": "off",
            "snapshots": {"wled:desk": {"state": {"on": True},
                                        "reachable": True,
                                        "freshness": time.time()}},
        })
        self.assertEqual(lines, [EMPTY])
        self.assertNotIn("on: True", lines[0])


@unittest.skipUnless(_QT, "Qt unavailable")
class DeviceRowWidgetTests(unittest.TestCase):
    """Qt render: the row on the real Permissions page."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")

    def tearDown(self):
        pg.clear_path_override()
        self._tmp.cleanup()

    def _labels(self, widget):
        return [label.text() for label in widget._device_labels]

    def test_connected_states_render_on_page(self):
        # INTENTIONAL (mirrors GUI): connected devices render
        # as rows with state and freshness age, and the
        # honest-empty message is gone.
        now = time.time()
        widget = PermissionsWidget(mirror_reader=lambda specs: _connected({
            "wled:desk": {"state": {"on": True}, "reachable": True,
                          "freshness": now - 5},
            "openrgb:case": {"state": {"mode": "static"},
                             "reachable": True,
                             "freshness": now - 90},
        }))
        labels = self._labels(widget)
        self.assertEqual(len(labels), 2)
        self.assertTrue(any("wled:desk" in t and "on: True" in t
                            for t in labels))
        self.assertTrue(any("openrgb:case" in t and "ago" in t
                            for t in labels))
        self.assertNotIn(EMPTY, labels)

    def test_honest_empty_when_nothing_connected(self):
        # INTENTIONAL (mirrors GUI): no specs / no devices
        # keeps the existing honest-empty message, unchanged.
        widget = PermissionsWidget(
            mirror_reader=lambda specs: {
                "connected": False, "blinded": False,
                "reason": EMPTY, "snapshots": {}})
        self.assertEqual(self._labels(widget), [EMPTY])

    def test_denied_gate_shows_nothing_past_it(self):
        # INTENTIONAL (mirrors GUI): a blinded read shows no
        # device state at all — the gate wins, nothing leaks.
        widget = PermissionsWidget(
            mirror_reader=lambda specs: {
                "connected": False, "blinded": True,
                "reason": "off", "snapshots": {}})
        labels = self._labels(widget)
        self.assertEqual(labels, [EMPTY])

    def test_raising_reader_degrades_without_crash(self):
        # INTENTIONAL (mirrors GUI): a failing reader degrades
        # to the honest-empty message; Settings never crashes.
        def _boom(specs):
            raise RuntimeError("reader down")
        widget = PermissionsWidget(mirror_reader=_boom)
        self.assertEqual(self._labels(widget), [EMPTY])

    def test_default_production_path_reads_honest_empty(self):
        # INTENTIONAL (mirrors GUI): the default path (real
        # entry, no specs) reads honest-empty today — no
        # configured devices, no fabricated list.
        widget = PermissionsWidget()
        self.assertEqual(self._labels(widget), [EMPTY])

    def test_refresh_rereads_after_store_refresh(self):
        # INTENTIONAL (mirrors GUI): the row re-reads on every
        # page refresh, so a flipped permission cannot leave
        # a stale device's state on screen.
        holder = {"result": {"connected": False, "blinded": False,
                             "reason": EMPTY, "snapshots": {}}}
        widget = PermissionsWidget(
            mirror_reader=lambda specs: holder["result"])
        self.assertEqual(self._labels(widget), [EMPTY])
        holder["result"] = _connected({
            "wled:desk": {"state": {"on": True}, "reachable": True,
                          "freshness": time.time()},
        })
        widget._refresh_from_store()
        labels = self._labels(widget)
        self.assertEqual(len(labels), 1)
        self.assertIn("wled:desk", labels[0])

    def test_device_rows_do_not_overlap(self):
        # INTENTIONAL (mirrors GUI, regression pin): a nested
        # container widget painted all device labels on top
        # of each other — the card layout does not propagate
        # a nested widget's height. Labels inserted directly
        # into the card layout must stack without overlap.
        from PySide6.QtWidgets import QApplication
        now = time.time()
        widget = PermissionsWidget(mirror_reader=lambda specs: _connected({
            "wled:desk": {"state": {"on": True}, "reachable": True,
                          "freshness": now - 5},
            "openrgb:case": {"state": {"mode": "static"},
                             "reachable": True,
                             "freshness": now - 90},
            "busylight:door": {"state": {"on": True},
                               "reachable": False,
                               "freshness": now},
        }))
        widget.resize(640, 900)
        widget.show()
        QApplication.processEvents()
        ordered = sorted(widget._device_labels,
                         key=lambda label: label.geometry().top())
        self.assertEqual(len(ordered), 3)
        for lower, upper in zip(ordered, ordered[1:]):
            self.assertLessEqual(lower.geometry().bottom(),
                                 upper.geometry().top())
        widget.close()

    def test_unreachable_and_unstamped_render_honestly(self):
        # INTENTIONAL (mirrors GUI): unreachable and unstamped
        # snapshots render their honest labels on the page.
        widget = PermissionsWidget(mirror_reader=lambda specs: _connected({
            "wled:desk": {"state": {"on": True}, "reachable": False,
                          "freshness": time.time()},
            "openrgb:case": {"state": {"mode": "static"},
                             "reachable": True},
        }))
        labels = self._labels(widget)
        self.assertTrue(any("unreachable" in t for t in labels))
        self.assertTrue(any("freshness unknown" in t for t in labels))
        self.assertFalse(any("on: True" in t for t in labels))


if __name__ == "__main__":
    unittest.main()
