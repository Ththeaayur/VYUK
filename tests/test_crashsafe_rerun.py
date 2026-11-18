"""Crash-safe re-run (B21, 2026-11-07): a step interrupted by a
crash mid-execution is reconciled on resume, never re-run blindly.

Gap proved first (from the tree): the ledger caps args at 4000
chars (task_ledger._MAX_ARG_BLOB, task_ledger.py:141) and a
truncated blob is replaced on rebuild, while _advance_index
(task_ledger.py:201) advances past success/skipped only — "a
step with no outcome (crash mid-step) likewise never
advances". run_task writes the ledger at start (state running)
and per step only AFTER the outcome is appended, so a crash
between the tool applying and that write leaves no outcome
and the step re-runs blindly on resume — a step that sends a
message, writes a file, or moves money can apply TWICE.

What this batch builds (and this suite proves):

* ONE design: a durable per-step intent + outcome record
  ("step-runs") on the same ledger record — before a step
  executes, the intent (step, tool, args digest, unique
  step-run id) is ledger-written; after it executes, the
  outcome is recorded against the same id. On resume, a
  step whose intent is recorded but whose outcome is missing
  is reconciled first (re-read the world through the EXISTING
  verifier — the B10 goal-check shape: the expected end state
  judged true now, with no before snapshot) and only re-run
  if the reconciliation says it did not apply. A step whose
  outcome IS recorded is never re-run — the outcome record
  carries the verdict, not the args, so a truncated args blob
  changes nothing. The args cap stays at 4000 chars.
* A retried step gets a NEW step-run id (a retry is a new
  attempt); each parallel-group branch gets its own id and
  reconciles on its own; a cancelled step's outcome is
  recorded as cancelled and is never reconciled; when the
  world cannot be re-read the task pauses with
  _pending_reconcile and only the human decides
  (resolve_reconcile, GUI-only per C-14).

Fakes only: a _SimulatedCrash (BaseException, so it
propagates out of _execute, which catches Exception only),
scripted executors/verifiers, a temp ledger, no real crash,
no real files, no real cloud calls. Dialog tests are Qt-only
(skipped on the system interpreter, which has no PySide6).
"""

import pathlib
import sys
import tempfile
import types as _types_mod
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

# --- voice_assistant.* alias (mirrors test_mic_first_use_ask /
# test_startup_recovery) -------------------------------------------
# gui/pending_recovery.py imports voice_assistant.* at module
# level, and the tree root is not a package — so the alias is
# installed here, before any gui import that needs it. B6: the
# GUI reads the ALIASED module; tests drive the aliased module
# for anything that touches it.
_VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")}
_pkg = _types_mod.ModuleType("voice_assistant")
_pkg.__path__ = [str(ROOT)]
_core_alias = _types_mod.ModuleType("voice_assistant.core")
_core_alias.__path__ = [str(ROOT / "core")]
sys.modules["voice_assistant"] = _pkg
sys.modules["voice_assistant.core"] = _core_alias


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


from core.brain import task_coordinator as tc
from core.brain import task_ledger as tl
from core.brain.task_ledger import TaskLedger
from core.confirm_gate_helpers import _canonical_args_digest

try:
    from PySide6.QtWidgets import QApplication
    _HAS_QT = True
except Exception:  # noqa: BLE001 — no PySide6 on sys
    _HAS_QT = False


class _SimulatedCrash(BaseException):
    """A crash between 'applied' and 'recorded'.

    BaseException on purpose: _execute catches Exception
    only, so this propagates out of _run_step_inner,
    _run_step and run_task exactly like a real process
    crash — no outcome is written, no receipt is returned.
    """


def _ok(result="done"):
    return {"status": "success", "result": result}


