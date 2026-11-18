"""GAPS-6: per-query model routing (BLOCKS #5, routing half).

The scorer ranks a hard query above a trivial one; the router
picks lite for trivial, balanced for routine, quality for hard;
a cheap query never falls up to quality; the fallbacks are
today's behaviour, stated; every decision is recorded with its
score, rule and reason. Stdlib only, no model call.
"""

import unittest

from core import model_manager as mm
from core import query_routing as qr

TRIVIAL = "what time is it"
ROUTINE = "Write a short email to my professor asking for an extension"
HARD = ("Explain step by step how to compare the pros and cons of "
        "investing in index funds versus individual stocks, and "
        "calculate the expected return for a 10-year horizon with "
        "monthly contributions, showing your working in detail")
ALL = {"quality": True, "balanced": True, "lite": True}


class ScorerTests(unittest.TestCase):
    def test_hard_scores_above_trivial(self):
        self.assertGreater(qr.score_query(HARD)["score"],
                           qr.score_query(TRIVIAL)["score"])

    def test_bands(self):
        self.assertEqual(qr.score_query(TRIVIAL)["band"], "trivial")
        self.assertEqual(qr.score_query(ROUTINE)["band"], "routine")
        self.assertEqual(qr.score_query(HARD)["band"], "hard")

    def test_deterministic(self):
        self.assertEqual(qr.score_query(HARD), qr.score_query(HARD))

    def test_empty_scores_zero(self):
        scored = qr.score_query("")
        self.assertEqual(scored["score"], 0.0)
        self.assertEqual(scored["band"], "trivial")

    def test_hints_raise_a_troubled_turn(self):
        plain = qr.score_query("What is the capital of France?")
        troubled = qr.score_query(
            "What is the capital of France?",
            {"repair_rounds": 1, "prior_turn_failed": True})
        self.assertGreater(troubled["score"], plain["score"])

    def test_short_control_command_stays_trivial(self):
        self.assertEqual(qr.score_query("turn off the bedroom light")
                         ["band"], "trivial")
        self.assertEqual(qr.score_query("set a timer for ten minutes")
                         ["band"], "trivial")


class RouterTests(unittest.TestCase):
    def test_tier_per_band(self):
        self.assertEqual(
            qr.route_query(TRIVIAL, availability=ALL,
                           effective_tier="balanced")["tier"], "lite")
        self.assertEqual(
            qr.route_query(ROUTINE, availability=ALL,
                           effective_tier="balanced")["tier"], "balanced")
        self.assertEqual(
            qr.route_query(HARD, availability=ALL,
                           effective_tier="balanced")["tier"], "quality")

    def test_cheap_query_never_gets_quality_by_default(self):
        decision = qr.route_query(TRIVIAL, availability=ALL,
                                  effective_tier="balanced")
        self.assertNotEqual(decision["tier"], "quality")

    def test_fallback_is_nearest_cheaper_first(self):
        # lite missing: trivial falls to balanced, never quality.
        self.assertEqual(
            qr.route_query(TRIVIAL,
                           availability={"quality": True,
                                         "balanced": True},
                           effective_tier="balanced")["tier"], "balanced")
        # balanced missing: routine falls to lite.
        self.assertEqual(
            qr.route_query(ROUTINE,
                           availability={"quality": True, "lite": True},
                           effective_tier="balanced")["tier"], "lite")
        # quality missing: hard falls to balanced.
        self.assertEqual(
            qr.route_query(HARD,
                           availability={"balanced": True, "lite": True},
                           effective_tier="balanced")["tier"], "balanced")

    def test_routed_file_is_a_known_tier_file(self):
        for text in (TRIVIAL, ROUTINE, HARD):
            decision = qr.route_query(text, availability=ALL,
                                      effective_tier="balanced")
            self.assertIn(decision["file"], set(mm.TIER_FILES))

    def test_reason_names_the_rule_and_the_score(self):
        decision = qr.route_query(HARD, availability=ALL,
                                  effective_tier="balanced")
        self.assertEqual(decision["rule"], "score-band")
        self.assertIn("hard", decision["reason"])
        self.assertIn(str(decision["score"]), decision["reason"])

    def test_resident_model_boundary_is_stated(self):
        decision = qr.route_query(HARD, availability=ALL,
                                  effective_tier="balanced")
        self.assertIn("stays until the next model load",
                      decision["reason"])


class FallbackTests(unittest.TestCase):
    def test_routing_off_uses_todays_behaviour(self):
        decision = qr.route_query(HARD, availability=ALL,
                                  effective_tier="balanced", enabled=False)
        self.assertEqual(decision["tier"], "balanced")
        self.assertEqual(decision["rule"], "routing-disabled")
        self.assertFalse(decision["routed"])
        self.assertTrue(decision["fallback"])

    def test_empty_query_is_undecided(self):
        decision = qr.route_query("", availability=ALL,
                                  effective_tier="balanced")
        self.assertEqual(decision["tier"], "balanced")
        self.assertEqual(decision["rule"], "undecided")
        self.assertTrue(decision["fallback"])

    def test_user_pick_wins(self):
        decision = qr.route_query(HARD, availability=ALL,
                                  effective_tier="balanced",
                                  user_tier="lite")
        self.assertEqual(decision["tier"], "lite")
        self.assertEqual(decision["rule"], "user-pick")
        self.assertFalse(decision["routed"])

    def test_nothing_available_falls_back_honestly(self):
        decision = qr.route_query(TRIVIAL, availability={},
                                  effective_tier="balanced")
        self.assertEqual(decision["tier"], "balanced")
        self.assertEqual(decision["rule"], "unavailable")
        self.assertTrue(decision["fallback"])

    def test_route_for_turn_never_raises(self):
        decision = qr.route_for_turn(HARD, availability=None,
                                     effective_tier="balanced",
                                     data_dir="/nonexistent-dir")
        # No discovery data: nothing is known available, so the
        # hard query falls back to the effective tier honestly
        # instead of raising or inventing a model.
        self.assertEqual(decision["tier"], "balanced")
        self.assertEqual(decision["rule"], "unavailable")
        self.assertTrue(decision["fallback"])


class RecordingTests(unittest.TestCase):
    def test_last_decision_tracks_routing(self):
        made = qr.route_query(HARD, availability=ALL,
                              effective_tier="balanced")
        last = qr.last_decision()
        self.assertIsNotNone(last)
        self.assertEqual(last["tier"], made["tier"])
        self.assertEqual(last["rule"], made["rule"])
        self.assertEqual(last["reason"], made["reason"])

    def test_history_is_bounded_and_most_recent_first(self):
        for _ in range(25):
            qr.route_query(TRIVIAL, availability=ALL,
                           effective_tier="balanced")
        history = qr.decision_history()
        self.assertLessEqual(len(history), 20)
        self.assertEqual(history[0]["tier"], "lite")


class ConfigTests(unittest.TestCase):
    def test_query_routing_flag_exists_and_defaults_on(self):
        from core.config import config
        self.assertIs(config.query_routing, True)

    def test_turn_wiring_consults_the_router(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "core",
                            "voice", "local_client_mic.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("route_for_turn", src)
        self.assertIn("self._active_routing", src)
        # The wiring sits next to the profile decision.
        self.assertLess(src.index("_active_profile"),
                        src.index("_active_routing"))


if __name__ == "__main__":
    unittest.main()
