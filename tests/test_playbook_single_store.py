"""Q10 single-store tests (stdlib unittest, no GUI, no network).

The finance playbook and the goal playbook are ONE store now
(core/playbook_store.py): goal entries + finance bullets in one
file, two namespaces. These tests pin the merge contract:

* A goal entry and a finance bullet may share a name/text
  without colliding, and neither namespace leaks into the other.
* Writing one kind and reading the other back leaves both intact.
* Both kinds survive a persistence round-trip in the single file.
* Version 1 files (goal entries only) still load.
* A pre-Q10 finance file is imported on load when the single
  file has no bullets, and is left untouched.
* The old modules are gone (Q1 clean break, no shims).
* Goal rules are unchanged: proven-only reuse, deletion refusal
  at add and on load, corrupt file -> empty + untouched, reuse
  never writes.
"""

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from core.memory import playbook_store as store


class SingleStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "playbooks.json"

    def _store(self):
        return store.PlaybookStore(self.path)

    def test_goal_and_bullet_share_name_without_collision(self):
        book = self._store()
        book.add("budget", [{"tool": "quick_math", "args": {"x": 1}}],
                 proof_job_id="job-1")
        bullet_id = book.add_bullet("budget")
        self.assertEqual(book.names(), ["budget"])
        self.assertEqual([b["text"] for b in book.list_bullets()], ["budget"])
        self.assertEqual(bullet_id, 1)
        # Goal lookup returns goal steps, never the bullet.
        lookup = book.reuse("budget")
        self.assertTrue(lookup["ok"])
        self.assertEqual(lookup["steps"],
                         [{"tool": "quick_math", "args": {"x": 1}}])
        # Finance listing returns bullets, never goal steps.
        self.assertEqual(book.list_bullets(),
                         [{"id": 1, "text": "budget",
                           "helpful": 0, "harmful": 0}])

    def test_write_one_read_other_back_intact(self):
        book = self._store()
        book.add("goal-a", [{"tool": "quick_math", "args": {"x": 1}}],
                 proof_job_id="job-1")
        book.add_bullet("Keep receipts for a year")
        book.helpful(1)
        self.assertTrue(book.save()["saved"])
        fresh = self._store()
        fresh.load()
        self.assertTrue(fresh.reuse("goal-a")["ok"])
        bullets = fresh.list_bullets()
        self.assertEqual(len(bullets), 1)
        self.assertEqual(bullets[0]["text"], "Keep receipts for a year")
        self.assertEqual(bullets[0]["helpful"], 1)

    def test_no_cross_contamination_on_load(self):
        book = self._store()
        book.add("goal-only", [{"tool": "quick_math", "args": {"x": 1}}],
                 proof_job_id="job-1")
        book.add_bullet("bullet-only")
        book.save()
        fresh = self._store()
        fresh.load()
        # No goal entry named like the bullet: lookup refuses.
        self.assertFalse(fresh.reuse("bullet-only")["ok"])
        # No bullet named like the goal: finance list is bullets only.
        self.assertEqual([b["text"] for b in fresh.list_bullets()],
                         ["bullet-only"])
        self.assertEqual(fresh.names(), ["goal-only"])

    def test_persistence_round_trip_both_kinds(self):
        book = self._store()
        book.add("proven", [{"tool": "quick_math", "args": {"x": 1}}],
                 proof_job_id="job-1")
        book.add("unproven", [{"tool": "quick_math", "args": {"x": 2}}])
        book.add_bullet("first bullet")
        book.add_bullet("second bullet")
        book.helpful(2)
        book.helpful(2)
        book.harmful(1)
        book.save()
        fresh = self._store()
        fresh.load()
        self.assertTrue(fresh.reuse("proven")["ok"])
        self.assertFalse(fresh.reuse("unproven")["ok"])
        self.assertEqual([b["text"] for b in fresh.bullets],
                         ["first bullet", "second bullet"])
        by_text = {b["text"]: b for b in fresh.list_bullets()}
        self.assertEqual(by_text["second bullet"]["helpful"], 2)
        self.assertEqual(by_text["first bullet"]["harmful"], 1)

    def test_v1_file_loads_goal_entries_only(self):
        payload = {"version": 1, "entries": {
            "old goal": {"steps": [{"tool": "quick_math", "args": {"x": 1}}],
                         "proof_job_id": "job-old", "proven": True}}}
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        fresh = self._store()
        fresh.load()
        self.assertTrue(fresh.reuse("old goal")["ok"])
        self.assertEqual(fresh.bullets, [])

    def test_legacy_finance_file_imported_and_left_untouched(self):
        legacy = Path(self._tmp.name) / "finance_playbook.json"
        legacy.write_text(json.dumps({
            "version": 1, "bullets": [
                {"text": "Legacy bullet", "source": "user",
                 "helpful": 3, "harmful": 1}]}), encoding="utf-8")
        before = legacy.read_text(encoding="utf-8")
        orig = store.legacy_finance_path
        store.legacy_finance_path = lambda: legacy
        try:
            fresh = self._store()
            notes = fresh.load()
            self.assertEqual([b["text"] for b in fresh.bullets],
                             ["Legacy bullet"])
            self.assertEqual(fresh.bullets[0]["helpful"], 3)
            self.assertTrue(any("legacy" in n for n in notes))
            self.assertEqual(legacy.read_text(encoding="utf-8"), before)
            # Saving writes bullets into the single file; a reload
            # then keeps the single file's bullets, not the legacy's.
            fresh.save()
            legacy.write_text(json.dumps({
                "version": 1, "bullets": [
                    {"text": "Stale bullet", "source": "user",
                     "helpful": 0, "harmful": 0}]}), encoding="utf-8")
            again = self._store()
            again.load()
            self.assertEqual([b["text"] for b in again.bullets],
                             ["Legacy bullet"])
        finally:
            store.legacy_finance_path = orig

    def test_corrupt_bullets_section_drops_bullets_keeps_entries(self):
        payload = {"version": 2,
                   "entries": {"kept": {
                       "steps": [{"tool": "quick_math", "args": {"x": 1}}],
                       "proof_job_id": "job-1", "proven": True}},
                   "bullets": "notalist"}
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        fresh = self._store()
        notes = fresh.load()
        self.assertTrue(fresh.reuse("kept")["ok"])
        self.assertEqual(fresh.bullets, [])
        self.assertTrue(any("bullets" in n for n in notes))

    def test_deletion_refused_at_add_and_dropped_on_load(self):
        book = self._store()
        result = book.add("bad goal",
                          [{"tool": "delete_file", "args": {"p": "x"}}],
                          proof_job_id="job-1")
        self.assertFalse(result["added"])
        payload = {"version": 2, "entries": {
            "sneaky": {"steps": [{"tool": "purge_all", "args": {}}],
                       "proof_job_id": "job-1", "proven": True}},
            "bullets": []}
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        fresh = self._store()
        notes = fresh.load()
        self.assertNotIn("sneaky", fresh.names())
        self.assertTrue(any("sneaky" in n for n in notes))

    def test_corrupt_file_gives_empty_and_keeps_file(self):
        self.path.write_text("{not json", encoding="utf-8")
        fresh = self._store()
        notes = fresh.load()
        self.assertEqual(fresh.names(), [])
        self.assertEqual(fresh.bullets, [])
        self.assertTrue(any("unreadable" in n for n in notes))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")

    def test_reuse_never_writes(self):
        book = self._store()
        book.add("later", [{"tool": "quick_math", "args": {"x": 1}}],
                 proof_job_id="job-1")
        mtime = self.path.stat().st_mtime_ns
        book.reuse("later")
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_finance_vote_delete_positional_ids(self):
        book = self._store()
        book.add_bullet("alpha")
        book.add_bullet("beta")
        book.helpful(2)
        self.assertEqual(book.list_bullets()[0]["text"], "beta")
        self.assertTrue(book.delete_bullet(1))
        self.assertEqual([b["text"] for b in book.bullets], ["beta"])
        self.assertFalse(book.delete_bullet(9))
        with self.assertRaises(ValueError):
            book.helpful(9)

    def test_curate_and_merge(self):
        book = self._store()
        touched = book.curate_from_traces([
            {"text": "Never pay without a receipt", "outcome": "helpful"},
            {"text": "Ignore small fees", "outcome": "harmful"},
            {"text": "A neutral note", "outcome": "neutral"},
            {"text": "  ", "outcome": "helpful"},
        ])
        self.assertEqual(len(touched), 2)
        other = store.PlaybookStore()
        other.add_bullet("Never pay without a receipt")
        other.helpful(1)
        other.helpful(1)
        book.merge(other)
        by_text = {b["text"]: b for b in book.list_bullets()}
        self.assertEqual(by_text["Never pay without a receipt"]["helpful"], 3)

    def test_planner_lookup_never_returns_a_bullet(self):
        book = self._store()
        book.add_bullet("Check the ledger before paying")
        lookup = book.reuse("Check the ledger before paying")
        self.assertFalse(lookup["ok"])
        self.assertIn("no playbook entry", lookup["reason"])

    def test_old_modules_removed(self):
        for name in ("core.fl_playbook_v574", "core.j9_playbook_w2",
                     "core.playbook_store_p2"):
            with self.assertRaises(ImportError):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