class _ScriptExecutor:
    """Per-tool scripted results; records every call."""

    def __init__(self, script=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append((tool, dict(args)))
        results = self.script.get(tool)
        if not results:
            return _ok()
        if len(results) > 1:
            return results.pop(0)
        return results[0]


class _CrashExecutor(_ScriptExecutor):
    """Crashes on the FIRST call of each tool in `crash_tools`.

    `apply_first` names the tools whose fake world IS applied
    before the crash (the effect landed, the outcome did not);
    a crashing tool NOT in apply_first never applied. The
    fake world is `self.world` (tool -> True once applied).
    Later calls behave like _ScriptExecutor.
    """

    def __init__(self, script=None, crash_tools=(), apply_first=()):
        super().__init__(script)
        self._crash_tools = set(crash_tools)
        self._apply_first = set(apply_first)
        self._crashed = set()
        self.world = {}

    def __call__(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool in self._crash_tools and tool not in self._crashed:
            self._crashed.add(tool)
            if tool in self._apply_first:
                self.world[tool] = True
            raise _SimulatedCrash(f"crash during {tool}")
        results = self.script.get(tool)
        if not results:
            return _ok()
        if len(results) > 1:
            return results.pop(0)
        return results[0]


class _ScriptVerifier:
    """Per-expect scripted verdicts; records every call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, expect, before, after, result_text):
        self.calls.append((expect, before, after, result_text))
        queue = self.script.get(expect)
        if not queue:
            return ("unverifiable", "no script")
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]


class _Snapshot:
    def __call__(self):
        return {}


class CrashsafeRerunTests(unittest.TestCase):
    def _coord(self, tmp, executor=None, verifier=None):
        ledger = TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
        coord = tc.TurnCoordinator(
            executor=executor or _ScriptExecutor(),
            verifier=verifier or _ScriptVerifier({}),
            snapshot=_Snapshot(), ledger=ledger)
        return coord, ledger

    def _task(self, coord, steps):
        return coord.new_task(
            "t", "saved_plan",
            [tc.StepDef.from_mapping(m) for m in steps])

    def _resume(self, tmp, ledger, task_id, executor=None,
                verifier=None):
        """A fresh coordinator resuming the ledger record —
        the process-restart shape."""
        coord2, _ = self._coord(tmp, executor=executor,
                                verifier=verifier)
        record = ledger.get(task_id)
        self.assertIsNotNone(record)
        return coord2, coord2.resume_ledger_task(record, ledger=ledger)

    def _step_runs(self, ledger, task_id):
        record = ledger.get(task_id)
        self.assertIsNotNone(record)
        return record.get("step_runs") or []

    # -- intent: written BEFORE the step executes ---------------

    def test_intent_written_before_step_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen = {}

            class _PeekingExecutor(_ScriptExecutor):
                def __call__(self, tool, args):
                    # Read the LEDGER FILE from inside the fake
                    # tool, mid-step: the intent must already be
                    # durable there, before this call returns.
                    ledger2 = TaskLedger(
                        path=pathlib.Path(tmp) / "ledger.json")
                    record = ledger2.get(_task_id[0])
                    runs = (record or {}).get("step_runs") or []
                    seen["runs"] = runs
                    return super().__call__(tool, args)

            _task_id = [None]
            coord, ledger = self._coord(tmp, _PeekingExecutor())
            task = self._task(coord, [
                {"tool": "write_file", "args": {"path": "a.txt"},
                 "label": "write a"},
            ])
            _task_id[0] = task.id
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            runs = seen["runs"]
            self.assertEqual(len(runs), 1)
            run = runs[0]
            self.assertEqual(run["step_index"], 0)
            self.assertEqual(run["tool"], "write_file")
            self.assertEqual(run["label"], "write a")
            self.assertEqual(run["status"], "started")
            self.assertIsNone(run["resolution"])
            self.assertTrue(run["step_run_id"])
            self.assertTrue(run["args_digest"])

    def test_intent_args_digest_matches_dispatched_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = self._coord(tmp, executor=executor)
            task = self._task(coord, [
                {"tool": "write_file",
                 "args": {"path": "a.txt", "content": "hello"},
                 "label": "write a"},
            ])
            coord.run_task(task)
            dispatched = executor.calls[0][1]
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(
                runs[0]["args_digest"],
                _canonical_args_digest(dispatched))
            # The digest names what was dispatched, not the
            # plan's declared args: it changes with the args.
            self.assertNotEqual(
                runs[0]["args_digest"],
                _canonical_args_digest({"path": "b.txt"}))

    # -- reconcile: applied / not-applied -----------------------

    def test_crash_applied_reconciles_not_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _CrashExecutor(
                crash_tools={"write_file"}, apply_first={"write_file"})
            verifier = _ScriptVerifier(
                {"the file exists": [("met", "file is there")]})
            coord, ledger = self._coord(tmp, executor, verifier)
            task = self._task(coord, [
                {"tool": "write_file", "args": {"path": "a.txt"},
                 "label": "write a", "expect": "the file exists"},
            ])
            with self.assertRaises(_SimulatedCrash):
                coord.run_task(task)
            # The intent is in the ledger; no outcome is.
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "started")
            self.assertIsNone(runs[0]["resolution"])
            self.assertEqual(task.outcomes, [])
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor, verifier)
            self.assertEqual(receipt.state, "completed")
            # The step ran ONCE — the resume reconciled it,
            # it never re-ran.
            self.assertEqual(
                [c[0] for c in executor.calls], ["write_file"])
            # The outcomes live on the REBUILT task (resume
            # builds a fresh Task object), not the pre-crash
            # one this test still holds.
            live = coord2.get_task(task.id)
            self.assertEqual(len(live.outcomes), 1)
            self.assertEqual(live.outcomes[0].status, "success")
            self.assertEqual(live.outcomes[0].kind, "reconciled")
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["resolution"], "reconciled")
            self.assertEqual(runs[0]["status"], "success")
            # The reconciliation asked the verifier whether
            # the expected end state is true now — with no
            # before snapshot (the B10 goal-check shape).
            # The verifier is called three times: reconcile,
            # verification phase, then the B10 goal check
            # (task title "t").
            self.assertEqual(
                [c[0] for c in verifier.calls],
                ["the file exists", "the file exists", "t"])
            expect, before, _after, _text = verifier.calls[0]
            self.assertEqual(expect, "the file exists")
            self.assertIsNone(before)

    def test_crash_not_applied_reruns(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _CrashExecutor(
                crash_tools={"write_file"}, apply_first=set())
            verifier = _ScriptVerifier(
                {"the file exists": [
                    ("not-met", "file absent"),
                    ("met", "file is there")]})
            coord, ledger = self._coord(tmp, executor, verifier)
            task = self._task(coord, [
                {"tool": "write_file", "args": {"path": "a.txt"},
                 "label": "write a", "expect": "the file exists"},
            ])
            with self.assertRaises(_SimulatedCrash):
                coord.run_task(task)
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor, verifier)
            self.assertEqual(receipt.state, "completed")
            # The step ran TWICE: once before the crash (which
            # never applied), once on the resume.
            self.assertEqual(
                [c[0] for c in executor.calls],
                ["write_file", "write_file"])
            # Two attempts, two step-run ids: the stale intent
            # was superseded, the re-run minted a fresh one.
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 2)
            self.assertNotEqual(runs[0]["step_run_id"],
                                runs[1]["step_run_id"])
            self.assertEqual(runs[0]["resolution"], "superseded")
            self.assertEqual(runs[1]["resolution"], "recorded")
            self.assertEqual(runs[1]["status"], "success")

    # -- recorded outcome: never re-run, even truncated ---------

    def test_recorded_outcome_never_rerun_with_truncated_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Words, not one long token: the sanitizer's
            # long-token scrub ([A-Za-z0-9_-]{32,}) would
            # redact a single 5000-char run before the cap
            # check ever saw it. Spaced words survive the
            # scrub and the blob still exceeds the 4000-char
            # cap, so the ledger truncates the args.
            big = "lorem ipsum " * 500
            executor = _CrashExecutor(
                crash_tools={"send_message"}, apply_first=set())
            verifier = _ScriptVerifier(
                {"the message is sent": [
                    ("not-met", "not sent yet"),
                    ("met", "sent")]})
            coord, ledger = self._coord(tmp, executor, verifier)
            task = self._task(coord, [
                {"tool": "write_file", "args": {"path": "a.txt",
                                                "content": big},
                 "label": "write the big file"},
                {"tool": "send_message",
                 "args": {"text": "hello"},
                 "label": "send it",
                 "expect": "the message is sent"},
            ])
            with self.assertRaises(_SimulatedCrash):
                coord.run_task(task)
            # Step 0's args were truncated by the ledger cap —
            # the outcome record carries the verdict, not the
            # args, so the truncation changes nothing.
            record = ledger.get(task.id)
            step0 = record["steps"][0]
            self.assertTrue(step0.get("args", {}).get("_truncated"))
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor, verifier)
            self.assertEqual(receipt.state, "completed")
            calls = [c[0] for c in executor.calls]
            # Step 0 ran once (its recorded outcome stands);
            # step 1 ran twice (crash, then re-run).
            self.assertEqual(calls, ["write_file", "send_message",
                                     "send_message"])
            runs = self._step_runs(ledger, task.id)
            step0_runs = [r for r in runs if r["step_index"] == 0]
            self.assertEqual(len(step0_runs), 1)
            self.assertEqual(step0_runs[0]["resolution"], "recorded")
            self.assertEqual(step0_runs[0]["status"], "success")

    # -- retry: a NEW step-run id -------------------------------

    def test_retried_step_gets_new_step_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            verifier = _ScriptVerifier(
                {"the file exists": [
                    ("not-met", "not yet"),
                    ("met", "file is there")]})
            coord, ledger = self._coord(tmp, executor, verifier)
            task = self._task(coord, [
                {"tool": "write_file", "args": {"path": "a.txt"},
                 "label": "write a", "expect": "the file exists"},
            ])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            # The verification phase retried the step once —
            # a retry is a new attempt, not the same attempt
            # twice, so it minted a NEW step-run id.
            self.assertEqual(
                [c[0] for c in executor.calls],
                ["write_file", "write_file"])
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 2)
            self.assertNotEqual(runs[0]["step_run_id"],
                                runs[1]["step_run_id"])
            self.assertEqual(runs[0]["resolution"], "recorded")
            self.assertEqual(runs[1]["resolution"], "recorded")

    # -- parallel groups: own ids, own reconciliation -----------

    def test_parallel_group_branches_get_own_step_run_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = self._coord(tmp, executor)
            task = self._task(coord, [
                {"tool": "write_file", "args": {"path": "a.txt"},
                 "label": "write a", "parallel": "g"},
                {"tool": "write_file", "args": {"path": "b.txt"},
                 "label": "write b", "parallel": "g"},
            ])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 2)
            self.assertEqual({r["step_index"] for r in runs}, {0, 1})
            self.assertNotEqual(runs[0]["step_run_id"],
                                runs[1]["step_run_id"])
            self.assertTrue(all(
                r["resolution"] == "recorded" for r in runs))

    def test_parallel_group_crash_reconciles_branch_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _CrashExecutor(
                crash_tools={"write_file"}, apply_first={"write_file"})
            # The crash fires on the FIRST write_file call —
            # whichever branch the pool runs first. Both
            # branches carry the same expect here, so the
            # reconciled branch is whichever one crashed.
            verifier = _ScriptVerifier(
                {"the file exists": [("met", "file is there")]})
            coord, ledger = self._coord(tmp, executor, verifier)
            task = self._task(coord, [
                {"tool": "write_file", "args": {"path": "a.txt"},
                 "label": "write a", "parallel": "g",
                 "expect": "the file exists"},
                {"tool": "write_file", "args": {"path": "b.txt"},
                 "label": "write b", "parallel": "g",
                 "expect": "the file exists"},
            ])
            with self.assertRaises(_SimulatedCrash):
                coord.run_task(task)
            # Exactly one branch applied-and-crashed; the
            # other finished cleanly before the crash
            # propagated (the pool's exit waits for it).
            self.assertEqual(len(executor.calls), 2)
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor, verifier)
            self.assertEqual(receipt.state, "completed")
            # Neither branch re-ran: the crashed one was
            # reconciled, the finished one was recovered
            # from its recorded success (the crash aborted
            # the join before it placed that outcome).
            self.assertEqual(len(executor.calls), 2)
            live = coord2.get_task(task.id)
            outcomes = {o.label: o for o in live.outcomes}
            self.assertEqual(len(outcomes), 2)
            # One branch was reconciled by the verifier,
            # the other recovered with its recorded kind —
            # the two "applied" paths read differently.
            kinds = sorted(o.kind for o in outcomes.values())
            self.assertEqual(kinds, ["", "reconciled"])

    # -- cancelled: recorded, never reconciled ------------------

    def test_cancelled_step_never_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({
                "write_file": [{"status": "cancelled",
                                "kind": "cancelled",
                                "message": "stopped by user"}]})
            coord, ledger = self._coord(tmp, executor)
            task = self._task(coord, [
                {"tool": "write_file", "args": {"path": "a.txt"},
                 "label": "write a"},
            ])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "cancelled")
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "cancelled")
            self.assertEqual(runs[0]["resolution"], "recorded")
            # A resolved run is not a pending intent: the
            # reconciler has nothing to decide.
            self.assertIsNone(coord._pending_step_run(task, 0))
            verdict, _reason, _pending = \
                coord._reconcile_pending_intent(task, 0)
            self.assertEqual(verdict, "none")

    # -- unknown: pause for the human ---------------------------

    def _crashed_no_expect(self, tmp):
        """A task whose only step crashed with no expect —
        the world cannot be re-read. Returns (coord, ledger,
        task, executor) with the crash already raised."""
        executor = _CrashExecutor(
            crash_tools={"write_file"}, apply_first={"write_file"})
        coord, ledger = self._coord(tmp, executor)
        task = self._task(coord, [
            {"tool": "write_file", "args": {"path": "a.txt"},
             "label": "write a"},
        ])
        with self.assertRaises(_SimulatedCrash):
            coord.run_task(task)
        return coord, ledger, task, executor

    def test_unknown_pauses_with_pending_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, task, executor = \
                self._crashed_no_expect(tmp)
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor)
            self.assertEqual(receipt.state, "paused")
            self.assertEqual([c[0] for c in executor.calls],
                             ["write_file"])
            pending = coord2.pending_reconcile(task.id)
            self.assertIsNotNone(pending)
            self.assertEqual(pending["step_index"], 0)
            self.assertEqual(pending["tool"], "write_file")
            self.assertIn("write a", pending["question"])
            self.assertIn("no expectation", pending["reason"])
            self.assertIn("Did step", pending["question"])
            # The pause is in the ledger record, not just memory.
            record = ledger.get(task.id)
            self.assertEqual(record.get("state"), "paused")
            self.assertEqual(
                record.get("pending_reconcile", {}).get("question"),
                pending["question"])
            # The receipt names the decision itself (the
            # "Needs a decision" label lives in the Activity
            # render — see test_task_state_renders_needs_a_
            # decision below).
            self.assertIn("did step 'write a' apply before "
                          "the crash?", receipt.reason)

    def test_resolve_reconcile_applied_records_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, task, executor = \
                self._crashed_no_expect(tmp)
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor)
            self.assertEqual(receipt.state, "paused")
            receipt = coord2.resolve_reconcile(task.id, True)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.state, "completed")
            # The step never re-ran: the human's decision
            # recorded it as applied.
            self.assertEqual([c[0] for c in executor.calls],
                             ["write_file"])
            live = coord2.get_task(task.id)
            self.assertEqual(live.outcomes[0].kind, "reconciled")
            self.assertIsNone(coord2.pending_reconcile(task.id))
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["resolution"], "reconciled")

    def test_resolve_reconcile_not_applied_reruns(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, task, executor = \
                self._crashed_no_expect(tmp)
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor)
            self.assertEqual(receipt.state, "paused")
            receipt = coord2.resolve_reconcile(task.id, False)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.state, "completed")
            # The step re-ran fresh, with a NEW step-run id.
            self.assertEqual([c[0] for c in executor.calls],
                             ["write_file", "write_file"])
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 2)
            self.assertNotEqual(runs[0]["step_run_id"],
                                runs[1]["step_run_id"])
            self.assertEqual(runs[0]["resolution"], "superseded")
            self.assertEqual(runs[1]["resolution"], "recorded")

    def test_resolve_reconcile_non_bool_resolves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, task, executor = \
                self._crashed_no_expect(tmp)
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor)
            self.assertEqual(receipt.state, "paused")
            self.assertIsNone(coord2.resolve_reconcile(task.id, "yes"))
            self.assertIsNone(coord2.resolve_reconcile(task.id, None))
            self.assertEqual(coord2.get_task(task.id).state, "paused")
            self.assertIsNotNone(coord2.pending_reconcile(task.id))
            self.assertEqual([c[0] for c in executor.calls],
                             ["write_file"])

    def test_cancel_clears_pending_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, task, executor = \
                self._crashed_no_expect(tmp)
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor)
            self.assertEqual(receipt.state, "paused")
            self.assertIsNotNone(coord2.pending_reconcile(task.id))
            self.assertTrue(coord2.cancel_task(task.id))
            self.assertIsNone(coord2.pending_reconcile(task.id))
            self.assertEqual(
                coord2.get_task(task.id).state, "cancelled")
            # The step-runs stay: they are history, like
            # outcomes — only the pending decision dies.
            runs = self._step_runs(ledger, task.id)
            self.assertEqual(len(runs), 1)

    # -- read-class: safe to re-run, never pauses ---------------

    def test_read_class_step_reruns_without_pausing(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _CrashExecutor(
                crash_tools={"read_file"}, apply_first=set())
            coord, ledger = self._coord(tmp, executor)
            task = self._task(coord, [
                {"tool": "read_file", "args": {"path": "a.txt"},
                 "label": "read a"},
            ])
            with self.assertRaises(_SimulatedCrash):
                coord.run_task(task)
            coord2, receipt = self._resume(
                tmp, ledger, task.id, executor)
            self.assertEqual(receipt.state, "completed")
            # A pure local read has no effects to duplicate:
            # it re-ran without pausing for a decision.
            self.assertEqual([c[0] for c in executor.calls],
                             ["read_file", "read_file"])
            self.assertIsNone(coord2.pending_reconcile(task.id))

    # -- ledger round-trip --------------------------------------

    def test_step_runs_survive_ledger_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A crashed task's record is resumable (state
            # running) — a completed record is terminal
            # history and rebuild_task refuses it.
            _coord, ledger, task, _executor = \
                self._crashed_no_expect(tmp)
            record = ledger.get(task.id)
            rebuilt = TaskLedger().rebuild_task(record)
            self.assertEqual(len(rebuilt._step_runs), 1)
            self.assertEqual(rebuilt._step_runs[0]["status"],
                             "started")
            self.assertIsNone(rebuilt._step_runs[0]["resolution"])
            self.assertIsNone(rebuilt._pending_reconcile)
            self.assertIsNotNone(
                _coord._pending_step_run(rebuilt, 0))

    def test_malformed_step_runs_dropped_on_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, task, _executor = \
                self._crashed_no_expect(tmp)
            record = ledger.get(task.id)
            record["step_runs"] = [
                {"step_index": 0},  # no id: malformed, dropped
                {"step_run_id": "x" * 8},  # no index: dropped
                record["step_runs"][0],  # the real one: kept
            ]
            rebuilt = TaskLedger().rebuild_task(record)
            self.assertEqual(len(rebuilt._step_runs), 1)
            self.assertEqual(rebuilt._step_runs[0]["step_index"], 0)

    # -- B4 jumps: a jump target re-runs on purpose ---------

    def test_jump_target_reruns_despite_recorded_success(self):
        # Neighbour-caught B21 regression (test_complex_task:
        # test_voice_routine_goto_runs_as_coordinator_task):
        # step_b fails and jumps back to step_a; step_a's
        # previous-pass success is still in its step-run,
        # but _take_jump truncated its outcome — the jump IS
        # the instruction to re-run it, so the reconciler
        # must not skip it as "recorded success stands".
        # step_b fails three times and strikes out; the
        # call sequence is a,b,a,b,a,b.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({
                "step_b": [{"status": "failure",
                            "kind": "handler_error",
                            "message": "mirror down"}]})
            coord, ledger = self._coord(tmp, executor)
            task = self._task(coord, [
                {"tool": "step_a", "args": {}, "label": "step a"},
                {"tool": "step_b", "args": {}, "label": "step b",
                 "on_fail": {"goto": 1}},
            ])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertIn("strike limit", receipt.reason)
            self.assertEqual(
                [c[0] for c in executor.calls],
                ["step_a", "step_b",
                 "step_a", "step_b",
                 "step_a", "step_b"])
            # Two jumps taken (the third failure strikes
            # out instead of jumping again).
            jumped = [e for e in (task._jumps or [])
                      if isinstance(e, dict) and e.get("jumped")]
            self.assertEqual(len(jumped), 2)
            self.assertTrue(all(
                e.get("to_index") == 0 for e in jumped))
            # step_a ran three times — one NEW step-run id
            # per pass, never a reconciled skip.
            runs = self._step_runs(ledger, task.id)
            step_a_runs = [r for r in runs if r["step_index"] == 0]
            self.assertEqual(len(step_a_runs), 3)
            self.assertEqual(
                len({r["step_run_id"] for r in step_a_runs}), 3)
            self.assertTrue(all(
                r["resolution"] == "recorded"
                for r in step_a_runs))

    # -- GUI-adjacent renders (stdlib-only, run on sys too) -----

    def test_task_state_renders_needs_a_decision(self):
        from gui.task_state import render_task

        record = {
            "task_id": "t1", "title": "t", "state": "paused",
            "steps": [{"tool": "write_file", "label": "write a",
                       "args": {}}],
            "outcomes": [],
            "pending_reconcile": {
                "step_index": 0, "tool": "write_file",
                "label": "write a",
                "question": "Did step 'write a' apply before "
                            "the crash?",
                "reason": "no expectation was declared"},
        }
        rendered = render_task(record)
        self.assertEqual(rendered["state"], "Needs a decision")
        self.assertIn("Decide what happened", rendered["action"])
        self.assertIn("Did step 'write a' apply",
                      rendered["rationale"])

    def test_recovery_offer_excludes_pending_reconcile(self):
        from gui.pending_recovery import is_recoverable_task

        record = {
            "task_id": "t1", "state": "paused",
            "pending_reconcile": {
                "step_index": 0,
                "question": "Did step 'write a' apply before "
                            "the crash?"},
        }
        # The reconcile queue already offers this record —
        # the recovery offer never double-offers it.
        self.assertFalse(is_recoverable_task(record))
        record2 = {"task_id": "t2", "state": "paused"}
        self.assertTrue(is_recoverable_task(record2))

    def test_interrupted_pending_reconciles_lists_offers(self):
        from gui.pending_reconcile import (
            interrupted_pending_reconciles,
            pending_reconcile_summary)

        summaries = [
            {"task_id": "t1", "pending_reconcile": {
                "step_index": 0, "tool": "write_file",
                "label": "write a",
                "question": "Did step 'write a' apply?",
                "reason": "no expectation was declared"}},
            {"task_id": "t2"},  # no pending reconcile: dropped
            {"task_id": "t3", "pending_reconcile": {}},  # dropped
            {"pending_reconcile": {  # no task id: dropped
                "question": "Did step 'x' apply?"}},
        ]
        offers = interrupted_pending_reconciles(summaries)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0][0], "t1")
        tool, text = pending_reconcile_summary(offers[0][1])
        self.assertEqual(tool, "write_file")
        self.assertIn("Did step 'write a' apply?", text)
        self.assertIn("no expectation was declared", text)


@unittest.skipUnless(_HAS_QT, "PySide6 required")
class ReconcileDialogTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        from PySide6.QtWidgets import QWidget
        self.window = QWidget()

    def tearDown(self):
        # R5 lesson: sweep leftover dialogs (closed plan
        # dialogs linger in topLevelWidgets without
        # WA_DeleteOnClose) so a stale dialog never answers
        # the next test's click scan.
        try:
            from PySide6.QtWidgets import QApplication
            for widget in QApplication.topLevelWidgets():
                if hasattr(widget, "_reconcile_btn_applied"):
                    widget.close()
                    widget.deleteLater()
            QApplication.processEvents()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass
        try:
            self.window.close()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass

    def _pending(self):
        return {"step_index": 0, "tool": "write_file",
                "label": "write a",
                "question": "Did step 'write a' apply before "
                            "the crash?",
                "reason": "no expectation was declared for this "
                          "step, so the world cannot be re-read "
                          "to tell whether it applied"}

    def _click_visible(self, attr):
        # R5 lesson: click only a VISIBLE dialog's button —
        # a first-match scan can hit a hidden stale dialog.
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible() and hasattr(widget, attr):
                getattr(widget, attr).click()
                return

    def test_dialog_renders_question_and_reason(self):
        from gui.pending_reconcile import build_reconcile_dialog

        dlg = build_reconcile_dialog(self.window, self._pending())
        self.assertIsNotNone(dlg._reconcile_btn_applied)
        self.assertIsNotNone(dlg._reconcile_btn_not_applied)
        self.assertEqual(dlg._reconcile_btn_applied.text(),
                         "It applied")
        self.assertEqual(dlg._reconcile_btn_not_applied.text(),
                         "It didn't apply")
        self.assertIsNone(dlg._reconcile_decision)

    def test_neither_button_is_default_or_focused(self):
        from gui.pending_reconcile import build_reconcile_dialog

        dlg = build_reconcile_dialog(self.window, self._pending())
        self.assertFalse(dlg._reconcile_btn_applied.isDefault())
        self.assertFalse(
            dlg._reconcile_btn_not_applied.isDefault())
        self.assertFalse(dlg._reconcile_btn_applied.hasFocus())
        self.assertFalse(
            dlg._reconcile_btn_not_applied.hasFocus())

    def test_it_applied_button_decides_true(self):
        from PySide6.QtCore import QTimer
        from gui.pending_reconcile import present_reconcile_dialog

        result = {}
        QTimer.singleShot(
            50, lambda: self._click_visible("_reconcile_btn_applied"))
        result.update(present_reconcile_dialog(
            self.window, self._pending(), timeout_s=30))
        self.assertEqual(result["outcome"], "decided")
        self.assertIs(result["applied"], True)

    def test_it_did_not_apply_button_decides_false(self):
        from PySide6.QtCore import QTimer
        from gui.pending_reconcile import present_reconcile_dialog

        result = {}
        QTimer.singleShot(
            50, lambda: self._click_visible(
                "_reconcile_btn_not_applied"))
        result.update(present_reconcile_dialog(
            self.window, self._pending(), timeout_s=30))
        self.assertEqual(result["outcome"], "decided")
        self.assertIs(result["applied"], False)

    def test_silence_timeout_is_never_a_decision(self):
        from gui.pending_reconcile import present_reconcile_dialog

        result = present_reconcile_dialog(
            self.window, self._pending(), timeout_s=0.02)
        self.assertEqual(result["outcome"], "timeout")
        self.assertIsNone(result["applied"])


if __name__ == "__main__":
    unittest.main()
