"""Phase 3 tests: C06 tier-routing REMOVAL + 7th-B09 feature classifier.

C06 VERDICT: REMOVED (see module docstring of the report for the
evidence). The four-tier per-turn router (core/router_v563.py) was
never imported by any turn path, had no tests, its ONNX engine
could not run (no artefact, no runtime, licence OWED), its
deep-think tier had no engine, and its only runnable engine was
a word-count heuristic of exactly the kind 7th-B09 replaces.
What users see as "tiers" — the local Brain-size picker and
local_assets.MODEL_TIERS — is model-file selection, wired and
legitimate, and is preserved, not removed.

7th-B09: classify_turn_profile is now feature-based. Text shape
is one input among several; turn evidence (prior failure,
context pressure, tool use, repairs, bad output, unreliable
output) decides with it. Escalation is monotonic: once think,
a turn never demotes.
"""

import ast
import re
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent

# --- The replaced rule, snapshotted for disagreement tests --------
# This is the pre-Phase-3 classify_turn_profile, copied verbatim
# in logic (not imported — the function no longer exists in this
# form). It exists ONLY so the tests can prove where the new
# classifier deliberately disagrees with it.
_OLD_THINK_RE = re.compile(
    r"\b(why|how come|explain|compare|difference|reason|analyse|analyze|"
    r"think|plan|strategy|calculate|compute|solve|prove|debug|fix|"
    r"should i|which is better|pros and cons|step by step|write|draft|"
    r"compose|summarise|summarize|translate|idea|ideas|brainstorm)\b",
    re.IGNORECASE)
_OLD_QUICK_RE = re.compile(
    r"\b(time|date|day|battery|volume|brightness|mute|unmute|lock|unlock|"
    r"open|launch|close|play|pause|stop|resume|next|previous|turn|set|"
    r"switch|wifi|weather|remind|timer|alarm|screenshot|clipboard)\b",
    re.IGNORECASE)


def _old_classify(user_text, after_repair=False):
    if after_repair:
        return "think"
    text = " ".join(str(user_text or "").split())
    if not text:
        return "quick"
    words = text.split()
    questions = text.count("?")
    if _OLD_THINK_RE.search(text):
        return "think"
    if len(words) > 25 or questions >= 2:
        return "think"
    if _OLD_QUICK_RE.search(text) or len(words) <= 12:
        return "quick"
    return "think"


from core.voice.local_client import (  # noqa: E402  # Phase 3 V1b INTENTIONAL: moved to core/voice/
    TurnProfileFeatures,
    classify_turn_from_features,
    classify_turn_profile,
    context_pressure_from_history,
    escalate_turn_profile,
    features_from_text,
    prior_turn_failed_from_trace,
    profile_escalation_triggers,
)


class C06RemovalTests(unittest.TestCase):
    def test_router_module_file_is_gone(self):
        self.assertFalse((TREE / "core" / "router_v563.py").exists())

    def test_no_import_of_router_anywhere(self):
        offenders = []
        for base in ("core", "gui", "tests"):
            for path in sorted((TREE / base).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    if any(n == "router_v563" or n.endswith(".router_v563")
                           for n in names):
                        offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_architecture_doc_states_removed(self):
        text = (TREE / "ARCHITECTURE.md").read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if "s1 auto-router" in ln]
        self.assertEqual(len(lines), 1)
        self.assertIn("REMOVED", lines[0])
        self.assertIn("C06", lines[0])

    def test_w8_docstring_states_removed(self):
        text = (TREE / "core" / "w8_wiring.py").read_text(encoding="utf-8")
        self.assertIn("s1 auto-router (REMOVED Phase 3 C06", text)

    def test_live_selection_is_config_not_router(self):
        from core.provider_identity import resolve_provider as r
        self.assertEqual(r("local", "gemini")["provider"], "local")
        self.assertEqual(r("cloud", "groq")["provider"], "groq")
        self.assertEqual(r("cloud", "gemini")["provider"], "gemini")
        # Unrecognized inputs fall back to gemini, flagged normalized.
        res = r("bogus", "bogus")
        self.assertEqual(res["provider"], "gemini")
        self.assertTrue(res["normalized"])

    def test_local_model_tiers_are_the_live_tiers(self):
        from core.voice.local_assets import MODEL_TIERS, model_tier_for  # Phase 3 V2 INTENTIONAL: local_assets moved to core/voice/
        self.assertEqual(set(MODEL_TIERS), {"lite", "balanced", "quality"})
        self.assertEqual(
            model_tier_for("Qwen3-1.7B-Q4_K_M.gguf")[0], "lite")
        self.assertEqual(
            model_tier_for("Qwen3-4B-Instruct-2507-Q4_K_M.gguf")[0],
            "balanced")
        self.assertEqual(
            model_tier_for("Qwen3-8B-Instruct-2507-Q4_K_M.gguf")[0],
            "quality")

    def test_deep_think_has_no_voice_entry(self):
        from core import think
        self.assertEqual(think.voice_path_entry_points(), [])
        for name in ("think_sync", "generate_sync", "run"):
            self.assertFalse(hasattr(think, name),
                             f"deep-think harness must not gain {name}")


