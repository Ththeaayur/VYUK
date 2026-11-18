"""Brain-plan capture-stamp pins (2026-11-05).

QUESTION: which turn's screenshot did the model see when it
issued a coordinate action — i.e. which capture id may a
coordinator step HONESTLY carry as ``source_capture_id``?

ANSWER (derived from code reads 2026-11-05; the parent
verifies it from disk): NONE. No product construction site
provably knows which capture the model saw, so NO site is
stamped. Stamping any site below with the current slot id
would be the dishonest "latest capture" guess: the slot holds
only the LATEST sent frame, while the step's coordinates were
authored against some unknown earlier screen — and stamping
at load/dispatch time would make the C7 binding tautological
(the id would always match because the stamper just wrote
it), laundering unauthenticated coordinates through a
rigorous-looking check. That false confidence is worse than
the honest absence the producer core already implements
(no id -> no crop -> C6 region-unobserved for destructive
points, live re-proof otherwise).

TURN-LAYER MAP (every coordinator step-construction site):

1. Resolver path (task_coordinator_lifecycle.py:419,
   StepDef.from_mapping over resolver output) — NOT
   STAMPABLE. saved_plan steps come from the persistent
   playbook store (playbook.reuse) and routine steps from
   planner get_routines actions: pre-authored step dicts
   whose coordinates were written at authoring time, with no
   capture context attached and no capture surviving to load
   time. The screen at execution time is certainly not the
   screen at authoring time.
2. Ledger restore (task_ledger.py:545, StepDef.from_mapping
   over stored records) — NOT STAMPABLE. Same reason, worse:
   restored across sessions; the authoring capture is gone.
3. Voice-goal tasks (voice_goal.py:196, new_task with [] steps)
   — NOTHING TO STAMP. Goal-level record only; the text loop
   picks steps internally and executes funnel-direct, never
   as coordinator steps.
4. Typed turns (task_coordinator_tail.py handle_typed_turn ->
   handle_turn) — NOT STAMPABLE. Turns that become coordinator
   tasks go through the SAME resolver path (site 1); other
   turns (multi_step / conversation / single_action) are not
   handled by the coordinator at all and flow to the live
   session.
5. Live loop (live_client_receive._execute_tool_sync ->
   tools.execute) — NOT A COORDINATOR SITE. The model DOES
   see screenshot images here and DOES issue cursor_control
   afterwards — but funnel-direct, never as a StepDef, so
   there is nothing to stamp. (Boundary note: the funnel
   never imports control_existence, so these calls are
   entirely outside C7 verification. That is a separate
   coverage question, not a stamping site.)
6. Text goal loops (planner.run_goal, cloud/local text goals)
   — NOT A COORDINATOR SITE. Observations are TEXT (UIA
   desktop_snapshot + marks table, no pixels, no capture id
   in the text) and picked actions execute funnel-direct,
   never as StepDefs.
7. Teach sequences — NO COORDINATOR CONSTRUCTION AT ALL
   (no new_task / StepDef / run_task references).
8. §7 engines (menu/file_dialog/system_surface/drag_drop/
   context_menu) and click_by_mark — NOT PRODUCER SITES
   (handle/menu re-proof, never kind:point targets).
9. window_control — HONEST ABSENCE (action + title
   substring; no coordinates exist to crop around).

UNBLOCKING CONDITION (not an unclear item): a turn-layer
site that hands the model a specific screenshot AND receives
that model's coordinate actions back in the same turn AND
builds coordinator steps from them. The Live loop has the
first two properties but builds no steps; if a future change
routes Live-loop model actions through coordinator steps,
THAT loop is where stamping belongs — each step stamped
with the capture id of the screenshot just shown. Until
then, the producer core (slot + cutter + C3/pending attach)
correctly stays dark for live steps, and these pins force
any future stamping work to confront the map rather than
guess.

RESIDUAL (documented, NOT fixed here): from_mapping passes
args verbatim, so a HAND-AUTHORED source_capture_id inside
a stored plan/routine/ledger record would flow through to
the step. No producer writes such records, and the id only
serves when it equals the retained slot id — but it is
data, not a stamp, and a future hardening pass may choose
to validate or strip it. That choice is verifier-adjacent
semantics, out of this batch's stamping-only scope.
"""

import unittest

from core.brain import task_coordinator as tc
from core.brain.task_coordinator_steps import StepDef
from core.desktop import point_crop as _pc


def _slot(capture_id, width=10, height=10):
    """Fake retained sent-frame slot: 10x10 gray ramp."""
    gray = bytes([i % 256 for i in range(width * height)])
    record = {
        "screen_width": 100,
        "screen_height": 100,
        "image_width": width,
        "image_height": height,
    }
    return {
        "capture_id": capture_id,
        "width": width,
        "height": height,
        "gray": gray,
        "record": record,
    }


def _plan_mapping():
    """Saved-plan-shaped step: cursor_control coords, no id."""
    return {"tool": "cursor_control",
            "args": {"x": 500, "y": 500},
            "label": "Click the button"}


def _routine_mapping():
    """Routine-action-shaped step: mouse_control pixels, no id."""
    return {"tool": "mouse_control",
            "args": {"x": 50, "y": 50},
            "label": "Move to the button"}


