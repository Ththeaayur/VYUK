"""Wiring Build 2b tests (stdlib unittest, no GUI, no network, no model).

What is wired here, and what is NOT claimed: the two text-loop
clients (GroqSession, LocalLiveSession) now pass planner.run_goal's
Build 2 kwargs — goal_context, playbook, interruption_check,
control_snapshot — instead of leaving them dormant. The planner
seam is faked for the pass-through tests (following the existing
regression pattern: the clients' picker/backends are not driven),
and the real planner is used for the J7 / honest-skip semantics
those supplied values drive. No GUI render, no real control drive,
and no field goal is claimed — field proof on the owner's machine
after the BB101 quiz is the exit proof for loop behaviour on real
goals.

Covered, in the task's order:
- Each client entry point passes all four kwargs through to
  planner.run_goal; goal_context comes from the client's current
  UIA observation (fg/focus), playbook is the shared in-memory
  j9 Playbook, interruption_check is the failsafe stop signal,
  control_snapshot is an explicit None (no control-list snapshot
  source exists in the tree — see goal_client_supply).
- An unobserved goal start passes an explicit empty context, not
  a guessed one.
- A playbook hit supplied via the client is actually looked up
  (proven entry, exact goal name); an unproven entry is not
  reusable (j9 enforces this, the client does not bypass it).
- Interruption via the real stop path: tripping the failsafe
  makes the supplied check return a J7-shaped, resume=False
  request; that shape, fed to the real planner, pauses then
  abandons per J7 rules. ALSO documented by a test: in a real
  run the planner's own failsafe halt fires at the step boundary
  before the J7 check is consulted, so a GUI stop halts via the
  failsafe, not via a J7 pause report.
- Missing snapshot still runs: with control_snapshot=None a
  control-named action is honestly skipped by the breadth gate
  (note emitted) and still passes through the registry funnel.
"""

import asyncio
import unittest

from core.voice import cloud_text_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core import failsafe
from core.brain import goal_client_supply as supply  # Phase 3 B1b INTENTIONAL: goal_client_supply moved to core/brain/
from core.voice import local_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.brain import planner  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.brain import planner_checkpoint, planner_rungoal  # INTENTIONAL (skills split B41): impl modules for dual-patching (see setUp notes below)
from core.desktop import uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.funnel.tools_registry import tools as registry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _ClientBase(unittest.TestCase):
    def setUp(self):
        failsafe.reset()
        self._orig_planner_run_goal = planner.run_goal
        self._orig_snapshot = uia.desktop_snapshot
        self._captured = {}

        async def fake_run_goal(goal_text, **kwargs):
            self._captured = dict(kwargs)
            self._captured["goal_text"] = goal_text
            return "fake planner report"

        planner.run_goal = fake_run_goal
        # A known observation at goal start: fg + focus observed.
        uia.desktop_snapshot = lambda: {"fg": "Notepad",
                                        "focus": "Save button"}

    def tearDown(self):
        planner.run_goal = self._orig_planner_run_goal
        uia.desktop_snapshot = self._orig_snapshot
        failsafe.reset()
        # The shared playbook is session state: clear it so one
        # test's proven entry cannot leak into the next.
        supply.shared_playbook()._entries.clear()

    def _assert_supply(self):
        self.assertEqual(
            self._captured.get("goal_context"),
            {"active_window": {"label": "Notepad"},
             "focused_control": {"label": "Save button"}})
        self.assertIs(self._captured.get("playbook"),
                      supply.shared_playbook())
        self.assertIs(self._captured.get("interruption_check"),
                      supply.stop_interruption_check)
        # Code-Perfection Build 2: the clients now pass the real
        # snapshot callable, not None — a snapshot source exists
        # (core/desktop/control_snapshot_p2.py). Calling it here must
        # not raise and must return a list or None (None in this
        # Linux lab, where no automation source exists).
        self.assertIn("control_snapshot", self._captured)
        snap_fn = self._captured.get("control_snapshot")
        self.assertTrue(callable(snap_fn))
        snap = snap_fn()
        self.assertTrue(snap is None or isinstance(snap, list))


class CloudClientTests(_ClientBase):
    def test_cloud_passes_build2_kwargs(self):
        session = cloud_text_client.GroqSession()
        report = _run(session.run_goal("wiring2b cloud goal"))
        self.assertEqual(report, "fake planner report")
        self._assert_supply()

    def test_cloud_empty_context_when_unobserved(self):
        uia.desktop_snapshot = lambda: None
        session = cloud_text_client.GroqSession()
        _run(session.run_goal("wiring2b cloud goal"))
        # Explicit empty context, never a guessed window/control.
        self.assertEqual(self._captured.get("goal_context"), {})


