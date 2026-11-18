"""Conformance suite for the capability contract (identity-gap P1).

Every test pins the contract (core/brain_capabilities.py) against the
real code it describes: class interfaces, method sources, and — for the
research loop — an AST scan of the production tree. No funnel executes
happen here, so this suite cannot trip the process-global tool budget.

Honest limits, stated in the tests themselves:
* Voice/text loop behavior is pinned at the interface + source level
  (a live Gemini/Groq session cannot be driven headlessly in the lab);
  the tests prove WHICH loop each brain's run_goal calls, not a live
  goal run.
* The research_loop test is deliberately brittle: it pins exactly
  one production caller (core/memory/deep_research.py, the research
  wiring batch) and MUST fail the day a second caller appears,
  so the contract gets updated with it instead of silently
  going stale.
"""

import ast
import inspect
import unittest
from pathlib import Path

from core import brain_capabilities as bc
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/

# Brain modules: imported for interface/source conformance. These are
# the same modules the tree's own suites import; a failure here is a
# real import break, not a reason to skip.
from core.voice import cloud_text_client as _cloud  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice import cloud_text_lifecycle as _cloud_lifecycle  # B39 INTENTIONAL: cloud split — source read covers all modules
from core.voice import cloud_text_turn as _cloud_turn  # B39 INTENTIONAL: cloud split — funnel call lives in turn mixin
from core.voice import cloud_text_goal as _cloud_goal  # B39 INTENTIONAL: cloud split — source read covers all modules
from core.voice import lite_client as _lite  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice import lite_client_helpers as _lite_helpers  # B27 INTENTIONAL: lite_client split — funnel call lives in the session module
from core.voice import lite_client_session as _lite_session  # B27 INTENTIONAL: lite_client split — funnel call lives here
from core.voice import live_client as _live  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice import local_client as _local  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.brain import planner as _planner  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.voice import voice_text_bridge as _bridge

TREE_ROOT = Path(__file__).resolve().parent.parent


class ContractShapeTests(unittest.TestCase):
    def test_four_brains_declared(self):
        self.assertEqual(
            sorted(bc.BRAIN_CONTRACTS),
            ["gemini_live", "groq_text", "lite_first_run", "local_text"])

    def test_every_entry_has_evidence_naming_its_module(self):
        for brain_id, contract in bc.BRAIN_CONTRACTS.items():
            self.assertTrue(contract.evidence.strip(), brain_id)
            # Evidence cites the module by bare filename (e.g. the
            # live_client.py run_goal docstring); the full path is
            # checked by the file-existence assertion below.
            self.assertIn(Path(contract.module).name,
                          contract.evidence, brain_id)
            # The cited module file must really exist in the tree.
            self.assertTrue((TREE_ROOT / contract.module).is_file(), brain_id)

    def test_tool_scopes_are_known_values(self):
        for contract in bc.BRAIN_CONTRACTS.values():
            self.assertIn(contract.tool_scope,
                          ("full", "matched_intents_only"))

    def test_confirm_gated_everywhere_and_no_brain_owns_tasks(self):
        # C-14 applies uniformly: every brain goes through the funnel.
        # Task lifecycle lives in the coordinator (P0), not in any brain.
        for contract in bc.BRAIN_CONTRACTS.values():
            self.assertTrue(contract.confirm_gated, contract.brain_id)
            self.assertFalse(contract.holds_task_registry, contract.brain_id)
        self.assertTrue(hasattr(tc, "TurnCoordinator"))

    def test_engine_map_covers_picker_only(self):
        # GUI picker offers gemini/local only (window_brain.py).
        self.assertEqual(bc.ENGINE_TO_BRAIN,
                         {"gemini": "gemini_live", "local": "local_text"})
        self.assertEqual(bc.contract_for_engine("gemini").brain_id,
                         "gemini_live")
        self.assertEqual(bc.contract_for_engine("local").brain_id,
                         "local_text")
        # groq_text is real (cloud path) but NOT a picker value.
        self.assertIsNone(bc.contract_for_engine("groq"))
        self.assertIsNone(bc.contract_for_engine("bogus"))
        self.assertIsNone(bc.contract_for("bogus"))


