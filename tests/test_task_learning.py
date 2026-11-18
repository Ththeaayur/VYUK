# B17 — learning across tasks (closes audit L16).
#
# The playbook was never written in production: .add() /
# set_proof() were called only by tests, and Reflexion never
# ran in a live loop. This batch revives the playbook as the
# ONE learning home: the completion paths write the outcome
# (coordinator run_task after the receipt is built; run_goal's
# _end on every exit), the next task's picker reads it before
# its FIRST pick only, a lesson is written only with its proof
# (the B10 goal verdict), and a lesson that fails on reuse is
# struck. Reflexion is RETIRED, not wired (see
# core/brain/reflection.py — B12's retry ladder already
# provides the one bounded retry).
#
# Fakes only: no real cloud calls, no real tools. The picker
# is a script; the executor, the verifier and the snapshot are
# fakes; the observation changes every call so steps count as
# changed. Playbook stores and ledgers are temp-path only —
# PlaybookStore() with no path writes the REAL
# data/playbooks.json (pre-existing behaviour), so every test
# here passes an explicit temp path.

import asyncio
import pathlib
import tempfile
import unittest
from unittest import mock

from core.brain import planner_rungoal as rg
from core.brain import reflection
from core.brain import task_coordinator as tc
from core.brain import task_ledger as tl
from core.brain.task_coordinator_steps import StepDef
from core.memory.playbook_store import PlaybookStore

GOAL = "organise the downloads folder"
TITLE = "organise the downloads folder"


# ---------------------------------------------------------------------------
# Shared fakes (copied patterns from test_goal_verification.py and
# test_rungoal_persistence.py — the same harnesses those suites use).
# ---------------------------------------------------------------------------

class _Snapshot:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return {"fg": "fake", "n": self.n, "windows": ["fake"],
                "texts": []}


def _ok(result="did it"):
    return {"status": "success", "result": result}


def _fail(result="app busy"):
    return {"status": "failure", "kind": "handler_error",
            "message": result, "result": result}


class _ScriptExecutor:
    """Per-tool scripted results; records every call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append((tool, dict(args)))
        results = self.script.get(tool)
        if not results:
            return _ok()
        if len(results) > 1:
            return results.pop(0)
        return results[0]


class _RecordingVerifier:
    """Per-expect scripted verdicts; records every full call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, expect_text, before, after, result_text):
        self.calls.append((expect_text, before, after, result_text))
        verdicts = self.script.get(expect_text)
        if not verdicts:
            return ("unverifiable", "no script")
        if len(verdicts) > 1:
            return verdicts.pop(0)
        return verdicts[0]


def _steps(*mappings):
    return [StepDef.from_mapping(m) for m in mappings]


def _store(tmp):
    return PlaybookStore(path=pathlib.Path(tmp) / "playbooks.json")


def _ledger(tmp):
    return tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")


# ---------------------------------------------------------------------------
# 1. coordinator: the completion path writes the lesson
# ---------------------------------------------------------------------------

