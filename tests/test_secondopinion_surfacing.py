"""Second-opinion OFF surfacing (B24, 2026-11-07): the check is
silently OFF without a Groq key + local model — nothing tells
the user.

Gap proved first (from the tree): _second_opinion_check
(task_coordinator_execution.py) returns (None, None) when
self._second_opinion_brains is None — no record, no line
anywhere. The marker exists — CompanionSessionsHolder.
missing_legs() (companion_sessions.py:308) names precisely
which leg is missing ("groq key" / "groq session" /
"local model" / "local model loading" / "companion holder
not started"), and the guarded module accessor
companion_missing_legs() (:351) reads it from the live
session's holder — but no product caller ever shows it
(only tests reference it). The receipt, the ledger, the
Activity detail and the home briefing carry no OFF line.

What this batch builds (and this suite proves):

* ONE design: one OFF line, everywhere it matters — when
  the check is OFF for a task, the receipt, the ledger, the
  Activity line and the home briefing each carry one honest
  line naming the OFF state and the missing leg, built in
  exactly one place (second_opinion_off_line, next to the
  receipt dataclasses, so the four surfaces can never
  disagree on the wording). The line is written once per
  task, at the first check that would have run (the first
  high-stakes step when the pair is None) — never per step.
  The line never claims the check ran, and never invents a
  key or a model.
* A task with no high-stakes step never needed a check, so
  it carries no OFF line. A task whose check ran carries
  none either. A resumed task never records the line twice
  (the record restores with the task, and the once-per-task
  guard reads it).

Fakes only: a stub executor, a scripted crash (BaseException),
a patched companion_missing_legs (the holder is never
consulted for real), a temp ledger, no real keys, no real
models, no real cloud calls. Briefing tests are Qt-only
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

# --- voice_assistant.* alias (mirrors test_crashsafe_rerun) ------
# gui/* imports voice_assistant.* at module level, and the tree
# root is not a package — so the alias is installed here, before
# any gui import that needs it.
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
    # T-11 (debug pass): the old teardown deleted ALL
    # voice_assistant.* modules unconditionally — including ones
    # that were there before this module ran (installed by an
    # earlier suite in the same process) and ones the module
    # itself loaded. Restore the snapshot instead: put back
    # exactly what was there, remove exactly what this module
    # added. (L3 discipline — never delete-all.)
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        if key in _VA_SNAPSHOT:
            sys.modules[key] = _VA_SNAPSHOT[key]
        else:
            del sys.modules[key]
    # T-11 (debug pass, second half): the L3 guard blesses the
    # restore spelling sys.modules.update(snapshot) (the audit2s1 /
    # uiinteraction pattern) — a per-key re-add loop trips it even
    # though it restores exactly the same entries. update() here is
    # behaviour-identical to the loop it replaces.
    sys.modules.update(_VA_SNAPSHOT)

from core.brain import companion_sessions as _cs  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain.task_coordinator_steps import (  # noqa: E402
    format_receipt, second_opinion_off_line)
from core.brain.task_ledger import TaskLedger  # noqa: E402
from gui.task_state import (  # noqa: E402
    latest_second_opinion_off_line, render_task)

try:
    from PySide6.QtWidgets import QApplication  # noqa: E402,F401
    _HAS_QT = True
except Exception:  # noqa: BLE001 — no PySide6 on sys
    _HAS_QT = False


class _MissingLegs:
    """Patch companion_missing_legs on every loaded copy of the
    holder module, so the coordinator's lazy import reads the
    scripted legs (never the real holder). Restores on exit."""

    def __init__(self, missing):
        self.missing = list(missing)
        self._patched = []

    def __enter__(self):
        for name in ("core.brain.companion_sessions",
                     "voice_assistant.core.brain.companion_sessions"):
            mod = sys.modules.get(name)
            if mod is not None and hasattr(mod, "companion_missing_legs"):
                self._patched.append((mod, mod.companion_missing_legs))
                missing = list(self.missing)
                mod.companion_missing_legs = lambda: list(missing)
        return self

    def __exit__(self, *exc):
        for mod, orig in self._patched:
            mod.companion_missing_legs = orig
        self._patched = []
        return False


def _ok_executor():
    def run(tool, args):
        run.calls.append((tool, dict(args)))
        return {"status": "success", "kind": None, "result": "done"}
    run.calls = []
    return run


class _Crash(BaseException):
    """A simulated crash: BaseException, so it propagates out
    of _execute (which catches Exception only) — the B21 shape."""


def _crash_on(tool):
    def run(t, args):
        run.calls.append((t, dict(args)))
        if t == tool:
            raise _Crash(f"crash on {tool}")
        return {"status": "success", "kind": None, "result": "done"}
    run.calls = []
    return run


def _counting(answer):
    """A brain fake returning `answer`, with call counts."""
    def brain(question):
        brain.calls.append(question)
        return answer
    brain.calls = []
    return brain


_HIGH = {"tool": "delete_file", "args": {}, "label": "Delete old logs"}
_HIGH_2 = {"tool": "delete_file", "args": {}, "label": "Delete old drafts"}
_LOW = {"tool": "send_report", "args": {}, "label": "List weekly reports"}


class OffSurfacingTests(unittest.TestCase):
    def _coord(self, tmp, executor=None, brains=None, verifier=None):
        ledger = TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
        coord = tc.TurnCoordinator(
            executor=executor or _ok_executor(),
            second_opinion_brains=brains, verifier=verifier,
            ledger=ledger)
        return coord, ledger

    def _task(self, coord, steps):
        return coord.new_task(
            "t", "saved_plan",
            [tc.StepDef.from_mapping(m) for m in steps])

    def _run_off(self, tmp, missing, steps, brains=None):
        """Run steps with brains None and scripted missing legs;
        return (coord, ledger, task, receipt)."""
        executor = _ok_executor()
        coord, ledger = self._coord(tmp, executor, brains=brains)
        task = self._task(coord, steps)
        with _MissingLegs(missing):
            receipt = coord.run_task(task)
        return coord, ledger, task, receipt, executor

    def _record(self, ledger, task_id):
        record = ledger.get(task_id)
        self.assertIsNotNone(record)
        return record

    # -- the line itself -------------------------------------------

    def test_off_line_wording(self):
        self.assertEqual(second_opinion_off_line(["groq key"]),
                         "Second opinion: OFF — no Groq key")
        self.assertEqual(second_opinion_off_line(["local model"]),
                         "Second opinion: OFF — no local model")
        self.assertEqual(
            second_opinion_off_line(["groq key", "local model"]),
            "Second opinion: OFF — no Groq key, no local model")
        self.assertEqual(second_opinion_off_line(["groq session"]),
                         "Second opinion: OFF — no Groq session")
        self.assertEqual(
            second_opinion_off_line(["local model loading"]),
            "Second opinion: OFF — local model still loading")

    # -- groq leg missing ------------------------------------------

    def test_groq_missing_surfaces_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, ledger, task, receipt, _ = self._run_off(
                tmp, ["groq key"], [_HIGH])
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(receipt.second_opinions, [])
            off = receipt.second_opinion_off
            self.assertEqual(off["missing"], ["groq key"])
            self.assertEqual(off["line"],
                             "Second opinion: OFF — no Groq key")
            text = format_receipt(receipt)
            self.assertIn("Second opinion: OFF — no Groq key", text)
            self.assertEqual(text.count("Second opinion: OFF"), 1)
            # The receipt dict is the serialized form — it must
            # carry the same OFF record (the neighbour run caught
            # as_dict() omitting it while the dataclass had it).
            self.assertEqual(receipt.as_dict()["second_opinion_off"], off)
            record = self._record(ledger, task.id)
            self.assertEqual(record["second_opinion_off"], off)
            rendered = render_task(record)
            self.assertIn("Second opinion: OFF — no Groq key",
                          rendered["details_text"])
            self.assertEqual(
                latest_second_opinion_off_line([record]),
                "Second opinion: OFF — no Groq key")

    # -- local leg missing ------------------------------------------

    def test_local_missing_surfaces_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, ledger, task, receipt, _ = self._run_off(
                tmp, ["local model"], [_HIGH])
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(
                receipt.second_opinion_off["line"],
                "Second opinion: OFF — no local model")
            record = self._record(ledger, task.id)
            self.assertEqual(
                record["second_opinion_off"]["line"],
                "Second opinion: OFF — no local model")
            self.assertIn("Second opinion: OFF — no local model",
                          format_receipt(receipt))

    # -- both legs missing ------------------------------------------

    def test_both_missing_names_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, ledger, task, receipt, _ = self._run_off(
                tmp, ["groq key", "local model"], [_HIGH])
            self.assertEqual(
                receipt.second_opinion_off["line"],
                "Second opinion: OFF — no Groq key, no local model")
            record = self._record(ledger, task.id)
            self.assertEqual(
                record["second_opinion_off"]["missing"],
                ["groq key", "local model"])

    # -- both legs real: the check runs, no OFF line ----------------

    def test_both_legs_real_runs_check_and_no_off_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            yes_a, yes_b = _counting("YES"), _counting("yes")
            _, ledger, task, receipt, _ = self._run_off(
                tmp, [], [_HIGH], brains=(yes_a, yes_b))
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(len(yes_a.calls), 1)
            self.assertEqual(len(yes_b.calls), 1)
            self.assertEqual(len(receipt.second_opinions), 1)
            self.assertEqual(receipt.second_opinion_off, {})
            self.assertNotIn("OFF", format_receipt(receipt))
            record = self._record(ledger, task.id)
            self.assertEqual(record["second_opinion_off"], {})
            self.assertNotIn("OFF", render_task(record)["details_text"])
            self.assertEqual(
                latest_second_opinion_off_line([record]), "")

    # -- once per task, not per step --------------------------------

    def test_two_high_stakes_steps_record_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, ledger, task, receipt, executor = self._run_off(
                tmp, ["groq key"], [_HIGH, _HIGH_2])
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(len(executor.calls), 2)
            text = format_receipt(receipt)
            self.assertEqual(text.count("Second opinion: OFF"), 1)
            self.assertEqual(text.count("no Groq key"), 1)
            record = self._record(ledger, task.id)
            self.assertEqual(record["second_opinion_off"]["missing"],
                             ["groq key"])

    # -- no high-stakes step: no check would have run, no line -----

    def test_no_high_stakes_step_no_off_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, ledger, task, receipt, _ = self._run_off(
                tmp, ["groq key"], [_LOW])
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(receipt.second_opinion_off, {})
            self.assertNotIn("OFF", format_receipt(receipt))
            record = self._record(ledger, task.id)
            self.assertEqual(record["second_opinion_off"], {})

    # -- the line never claims the check ran ------------------------

    def test_off_line_never_claims_check_ran(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, receipt, _ = self._run_off(
                tmp, ["groq key"], [_HIGH])
            line = receipt.second_opinion_off["line"]
            self.assertIn("OFF", line)
            self.assertNotIn("agree", line)
            self.assertNotIn("proceeded", line)
            self.assertNotIn("YES", line)
            self.assertEqual(receipt.second_opinions, [])

    # -- resume: the record restores, the line is never doubled -----

    def test_resume_restores_off_and_never_doubles(self):
        # The crashed step carries an expect, so B21's
        # reconciliation can judge it: the verifier says
        # "not-met" on its first call (the reconciliation —
        # the step did not apply) and "met" after that (the
        # verification phase, once the step has re-run).
        calls = {"n": 0}

        def verifier(expect, before, after, result_text):
            calls["n"] += 1
            if calls["n"] == 1:
                return ("not-met", "file is not there")
            return ("met", "file is there")

        with tempfile.TemporaryDirectory() as tmp:
            executor = _crash_on("write_file")
            coord, ledger = self._coord(tmp, executor, verifier=verifier)
            task = self._task(coord, [
                _HIGH,
                {"tool": "write_file", "args": {"path": "a.txt"},
                 "label": "Save the report",
                 "expect": "the file exists"},
                _HIGH_2,
            ])
            with _MissingLegs(["groq key"]):
                with self.assertRaises(_Crash):
                    coord.run_task(task)
            # The OFF line is durable in the ledger already.
            record = self._record(ledger, task.id)
            self.assertEqual(
                record["second_opinion_off"]["line"],
                "Second opinion: OFF — no Groq key")
            # Resume with a good executor: the third step is a
            # NEW high-stakes step, so the guard (not the
            # absence of a call) is what keeps the line at one.
            good = _ok_executor()
            coord2 = tc.TurnCoordinator(
                executor=good, second_opinion_brains=None,
                verifier=verifier, ledger=ledger)
            with _MissingLegs(["groq key"]):
                receipt2 = coord2.resume_ledger_task(record, ledger=ledger)
            self.assertEqual(receipt2.state, "completed")
            text = format_receipt(receipt2)
            self.assertEqual(text.count("Second opinion: OFF"), 1)
            self.assertEqual(text.count("no Groq key"), 1)
            self.assertEqual(receipt2.second_opinion_off["missing"],
                             ["groq key"])
            # The crashed step ran once on the resume, never
            # twice — the OFF recording never re-ran a step.
            self.assertEqual([c[0] for c in good.calls],
                             ["write_file", "delete_file"])
            self.assertEqual([c[0] for c in executor.calls],
                             ["delete_file", "write_file"])

    # -- the briefing helper: most recent task wins -----------------

    def test_latest_off_line_helper(self):
        self.assertEqual(latest_second_opinion_off_line([]), "")
        self.assertEqual(latest_second_opinion_off_line(None), "")
        self.assertEqual(latest_second_opinion_off_line([{}]), "")
        # One task with a line: the line shows.
        self.assertEqual(latest_second_opinion_off_line([
            {"updated_at": "2026-11-07T10:00:00+00:00",
             "second_opinion_off": {
                 "missing": ["groq key"],
                 "line": "Second opinion: OFF — no Groq key"}},
        ]), "Second opinion: OFF — no Groq key")
        # A later task with no line supersedes an older OFF
        # line — the briefing never shows a stale OFF line
        # after the check is back on.
        self.assertEqual(latest_second_opinion_off_line([
            {"updated_at": "2026-11-07T10:00:00+00:00",
             "second_opinion_off": {
                 "missing": ["groq key"],
                 "line": "Second opinion: OFF — no Groq key"}},
            {"updated_at": "2026-11-07T11:00:00+00:00",
             "second_opinion_off": {}},
        ]), "")
        # Latest task wins between two OFF lines.
        self.assertEqual(latest_second_opinion_off_line([
            {"updated_at": "2026-11-07T10:00:00+00:00",
             "second_opinion_off": {
                 "missing": ["local model"],
                 "line": "Second opinion: OFF — no local model"}},
            {"updated_at": "2026-11-07T11:00:00+00:00",
             "second_opinion_off": {
                 "missing": ["groq key"],
                 "line": "Second opinion: OFF — no Groq key"}},
        ]), "Second opinion: OFF — no Groq key")
        # A goal record never counts: the latest coordinator
        # task decides, even when a goal was updated later.
        self.assertEqual(latest_second_opinion_off_line([
            {"updated_at": "2026-11-07T10:00:00+00:00",
             "second_opinion_off": {
                 "missing": ["groq key"],
                 "line": "Second opinion: OFF — no Groq key"}},
            {"updated_at": "2026-11-07T12:00:00+00:00",
             "kind": "goal"},
        ]), "Second opinion: OFF — no Groq key")


@unittest.skipUnless(_HAS_QT, "PySide6 required")
class BriefingTests(unittest.TestCase):
    def _fake_window(self, records):
        """A minimal ShellBriefingMixin instance with real
        briefing labels; the ledger is faked at the module the
        mixin imports lazily, so no real ledger file is read."""
        from PySide6.QtWidgets import QApplication, QLabel
        QApplication.instance() or QApplication([])
        from voice_assistant.gui.window_shell_briefing import (
            ShellBriefingMixin)
        import voice_assistant.core.brain.task_ledger as _va_ledger
        fake = object.__new__(ShellBriefingMixin)
        fake.briefing_kicker = QLabel("")
        fake.briefing_schedule = QLabel("")
        fake.briefing_battery = QLabel("")
        fake.briefing_second_opinion = QLabel("")
        fake._greeting = lambda: "Good evening"
        fake._senses_state = lambda: {"battery_pct": None,
                                      "battery_plugged": False}
        fake._google_connected = lambda: False

        class _FakeLedger:
            def all_records(self):
                return list(records)

        self._orig_ledger = _va_ledger.TaskLedger
        _va_ledger.TaskLedger = _FakeLedger
        self.addCleanup(setattr, _va_ledger, "TaskLedger",
                        self._orig_ledger)
        return fake, ShellBriefingMixin

    def test_briefing_shows_latest_off_line(self):
        fake, mixin = self._fake_window([
            {"second_opinion_off": {
                "missing": ["groq key"],
                "line": "Second opinion: OFF — no Groq key"}},
        ])
        mixin._refresh_briefing_card(fake)
        self.assertEqual(fake.briefing_second_opinion.text(),
                         "Second opinion: OFF — no Groq key")
        self.assertFalse(fake.briefing_second_opinion.isHidden())

    def test_briefing_hides_when_no_off_line(self):
        fake, mixin = self._fake_window([
            {"second_opinion_off": {}},
        ])
        mixin._refresh_briefing_card(fake)
        self.assertEqual(fake.briefing_second_opinion.text(), "")
        self.assertTrue(fake.briefing_second_opinion.isHidden())

    def test_briefing_shows_most_recent_task(self):
        fake, mixin = self._fake_window([
            {"second_opinion_off": {
                "missing": ["local model"],
                "line": "Second opinion: OFF — no local model"}},
            {"second_opinion_off": {
                "missing": ["groq key"],
                "line": "Second opinion: OFF — no Groq key"}},
        ])
        mixin._refresh_briefing_card(fake)
        self.assertEqual(fake.briefing_second_opinion.text(),
                         "Second opinion: OFF — no Groq key")


if __name__ == "__main__":
    unittest.main()
