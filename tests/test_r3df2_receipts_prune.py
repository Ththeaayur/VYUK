"""Round-3 section D fix queue item D-F2: receipts prune archives
the dropped rows instead of destroying them silently.

The 5,000-line live cap is unchanged; the prune (load-only, never
in the dispatch hot path) now writes the dropped rows to a dated
file under tool_receipts_archive/ next to the live file first,
keeps the event in sticky per-process state, and the Activity
feed renders a marker naming where the older rows went. If the
archive write itself fails, the load still succeeds and the
failure is logged + recorded + marked — the loss is announced
either way, never silent.
"""
import json
import pathlib
import tempfile
import unittest
import uuid

from core import activity_feed as af
from core import tool_receipts as receipts_mod
from core.tool_receipts_normalize import (
    _MAX_LINES, effect_signatures)
from core.tool_receipts_store import ReceiptStore


def _attempt(rid, args, created=1796000000.0):
    sigs = effect_signatures("write_file", args)
    return {
        "type": "attempt", "receipt_id": rid,
        "tool": "write_file", "args_signature": sigs[0],
        "effect_signatures": sigs,
        "args_preview": json.dumps(args, sort_keys=True),
        "handle_id": None, "snapshot_stamp": None,
        "compensations": [], "manual_steps": [],
        "created_at": created, "status": "attempted",
        "dispatched": True, "outcome_summary": "",
    }


def _write_attempts(path, count, prefix="seed", start=0):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(start, start + count):
            args = {"filepath": f"/tmp/df2_{prefix}_{i:06d}.txt",
                    "content": "df2-seed"}
            f.write(json.dumps(
                _attempt(f"{prefix}-{i:06d}", args,
                         1796000000.0 + i * 0.01),
                sort_keys=True) + "\n")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        receipts_mod.reset_prune_archive_state()
        receipts_mod.reset_receipt_degraded()
        self.addCleanup(receipts_mod.reset_prune_archive_state)
        self.addCleanup(receipts_mod.reset_receipt_degraded)

    def _feed_observability(self):
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        return [r for r in rows if r.get("source") == "observability"]


class PruneArchiveTests(_Base):
    """The archive itself: rows, order, cap, state."""

    def test_dropped_rows_archived_in_order(self):
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 5010)
        ReceiptStore(path).attempts()
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 5000)
        arch_dir = self.tmp / "tool_receipts_archive"
        files = sorted(arch_dir.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        rows = files[0].read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 10)
        ids = [json.loads(r)["receipt_id"] for r in rows]
        self.assertEqual(ids, [f"seed-{i:06d}" for i in range(10)])

    def test_archive_rows_are_valid_receipts(self):
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 5005)
        ReceiptStore(path).attempts()
        files = sorted((self.tmp / "tool_receipts_archive").glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        archived = ReceiptStore(files[0])
        self.assertEqual(len(archived.attempts()), 5)

    def test_small_cap_archives_the_same_way(self):
        # The phase-2 shape (max_lines=3, 5 attempts): the live
        # file keeps 3, the archive keeps the dropped 2.
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 5)
        ReceiptStore(path, max_lines=3).attempts()
        self.assertEqual(
            len(path.read_text(encoding="utf-8").splitlines()), 3)
        files = sorted((self.tmp / "tool_receipts_archive").glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        ids = [json.loads(r)["receipt_id"] for r in
               files[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(ids, ["seed-000000", "seed-000001"])

    def test_no_archive_under_cap(self):
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 100)
        ReceiptStore(path).attempts()
        self.assertEqual(receipts_mod.prune_archive_state(), {})
        self.assertFalse((self.tmp / "tool_receipts_archive").exists())

    def test_state_names_archive_and_count(self):
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 5020)
        ReceiptStore(path).attempts()
        state = receipts_mod.prune_archive_state()
        self.assertEqual(len(state), 1)
        rec = next(iter(state.values()))
        self.assertEqual(rec["count"], 20)
        self.assertTrue(rec["archive_path"].endswith(".jsonl"))
        self.assertIn("tool_receipts_archive", rec["archive_path"])
        self.assertEqual(rec["last_error"], "")

    def test_second_archive_same_second_gets_own_name(self):
        # Two prunes in the same second must never overwrite
        # each other's archive file.
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 5001)
        store = ReceiptStore(path)
        store.attempts()
        store._archive_dropped(
            [json.dumps(_attempt("dup-1", {"filepath": "/tmp/dup1.txt"}))])
        store._archive_dropped(
            [json.dumps(_attempt("dup-2", {"filepath": "/tmp/dup2.txt"}))])
        files = sorted((self.tmp / "tool_receipts_archive").glob("*.jsonl"))
        self.assertEqual(len(files), 3)
        self.assertEqual(len({p.name for p in files}), 3)

    def test_reset_clears_state(self):
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 5001)
        ReceiptStore(path).attempts()
        self.assertTrue(receipts_mod.prune_archive_state())
        receipts_mod.reset_prune_archive_state()
        self.assertEqual(receipts_mod.prune_archive_state(), {})

    def test_cap_unchanged(self):
        self.assertEqual(_MAX_LINES, 5000)