class FreeformLoopTests(unittest.TestCase):
    def test_can_freeform_goal_answers(self):
        # INTENTIONAL (voice-brain adoption): the voice brain's
        # STATIC declaration is now True — but CONDITIONAL (see
        # test_voice_freeform_is_conditional below). A blanket True
        # without the condition field would be a batch failure;
        # the condition is asserted alongside the flag.
        self.assertIs(bc.can_freeform_goal("gemini_live"), True)
        _voice = bc.contract_for("gemini_live")
        self.assertIsNotNone(_voice.freeform_goal_condition)
        self.assertIn("bridge", _voice.freeform_goal_condition)
        self.assertIs(bc.can_freeform_goal("local_text"), True)
        self.assertIs(bc.can_freeform_goal("groq_text"), True)
        self.assertIs(bc.can_freeform_goal("lite_first_run"), False)
        # Unknown brains claim nothing (fail-closed, not a default).
        self.assertIsNone(bc.can_freeform_goal(None))
        self.assertIsNone(bc.can_freeform_goal("bogus"))

    def test_voice_freeform_is_conditional(self):
        # INTENTIONAL (voice-brain adoption): the runtime answer
        # for the voice brain follows the bridge state, not the
        # static flag. Bridge state is saved and restored here so
        # no other suite sees the toggle.
        _saved_on = _bridge.is_voice_bridge_enabled()
        _saved_brain = _bridge.get_text_brain()
        try:
            _bridge.disable_voice_bridge()
            self.assertIs(bc.can_freeform_goal_now("gemini_live"),
                          False)
            _bridge.enable_voice_bridge()
            _bridge.set_text_brain(None)
            self.assertIs(bc.can_freeform_goal_now("gemini_live"),
                          False)

            class _StubBrain:
                async def run_goal(self, goal_text):
                    return "done"

            _bridge.set_text_brain(_StubBrain())
            self.assertIs(bc.can_freeform_goal_now("gemini_live"),
                          True)
            # Non-voice brains and unknown ids are unchanged.
            self.assertIs(bc.can_freeform_goal_now("local_text"),
                          True)
            self.assertIs(bc.can_freeform_goal_now("groq_text"),
                          True)
            self.assertIsNone(bc.can_freeform_goal_now("bogus"))
            self.assertIsNone(bc.can_freeform_goal_now(None))
        finally:
            if _saved_on:
                _bridge.enable_voice_bridge()
            else:
                _bridge.disable_voice_bridge()
            _bridge.set_text_brain(_saved_brain)

    # INTENTIONAL (voice-brain adoption): renamed from
    # test_voice_run_goal_is_saved_plan_or_refuse_not_a_loop. The
    # old name claimed run_goal is not a loop; since adoption it
    # CAN loop via the bridge when enabled. What this test actually
    # pins is unchanged and still true: the refusal text is
    # preserved, and run_goal never calls the planner loop
    # directly (planning arrives through the bridge wrapper).
    def test_voice_run_goal_refusal_preserved_no_direct_planner_loop(self):
        # Interface exists (a run_goal method) — and the refusal
        # for the unavailable case is preserved byte-identical.
        self.assertTrue(hasattr(_live.GeminiLiveSession, "run_goal"))
        self.assertTrue(hasattr(_live.GeminiLiveSession, "run_plan"))
        src = inspect.getsource(_live.GeminiLiveSession.run_goal)
        self.assertIn("can't run a free-form goal loop", src)
        # The voice run_goal never calls the planner's free-form loop
        # directly; its only delegations are run_plan (declared steps)
        # and the bridge wrapper (which hands off to a text brain).
        self.assertNotIn("_planner.run_goal", src)
        self.assertIn("run_plan", src)

    def test_voice_bridge_defaults_off(self):
        # The only path from a voice free-form goal to a text loop is
        # explicitly opt-in and starts OFF.
        self.assertIs(_bridge._ENABLED, False)

    def test_text_brains_drive_the_planner_loop(self):
        for cls in (_local.LocalLiveSession, _cloud.GroqSession):
            self.assertTrue(hasattr(cls, "run_goal"))
            self.assertTrue(hasattr(cls, "run_plan"))
            src = inspect.getsource(cls.run_goal)
            self.assertIn("_planner.run_goal", src)
        self.assertTrue(callable(_planner.run_goal))

    def test_lite_has_no_goal_or_plan_entry(self):
        self.assertFalse(hasattr(_lite.LiteLiveSession, "run_goal"))
        self.assertFalse(hasattr(_lite.LiteLiveSession, "run_plan"))


