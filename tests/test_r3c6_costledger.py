"""Round-3 section C fix queue item 6 (C3-F6) — cost-ledger
write failures swallowed silently.

The Live cost hook has marked a lost row since field-check fix #2
(T7 #20): a log line plus a sticky degraded record the Activity
feed surfaces. The local and cloud-text record sites guarded
their writes with a bare `except: pass` — a cost row lost to a
corrupt ledger file vanished with no marker and no log line.
This suite pins the C3-F6 fix: the same shape (log + sticky
degraded state, keyed by brain) at all three record sites, with
the turn still completing and the guard never raising.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import cost_ledger as cost_mod

GARBAGE = (b"\x00\x01\x02GARBAGE-NOT-SQLITE {{{{\nno equals here\n"
           b"[unclosed" * 8)


def _make_ledger(path: Path) -> None:
    cost_mod.CostLedger(db_path=path).record_turn(
        "local", "llama-3.1-8b-instant", 10, 5)


def _corrupt(path: Path) -> None:
    path.write_bytes(GARBAGE)


class _DegradedReset(unittest.TestCase):
    def setUp(self):
        cost_mod.reset_live_record_degraded()
        self.addCleanup(cost_mod.reset_live_record_degraded)


class TestNoteRecordFailure(_DegradedReset):
    def test_note_records_per_brain(self):
        cost_mod.note_record_failure("local",
                                     RuntimeError("ledger down"))
        state = cost_mod.live_record_degraded_state()
        self.assertIn("local", state)
        self.assertEqual(state["local"]["count"], 1)
        self.assertIn("ledger down", state["local"]["last_error"])
        cost_mod.note_record_failure("local",
                                     RuntimeError("ledger down"))
        self.assertEqual(
            cost_mod.live_record_degraded_state()["local"]["count"], 2)

    def test_note_live_still_records_under_live(self):
        # The Live hook (and the existing tests) call the old
        # name — it must keep recording under "live".
        cost_mod.note_live_record_failure(RuntimeError("ledger down"))
        state = cost_mod.live_record_degraded_state()
        self.assertIn("live", state)
        self.assertEqual(state["live"]["count"], 1)

    def test_degraded_state_is_a_copy(self):
        cost_mod.note_record_failure("local",
                                     RuntimeError("ledger down"))
        state = cost_mod.live_record_degraded_state()
        state["local"]["count"] = 99
        self.assertEqual(
            cost_mod.live_record_degraded_state()["local"]["count"], 1)

    def test_empty_when_healthy(self):
        self.assertEqual(cost_mod.live_record_degraded_state(), {})


class TestFeedMarkersPerBrain(_DegradedReset):
    def test_markers_name_the_brain(self):
        from core.activity_feed import read_unified

        cost_mod.note_record_failure("local",
                                     RuntimeError("file is not a database"))
        cost_mod.note_record_failure(
            "cloud_text", RuntimeError("file is not a database"))
        with tempfile.TemporaryDirectory() as tmp:
            entries = read_unified(limit=50, data_dir=Path(tmp))
        markers = [e for e in entries
                   if e.get("source") == "observability"]
        cost_markers = [m for m in markers
                        if m["title"].startswith("Cost ledger degraded")]
        self.assertEqual(len(cost_markers), 2)
        titles = [m["title"] for m in cost_markers]
        self.assertTrue(any("1 Local cost row(s) lost" in t
                            for t in titles))
        self.assertTrue(any("1 Cloud text cost row(s) lost" in t
                            for t in titles))
        self.assertTrue(all("file is not a database" in t
                            for t in titles))
        self.assertEqual({m.get("kind") for m in cost_markers},
                         {"degraded"})

    def test_live_marker_unchanged(self):
        from core.activity_feed import read_unified

        cost_mod.note_live_record_failure(RuntimeError("ledger down"))
        with tempfile.TemporaryDirectory() as tmp:
            entries = read_unified(limit=50, data_dir=Path(tmp))
        titles = [e["title"] for e in entries
                  if e.get("source") == "observability"]
        self.assertEqual(len(titles), 1)
        self.assertIn("1 Live cost row(s) lost", titles[0])
        self.assertIn("ledger down", titles[0])

    def test_no_markers_when_healthy(self):
        from core.activity_feed import read_unified

        with tempfile.TemporaryDirectory() as tmp:
            entries = read_unified(limit=50, data_dir=Path(tmp))
        self.assertEqual(
            [e for e in entries
             if e.get("source") == "observability"], [])


class _RecordSiteBase(_DegradedReset):
    """Drive the real record sites against a temp ledger file
    (VYUK_COST_LEDGER_PATH), with the log line captured instead
    of written to the tree's data/activity.log."""

    def _reset_singletons(self):
        from core.voice import local_client_cost as local_mod
        from core.voice import cloud_text_helpers as cloud_mod

        real_local = local_mod._COST_LEDGER
        real_cloud = cloud_mod._COST_LEDGER
        local_mod._COST_LEDGER = None
        cloud_mod._COST_LEDGER = None
        self.addCleanup(lambda: setattr(local_mod, "_COST_LEDGER",
                                        real_local))
        self.addCleanup(lambda: setattr(cloud_mod, "_COST_LEDGER",
                                        real_cloud))