class ClassifierDisagreementTests(unittest.TestCase):
    def test_old_think_new_quick_short_control_command(self):
        # "fix" is a think-shape word, so the old rule said think;
        # but this is a 3-word device-control command (quick-shape
        # "volume", short, no turn complexity), so the new rule
        # says quick. The quick-shape + brevity outweigh one
        # lexical hit when nothing else suggests complexity.
        text = "fix the volume"
        self.assertEqual(_old_classify(text), "think")
        self.assertEqual(classify_turn_profile(text), "quick")

    def test_old_quick_new_think_after_failed_prior_turn(self):
        text = "what time is it"
        self.assertEqual(_old_classify(text), "quick")
        self.assertEqual(
            classify_turn_profile(text, prior_turn_failed=True), "think")

    def test_old_quick_new_think_under_context_pressure(self):
        text = "what time is it"
        self.assertEqual(_old_classify(text), "quick")
        self.assertEqual(
            classify_turn_profile(text, context_pressure=0.85), "think")

    def test_old_quick_new_think_after_bad_tool_output(self):
        # The old classifier had no mid-turn evidence at all: a
        # turn that started quick stayed quick even after a tool
        # failed. The new escalation moves it to think.
        text = "play some music"
        self.assertEqual(_old_classify(text), "quick")
        profile, triggers = escalate_turn_profile(
            "quick", bad_tool_output=True)
        self.assertEqual(profile, "think")
        self.assertIn("bad_tool_output", triggers)


class ClassifierTriggerTests(unittest.TestCase):
    def test_ambiguous_band_escalates(self):
        # Neither shape, middle length: shape cannot resolve it.
        text = ("the meeting notes from yesterday afternoon need "
                "to be filed somewhere sensible today please")
        f = features_from_text(text)
        self.assertFalse(f.think_shape)
        self.assertFalse(f.quick_shape)
        self.assertTrue(13 <= f.words <= 25)
        self.assertIn("ambiguous", profile_escalation_triggers(f))
        self.assertEqual(classify_turn_from_features(f), "think")

    def test_low_confidence_escalates(self):
        profile, triggers = escalate_turn_profile(
            "quick", low_confidence=True)
        self.assertEqual(profile, "think")
        self.assertIn("low_confidence", triggers)

    def test_repeated_repair_escalates_two_ways(self):
        profile, triggers = escalate_turn_profile(
            "quick", repair_rounds=2)
        self.assertEqual(profile, "think")
        self.assertIn("repeated_repair", triggers)
        # The old after_repair rule survives as this trigger.
        self.assertEqual(
            classify_turn_profile("what time is it", after_repair=True),
            "think")
        f = TurnProfileFeatures(after_repair=True)
        self.assertIn("repeated_repair", profile_escalation_triggers(f))

    def test_single_repair_scores_think_without_trigger(self):
        # One repair spent is complexity evidence (scoring), not
        # yet a repeated-repair trigger — the distinction matters:
        # triggers are explicit, scoring is cumulative.
        f = TurnProfileFeatures(words=4, quick_shape=True,
                                repair_rounds=1)
        self.assertEqual(profile_escalation_triggers(f), ())
        self.assertEqual(classify_turn_from_features(f), "think")

    def test_tool_use_scores_think(self):
        base = dict(words=4, quick_shape=True)
        self.assertEqual(
            classify_turn_from_features(
                TurnProfileFeatures(tool_calls=2, **base)), "think")
        self.assertEqual(
            classify_turn_from_features(
                TurnProfileFeatures(distinct_tools=2, **base)), "think")
        self.assertEqual(
            classify_turn_from_features(
                TurnProfileFeatures(tool_calls=1, **base)), "quick")

    def test_context_pressure_boundary(self):
        text = "what time is it"
        self.assertEqual(
            classify_turn_profile(text, context_pressure=0.7), "think")
        self.assertEqual(
            classify_turn_profile(text, context_pressure=0.69), "quick")


