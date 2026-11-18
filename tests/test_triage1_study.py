"""Triage Wire Build 1 — Study top 3 wired (st_deck / st_schedule /
st_practice) as live tools and one flow.

Tests run against tmp dirs and a tmp schedule store wherever a
file is written, except the two registry-gate tests, which use
the real registry funnel (its audit drift is restored by the
build's data restore, like every other suite). No Anki binary
is involved: .apkg validity is checked with the leaf's own
read_apkg_summary (zip + sqlite), which is what a suite can
prove — opening the deck in HIS Anki is the field proof owed
after the BB101 quiz, not claimed here.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from core import study_flow_t1 as flow
from core import st_deck as deck_mod
from core import st_schedule as sched

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)



def _materials():
    return json.dumps([
        {"key": "bb101-photo51", "prompt": "What did Photo 51 show?",
         "answer": "The X-ray diffraction pattern of DNA",
         "syllabus": "BB101", "source": "BB101 L14 notes"},
        {"key": "bb101-franklin", "prompt": "Who took Photo 51?",
         "answer": "Rosalind Franklin",
         "syllabus": "BB101", "source": "BB101 L14 notes"},
    ])


class ExportTests(unittest.TestCase):
    def test_export_produces_valid_apkg_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "bb101.apkg")
            result = json.loads(flow.study_export_deck_handler(
                _materials(), "BB101 Test", out, exported_date="2026-10-26"))
            self.assertEqual(result["status"], "ok")
            self.assertTrue(os.path.exists(out))
            info = deck_mod.read_apkg_summary(out)
            self.assertEqual(len(info["notes"]), 2)
            self.assertEqual(len(info["cards"]), 2)
            for note in info["notes"]:
                # Source is the last field in the QA model.
                self.assertEqual(note["fields"][-1], "BB101 L14 notes")
            decks = info["decks"]
            desc = decks[str(flow._deck_id_for("BB101 Test"))]["desc"]
            self.assertIn("BB101 L14 notes", desc)
            self.assertIn("2026-10-26", desc)
            self.assertEqual(result["deck"]["notes"], 2)
            self.assertEqual(result["deck"]["sources"], ["BB101 L14 notes"])

    def test_export_refuses_sourceless_card_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "bad.apkg")
            bad = json.dumps([{"question": "Q?", "answer": "A",
                               "source": "   "}])
            result = json.loads(flow.study_export_deck_handler(
                bad, "Bad", out))
            self.assertEqual(result["status"], "error")
            self.assertFalse(os.path.exists(out))

    def test_export_refuses_non_apkg_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "deck.zip")
            result = json.loads(flow.study_export_deck_handler(
                _materials(), "BB101 Test", out))
            self.assertEqual(result["status"], "error")
            self.assertFalse(os.path.exists(out))


class ScheduleTests(unittest.TestCase):
    def _store(self, tmp):
        return flow.StudyScheduleStore(path=os.path.join(tmp, "sched.json"))

    def test_review_is_deterministic_and_survives_restart(self):
        when = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            key = store.ensure_card("What did Photo 51 show?",
                                    "The X-ray diffraction pattern of DNA",
                                    "BB101 L14 notes", material_key="bb101-photo51")
            first = store.review(key, sched.GOOD, review_datetime=when)
            # New card, GOOD at learning step 0 -> step 1, 10-minute step.
            self.assertEqual(first["due"], "2026-10-26T10:10:00+00:00")
            # Same inputs in a fresh store give the same due (fuzz off).
            store2 = self._store(os.path.join(tmp, "other"))
            os.makedirs(os.path.join(tmp, "other"), exist_ok=True)
            store2 = flow.StudyScheduleStore(
                path=os.path.join(tmp, "other", "sched.json"))
            key2 = store2.ensure_card("What did Photo 51 show?",
                                      "The X-ray diffraction pattern of DNA",
                                      "BB101 L14 notes")
            second = store2.review(key2, sched.GOOD, review_datetime=when)
            self.assertEqual(second["due"], first["due"])
            # Reloading the first store keeps the reviewed state.
            reloaded = self._store(tmp)
            self.assertEqual(reloaded._cards[key]["card"]["due"],
                             first["due"])
            self.assertEqual(len(reloaded._cards[key]["logs"]), 1)

    def test_review_by_material_key_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.ensure_card("Q", "A", "S", material_key="my-key")
            result = store.review("my-key", sched.EASY)
            self.assertEqual(result["key"],
                             flow.deck_card_key("Q", "A", "S"))

    def test_review_unknown_key_and_bad_rating_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            with self.assertRaises(ValueError):
                store.review("no-such-key", sched.GOOD)
            store.ensure_card("Q", "A", "S")
            with self.assertRaises(ValueError):
                store.review(flow.deck_card_key("Q", "A", "S"), 9)

    def test_corrupt_schedule_file_loads_empty_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sched.json")
            with open(path, "w") as f:
                f.write("{not json")
            store = flow.StudyScheduleStore(path=path)
            self.assertEqual(store.summary()["cards"], 0)
            self.assertTrue(store.last_load_notes)
            # The corrupt file is preserved, not overwritten on load.
            with open(path) as f:
                self.assertEqual(f.read(), "{not json")


class PracticeTests(unittest.TestCase):
    def test_practice_carries_provenance_on_every_problem(self):
        result = json.loads(flow.study_practice_handler(_materials()))
        self.assertEqual(result["status"], "ok")
        problems = result["practice"]["problems"]
        self.assertEqual(len(problems), 2)
        for problem in problems:
            self.assertEqual(problem["source"], "BB101 L14 notes")
        self.assertIn("Limits:", result["practice"]["limits"])

    def test_practice_refuses_protected_and_sourceless_material(self):
        protected = json.dumps([{"key": "p", "prompt": "Q?", "answer": "A",
                                 "syllabus": "BB101", "source": "Book",
                                 "protected": True}])
        result = json.loads(flow.study_practice_handler(protected))
        self.assertEqual(result["status"], "error")
        sourceless = json.dumps([{"key": "p", "prompt": "Q?", "answer": "A",
                                  "syllabus": "BB101", "source": ""}])
        result = json.loads(flow.study_practice_handler(sourceless))
        self.assertEqual(result["status"], "error")


class FullFlowTests(unittest.TestCase):
    def test_practice_schedule_export_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = flow.StudyScheduleStore(
                path=os.path.join(tmp, "sched.json"))
            out = os.path.join(tmp, "bb101.apkg")
            result = flow.study_flow(
                _materials(), "BB101 Test", out,
                reviews={"bb101-photo51": sched.GOOD}, store=store,
                exported_date="2026-10-26")
            self.assertTrue(os.path.exists(out))
            self.assertEqual(result["deck"]["notes"], 2)
            self.assertEqual(len(result["practice"]["problems"]), 2)
            # Both practised cards are scheduled; the reviewed one
            # has a due time from FSRS-6, not a guess.
            self.assertEqual(len(result["schedule"]), 2)
            self.assertEqual(result["schedule_summary"]["cards"], 2)
            # Schedule state survives a simulated restart.
            reloaded = flow.StudyScheduleStore(
                path=os.path.join(tmp, "sched.json"))
            self.assertEqual(reloaded.summary()["cards"], 2)

    def test_session_handler_uses_tmp_store_when_seamed(self):
        # study_session_handler's default store is the app data dir;
        # the module seam lets tests point it at tmp instead, so
        # this test never writes to the real data/ tree.
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "bb101.apkg")
            original = flow._schedule_store_path
            flow._schedule_store_path = lambda: os.path.join(tmp, "sched.json")
            try:
                result = json.loads(flow.study_session_handler(
                    _materials(), "BB101 Test", out,
                    reviews_json=json.dumps({"bb101-franklin": 3})))
            finally:
                flow._schedule_store_path = original
            self.assertEqual(result["status"], "ok")
            self.assertTrue(os.path.exists(out))
            self.assertEqual(result["deck"]["notes"], 2)


class RegistryTests(unittest.TestCase):
    def test_tools_registered_and_export_tools_gated(self):
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        from core.funnel.tools_gate import (_CONFIRM_GATE_TOOLS, _FREE_BY_DESIGN,
                                     _READ_ONLY_TOOLS)  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/

        names = {d["name"] for d in tools.get_declarations()}
        for name in ("study_practice", "study_review",
                     "study_export_deck", "study_session"):
            self.assertIn(name, names)
        self.assertIn("study_export_deck", _CONFIRM_GATE_TOOLS)
        self.assertIn("study_session", _CONFIRM_GATE_TOOLS)
        # Classification completeness (v5.47 item 12): freedom by
        # omission is forbidden — practice is pure composition
        # (read-only), review writes only the app's own store.
        self.assertIn("study_practice", _READ_ONLY_TOOLS)
        self.assertEqual(_FREE_BY_DESIGN.get("study_review"), "app-state")

    def test_export_via_registry_refuses_without_confirm_handler(self):
        from core import confirm_gate
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

        confirm_gate.clear_handler()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "bb101.apkg")
            result = tools.execute("study_export_deck", {
                "materials_json": _materials(), "deck_name": "BB101 Test",
                "output_path": out})
            self.assertEqual(result.get("kind"), "not_confirmed")
            self.assertFalse(os.path.exists(out))

    def test_export_via_registry_writes_with_confirm_handler(self):
        from core import confirm_gate
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

        confirm_gate.register_handler(lambda name, summary: True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "bb101.apkg")
                result = tools.execute("study_export_deck", {
                    "materials_json": _materials(),
                    "deck_name": "BB101 Test", "output_path": out})
                self.assertTrue(os.path.exists(out))
                payload = json.loads(result["result"])
                self.assertEqual(payload["status"], "ok")
        finally:
            confirm_gate.clear_handler()


if __name__ == "__main__":
    unittest.main()


# L3: budget-isolation guest rule (test-isolation precedent,
# shared helper tests/_budget_isolation.py) — this suite
# executes tools through the shared funnel, so it snapshots
# the budget window + clock + confirm handler, opens a clean
# window, and restores byte-for-byte, instead of inheriting
# the earlier suites' dispatches and tripping on ordering.
_BUDGET_SNAPSHOT = None


def setUpModule():
    global _BUDGET_SNAPSHOT
    _BUDGET_SNAPSHOT = snapshot_budget()


def tearDownModule():
    global _BUDGET_SNAPSHOT
    if _BUDGET_SNAPSHOT is not None:
        restore_budget(_BUDGET_SNAPSHOT)
        _BUDGET_SNAPSHOT = None