class FunnelAndResearchTests(unittest.TestCase):
    def test_all_brains_execute_through_the_funnel(self):
        # Interface/source conformance: each brain's tool path reaches
        # tools.execute (the funnel carrying confirm, taint, validation,
        # budget, and the gate stack). No live tool runs happen here.
        self.assertIn("tools.execute",
                      inspect.getsource(_live.GeminiLiveSession._execute_tool_sync))
        # B39 INTENTIONAL: cloud split into facade + 3 mixins; the
        # funnel call lives in the turn mixin. Assertion unchanged
        # ("tools.execute" present); the source read now covers all
        # four modules (a superset of the old single-file read),
        # never weakened — same pattern as the lite read below.
        self.assertIn("tools.execute",
                      inspect.getsource(_cloud)
                      + inspect.getsource(_cloud_lifecycle)
                      + inspect.getsource(_cloud_turn)
                      + inspect.getsource(_cloud_goal))
        # B27 INTENTIONAL: lite_client split into facade + helpers +
        # session modules; the funnel call lives in the session module.
        # Assertion unchanged ("tools.execute" present); the source read
        # now covers all three modules (a superset of the old
        # single-file read), never weakened.
        self.assertIn("tools.execute",
                      inspect.getsource(_lite)
                      + inspect.getsource(_lite_helpers)
                      + inspect.getsource(_lite_session))
        self.assertIn("tools.execute", inspect.getsource(_local))

    def test_research_loop_has_exactly_one_production_caller(self):
        # Research wiring batch: INTENTIONAL behavior change — the
        # old test pinned ZERO production callers (the
        # WIRE-EVERYTHING violation). The loop is now wired at
        # tool level: exactly one production call site, in
        # core/memory/deep_research.py (the deep_research tool's
        # implementation). Any other caller appearing here is a
        # second wiring that must be justified, not silently
        # added — AST-based, strings and comments do not count.
        offenders = []
        for area in ("core", "gui"):
            for path in sorted((TREE_ROOT / area).rglob("*.py")):
                # INTENTIONAL (Phase 2 B2-1 rename): exclusion moved with its module (was "research_v568.py").
                if path.name == "research_edition_deltas.py":
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func = node.func
                        name = None
                        if isinstance(func, ast.Name):
                            name = func.id
                        elif isinstance(func, ast.Attribute):
                            name = func.attr
                        if name == "research_loop":
                            offenders.append(str(path))
        # Phase 3 M1a: expected caller moved to core/memory/ (path pin moves
        # with the module; the exactly-one-caller invariant is unchanged).
        self.assertEqual(offenders, [str(TREE_ROOT / "core"
                                          / "memory" / "deep_research.py")])