class ClassifierPreservedTests(unittest.TestCase):
    def test_simple_turn_stays_quick(self):
        f = features_from_text("what time is it")
        self.assertEqual(profile_escalation_triggers(f), ())
        self.assertEqual(classify_turn_profile("what time is it"), "quick")

    def test_old_agreements_still_hold(self):
        self.assertEqual(
            classify_turn_profile("explain quantum computing"), "think")
        self.assertEqual(
            classify_turn_profile(" ".join(["word"] * 30)), "think")
        self.assertEqual(
            classify_turn_profile("why is the sky blue?"), "think")
        self.assertEqual(
            classify_turn_profile("is it raining? or snowing?"), "think")
        self.assertEqual(classify_turn_profile(""), "quick")
        self.assertEqual(classify_turn_profile(None), "quick")

    def test_escalation_never_demotes(self):
        profile, triggers = escalate_turn_profile("think")
        self.assertEqual((profile, triggers), ("think", ()))
        profile, triggers = escalate_turn_profile("quick")
        self.assertEqual((profile, triggers), ("quick", ()))


class ClassifierFeatureHelperTests(unittest.TestCase):
    def test_prior_turn_failed_from_trace(self):
        self.assertFalse(prior_turn_failed_from_trace(None))
        self.assertFalse(prior_turn_failed_from_trace([]))
        self.assertFalse(prior_turn_failed_from_trace(
            [{"actions": [{"tool": "t", "status": "succeeded"}]}]))
        self.assertTrue(prior_turn_failed_from_trace(
            [{"actions": [{"tool": "t", "status": "failed"}]}]))
        self.assertTrue(prior_turn_failed_from_trace(
            [{"actions": [{"tool": "t", "status": "rejected"}]}]))
        # Only the LAST recorded turn counts.
        self.assertFalse(prior_turn_failed_from_trace([
            {"actions": [{"tool": "t", "status": "failed"}]},
            {"actions": [{"tool": "t", "status": "succeeded"}]},
        ]))

    def test_context_pressure_from_history(self):
        self.assertEqual(
            context_pressure_from_history([], 1000), 0.0)
        self.assertEqual(
            context_pressure_from_history([{"content": "x"}], 0), 0.0)
        self.assertEqual(
            context_pressure_from_history([{"content": "x"}], -5), 0.0)
        p = context_pressure_from_history(
            [{"content": "x" * 320}], 1000)
        self.assertTrue(0.0 < p < 0.2)
        p = context_pressure_from_history(
            [{"content": "x" * 100000}], 1000)
        self.assertEqual(p, 1.0)


class ClassifierWiringTests(unittest.TestCase):
    def _builtin_segment(self):
        # BATCH 4 INTENTIONAL: _brain_turn_builtin moved to the
        # builtin mixin; the scan covers facade + mixin.
        src = ((TREE / "core" / "voice" / "local_client.py").read_text(
            encoding="utf-8")
            + (TREE / "core" / "voice" / "local_client_builtin.py")
            .read_text(encoding="utf-8"))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and (
                    node.name == "_brain_turn_builtin"):
                return ast.get_source_segment(src, node)
        self.fail("_brain_turn_builtin not found")

    def test_brain_turn_passes_session_features(self):
        src = (TREE / "core" / "voice" / "local_client.py").read_text(
            encoding="utf-8")  # Phase 3 V1b INTENTIONAL: moved to core/voice/
        # INTENTIONAL (batch 3): _brain_turn moved verbatim to the
        # mic-utterance mixin; the scan covers facade + mixin.
        src += (TREE / "core" / "voice" / "local_client_mic.py").read_text(
            encoding="utf-8")
        # GAPS-22 (B-V8): the feature is computed once into
        # _prior_failed and passed by name — the value still comes
        # from the trace, exactly as before.
        self.assertIn(
            "_prior_failed = prior_turn_failed_from_trace(", src)
        self.assertIn(
            "prior_turn_failed=_prior_failed", src)
        self.assertIn(
            "context_pressure=context_pressure_from_history(", src)

    def test_escalation_sites_in_turn_loop(self):
        seg = self._builtin_segment()
        self.assertEqual(seg.count("escalate_turn_profile("), 5)
        self.assertEqual(seg.count("bad_tool_output=True"), 2)
        self.assertEqual(seg.count("low_confidence=True"), 3)

    def test_no_bare_think_assignment_in_turn_loop(self):
        seg = self._builtin_segment()
        self.assertNotIn('_active_profile = "think"', seg)

    def test_turn_loop_tolerates_unset_active_profile(self):
        # Regression: sessions driven straight into
        # _brain_turn_builtin (truthtelling harnesses) never ran
        # _brain_turn, so _active_profile may be unset. Every
        # escalation call must read it through the one accessor
        # (GAPS-22, B-V8: entry_profile), never bare and never a
        # silent getattr default at the call site — the profile
        # is initialised to None (undecided) in __init__.
        seg = self._builtin_segment()
        lines = seg.splitlines()
        calls = [i for i, ln in enumerate(lines)
                 if "escalate_turn_profile(" in ln]
        self.assertEqual(len(calls), 5)
        for i in calls:
            self.assertIn("entry_profile(self)",
                          lines[i + 1])


if __name__ == "__main__":
    unittest.main()