class ArchiveFailureTests(_Base):
    """The archive write fails: load still succeeds, the loss is
    logged + recorded + marked — never silent."""

    def _blocked_dir(self):
        (self.tmp / "tool_receipts_archive").write_text(
            "not a directory", encoding="utf-8")
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 5010)
        return path

    def test_load_succeeds_and_live_still_prunes(self):
        path = self._blocked_dir()
        store = ReceiptStore(path)
        self.assertEqual(len(store.attempts()), 5000)
        self.assertEqual(
            len(path.read_text(encoding="utf-8").splitlines()), 5000)

    def test_failure_recorded_in_state(self):
        path = self._blocked_dir()
        ReceiptStore(path).attempts()
        rec = receipts_mod.prune_archive_state().get("archive_failed")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["count"], 10)
        self.assertIn("File exists", rec["last_error"])

    def test_failure_marker_names_the_loss(self):
        path = self._blocked_dir()
        ReceiptStore(path).attempts()
        obs = self._feed_observability()
        self.assertEqual(len(obs), 1)
        self.assertIn("Receipts degraded", obs[0]["title"])
        self.assertIn("10", obs[0]["title"])
        self.assertEqual(obs[0]["kind"], "degraded")


class FeedMarkerTests(_Base):
    """The Activity feed names where the older rows went."""

    def test_archived_marker_names_archive(self):
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 5010)
        ReceiptStore(path).attempts()
        obs = self._feed_observability()
        self.assertEqual(len(obs), 1)
        self.assertIn("Receipts archived", obs[0]["title"])
        self.assertIn("10", obs[0]["title"])
        self.assertIn("tool_receipts_archive", obs[0]["title"])
        self.assertEqual(obs[0]["kind"], "archived")

    def test_no_marker_when_healthy(self):
        path = self.tmp / "receipts.jsonl"
        _write_attempts(path, 100)
        ReceiptStore(path).attempts()
        self.assertEqual(self._feed_observability(), [])


class GateUnchangedTests(_Base):
    """The gate reads the live file only; archived rows are
    history, not live gate state."""

    def test_gate_finds_live_unknown_after_prune(self):
        path = self.tmp / "receipts.jsonl"
        live_args = {"filepath": "/tmp/df2_live.txt",
                     "content": "live"}
        done_args = {"filepath": "/tmp/df2_done.txt",
                     "content": "done"}
        with open(path, "w", encoding="utf-8") as f:
            for i in range(5008):
                args = {"filepath": f"/tmp/df2_fill_{i:06d}.txt",
                        "content": "fill"}
                f.write(json.dumps(_attempt(f"fill-{i:06d}", args,
                                            1796000000.0 + i * 0.01),
                                   sort_keys=True) + "\n")
            f.write(json.dumps(_attempt("live-1", live_args,
                                        1796000000.0 + 5008 * 0.01),
                               sort_keys=True) + "\n")
            done_rid = "done-1"
            f.write(json.dumps(_attempt(done_rid, done_args,
                                        1796000000.0 + 5009 * 0.01),
                               sort_keys=True) + "\n")
            f.write(json.dumps({
                "type": "outcome", "receipt_id": done_rid,
                "status": "succeeded", "summary": "ok",
                "dispatched": True,
                "created_at": 1796000010.0,
            }, sort_keys=True) + "\n")
        store = ReceiptStore(path)
        store.attempts()
        # 5,011 rows -> 5,000 live; the newest probes survive.
        self.assertEqual(
            len(path.read_text(encoding="utf-8").splitlines()), 5000)
        found = store.find_unknown(tool="write_file", args=live_args)
        self.assertIsNotNone(found)
        self.assertEqual(found.get("receipt_id"), "live-1")
        self.assertIsNone(store.find_unknown(
            tool="write_file", args=done_args))


if __name__ == "__main__":
    unittest.main()
