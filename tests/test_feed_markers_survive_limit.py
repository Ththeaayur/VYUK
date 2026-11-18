"""Field-check fix #26 (round-2 item 6, T8 #24 PARTIAL) — marker rows
survive the feed limit.

Fix #16's "unavailable" markers and fix #2's observability markers
were produced correctly, but read_unified sorted undated markers last
and returned entries[:limit]: on a busy feed the marker fell below the
default limit of 50 and the user saw nothing — the exact round-1 FAIL
signature, reproduced in round 2 (garbage records.jsonl + 60 log lines
-> 50 rows, 0 markers; limit 200 -> the marker). The limit now bounds
dated content, never markers: the return is the newest `limit` entries
plus any marker row that fell below the cut, appended after them in
sorted order. A marker already inside the cut stays where the sort
put it; the first `limit` rows are byte-identical to the old cut.
"""
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    import types as _types_mod

    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

from core import activity_feed as af  # noqa: E402
from core import audit_chain as chain_mod  # noqa: E402
from core import cost_ledger as cost_mod  # noqa: E402
from core import record_family as rf_mod  # noqa: E402
from core import tool_receipts as receipts_mod  # noqa: E402

try:
    from PySide6.QtCore import Qt as _Qt  # noqa: F401
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None

OLD_EPOCH = 1577836800.0  # 2020-01-01T00:00:00Z


def setUpModule():
    """Mirror test_c7_dialog_crop's voice_assistant.* alias."""
    global _APP, _VA_SNAPSHOT
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    import types as _types_mod

    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


def _unavailable(rows):
    return [r for r in rows if r.get("kind") == "unavailable"]


def _observability(rows):
    return [r for r in rows if r.get("source") == "observability"]


def _log_lines(tmp, count=60):
    """`count` dated log lines, one per minute, tool_00..tool_59."""
    lines = []
    for i in range(count):
        lines.append(f"2026-01-01T00:{i:02d}:00 | tool_{i:02d} | success\n")
    (tmp / "activity.log").write_text("".join(lines), encoding="utf-8")


class _FeedBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        chain_mod.configure(data_dir=self.tmp)
        rf_mod.configure(data_dir=self.tmp)
        self.addCleanup(rf_mod.reset_configuration)
        self.addCleanup(chain_mod.reset_configuration)
        receipts_mod.reset_receipt_degraded()
        cost_mod.reset_live_record_degraded()
        self.addCleanup(receipts_mod.reset_receipt_degraded)
        self.addCleanup(cost_mod.reset_live_record_degraded)

    def tearDown(self):
        self._tmp.cleanup()


class MarkerSurvivesLimitTests(_FeedBase):
    """The round-2 measured shape: garbage chain + 60 log lines."""

    def _busy_broken_feed(self):
        (self.tmp / "records.jsonl").write_text("{{{not json\n",
                                                encoding="utf-8")
        _log_lines(self.tmp, 60)

    def test_unavailable_marker_survives_limit_50(self):
        self._busy_broken_feed()
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _unavailable(rows)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].get("source"), "activity")
        self.assertEqual(len(rows), 51)

    def test_marker_text_unchanged(self):
        self._busy_broken_feed()
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _unavailable(rows)
        self.assertEqual(len(markers), 1)
        self.assertIn("unavailable", markers[0]["title"].lower())
        self.assertIn("records", markers[0]["title"].lower())

    def test_dated_order_unchanged(self):
        # The first 50 rows are exactly the 50 newest log rows, in
        # the same order as the old cut — the fix may not reorder
        # or drop dated content to make room for the marker.
        self._busy_broken_feed()
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        expected = [f"tool_{i:02d}: success" for i in range(59, 9, -1)]
        self.assertEqual([r.get("title") for r in rows[:50]], expected)

    def test_marker_still_sorts_last_on_busy_feed(self):
        self._busy_broken_feed()
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _unavailable(rows)
        self.assertEqual(len(markers), 1)
        self.assertIs(rows[-1], markers[0])

    def test_limit_zero_still_empty(self):
        self._busy_broken_feed()
        self.assertEqual(af.read_unified(limit=0, data_dir=self.tmp), [])

    def test_healthy_feed_unchanged(self):
        rf_mod.log_activity_human("t8 healthy line")
        (self.tmp / "activity.log").write_text(
            "2026-01-01T00:00:00 | get_current_time_date | success\n",
            encoding="utf-8")
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        self.assertEqual(len(rows), 2)
        self.assertEqual(_unavailable(rows), [])
        self.assertEqual(_observability(rows), [])

    def test_missing_sources_still_silent(self):
        _log_lines(self.tmp, 60)
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        self.assertEqual(_unavailable(rows), [])
        self.assertEqual(_observability(rows), [])
        self.assertEqual(len(rows), 50)


class DegradedMarkerSurvivesLimitTests(_FeedBase):
    """T7 #19/#20: an observability marker whose last failure is
    old enough to sort below the cut is truncated the same way."""

    def test_old_receipts_marker_survives_limit_50(self):
        _log_lines(self.tmp, 60)
        receipts_mod._note_failure("attempt", RuntimeError("store down"))
        receipts_mod._failures["attempt"]["last_at"] = OLD_EPOCH
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _observability(rows)
        self.assertEqual(len(markers), 1)
        self.assertIn("Receipts degraded", markers[0]["title"])
        self.assertEqual(len(rows), 51)

    def test_old_cost_marker_survives_limit_50(self):
        _log_lines(self.tmp, 60)
        cost_mod.note_live_record_failure(RuntimeError("ledger down"))
        cost_mod._LIVE_RECORD_FAILURES["live"]["last_at"] = OLD_EPOCH
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _observability(rows)
        self.assertEqual(len(markers), 1)
        self.assertIn("Cost ledger degraded", markers[0]["title"])
        self.assertEqual(len(rows), 51)

    def test_recent_degraded_marker_stays_first(self):
        # A degraded marker with a recent failure is already
        # inside the cut — the fix must not move it.
        _log_lines(self.tmp, 60)
        receipts_mod._note_failure("attempt", RuntimeError("store down"))
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _observability(rows)
        self.assertEqual(len(markers), 1)
        self.assertIs(rows[0], markers[0])

    def test_two_markers_both_survive(self):
        # Garbage chain AND an old receipts failure: both markers
        # ride along after the cut.
        (self.tmp / "records.jsonl").write_text("{{{not json\n",
                                                encoding="utf-8")
        _log_lines(self.tmp, 60)
        receipts_mod._note_failure("attempt", RuntimeError("store down"))
        receipts_mod._failures["attempt"]["last_at"] = OLD_EPOCH
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        self.assertEqual(len(_unavailable(rows)), 1)
        self.assertEqual(len(_observability(rows)), 1)
        self.assertEqual(len(rows), 52)
        self.assertEqual(rows[-1].get("kind"), "unavailable")


if __name__ == "__main__":
    unittest.main()