class StampAbsenceTests(unittest.TestCase):
    def test_plan_shaped_mapping_carries_no_id(self):
        # INTENTIONAL (brain stamp 2026-11-05): resolver-path
        # steps are pre-authored; construction adds no id.
        step = StepDef.from_mapping(_plan_mapping())
        self.assertNotIn("source_capture_id", step.args)
        self.assertIsNone(
            _pc.stored_crop_for_step(
                step.tool, step.args, _slot(7)))

    def test_routine_shaped_mapping_carries_no_id(self):
        # INTENTIONAL (brain stamp 2026-11-05): routine
        # actions are pre-authored; construction adds no id.
        step = StepDef.from_mapping(_routine_mapping())
        self.assertNotIn("source_capture_id", step.args)
        self.assertIsNone(
            _pc.stored_crop_for_step(
                step.tool, step.args, _slot(7)))

    def test_ledger_shaped_mapping_carries_no_id(self):
        # INTENTIONAL (brain stamp 2026-11-05): ledger
        # restore rebuilds the mapping then from_mapping;
        # the mapping shape is what matters, and it has no id.
        raw = {"tool": "cursor_control",
               "args": {"x": 500, "y": 500},
               "label": "Click the button",
               "on_fail": "halt"}
        mapping = {"tool": raw.get("tool", ""),
                   "args": raw.get("args", {}) or {},
                   "label": raw.get("label", "") or raw.get("tool", ""),
                   "on_fail": raw.get("on_fail", "halt") or "halt"}
        step = StepDef.from_mapping(mapping)
        self.assertNotIn("source_capture_id", step.args)
        self.assertIsNone(
            _pc.stored_crop_for_step(
                step.tool, step.args, _slot(7)))

    def test_construction_does_not_stamp(self):
        # INTENTIONAL (brain stamp 2026-11-05): the general
        # rule — from_mapping never invents an id, whatever
        # the mapping shape.
        for mapping in (_plan_mapping(), _routine_mapping()):
            step = StepDef.from_mapping(mapping)
            self.assertEqual(step.args, mapping["args"])
            self.assertNotIn("source_capture_id", step.args)

    def test_voice_goal_task_has_no_steps(self):
        # INTENTIONAL (brain stamp 2026-11-05): voice-goal
        # tasks are goal-level records with [] steps (the
        # text loop picks steps internally, funnel-direct).
        task = tc.TurnCoordinator(ledger=None).new_task(
            "do the thing", "multi_step", [], turn_text="do it")
        self.assertEqual(task.steps, [])

    def test_dispatch_does_not_stamp(self):
        # INTENTIONAL (brain stamp 2026-11-05): dispatch
        # copies args + injects only the declared handle;
        # it never writes a capture id (dispatch-time
        # stamping would make C7 binding tautological).
        coord = tc.TurnCoordinator(ledger=None)
        step = StepDef.from_mapping(_plan_mapping())
        dispatched = coord._dispatch_args(step)
        self.assertNotIn("source_capture_id", dispatched)
        self.assertNotIn("source_capture_id", step.args)

    def test_passthrough_is_not_stamping(self):
        # INTENTIONAL (brain stamp 2026-11-05): a
        # HAND-AUTHORED id inside stored args flows through
        # from_mapping verbatim — that is passthrough, not a
        # stamp. No producer writes such records; see the
        # module docstring residual.
        mapping = {"tool": "cursor_control",
                   "args": {"x": 500, "y": 500,
                            "source_capture_id": 7},
                   "label": "Click the button"}
        step = StepDef.from_mapping(mapping)
        self.assertEqual(step.args["source_capture_id"], 7)


class BindingIntegrityTests(unittest.TestCase):
    def test_newer_capture_does_not_bind(self):
        # INTENTIONAL (brain stamp 2026-11-05): a step
        # stamped with capture N serves NOTHING once the
        # slot holds N+1 — the binding is to N's pixels,
        # never to whatever frame is latest.
        args = {"x": 500, "y": 500, "source_capture_id": 7}
        self.assertIsNone(
            _pc.stored_crop_for_step("cursor_control", args, _slot(8)))

    def test_matching_capture_serves_grid(self):
        # INTENTIONAL (brain stamp 2026-11-05): the
        # positive control — id == slot id + one primary
        # point serves a real grid.
        args = {"x": 500, "y": 500, "source_capture_id": 7}
        grid = _pc.stored_crop_for_step(
            "cursor_control", args, _slot(7))
        self.assertIsNotNone(grid)
        for row in grid:
            for value in row:
                self.assertIsInstance(value, int)
                self.assertGreaterEqual(value, 0)
                self.assertLessEqual(value, 255)

    def test_no_id_serves_nothing_despite_slot(self):
        # INTENTIONAL (brain stamp 2026-11-05): a filled
        # slot alone stamps nothing — the step must name
        # the capture, and live steps name none.
        args = {"x": 500, "y": 500}
        self.assertIsNone(
            _pc.stored_crop_for_step("cursor_control", args, _slot(7)))

    def test_window_control_has_no_point(self):
        # INTENTIONAL (brain stamp 2026-11-05): honest
        # absence — window_control carries no coordinates,
        # so even an id + slot yield no crop.
        args = {"action": "focus", "title_substring": "Notes",
                "source_capture_id": 7}
        self.assertIsNone(_pc.primary_point_for_step(
            "window_control", args))
        self.assertIsNone(
            _pc.stored_crop_for_step("window_control", args, _slot(7)))

    def test_multi_point_path_serves_nothing(self):
        # INTENTIONAL (brain stamp 2026-11-05): a path is
        # not a spot — multi-point steps never serve a
        # single-spot crop, even with a matching id.
        args = {"points": [[10, 10], [20, 20]],
                "source_capture_id": 7}
        self.assertIsNone(_pc.primary_point_for_step(
            "cursor_control", args))
        self.assertIsNone(
            _pc.stored_crop_for_step("cursor_control", args, _slot(7)))


if __name__ == "__main__":
    unittest.main()