class LocalClientTests(_ClientBase):
    def test_local_passes_build2_kwargs(self):
        session = local_client.LocalLiveSession()
        session._lite_plan_confirmed = True
        report = _run(session.run_goal("wiring2b local goal"))
        self.assertEqual(report, "fake planner report")
        self._assert_supply()


class PlaybookSupplyTests(_ClientBase):
    def test_proven_playbook_hit_is_looked_up_via_client(self):
        book = supply.shared_playbook()
        book.add("wiring2b proven goal",
                 [{"tool": "quick_math", "args": {"x": 1}}],
                 proof_job_id="field-job-2b")

        async def lookup_run_goal(goal_text, **kwargs):
            lookup = kwargs["playbook"].reuse(goal_text)
            return f"ok={lookup.get('ok')} steps={len(lookup.get('steps', []))}"

        planner.run_goal = lookup_run_goal
        session = cloud_text_client.GroqSession()
        report = _run(session.run_goal("wiring2b proven goal"))
        self.assertEqual(report, "ok=True steps=1")

    def test_unproven_playbook_entry_is_not_reusable_via_client(self):
        book = supply.shared_playbook()
        book.add("wiring2b unproven goal",
                 [{"tool": "quick_math", "args": {"x": 1}}])

        async def lookup_run_goal(goal_text, **kwargs):
            lookup = kwargs["playbook"].reuse(goal_text)
            return f"ok={lookup.get('ok')}"

        planner.run_goal = lookup_run_goal
        session = cloud_text_client.GroqSession()
        report = _run(session.run_goal("wiring2b unproven goal"))
        self.assertEqual(report, "ok=False")


class InterruptionSupplyTests(unittest.TestCase):
    def setUp(self):
        failsafe.reset()
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
        failsafe.reset()

    def test_real_stop_path_yields_j7_abandon_shape(self):
        # Untouched: no interruption.
        self.assertIsNone(supply.stop_interruption_check())
        # The real stop path: trip the failsafe, as the goal
        # pill's Stop button does.
        failsafe.trip_now("stopped from the goal pill (test)")
        request = supply.stop_interruption_check()
        self.assertEqual(request.get("resume"), False)
        self.assertIn("goal pill", request.get("reason"))

    def test_stop_shape_pauses_then_abandons_in_real_planner(self):
        # The check the client supplies, driven by the tripped
        # signal's shape (failsafe faked off here so the planner
        # reaches the J7 check instead of its own halt first —
        # see the ordering test below for the real-run order).
        async def pick(goal, observation, history):
            return {"done": "done"}

        report = _run(planner.run_goal(
            "wiring2b stop goal", pick_action=pick,
            observe=lambda: 0,
            interruption_check=lambda: {"reason": "stopped",
                                       "resume": False}))
        self.assertIn("interrupted", report)
        self.assertIn("Goal stopped at step 1: interrupted", report)
        self.assertEqual(self.executions, [])

    def test_real_run_order_failsafe_halt_fires_first(self):
        # Documents the production ordering, plainly: with the
        # failsafe genuinely tripped, planner halts via its own
        # _stop_checks before consulting interruption_check, so a
        # GUI stop halts via the failsafe, not via a J7 report.
        planner._failsafe_tripped = lambda: True
        # INTENTIONAL (skills split B41): same dual-patch as setUp — the
        # driven run_goal resolves _failsafe_tripped from rungoal globals.
        planner_checkpoint._failsafe_tripped = lambda: True
        planner_rungoal._failsafe_tripped = lambda: True

        async def pick(goal, observation, history):
            return {"done": "done"}

        report = _run(planner.run_goal(
            "wiring2b stop goal", pick_action=pick,
            observe=lambda: 0,
            interruption_check=supply.stop_interruption_check))
        self.assertIn("Goal halted: the failsafe is tripped", report)


class MissingSnapshotTests(unittest.TestCase):
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

    def test_no_snapshot_runs_with_honest_skip(self):
        async def pick(goal, observation, history):
            if not self.executions:
                return {"tool": "uia_click_control",
                        "args": {"control_id": "btn-save"}}
            return {"done": "done"}

        emitted = []
        report = _run(planner.run_goal(
            "wiring2b no-snapshot goal", pick_action=pick,
            observe=lambda: 0, emit=emitted.append,
            control_snapshot=None))
        # The gate is honestly skipped (Build 2's exact note) and
        # the action still runs through the registry funnel.
        self.assertTrue(any("control revalidation skipped: no snapshot"
                            in line for line in emitted))
        self.assertEqual(self.executions[0][0], "uia_click_control")


if __name__ == "__main__":
    unittest.main()