class CoordinatorLessonTests(unittest.TestCase):
    def _run(self, title, steps, verifier, executor, tmp):
        # ONE store instance: PlaybookStore.__init__ never loads
        # from disk, so a second instance would see nothing
        # until load() is called — return the instance the
        # coordinator actually wrote into.
        store = _store(tmp)
        coord = tc.TurnCoordinator(
            executor=executor, verifier=verifier, snapshot=_Snapshot(),
            ledger=_ledger(tmp), playbook=store)
        task = coord.new_task(title, "routine", steps)
        receipt = coord.run_task(task)
        return task, receipt, store

    def test_completed_met_writes_proven_lesson(self):
        verifier = _RecordingVerifier({
            "text 'step one done' present": [("met", "step observed")],
            TITLE: [("met", "goal state observed")]})
        executor = _ScriptExecutor({})
        with tempfile.TemporaryDirectory() as tmp:
            task, receipt, store = self._run(
                TITLE,
                _steps({"tool": "fake_tool", "args": {"x": 1},
                        "label": "step one",
                        "expect": "text 'step one done' present"}),
                verifier, executor, tmp)
            self.assertEqual(receipt.state, "completed")
            entry = store._entries.get(TITLE)
            self.assertIsNotNone(entry, "no lesson written")
            self.assertTrue(entry.proven)
            self.assertEqual(entry.proof_job_id, f"task:{task.id}")
            self.assertEqual(
                [s["tool"] for s in entry.steps], ["fake_tool"])
            self.assertEqual(entry.steps[0]["args"], {"x": 1})
            self.assertEqual(entry.steps[0]["description"], "step one")
            # The proof is the B10 goal verdict, not a bare claim.
            self.assertEqual(store.reuse(TITLE)["ok"], True)

    def test_completed_unverifiable_writes_nothing(self):
        verifier = _RecordingVerifier({
            "text 'step one done' present": [("met", "step observed")],
            TITLE: [("unverifiable", "cannot judge")]})
        with tempfile.TemporaryDirectory() as tmp:
            task, receipt, store = self._run(
                TITLE,
                _steps({"tool": "fake_tool", "label": "step one",
                        "expect": "text 'step one done' present"}),
                verifier, _ScriptExecutor({}), tmp)
            self.assertEqual(receipt.state, "completed")
            self.assertNotIn(TITLE, store._entries)
            self.assertEqual(store.reuse(TITLE)["ok"], False)

    def test_failed_task_writes_failure_lesson(self):
        verifier = _RecordingVerifier({
            TITLE: [("met", "goal state observed")]})
        # One step that fails every time: the coordinator strikes
        # it out at 3 and the task fails.
        executor = _ScriptExecutor({"fake_tool": [_fail()] * 6})
        with tempfile.TemporaryDirectory() as tmp:
            task, receipt, store = self._run(
                TITLE,
                _steps({"tool": "fake_tool", "label": "step one"}),
                verifier, executor, tmp)
            self.assertEqual(receipt.state, "failed")
            entry = store._entries.get(TITLE)
            self.assertIsNotNone(entry, "no failure lesson written")
            self.assertFalse(entry.proven)
            self.assertNotEqual(entry.failure, "")
            self.assertEqual(
                [s["tool"] for s in entry.steps], ["fake_tool"])

    def test_failed_after_proven_keeps_proof_and_notes_failure(self):
        verifier = _RecordingVerifier({
            "text 'step one done' present": [("met", "step observed")],
            TITLE: [("met", "goal state observed")]})
        # First run succeeds (one ok), second run fails (the
        # step is struck out at 3 — three failures per run).
        executor = _ScriptExecutor(
            {"fake_tool": [_ok()] + [_fail()] * 6})
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            coord = tc.TurnCoordinator(
                executor=executor, verifier=verifier,
                snapshot=_Snapshot(), ledger=_ledger(tmp),
                playbook=store)
            task1 = coord.new_task(TITLE, "routine", _steps(
                {"tool": "fake_tool", "label": "step one",
                 "expect": "text 'step one done' present"}))
            receipt1 = coord.run_task(task1)
            self.assertEqual(receipt1.state, "completed")
            proof1 = store._entries[TITLE].proof_job_id
            self.assertTrue(store._entries[TITLE].proven)
            # The second task fails — but its failure was not in
            # the proven lesson's steps (the lesson's step is
            # the one that just failed... in this harness the
            # same tool fails, so the lesson is re-written as a
            # failure note ONLY if the proof survives: the
            # design keeps the proof and records the failure
            # as a note when a proven lesson stands.
            task2 = coord.new_task(TITLE, "routine", _steps(
                {"tool": "fake_tool", "label": "step one",
                 "expect": "text 'step one done' present"}))
            receipt2 = coord.run_task(task2)
            self.assertEqual(receipt2.state, "failed")
            entry = store._entries[TITLE]
            self.assertTrue(entry.proven,
                            "the proof must survive a failure note")
            self.assertEqual(entry.proof_job_id, proof1)
            self.assertNotEqual(entry.failure, "")