class TestLocalRecordSite(_RecordSiteBase):
    def test_corrupt_ledger_marks_degraded_and_turn_completes(self):
        from core.voice import local_client_cost as local_mod

        self._reset_singletons()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost_ledger.sqlite3"
            _make_ledger(path)
            _corrupt(path)
            lines: list[str] = []
            with mock.patch.dict(os.environ,
                                 {"VYUK_COST_LEDGER_PATH": str(path)}):
                with mock.patch.object(local_mod, "_log_line",
                                       lambda m: lines.append(m),
                                       create=True):
                    # Must not raise: the turn completes.
                    local_mod._cost_record_local(
                        None, "llama-3.1-8b-instant", "hello")
            state = cost_mod.live_record_degraded_state()
            self.assertIn("local", state)
            self.assertEqual(state["local"]["count"], 1)
            self.assertIn("file is not a database",
                          state["local"]["last_error"])
            self.assertTrue(
                any("Local cost row lost" in line for line in lines),
                f"no loss log line in {lines!r}")
            self.assertTrue(
                any("cost_ledger.sqlite3" in line for line in lines),
                f"log line names no ledger file: {lines!r}")
            self.assertTrue(
                any("file is not a database" in line for line in lines),
                f"log line names no cause: {lines!r}")

    def test_healthy_ledger_records_and_stays_clean(self):
        from core.voice import local_client_cost as local_mod

        self._reset_singletons()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost_ledger.sqlite3"
            _make_ledger(path)
            with mock.patch.dict(os.environ,
                                 {"VYUK_COST_LEDGER_PATH": str(path)}):
                local_mod._cost_record_local(
                    None, "llama-3.1-8b-instant", "hello")
            self.assertEqual(cost_mod.live_record_degraded_state(), {})
            rows = cost_mod.CostLedger(
                db_path=path).recent_turns(limit=5)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["brain"] == "local" for r in rows))

    def test_unopenable_ledger_marks_degraded(self):
        # A different failure mode than corruption: the ledger
        # path is a directory, so sqlite cannot open it at all.
        from core.voice import local_client_cost as local_mod

        self._reset_singletons()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-dir"
            path.mkdir()
            lines: list[str] = []
            with mock.patch.dict(os.environ,
                                 {"VYUK_COST_LEDGER_PATH": str(path)}):
                with mock.patch.object(local_mod, "_log_line",
                                       lambda m: lines.append(m),
                                       create=True):
                    local_mod._cost_record_local(
                        None, "llama-3.1-8b-instant", "hello")
            state = cost_mod.live_record_degraded_state()
            self.assertIn("local", state)
            self.assertIn("unable to open database file",
                          state["local"]["last_error"])
            self.assertTrue(any("Local cost row lost" in line
                                for line in lines))