class CoordinatorRoutingTests(unittest.TestCase):
    MULTI = "First open settings, then enable wifi"

    def test_multi_step_routing_records_contract_answer(self):
        coord = tc.TurnCoordinator()
        r = coord.handle_turn(self.MULTI, brain_id="gemini_live")
        self.assertFalse(r["handled"])
        self.assertEqual(r["routing"], {
            "brain_id": "gemini_live", "live_brain_freeform_goal": False})
        r = coord.handle_turn(self.MULTI, brain_id="local_text")
        self.assertEqual(r["routing"], {
            "brain_id": "local_text", "live_brain_freeform_goal": True})
        r = coord.handle_turn(self.MULTI, brain_id="groq_text")
        self.assertEqual(r["routing"], {
            "brain_id": "groq_text", "live_brain_freeform_goal": True})
        # Unknown brain: routing recorded, loop answer None (no claim).
        r = coord.handle_turn(self.MULTI)
        self.assertEqual(r["routing"], {
            "brain_id": None, "live_brain_freeform_goal": None})
        r = coord.handle_turn(self.MULTI, brain_id="bogus")
        self.assertEqual(r["routing"], {
            "brain_id": "bogus", "live_brain_freeform_goal": None})

    def test_routing_follows_bridge_runtime_state(self):
        # INTENTIONAL (voice-brain adoption): routing records the
        # RUNTIME answer for the voice brain. Bridge state is saved
        # and restored here so no other suite sees the toggle.
        _saved_on = _bridge.is_voice_bridge_enabled()
        _saved_brain = _bridge.get_text_brain()
        try:
            _bridge.disable_voice_bridge()
            coord = tc.TurnCoordinator()
            r = coord.handle_turn(self.MULTI, brain_id="gemini_live")
            self.assertIs(r["routing"]["live_brain_freeform_goal"],
                          False)
            _bridge.enable_voice_bridge()

            class _StubBrain:
                async def run_goal(self, goal_text):
                    return "done"

            _bridge.set_text_brain(_StubBrain())
            r = coord.handle_turn(self.MULTI, brain_id="gemini_live")
            self.assertIs(r["routing"]["live_brain_freeform_goal"],
                          True)
        finally:
            if _saved_on:
                _bridge.enable_voice_bridge()
            else:
                _bridge.disable_voice_bridge()
            _bridge.set_text_brain(_saved_brain)

    def test_non_multi_step_returns_unchanged_shape(self):
        coord = tc.TurnCoordinator()
        for text in ("hello there", "Open the calculator"):
            r = coord.handle_turn(text)
            self.assertFalse(r["handled"])
            # No routing key: the contract decision point is the
            # multi-step fall-through only; other returns are unchanged.
            self.assertNotIn("routing", r)

    def test_typed_turn_brain_id_lookup(self):
        class FakeSession:
            def __init__(self, engine):
                self._engine_value = engine

            def _brain_engine(self):
                if self._engine_value is None:
                    raise RuntimeError("no engine")
                return self._engine_value

        self.assertEqual(
            tc._typed_turn_brain_id(FakeSession("local")), "local_text")
        self.assertEqual(
            tc._typed_turn_brain_id(FakeSession("gemini")), "gemini_live")
        # A session without the method, or a failing lookup, is unknown.
        self.assertIsNone(tc._typed_turn_brain_id(object()))
        self.assertIsNone(tc._typed_turn_brain_id(FakeSession(None)))

    def test_typed_turn_passes_brain_id_to_handle_turn(self):
        # End-to-end through the session-handler entry: the routing
        # record reflects the session's engine, and the turn still
        # falls through (routing never changes behavior).
        seen = {}

        class FakeSession:
            def _brain_engine(self):
                return "gemini"

        class RecCoord(tc.TurnCoordinator):
            def handle_turn(self, text, **kw):
                seen.update(kw)
                return super().handle_turn(text, **kw)

        coord = RecCoord()
        handled = tc.handle_typed_turn(
            FakeSession(), self.MULTI, coordinator=coord)
        self.assertFalse(handled)
        self.assertEqual(seen.get("brain_id"), "gemini_live")


if __name__ == "__main__":
    unittest.main()
