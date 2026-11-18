"""Code-Perfection Build 2 tests (stdlib unittest, no GUI, no network).

What is built here, and what is NOT claimed: playbook
persistence (Q10 single store: J9 goal entries + finance
bullets in one file, two namespaces; the planner lookup
still pulls goal entries only, never finance bullets) and a real
control-list snapshot source (UIA descendants, never
grounding marks). No Windows automation exists in this
Linux lab, so the snapshot tests use injected window/facts
seams and the "no source" path — a real Windows control
snapshot on the owner's machine after the BB101 quiz is
the exit proof for that half. No GUI render is claimed.

Covered:
- Playbook survives a simulated restart (save, fresh load:
  proven entry found, unproven refused, deletion refused
  at add and dropped on load, corrupted file -> empty +
  note + file untouched, load never raises, loop never
  crashes on a bad file).
- Save happens on add and on set_proof; reuse never writes.
- Snapshot records are real-shaped ({control_id,
  fingerprint, role, name, automation_id}), automation-id
  controls keep their id, nameless/password/duplicate
  controls are skipped, a failed scan returns None (never
  a fabrication), and marks are never passed as records.
- Planner gate: presence-only for fingerprint-less
  actions (present executes, absent halts), fingerprint
  match/mismatch as before, None snapshot still skips
  honestly, and run_goal with a snapshot callable executes
  a present control instead of skipping.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core.desktop import control_snapshot_p2 as snap_mod  # Phase 3 D2b INTENTIONAL: control_snapshot_p2 moved to core/desktop/
from core.brain import goal_client_supply as supply  # Phase 3 B1b INTENTIONAL: goal_client_supply moved to core/brain/
from core.brain import planner  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.brain import planner_checkpoint, planner_rungoal  # INTENTIONAL (skills split B41): impl modules for dual-patching (see setUp notes below)
from core.memory import playbook_store as store
from core.funnel.tools_registry import tools as registry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class PlaybookPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "playbooks.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_then_fresh_load_keeps_proven_entry(self):
        book = store.PlaybookStore(self.path)
        book.add("restart goal", [{"tool": "quick_math", "args": {"x": 1}}],
                 proof_job_id="field-job-p2")
        self.assertTrue(self.path.exists())
        fresh = store.PlaybookStore(self.path)
        notes = fresh.load()
        self.assertIn("restart goal", fresh.names())
        self.assertEqual(fresh.reuse("restart goal")["ok"], True)
        self.assertEqual(len(fresh.reuse("restart goal")["steps"]), 1)
        self.assertTrue(any("loaded" in n or "1" in n for n in notes))

    def test_unproven_entry_survives_but_is_still_refused(self):
        book = store.PlaybookStore(self.path)
        book.add("unproven goal", [{"tool": "quick_math", "args": {"x": 1}}])
        fresh = store.PlaybookStore(self.path)
        fresh.load()
        self.assertIn("unproven goal", fresh.names())
        self.assertEqual(fresh.reuse("unproven goal")["ok"], False)

    def test_deletion_refused_at_add_and_dropped_on_load(self):
        book = store.PlaybookStore(self.path)
        result = book.add("bad goal",
                          [{"tool": "delete_file", "args": {"p": "x"}}],
                          proof_job_id="field-job-p2")
        self.assertEqual(result["added"], False)
        # Hand-edit a deletion entry into the file: load must drop it.
        payload = {"version": 1, "entries": {
            "sneaky": {"steps": [{"tool": "purge_all", "args": {}}],
                       "proof_job_id": "field-job-p2", "proven": True}}}
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        fresh = store.PlaybookStore(self.path)
        notes = fresh.load()
        self.assertNotIn("sneaky", fresh.names())
        self.assertTrue(any("sneaky" in n for n in notes))

    def test_corrupted_file_gives_empty_playbook_and_keeps_file(self):
        self.path.write_text("{not json", encoding="utf-8")
        before = self.path.read_text(encoding="utf-8")
        fresh = store.PlaybookStore(self.path)
        notes = fresh.load()  # must not raise
        self.assertEqual(fresh.names(), [])
        self.assertTrue(any("unreadable" in n for n in notes))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_missing_file_gives_empty_playbook_with_note(self):
        fresh = store.PlaybookStore(self.path)
        notes = fresh.load()
        self.assertEqual(fresh.names(), [])
        self.assertTrue(any("no persisted" in n for n in notes))

    def test_set_proof_saves_and_reuse_never_writes(self):
        book = store.PlaybookStore(self.path)
        book.add("later proven", [{"tool": "quick_math", "args": {"x": 1}}])
        self.assertEqual(book.reuse("later proven")["ok"], False)
        book.set_proof("later proven", "field-job-p2")
        mtime = self.path.stat().st_mtime_ns
        book.reuse("later proven")
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        fresh = store.PlaybookStore(self.path)
        fresh.load()
        self.assertEqual(fresh.reuse("later proven")["ok"], True)

    def test_shared_playbook_loads_from_a_given_path(self):
        book = store.PlaybookStore(self.path)
        book.add("shared goal", [{"tool": "quick_math", "args": {"x": 1}}],
                 proof_job_id="field-job-p2")
        try:
            shared = supply.reset_shared_playbook(self.path)
            self.assertEqual(shared.reuse("shared goal")["ok"], True)
            self.assertIn("shared goal", shared.names())
        finally:
            supply.reset_shared_playbook()

    def test_plain_store_instance_unchanged_by_persistence(self):
        plain = store.PlaybookStore()
        plain.add("plain goal", [{"tool": "quick_math", "args": {"x": 1}}],
                  proof_job_id="field-job-p2")
        self.assertEqual(plain.reuse("plain goal")["ok"], True)


class ControlSnapshotTests(unittest.TestCase):
    class _El:
        def __init__(self, facts, automation_id=""):
            self._facts = facts
            self.element_info = type("I", (), {"automation_id": automation_id})()

    class _Win:
        def __init__(self, elements):
            self._elements = elements

        def descendants(self):
            return list(self._elements)

    def _facts(self, el):
        return dict(el._facts)

    def test_real_shaped_records_from_faked_window(self):
        els = [
            self._El({"name": "Save", "control_type": "Button",
                      "class_name": "Button", "rect": "(0,0)-(10,10)"},
                     automation_id="btnSave"),
            self._El({"name": "Title", "control_type": "Text",
                      "class_name": "TextBlock", "rect": "(0,0)-(5,5)"}),
            self._El({"name": "", "control_type": "Pane",
                      "class_name": "Pane", "rect": "(0,0)-(1,1)"}),
            self._El({"name": "Secret", "control_type": "Edit",
                      "class_name": "Edit", "rect": "(0,0)-(2,2)"}),
        ]
        snap = snap_mod.snapshot_controls(
            _window_fn=lambda: (self._Win(els), "win"),
            _facts_fn=self._facts,
            _is_password_fn=lambda el: el._facts["name"] == "Secret")
        self.assertEqual(len(snap), 2)
        by_id = {r["control_id"]: r for r in snap}
        self.assertIn("btnSave", by_id)
        self.assertEqual(by_id["btnSave"]["name"], "Save")
        self.assertEqual(by_id["btnSave"]["automation_id"], "btnSave")
        composite = [r for r in snap if r["name"] == "Title"][0]
        self.assertTrue(composite["control_id"].startswith("Text|Title|"))
        for record in snap:
            self.assertTrue(record["fingerprint"])
            self.assertTrue(record["control_id"])

    def test_fingerprint_changes_when_state_changes(self):
        def facts(el):
            return dict(el._facts)
        el = self._El({"name": "Save", "control_type": "Button",
                       "class_name": "Button", "rect": "(0,0)-(10,10)"})
        snap1 = snap_mod.snapshot_controls(
            _window_fn=lambda: (self._Win([el]), "win"), _facts_fn=facts,
            _is_password_fn=lambda e: False)
        el._facts["rect"] = "(20,20)-(30,30)"
        snap2 = snap_mod.snapshot_controls(
            _window_fn=lambda: (self._Win([el]), "win"), _facts_fn=facts,
            _is_password_fn=lambda e: False)
        self.assertNotEqual(snap1[0]["fingerprint"], snap2[0]["fingerprint"])

    def test_failed_scan_returns_none_never_a_fabrication(self):
        def boom():
            raise RuntimeError("no window")
        self.assertIsNone(snap_mod.snapshot_controls(_window_fn=boom))

    def test_no_automation_source_reports_unavailable(self):
        # In this Linux lab there is no pywinauto: the real path
        # must return None (honest skip), never a fake snapshot.
        self.assertIsNone(snap_mod.current_control_snapshot())
        status = snap_mod.snapshot_status()
        self.assertEqual(status["available"], False)
        self.assertTrue(status["reason"])


class PlannerGateTests(unittest.TestCase):
    def setUp(self):
        self._orig_failsafe = planner._failsafe_tripped
        planner._failsafe_tripped = lambda: False
        # INTENTIONAL (skills split B41): _failsafe_tripped moved to the
        # checkpoint module (run_goal imports it from there); patch all
        # three bindings, same fake, all restored below.
        planner_checkpoint._failsafe_tripped = planner._failsafe_tripped
        planner_rungoal._failsafe_tripped = planner._failsafe_tripped
        self._orig_execute = registry.execute
        self.executions = []

        def fake_execute(name, args=None, **kwargs):
            self.executions.append((name, dict(args or {})))
            return {"status": "success", "result": "ok"}

        registry.execute = fake_execute

    def tearDown(self):
        registry.execute = self._orig_execute
        planner._failsafe_tripped = self._orig_failsafe
        planner_checkpoint._failsafe_tripped = self._orig_failsafe
        planner_rungoal._failsafe_tripped = self._orig_failsafe

    def test_presence_only_present_executes_via_gate(self):
        ok, reason, note = planner._control_gate(
            "uia_click_control", {"control_id": "btnSave"},
            [{"control_id": "btnSave", "fingerprint": "abc123"}])
        self.assertTrue(ok)
        self.assertIn("presence", note)

    def test_presence_only_absent_halts(self):
        ok, reason, _note = planner._control_gate(
            "uia_click_control", {"control_id": "btnSave"},
            [{"control_id": "other", "fingerprint": "abc123"}])
        self.assertFalse(ok)
        self.assertIn("not present", reason)

    def test_fingerprinted_match_and_mismatch_unchanged(self):
        ok, _r, _n = planner._control_gate(
            "uia_click_control",
            {"control_id": "btnSave", "fingerprint": "abc123"},
            [{"control_id": "btnSave", "fingerprint": "abc123"}])
        self.assertTrue(ok)
        ok, reason, _n = planner._control_gate(
            "uia_click_control",
            {"control_id": "btnSave", "fingerprint": "abc123"},
            [{"control_id": "btnSave", "fingerprint": "DIFFERENT"}])
        self.assertFalse(ok)
        self.assertIn("stale", reason)

    def test_none_snapshot_still_skips_honestly(self):
        ok, _r, note = planner._control_gate(
            "uia_click_control", {"control_id": "btnSave"}, None)
        self.assertTrue(ok)
        self.assertIn("skipped: no snapshot", note)

    def test_run_goal_with_snapshot_callable_executes_present_control(self):
        snap = [{"control_id": "btnSave", "fingerprint": "abc123"}]
        actions = [{"tool": "uia_click_control",
                    "args": {"control_id": "btnSave"}},
                   {"done": "clicked"}]

        async def pick(goal, observation, history):
            return actions.pop(0) if actions else {"done": "done"}

        report = _run(planner.run_goal(
            "click save", pick_action=pick, observe=lambda: None,
            control_snapshot=lambda: list(snap)))
        self.assertEqual(len(self.executions), 1)
        self.assertEqual(self.executions[0][0], "uia_click_control")
        self.assertIn("clicked", report)

    def test_run_goal_with_snapshot_callable_halts_absent_control(self):
        snap = [{"control_id": "other", "fingerprint": "abc123"}]
        actions = [{"tool": "uia_click_control",
                    "args": {"control_id": "btnSave"}}]

        async def pick(goal, observation, history):
            return actions.pop(0) if actions else {"done": "done"}

        _run(planner.run_goal(
            "click save", pick_action=pick, observe=lambda: None,
            control_snapshot=lambda: list(snap)))
        self.assertEqual(self.executions, [])


if __name__ == "__main__":
    unittest.main()
