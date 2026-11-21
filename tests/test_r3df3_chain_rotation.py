"""Round-3 section D fix queue item D-F3: chain rotation empties
the Activity feed silently.

Rotation (2 MiB / 90 days) archives the whole live chain to
data/records_archive/ and starts a fresh live file whose first
entry is a checkpoint (kind "checkpoint") naming the archived
file and how many entries moved. The feed only ever read kind
"activity" rows, so after a rotation it showed the entries since
the rotation and nothing about the history it still holds. The
feed now renders one "older entries archived" marker per
checkpoint in the live file (source "observability", kind
"archived"), naming the archive file and the entry count; when
the named archive is no longer on disk the marker says so
(kind "degraded") instead of pointing at a missing file. The
checkpoint lives in the chain itself, so the marker survives
restarts — it is not per-process sticky state. Rotation itself,
the 2 MiB / 90-day bounds, and the C3-F1 verify/append shapes
are unchanged.
"""
import json
import pathlib
import tempfile
import unittest

from core import activity_feed as af
from core import audit_chain as ac
from core import record_family as rf


def _archived_markers(rows):
    return [r for r in rows
            if r.get("source") == "observability"
            and r.get("kind") in ("archived", "degraded")
            and "archiv" in (r.get("title") or "").lower()]


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        ac.configure(data_dir=self.tmp, max_bytes=6000)
        rf.configure(data_dir=self.tmp)
        self.addCleanup(ac.reset_configuration)
        self.addCleanup(rf.reset_configuration)

    def _grow(self, count=30, prefix="seed activity"):
        """Append `count` activity entries (~280 B each); at a
        6,000-byte cap exactly one rotation fires, archiving
        the first ~19 entries behind a checkpoint."""
        for i in range(count):
            rf.log_activity_human(f"{prefix} {i:03d} "
                                  "— doing a thing that happened")

    def _archives(self):
        arch = self.tmp / "records_archive"
        return sorted(arch.glob("*.jsonl")) if arch.is_dir() else []

    def _checkpoints(self):
        return ac.read(kind="checkpoint", limit=0)

    def _live_entries(self):
        path = self.tmp / "records.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines()
                if line.strip()]


class RotationMarkerTests(_Base):
    """The marker itself: presence, content, survival."""

    def test_one_rotation_one_marker(self):
        self._grow()
        self.assertEqual(len(self._archives()), 1)
        self.assertEqual(len(self._checkpoints()), 1)
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _archived_markers(rows)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].get("kind"), "archived")
        self.assertEqual(markers[0].get("source"), "observability")

    def test_marker_names_archive_and_count(self):
        self._grow()
        ckpt = self._checkpoints()[0]
        cdata = ckpt.get("data") or {}
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        title = _archived_markers(rows)[0].get("title", "")
        self.assertIn(str(cdata.get("archived_file")), title)
        self.assertIn(str(cdata.get("archived_entries")), title)

    def test_marker_ts_is_rotation_time(self):
        self._grow()
        ckpt = self._checkpoints()[0]
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        marker = _archived_markers(rows)[0]
        self.assertEqual(marker.get("ts"), ckpt.get("ts"))

    def test_marker_survives_limit(self):
        self._grow()
        rows = af.read_unified(limit=1, data_dir=self.tmp)
        self.assertEqual(len(_archived_markers(rows)), 1)
        # Limit 1: one content row plus the marker, never just
        # the content row with the marker truncated away.
        self.assertEqual(len(rows), 2)

    def test_two_rotations_one_marker_naming_newest(self):
        self._grow()
        for i in range(3):
            rf.log_activity_human(f"between rotations {i}")
        ac.rotate_now()
        for i in range(2):
            rf.log_activity_human(f"after second rotation {i}")
        self.assertEqual(len(self._archives()), 2)
        # The first rotation's checkpoint was archived with its
        # live file, so the live file holds only the newest one.
        self.assertEqual(len(self._checkpoints()), 1)
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _archived_markers(rows)
        self.assertEqual(len(markers), 1)
        newest = self._checkpoints()[0].get("data") or {}
        self.assertIn(str(newest.get("archived_file")),
                      markers[0].get("title", ""))
        self.assertIn(str(newest.get("archived_entries")),
                      markers[0].get("title", ""))

    def test_missing_archive_is_degraded(self):
        self._grow()
        for p in self._archives():
            p.unlink()
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _archived_markers(rows)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].get("kind"), "degraded")
        self.assertIn("no longer", markers[0].get("title", ""))

    def test_no_rotation_no_marker(self):
        for i in range(3):
            rf.log_activity_human(f"control activity {i}")
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        self.assertEqual(_archived_markers(rows), [])

    def test_checkpoint_not_an_activity_row(self):
        self._grow()
        for i in range(3):
            rf.log_activity_human(f"after rotation {i}")
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        activity = [r for r in rows if r.get("kind") == "activity"]
        live_activity = [e for e in self._live_entries()
                         if e.get("kind") == "activity"]
        self.assertEqual(len(activity), len(live_activity))
        self.assertTrue(all(r.get("title") for r in activity))


class MarkerShapeTests(_Base):
    """Marker shape + the restart property that motivated it."""

    def test_marker_detail_matches_title(self):
        self._grow()
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        marker = _archived_markers(rows)[0]
        self.assertEqual(marker.get("detail"), marker.get("title"))
        self.assertTrue(marker.get("ts"))

    def test_marker_survives_restart(self):
        # The rotation happened in "an earlier run": drop all
        # in-process configuration and re-read the same dir
        # from scratch. The marker comes from the checkpoint
        # on disk, not from per-process state, so it is still
        # there.
        self._grow()
        ac.reset_configuration()
        rf.reset_configuration()
        ac.configure(data_dir=self.tmp, max_bytes=6000)
        rf.configure(data_dir=self.tmp)
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        self.assertEqual(len(_archived_markers(rows)), 1)


class ChainBehaviourUnchangedTests(_Base):
    """Rotation, bounds, verify and append are untouched."""

    def test_verify_still_covers_archive(self):
        self._grow()
        for i in range(3):
            rf.log_activity_human(f"after rotation {i}")
        self.assertTrue(ac.verify_chain())
        detail = ac.verify_chain_detail()
        arch_lines = sum(
            1 for p in self._archives()
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip())
        self.assertEqual(detail.get("checked"),
                         arch_lines + len(self._live_entries()))

    def test_partial_tear_appends(self):
        self._grow()
        path = self.tmp / "records.jsonl"
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw[:len(raw) - 20], encoding="utf-8")
        self.assertTrue(rf.log_activity_human("after a torn line"))
        self.assertFalse(ac.verify_chain_detail().get("ok"))

    def test_destroyed_chain_refuses(self):
        self._grow()
        path = self.tmp / "records.jsonl"
        path.write_text("{{{not json\nmore garbage\n", encoding="utf-8")
        self.assertEqual(rf.log_activity_human("on a destroyed "
                                               "chain"), "")
        self.assertFalse(ac.verify_chain_detail().get("ok"))

    def test_rotation_bounds_unchanged(self):
        self.assertEqual(ac.DEFAULT_MAX_BYTES, 2 * 1024 * 1024)
        self.assertEqual(ac.DEFAULT_MAX_AGE_DAYS, 90)


if __name__ == "__main__":
    unittest.main()