# ---------------------------------------------------------------------------
# 2. run_goal: the completion path writes the lesson; the next
#    task's picker reads it before its first pick only
# ---------------------------------------------------------------------------

class RungoalLessonTests(unittest.TestCase):
    def _run(self, pick, store, ledger, verify_script=None,
             budgets=None, goal_id=None, executor=None):
        snapshot = _Snapshot()

        async def _pick(goal, observation, history):
            out = pick(goal, observation, history)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        async def _obs():
            return snapshot()

        def _budget_int(name, default):
            return (budgets or {}).get(name, default)

        def _budget_seconds(name, default):
            return (budgets or {}).get(name, default)

        def _verify(expect_text, before, after, result_text):
            return (verify_script or {}).get(
                expect_text, ("unverifiable", "no script"))

        # A caller-supplied executor records or scripts the
        # tool calls; otherwise every tool succeeds.
        execute_patch = (
            mock.patch.object(rg, "_execute_tool",
                              side_effect=executor)
            if executor is not None
            else mock.patch.object(rg, "_execute_tool",
                                   return_value=_ok()))
        with mock.patch.object(rg, "_goal_budget_int",
                               side_effect=_budget_int), \
                mock.patch.object(rg, "_goal_budget_seconds",
                                  side_effect=_budget_seconds), \
                mock.patch.object(rg, "_known_tools",
                                  return_value=None), \
                execute_patch:
            if goal_id is not None:
                return asyncio.run(rg.resume_goal(
                    goal_id, pick_action=_pick, observe=_obs,
                    verify_expect=_verify, ledger=ledger,
                    playbook=store))
            return asyncio.run(rg.run_goal(
                GOAL, pick_action=_pick, observe=_obs,
                verify_expect=_verify, ledger=ledger,
                playbook=store))

    def _goal_id(self, ledger):
        records = [r for r in ledger.all_records()
                   if r.get("kind") == "goal"]
        self.assertEqual(len(records), 1)
        return records[0]["task_id"]

    def test_completed_met_writes_lesson_with_proof(self):
        calls = []

        async def pick(goal, observation, history):
            calls.append(list(history))
            if len(calls) >= 2:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {"x": 1},
                    "description": "do the thing"}

        with tempfile.TemporaryDirectory() as tmp:
            store, ledger = _store(tmp), _ledger(tmp)
            result = self._run(
                pick, store, ledger,
                verify_script={GOAL: ("met", "goal state observed")})
            self.assertIn("Goal complete: all done", result)
            entry = store._entries.get(GOAL)
            self.assertIsNotNone(entry, "no lesson written")
            self.assertTrue(entry.proven)
            self.assertTrue(entry.proof_job_id.startswith("goal:"))
            self.assertEqual(
                [s["tool"] for s in entry.steps], ["fake_tool"])
            self.assertEqual(entry.steps[0]["args"], {"x": 1})
            self.assertEqual(entry.steps[0]["description"],
                             "do the thing")

    def test_completed_unverifiable_writes_nothing(self):
        async def pick(goal, observation, history):
            if len(history) >= 1:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            store, ledger = _store(tmp), _ledger(tmp)
            result = self._run(
                pick, store, ledger,
                verify_script={GOAL: ("unverifiable", "cannot judge")})
            self.assertIn("Goal complete: all done", result)
            self.assertNotIn(GOAL, store._entries)
            self.assertEqual(store.reuse(GOAL)["ok"], False)

    def test_lesson_steers_next_first_pick_only(self):
        """The next goal's first action comes from the lesson
        (the picker is not even called for it); the second pick
        is the picker's own, never the lesson's."""
        # Run 1: teach the lesson (two steps, then done).
        async def teach(goal, observation, history):
            if len(history) >= 2:
                return {"done": "taught"}
            return {"tool": "fake_tool",
                    "args": {"n": len(history) + 1},
                    "description": f"step {len(history) + 1}"}

        # Run 2: the picker would pick something else entirely.
        picker_calls = []

        async def pick(goal, observation, history):
            picker_calls.append(list(history))
            if len(picker_calls) >= 2:
                return {"done": "done after lesson"}
            return {"tool": "picker_tool", "args": {},
                    "description": "the picker's own step"}

        with tempfile.TemporaryDirectory() as tmp:
            store, ledger = _store(tmp), _ledger(tmp)
            result1 = self._run(
                teach, store, ledger,
                verify_script={GOAL: ("met", "goal state observed")})
            self.assertIn("Goal complete: taught", result1)
            self.assertTrue(store._entries[GOAL].proven)
            # Run 2: the lesson's two steps run first (the
            # picker is not called for them), then the picker
            # picks its own step, then done.
            executed = []

            def _record(tool, args):
                executed.append(tool)
                return _ok()

            result2 = self._run(
                pick, store, ledger,
                verify_script={GOAL: ("met", "goal state observed")},
                executor=_record)
            self.assertIn("Goal complete: done after lesson", result2)
            self.assertEqual(
                executed, ["fake_tool", "fake_tool", "picker_tool"],
                "the lesson steered the first two steps, the "
                "picker steered the third — never the reverse")
            # The picker was called exactly once per pick it
            # made (step 3, then done) — never for the lesson's
            # steps.
            self.assertEqual(len(picker_calls), 2)

    def test_lesson_failing_on_reuse_is_struck(self):
        """A lesson whose step definitively fails on reuse is
        demoted and never trusted again on the same evidence."""
        calls = []

        async def pick(goal, observation, history):
            calls.append(list(history))
            # Run 1: one step, then done. Run 2: the lesson's
            # step fails and is struck; keep picking the same
            # tool until the goal strikes out (3 strikes).
            if len(calls) == 2:
                return {"done": "taught"}
            return {"tool": "fake_tool", "args": {}}

        mode = {"fail": False}

        def _execute(tool, args):
            if mode["fail"]:
                return _fail()
            return _ok()

        with tempfile.TemporaryDirectory() as tmp:
            store, ledger = _store(tmp), _ledger(tmp)
            result1 = self._run(
                pick, store, ledger,
                verify_script={GOAL: ("met", "goal state observed")})
            self.assertIn("Goal complete: taught", result1)
            proof1 = store._entries[GOAL].proof_job_id
            self.assertTrue(store._entries[GOAL].proven)
            # Run 2: the lesson step fails on reuse (transient
            # retry once, then strike-out) and the goal fails.
            mode["fail"] = True
            result2 = self._run(
                pick, store, ledger,
                verify_script={GOAL: ("met", "goal state observed")},
                executor=_execute)
            self.assertIn("failed", result2.lower())
            # The lesson is struck: unproven, proof cleared,
            # and reuse refuses — even though the goal failed
            # (nothing completed, so no new lesson overwrote it).
            entry = store._entries[GOAL]
            self.assertFalse(entry.proven)
            self.assertEqual(entry.proof_job_id, "")
            self.assertEqual(store.reuse(GOAL)["ok"], False)
            self.assertIn((GOAL, proof1),
                          [(d["name"], d["proof_job_id"])
                           for d in store.struck])

    def test_resumed_goal_lesson_covers_preresume_steps(self):
        """A goal paused by the budget, then resumed and
        completed, writes a lesson covering the steps run
        before the stop as well as after it."""
        async def pick(goal, observation, history):
            if len(history) >= 2:
                return {"done": "finished after resume"}
            return {"tool": "fake_tool",
                    "args": {"n": len(history) + 1},
                    "description": f"step {len(history) + 1}"}

        with tempfile.TemporaryDirectory() as tmp:
            store, ledger = _store(tmp), _ledger(tmp)
            # Run 1: budget of 1 step — pauses after step 1.
            result1 = self._run(
                pick, store, ledger,
                verify_script={GOAL: ("met", "goal state observed")},
                budgets={"goal_max_steps": 1})
            self.assertIn("Resumable: resume_goal('", result1)
            goal_id = self._goal_id(ledger)
            # Resume with the full budget: step 2 runs, then done.
            result2 = self._run(
                pick, store, ledger,
                verify_script={GOAL: ("met", "goal state observed")},
                goal_id=goal_id)
            self.assertIn("Goal complete: finished after resume",
                          result2)
            entry = store._entries.get(GOAL)
            self.assertIsNotNone(entry, "no lesson written")
            self.assertTrue(entry.proven)
            self.assertEqual(
                [s["tool"] for s in entry.steps],
                ["fake_tool", "fake_tool"])
            self.assertEqual(
                [s["args"] for s in entry.steps],
                [{"n": 1}, {"n": 2}],
                "the pre-stop step rides the resume into the lesson")


