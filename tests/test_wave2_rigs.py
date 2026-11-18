"""Wave 2 rig tests (stdlib unittest, no GUI, no network, no model).

Covers misrouting baseline, R3-F finder, R4-B canary, R4-D
taxonomy, R4-K bench mode, R4-A ask-or-act, R4-F provenance,
R3-R red-team corpus, R3-S slices.
"""

import unittest

from core import misrouting as mr
from core import w2_redteam as rt
from core import w2_rigs as rigs


class MisroutingTests(unittest.TestCase):
    def test_rate_and_wrong_window(self):
        b = mr.MisroutingBaseline()
        b.record("Mail", "Mail", "c1", "c1")
        b.record("Mail", "Browser", "c1", "c9")
        s = b.summary(min_cases=2)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["misrouted"], 1)
        self.assertEqual(s["wrong_window"], 1)
        self.assertTrue(s["baseline_complete"])

    def test_incomplete_without_min(self):
        b = mr.MisroutingBaseline()
        b.record("A", "A")
        self.assertFalse(b.summary()["baseline_complete"])
        self.assertIn("no verdict", b.summary()["note"])


class FinderTests(unittest.TestCase):
    def test_exact_token_ranks_first(self):
        cands = [{"name": "open_mail", "description": "open the mail app"},
                 {"name": "close_app", "description": "close an app"}]
        result = rigs.find_tools("open mail", cands)
        self.assertEqual(result["ranked"][0]["name"], "open_mail")
        self.assertFalse(result["miss"])

    def test_miss_reported_not_padded(self):
        result = rigs.find_tools("zzzqqq", [{"name": "open_mail", "description": ""}])
        self.assertTrue(result["miss"])
        self.assertEqual(result["ranked"], [])

    def test_empty_query_miss(self):
        self.assertTrue(rigs.find_tools("", [])["miss"])


class CanaryTests(unittest.TestCase):
    def test_no_drift(self):
        self.assertFalse(rigs.drift_canary({"a": "1"}, {"a": "1"})["drifted"])

    def test_drift_lists_keys(self):
        d = rigs.drift_canary({"a": "1", "b": "2"}, {"a": "9", "b": "2"})
        self.assertTrue(d["drifted"])
        self.assertEqual(d["changed_keys"], ["a"])


class TaxonomyTests(unittest.TestCase):
    def test_fixed_only_with_bench(self):
        t = rigs.FailureTaxonomy()
        t.record("wrong_window", "clicked browser not mail")
        self.assertFalse(t.is_fixed("wrong_window"))
        self.assertIn("wrong_window", t.open_classes())
        self.assertTrue(t.link_bench("wrong_window", "bench-ww-1"))
        self.assertTrue(t.is_fixed("wrong_window"))
        self.assertNotIn("wrong_window", t.open_classes())


class BenchModeTests(unittest.TestCase):
    def test_records_only(self):
        result = rigs.bench_mode([{"case": 1}], lambda c: {"ok": True})
        self.assertEqual(result["cases"], 1)
        self.assertEqual(result["results"][0]["outcome"], {"ok": True})

    def test_handler_error_recorded(self):
        def boom(c):
            raise RuntimeError("x")
        result = rigs.bench_mode([{"case": 1}], boom)
        self.assertIn("error", result["results"][0]["outcome"])


class AskOrActTests(unittest.TestCase):
    def test_consequential_always_asks(self):
        for action in ("send", "delete", "purchase", "message", "booking"):
            self.assertEqual(rigs.ask_or_act(action)["decision"], "ask")

    def test_default_is_ask(self):
        self.assertEqual(rigs.ask_or_act("open_app")["decision"], "ask")

    def test_caller_threshold_can_act_on_nonconsequential(self):
        result = rigs.ask_or_act("open_app", caller_thresholds={"open_app": "act"})
        self.assertEqual(result["decision"], "act")


class ProvenanceTests(unittest.TestCase):
    def test_trusted_clean(self):
        self.assertFalse(rigs.taint_by_provenance("trusted")["tainted"])

    def test_untrusted_taints(self):
        self.assertTrue(rigs.taint_by_provenance("untrusted")["tainted"])

    def test_unknown_fails_closed(self):
        self.assertTrue(rigs.taint_by_provenance("weird")["tainted"])

    def test_fingerprint_is_hash_not_content(self):
        fp = rigs.fingerprint_text("secret content")
        self.assertNotIn("secret", fp)
        self.assertEqual(len(fp), 64)


class RedteamTests(unittest.TestCase):
    def test_corpus_all_match_with_good_handler(self):
        def handler(case):
            return case["expected"]
        result = rt.run_redteam(handler)
        self.assertEqual(result["matched"], result["total"])
        self.assertEqual(result["mismatched"], [])

    def test_mismatch_reported(self):
        result = rt.run_redteam(lambda c: "act")
        self.assertTrue(result["mismatched"])

    def test_r3s_sized_and_unsized(self):
        self.assertEqual(rt.r3s_slice("CTRL")["status"], "SIZED")
        self.assertEqual(rt.r3s_slice("SIM")["status"], "NOT_SIZED")
        self.assertIn("not invented", rt.r3s_slice("SIM")["reason"])


if __name__ == "__main__":
    unittest.main()