class TestCloudRecordSite(_RecordSiteBase):
    _PARSED = {"usage": None,
               "choices": [{"message": {"content": "hi"}}]}

    def test_corrupt_ledger_marks_degraded_and_turn_completes(self):
        from core.voice import cloud_text_helpers as cloud_mod

        self._reset_singletons()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost_ledger.sqlite3"
            _make_ledger(path)
            _corrupt(path)
            lines: list[str] = []
            with mock.patch.dict(os.environ,
                                 {"VYUK_COST_LEDGER_PATH": str(path)}):
                with mock.patch.object(cloud_mod, "_log_line",
                                       lambda m: lines.append(m)):
                    # Must not raise: the turn completes.
                    cloud_mod._cost_record(
                        dict(self._PARSED),
                        "llama-3.3-70b-versatile", "hello")
            state = cost_mod.live_record_degraded_state()
            self.assertIn("cloud_text", state)
            self.assertEqual(state["cloud_text"]["count"], 1)
            self.assertIn("file is not a database",
                          state["cloud_text"]["last_error"])
            self.assertTrue(
                any("Cloud-text cost row lost" in line for line in lines),
                f"no loss log line in {lines!r}")
            self.assertTrue(
                any("cost_ledger.sqlite3" in line for line in lines),
                f"log line names no ledger file: {lines!r}")

    def test_healthy_ledger_records_and_stays_clean(self):
        from core.voice import cloud_text_helpers as cloud_mod

        self._reset_singletons()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost_ledger.sqlite3"
            _make_ledger(path)
            with mock.patch.dict(os.environ,
                                 {"VYUK_COST_LEDGER_PATH": str(path)}):
                cloud_mod._cost_record(
                    dict(self._PARSED),
                    "llama-3.3-70b-versatile", "hello")
            self.assertEqual(cost_mod.live_record_degraded_state(), {})
            rows = cost_mod.CostLedger(
                db_path=path).recent_turns(limit=5)
            self.assertEqual(len(rows), 2)
            brains = {r["brain"] for r in rows}
            self.assertIn("cloud_text", brains)

    def test_non_dict_parsed_is_not_a_loss(self):
        # _cost_record returns early on a non-dict parsed reply
        # — that is not a ledger failure, so nothing is marked.
        from core.voice import cloud_text_helpers as cloud_mod

        self._reset_singletons()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost_ledger.sqlite3"
            _make_ledger(path)
            _corrupt(path)
            with mock.patch.dict(os.environ,
                                 {"VYUK_COST_LEDGER_PATH": str(path)}):
                cloud_mod._cost_record("not-a-dict",
                                       "llama-3.3-70b-versatile", "hello")
            self.assertEqual(cost_mod.live_record_degraded_state(), {})


class TestLiveRecordSiteStillMarked(_DegradedReset):
    def test_live_hook_marks_degraded(self):
        # The generalisation must not break the Live path that
        # already worked (field-check fix #2).
        from core.voice import live_client_error_cost as costmod

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost_ledger.sqlite3"
            _make_ledger(path)
            _corrupt(path)
            real_ledger = costmod._COST_LEDGER

            class _FailingLedger:
                def record_live_turn(self, model, usage=None):
                    raise sqlite3.DatabaseError("file is not a database")

            costmod._COST_LEDGER = _FailingLedger()
            self.addCleanup(lambda: setattr(costmod, "_COST_LEDGER",
                                            real_ledger))
            lines: list[str] = []
            with mock.patch.object(costmod, "_log_line",
                                   lambda msg: lines.append(msg)):
                costmod._cost_record_live(
                    SimpleNamespace(usage_metadata=None),
                    model="test-model")  # must not raise
            state = cost_mod.live_record_degraded_state()
            self.assertIn("live", state)
            self.assertIn("file is not a database",
                          state["live"]["last_error"])
            self.assertTrue(any("lost" in line.lower()
                                for line in lines))


if __name__ == "__main__":
    unittest.main()