# ---------------------------------------------------------------------------
# 3. store-level: failure field + struck proofs
# ---------------------------------------------------------------------------

class StoreLessonTests(unittest.TestCase):
    def test_failure_field_roundtrip_and_struck_proof_cannot_reprove(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "playbooks.json"
            store = PlaybookStore(path=path)
            store.add("do the thing",
                      [{"tool": "fake_tool", "args": {}}],
                      proof_job_id="task:abc",
                      failure="step 2 failed: app busy")
            store.save()
            fresh = PlaybookStore(path=path)
            fresh.load()
            entry = fresh._entries["do the thing"]
            self.assertEqual(entry.failure,
                             "step 2 failed: app busy")
            # Strike it: demoted, proof cleared, struck pair kept.
            self.assertTrue(fresh.strike("do the thing",
                                         "failed on reuse"))
            fresh.save()
            reloaded = PlaybookStore(path=path)
            reloaded.load()
            self.assertFalse(reloaded._entries["do the thing"].proven)
            self.assertEqual(
                reloaded._entries["do the thing"].proof_job_id, "")
            self.assertEqual(
                reloaded.reuse("do the thing")["ok"], False)
            self.assertIn(("do the thing", "task:abc"),
                          [(d["name"], d["proof_job_id"])
                           for d in reloaded.struck])
            # The SAME proof can never re-prove it (it stores
            # unproven, never re-trusted on that evidence)...
            result = reloaded.add(
                "do the thing",
                [{"tool": "fake_tool", "args": {}}],
                proof_job_id="task:abc")
            self.assertFalse(result["proven"])
            self.assertFalse(
                reloaded._entries["do the thing"].proven)
            # ...but a NEW proof from a new task run may.
            result = reloaded.add(
                "do the thing",
                [{"tool": "fake_tool", "args": {}}],
                proof_job_id="task:def")
            self.assertTrue(result["proven"])
            self.assertTrue(reloaded._entries["do the thing"].proven)


# ---------------------------------------------------------------------------
# 4. Reflexion: retired, with the reason recorded
# ---------------------------------------------------------------------------

class ReflectionRetiredTests(unittest.TestCase):
    def test_reflection_retired_with_reason(self):
        self.assertTrue(reflection.RETIRED)
        self.assertIn("B12", reflection.RETIRED_REASON)
        self.assertIn("retry", reflection.RETIRED_REASON)

    def test_no_live_caller_supplies_reflect_fn(self):
        """No production code supplies a reflect_fn — the only
        reflect_fn= sites are the checkpoint route's own
        pass-throughs (planner_checkpoint.py,
        w8_wiring_steps.py), which have no production callers
        either. A live caller appearing here means Reflexion
        was re-wired without re-deciding (see the RETIRED
        banner in reflection.py)."""
        tree = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for path in sorted((tree / "core").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8",
                                   errors="replace").splitlines(), 1):
                if "reflect_fn=" not in line:
                    continue
                stripped = line.strip()
                if stripped.startswith("def "):
                    continue  # a signature, not a call
                if path.name in ("planner_checkpoint.py",
                                 "w8_wiring_steps.py"):
                    continue  # the checkpoint route's pass-through
                offenders.append(f"{path.relative_to(tree)}:{lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
