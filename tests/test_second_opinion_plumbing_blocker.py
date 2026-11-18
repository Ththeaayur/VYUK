"""Second-opinion companion-capability pins (owner ruling 2026-11-05).

The plumbing batch (2026-11-05) proved from disk that the ruled
wiring — cloud text session + local session via
second_opinion_from_sessions, default-on — CANNOT land yet: no
(cloud, local) pair was obtainable at any coordinator construction
site. It pinned that blocker here.

The companion-session capability batch (2026-11-05, same day)
LANDED the capability: one provider-blind companion holder,
started alongside the primary session, holding the (Groq, local)
pair (core/brain/companion_sessions.py), with the pair supplied
at the TurnCoordinator construction site. This file was UPDATED
with INTENTIONAL comments at that landing — the "no pair
obtainable" pins became "pair obtainable when..." pins. They were
NOT deleted to make the suite green.

What this pins now (and what it does NOT prove):
1. The production-status marker names the companion capability
   and the legs that gate the pair (Groq key + landed local
   model), not the plumbing-batch blocker.
2. Exactly ONE production TurnCoordinator construction supplies
   second_opinion_brains — the construction site in
   task_coordinator_tail.py, from the holder (AST scan of
   core/brain + gui). The scan is the pin — it does NOT prove
   the pair is real; the batch's prover proves that from the
   holder's key/model gating and the factory contract.
3. The live-session proxy exposes the companion holder at rest
   (_companion_holder, None before start) plus the companion_pair
   / companion_missing_legs accessors. It does NOT prove the
   engine matrix (gemini-only inner by default, groq/local only
   as the sole inner) — again, the prover proves that.

Stdlib unittest only. No live session is started; no store touched.
"""

import ast
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent


def _turn_coordinator_calls():
    """(relative path, lineno, passes second_opinion_brains) per
    TurnCoordinator( call in core/brain + gui (production code)."""
    hits = []
    for sub in ("core/brain", "gui"):
        for path in sorted((TREE / sub).rglob("*.py")):
            parsed = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(parsed):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name != "TurnCoordinator":
                    continue
                supplied = any(
                    kw.arg == "second_opinion_brains"
                    for kw in node.keywords)
                hits.append((str(path.relative_to(TREE)), node.lineno,
                             supplied))
    return hits


class MarkerCapabilityTests(unittest.TestCase):
    def test_marker_names_companion_capability(self):
        # INTENTIONAL (companion-session capability batch,
        # 2026-11-05): the plumbing batch pinned the marker naming
        # the companion BLOCKER; the capability batch replaces it
        # — the marker now names the capability and the two legs
        # that gate the pair (Groq key + landed local model), and
        # the blocker paragraph is gone.
        from core.w8_wiring_gates import SECOND_OPINION_TRIGGER_OWED
        self.assertIn("companion-session capability",
                      SECOND_OPINION_TRIGGER_OWED)
        self.assertIn("Groq key", SECOND_OPINION_TRIGGER_OWED)
        self.assertIn("local model", SECOND_OPINION_TRIGGER_OWED)
        self.assertNotIn("BLOCKER (plumbing batch, 2026-11-05)",
                         SECOND_OPINION_TRIGGER_OWED)
        self.assertIn("gated, not re-decided",
                      SECOND_OPINION_TRIGGER_OWED)

    def test_marker_still_records_ruling(self):
        # The capability does not re-decide the ruling: the marker
        # still records the ruled conjunction and default-on, and
        # the check stays OFF in production whenever the pair is
        # missing (either leg absent means pair None).
        from core.w8_wiring_gates import SECOND_OPINION_TRIGGER_OWED
        self.assertIn("RULED 2026-11-05", SECOND_OPINION_TRIGGER_OWED)
        self.assertIn("OFF in production", SECOND_OPINION_TRIGGER_OWED)


class ProductionSiteSuppliesBrainsTests(unittest.TestCase):
    def test_production_site_supplies_brains_from_holder(self):
        # INTENTIONAL (companion-session capability batch,
        # 2026-11-05): the plumbing batch pinned NO production
        # site supplying second_opinion_brains; the capability
        # batch inverts it — exactly ONE site supplies them, the
        # TurnCoordinator construction site in
        # task_coordinator_tail.py, from the companion holder's
        # pair via the existing factory. Any second supplying
        # site, or the tail site going silent, fails this pin.
        hits = _turn_coordinator_calls()
        # Non-vacuous: the known production sites must be found.
        files = {h[0] for h in hits}
        self.assertIn("core/brain/task_coordinator_tail.py", files)
        self.assertIn("core/brain/voice_goal.py", files)
        self.assertIn("gui/pending_approval.py", files)
        self.assertGreaterEqual(len(hits), 4)
        supplying = [h for h in hits if h[2]]
        self.assertEqual(len(supplying), 1,
                         f"sites supplying brains: {supplying}")
        self.assertEqual({h[0] for h in supplying},
                         {"core/brain/task_coordinator_tail.py"})


class CompanionHolderTests(unittest.TestCase):
    def test_live_session_exposes_companion_holder(self):
        # INTENTIONAL (companion-session capability batch,
        # 2026-11-05): the plumbing batch pinned the proxy exposing
        # NO companion holder; the capability batch inverts it —
        # the BrainSession proxy owns _companion_holder (None at
        # rest, created lazily on first start) and exposes the
        # companion_pair() / companion_missing_legs() accessors
        # (None / "not started" at rest). The primary inner and
        # the warm standby are still None at rest — the holder
        # never disturbs them.
        from core.brain.brain import live_session
        self.assertIsNone(live_session._inner)
        self.assertIsNone(live_session._standby_inner)
        self.assertTrue(hasattr(live_session, "_companion_holder"),
                        "_companion_holder")
        self.assertIsNone(live_session._companion_holder)
        self.assertIsNone(live_session.companion_pair())
        self.assertEqual(live_session.companion_missing_legs(),
                         ["companion holder not started"])


if __name__ == "__main__":
    unittest.main()
