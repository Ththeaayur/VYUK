"""Field-check fix #15 (T10): anticipation routines persist across a
restart. The store used to be in-memory only — plan_routine() created
a routine that vanished when the process ended. The wiring now
persists to data/anticipation_routines.json (a separate file from
data/routines.json on purpose: the planner's file validates actions
as a list of dicts, so an anticipation routine's plain-language
string actions would read as damaged there).

Qt-free. The path is patched to a temp file per test and
reset_anticipation() stands in for a fresh process (it drops the
singleton, so the next access rehydrates from disk). No live data/
is touched.
"""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from core import w7_wiring_anticipation as w7


class AnticipationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = pathlib.Path(self._tmp.name) / "anticipation_routines.json"
        patcher = mock.patch.object(
            w7, "_ANTICIPATION_ROUTINES_PATH", self._path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        w7.reset_anticipation()
        self.addCleanup(w7.reset_anticipation)

    def test_routine_survives_singleton_reset(self):
        w7.plan_routine("morning", "when I wake up",
                        ["open the news", "read the weather"])
        self.assertTrue(self._path.exists())
        w7.reset_anticipation()  # fresh-process stand-in
        got = w7.routine_store().get("morning")
        self.assertIsNotNone(got)
        self.assertEqual(got.trigger, "when I wake up")
        self.assertEqual(list(got.actions),
                         ["open the news", "read the weather"])

    def test_update_persists(self):
        w7.plan_routine("morning", "when I wake up", ["open the news"])
        w7.routine_store().update("morning", trigger="at 7am",
                                  actions=["open the news", "make coffee"])
        w7.reset_anticipation()
        got = w7.routine_store().get("morning")
        self.assertIsNotNone(got)
        self.assertEqual(got.trigger, "at 7am")
        self.assertEqual(list(got.actions),
                         ["open the news", "make coffee"])

    def test_delete_removes_from_disk(self):
        w7.plan_routine("morning", "when I wake up", ["open the news"])
        self.assertTrue(w7.routine_store().delete("morning"))
        w7.reset_anticipation()
        self.assertIsNone(w7.routine_store().get("morning"))
        self.assertEqual(json.loads(self._path.read_text(encoding="utf-8")),
                         [])

    def test_run_routine_sees_persisted_routine(self):
        w7.plan_routine("morning", "when I wake up",
                        ["open the news", "read the weather"])
        w7.reset_anticipation()
        result = w7.run_routine("morning")  # no executor: plan-only
        self.assertEqual(result.status, "plan-only")
        self.assertEqual(list(result.planned_steps),
                         ["open the news", "read the weather"])

    def test_duplicate_name_still_raises(self):
        w7.plan_routine("morning", "when I wake up", ["open the news"])
        with self.assertRaises(ValueError):
            w7.plan_routine("morning", "at 7am", ["make coffee"])
        stored = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["trigger"], "when I wake up")

    def test_corrupt_file_loads_empty_and_recovers(self):
        self._path.write_text("{not json", encoding="utf-8")
        w7.reset_anticipation()
        self.assertIsNone(w7.routine_store().get("morning"))
        w7.plan_routine("morning", "when I wake up", ["open the news"])
        w7.reset_anticipation()
        self.assertIsNotNone(w7.routine_store().get("morning"))

    def test_damaged_entries_skipped_good_ones_load(self):
        self._path.write_text(json.dumps([
            {"name": "good", "trigger": "at 7am",
             "actions": ["make coffee"]},
            {"name": "bad", "actions": ["no trigger here"]},
            "not even a dict",
            {"name": "empty-actions", "trigger": "x", "actions": []},
        ]), encoding="utf-8")
        w7.reset_anticipation()
        self.assertIsNotNone(w7.routine_store().get("good"))
        self.assertIsNone(w7.routine_store().get("bad"))
        self.assertIsNone(w7.routine_store().get("empty-actions"))
        self.assertEqual(
            [r.name for r in w7.routine_store().list_routines()],
            ["good"])


if __name__ == "__main__":
    unittest.main()
