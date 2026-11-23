"""R18-5 stanfordnlp/dspy rebuild — local prompt optimisation.

Fake Gemini endpoint only: evaluation and tuning are driven
through an injectable fake opener returning recorded answers,
against a temp store DB, with a fake key. No real LM is ever
contacted, no real state is ever touched, and no real credential
is ever used — the 'key' strings below are obviously fake.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import prompt_tune as pt
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import _needs_confirmation
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools

TREE = Path(__file__).resolve().parent.parent

FAKE_KEY = "test-dspy-fake-key-12345"
ANSWERS = {"capital of France": "Paris", "2+2": "4"}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self, n=-1):
        return self._payload if n < 0 else self._payload[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeLM:
    """Recorded Gemini endpoint: answers by the example input, in
    full sentences unless the instruction says to give only the
    answer; answers the propose meta-prompt with numbered rewrites."""

    def __init__(self, payload=None):
        self.payload = payload
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.payload is not None:
            return _Resp(self.payload)
        body = json.loads(req.data.decode("utf-8"))
        text = body["contents"][0]["parts"][0]["text"]
        if text.startswith("Rewrite the following instruction"):
            out = ("1. Answer the question briefly.\n"
                   "2. Give a short answer.")
        else:
            inp = text.split("Input:\n", 1)[1] \
                if "Input:\n" in text else text
            ans = None
            for k, v in ANSWERS.items():
                if k in inp:
                    ans = v
            if ans is None:
                ans = "I do not know."
            if "Give only the answer" in text:
                out = ans
            else:
                out = "The answer is %s, as you asked." % ans
        return _Resp(json.dumps(
            {"candidates": [{"content": {"parts": [{"text": out}]}}]}
        ).encode("utf-8"))


def _examples():
    return json.dumps([{"input": "What is the capital of France?",
                        "expected": "Paris"},
                       {"input": "What is 2+2?", "expected": "4"}])


class PromptTuneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "prompt_tune.sqlite3")
        self.fake = _FakeLM()
        self._old_key = pt.config.api_key
        self._old_model = pt.config.model_name
        pt.config.api_key = FAKE_KEY
        pt.config.model_name = "gemini-test"

    def tearDown(self):
        pt.config.api_key = self._old_key
        pt.config.model_name = self._old_model
        self.tmp.cleanup()

    def _call(self, *args, **kwargs):
        kwargs.setdefault("_opener", self.fake)
        kwargs.setdefault("_db_path", self.db)
        return pt.prompt_tune(*args, **kwargs)

    def _seed(self):
        out = self._call(action="prompt", mode="add", name="capital",
                         text="Answer the question.")
        self.assertTrue(out.startswith("Added prompt #1"), out)
        out = self._call(action="dataset", mode="add", name="capitals",
                         examples=_examples())
        self.assertTrue(out.startswith("Added dataset #1"), out)

    # -- scoring --------------------------------------------------------
    def test_metrics_exact_contains_f1(self):
        self.assertEqual(pt._score("exact", "Paris", "paris"), 1.0)
        self.assertEqual(pt._score("exact", "Paris!", "Paris"), 0.0)
        self.assertEqual(
            pt._score("contains", "The answer is Paris", "Paris"), 1.0)
        self.assertEqual(pt._score("contains", "London", "Paris"), 0.0)
        self.assertAlmostEqual(
            pt._score("f1", "The capital of France is Paris", "Paris"),
            0.286, places=3)
        self.assertEqual(pt._score("f1", "Paris", "Paris"), 1.0)
        self.assertEqual(pt._score("f1", "", "Paris"), 0.0)

    # -- prompts --------------------------------------------------------
    def test_prompt_add_list_get(self):
        out = self._call(action="prompt", mode="add", name="capital",
                         text="Answer the question.")
        self.assertEqual(out, "Added prompt #1 'capital'.")
        out = self._call(action="prompt", mode="list")
        self.assertIn("#1 capital", out)
        out = self._call(action="prompt", mode="get", prompt_id=1)
        self.assertIn("Answer the question.", out)
        self.assertIn("not evaluated yet", out)

    def test_prompt_add_refuses_empty(self):
        out = self._call(action="prompt", mode="add", name="x",
                         text="   ")
        self.assertTrue(out.startswith("refused:"), out)
        out = self._call(action="prompt", mode="add", name="   ",
                         text="Answer.")
        self.assertTrue(out.startswith("refused:"), out)

    def test_prompt_set_current_and_remove(self):
        self._call(action="prompt", mode="add", name="one",
                   text="First.")
        self._call(action="prompt", mode="add", name="two",
                   text="Second.")
        out = self._call(action="prompt", mode="set_current",
                         prompt_id=2)
        self.assertEqual(out, "Prompt #2 'two' is now current.")
        out = self._call(action="prompt", mode="list")
        self.assertIn("#2 two (current)", out)
        out = self._call(action="prompt", mode="remove", prompt_id=1)
        self.assertEqual(out, "Removed prompt #1 'one'.")
        out = self._call(action="prompt", mode="get", prompt_id=1)
        self.assertTrue(out.startswith("refused:"), out)

    # -- datasets -------------------------------------------------------
    def test_dataset_add_get(self):
        out = self._call(action="dataset", mode="add", name="capitals",
                         examples=_examples())
        self.assertEqual(out, "Added dataset #1 'capitals' (2 examples).")
        out = self._call(action="dataset", mode="get", dataset_id=1)
        self.assertIn("capital of France", out)
        self.assertIn("2+2", out)

    def test_dataset_add_bad_json_refuses(self):
        out = self._call(action="dataset", mode="add", name="bad",
                         examples="{not json")
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("not valid JSON", out)

    def test_dataset_add_over_50_refuses(self):
        big = json.dumps([{"input": "i%d" % i, "expected": "e%d" % i}
                          for i in range(51)])
        out = self._call(action="dataset", mode="add", name="big",
                         examples=big)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("at most 50", out)

    def test_dataset_add_bad_example_refuses(self):
        bad = json.dumps([{"input": "What?", "expected": ""}])
        out = self._call(action="dataset", mode="add", name="bad",
                         examples=bad)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("no 'expected'", out)

    # -- evaluate -------------------------------------------------------
    def test_evaluate_exact_scores_verbose_zero(self):
        self._seed()
        out = self._call(action="evaluate", prompt_id=1, dataset_id=1,
                         metric="exact", limit=2)
        self.assertIn("mean 0.000", out)
        self.assertIn("2 LM calls", out)
        self.assertIn("expected 'Paris', got 'The answer is Paris",
                      out)

    def test_evaluate_contains_scores_one(self):
        self._seed()
        out = self._call(action="evaluate", prompt_id=1, dataset_id=1,
                         metric="contains", limit=2)
        self.assertIn("mean 1.000", out)

    def test_evaluate_unknown_prompt_refuses(self):
        self._seed()
        out = self._call(action="evaluate", prompt_id=99, dataset_id=1)
        self.assertTrue(out.startswith("refused:"), out)

    def test_evaluate_unknown_dataset_refuses(self):
        self._seed()
        out = self._call(action="evaluate", prompt_id=1, dataset_id=99)
        self.assertTrue(out.startswith("refused:"), out)

    def test_evaluate_bad_metric_refuses(self):
        self._seed()
        out = self._call(action="evaluate", prompt_id=1, dataset_id=1,
                         metric="bleu")
        self.assertTrue(out.startswith("refused:"), out)

    # -- tune -----------------------------------------------------------
    def test_tune_variants_promotes_concise_winner(self):
        self._seed()
        out = self._call(action="tune", prompt_id=1, dataset_id=1,
                         variants=1, metric="exact", limit=2)
        self.assertIn("Winner: prompt #2 'capital (variant 3)' at 1.000",
                      out)
        # GAPS-4 held-out gate (2026-11-13): the 2-example slice
        # splits into 1 tuning + 1 held-out, so the 9 candidates are
        # ranked on 1 example (9 calls) and the gate scores the
        # winner and the base on the held-out example (2 calls) —
        # 11 calls, not the old 18 (9 candidates x 2 examples, no
        # gate). The winner still promotes: it scores 1.000 on the
        # held-out example against the base's 0.000.
        self.assertIn("11 LM calls", out)
        self.assertIn("Held-out gate: PASSED — winner 1.000 vs base "
                      "0.000", out)
        out = self._call(action="prompt", mode="list")
        self.assertIn("#2 capital (variant 3) (current)", out)
        self.assertIn("best 1.00 on dataset 1", out)

    def test_tune_with_supplied_candidates(self):
        self._seed()
        self._call(action="prompt", mode="add", name="concise",
                   text="Answer the question. "
                        "Give only the answer, nothing else.")
        out = self._call(action="tune", prompt_id=1, dataset_id=1,
                         candidates='["2"]', metric="exact", limit=2)
        self.assertIn("Winner: prompt #2 'concise' at 1.000", out)
        self.assertIn("4 LM calls", out)

    def test_tune_with_propose(self):
        self._seed()
        out = self._call(action="tune", prompt_id=1, dataset_id=1,
                         propose=2, metric="exact", limit=2)
        # GAPS-4 held-out gate (2026-11-13): 1 propose call + 3
        # candidates x 1 tuning example = 4 calls, not the old 7
        # (1 propose + 3 candidates x 2 examples, no gate). The
        # shortest prompt wins the all-zero tie: the base prompt —
        # and a base win now promotes NOTHING (no set_best, no
        # set_current, no gate calls), where the old code made the
        # base current. The new output says so plainly.
        self.assertIn("4 LM calls", out)
        self.assertIn("Base prompt was already best at 0.000; nothing "
                      "promoted, current unchanged", out)
        self.assertIn("Held-out gate: not run — the winner is the base "
                      "prompt", out)
        out = self._call(action="prompt", mode="list")
        self.assertNotIn("(current)", out)

    def test_tune_nothing_refuses(self):
        self._seed()
        out = self._call(action="tune", prompt_id=1, dataset_id=1)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("nothing to tune against", out)

    def test_tune_too_many_candidates_refuses(self):
        self._seed()
        many = json.dumps(["candidate %d" % i for i in range(10)])
        out = self._call(action="tune", prompt_id=1, dataset_id=1,
                         candidates=many)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("at most 10 per tune run", out)

    # -- propose / variants ---------------------------------------------
    def test_propose_returns_rewrites(self):
        self._seed()
        out = self._call(action="propose", prompt_id=1, limit=2)
        self.assertIn("Answer the question briefly.", out)
        self.assertIn("Give a short answer.", out)
        self.assertIn("not stored", out)

    def test_variants_lists_eight(self):
        out = self._call(action="variants")
        self.assertIn("Built-in variant bank (8)", out)
        self.assertIn("Give only the answer, nothing else.", out)
        self.assertIn("Think step by step", out)

    # -- status / ledger --------------------------------------------------
    def test_status_and_events_and_chain(self):
        self._seed()
        self._call(action="evaluate", prompt_id=1, dataset_id=1,
                   metric="exact", limit=1)
        out = self._call(action="status")
        self.assertIn("prompts: 1", out)
        self.assertIn("datasets: 1", out)
        self.assertIn("runs: 1", out)
        self.assertIn("variant bank: 8", out)
        self.assertIn("LM: key configured", out)
        self.assertIn("chain intact: yes", out)
        out = self._call(action="events", limit=20)
        self.assertIn("evaluate dataset #1", out)
        self.assertIn("prompt_add", out)

    def test_chain_tamper_detected(self):
        self._seed()
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE prompt_tune_events SET detail='tampered' "
                     "WHERE id=1")
        conn.commit()
        conn.close()
        out = self._call(action="status")
        self.assertIn("chain intact: NO", out)

    # -- safety -----------------------------------------------------------
    def test_no_key_refuses_before_sending(self):
        self._seed()
        pt.config.api_key = ""
        try:
            before = len(self.fake.requests)
            out = self._call(action="evaluate", prompt_id=1,
                             dataset_id=1)
            self.assertTrue(out.startswith("refused:"), out)
            self.assertIn("no Gemini key is configured", out)
            self.assertEqual(len(self.fake.requests), before)
        finally:
            pt.config.api_key = FAKE_KEY

    def test_key_never_echoed_or_stored(self):
        self._seed()
        out1 = self._call(action="evaluate", prompt_id=1, dataset_id=1,
                          metric="exact", limit=1)
        out2 = self._call(action="tune", prompt_id=1, dataset_id=1,
                          variants=1, metric="exact", limit=1)
        self.assertNotIn(FAKE_KEY, out1)
        self.assertNotIn(FAKE_KEY, out2)
        self.assertNotIn(FAKE_KEY.encode("utf-8"),
                         Path(self.db).read_bytes())

    def test_big_answer_refused(self):
        self._seed()
        big = _FakeLM(payload=b"x" * (1024 * 1024 + 1))
        out = pt.prompt_tune(action="evaluate", prompt_id=1,
                             dataset_id=1, metric="exact", limit=1,
                             _opener=big, _db_path=self.db)
        self.assertIn("1 MB cap", out)

    def test_non_json_answer_refused(self):
        self._seed()
        bad = _FakeLM(payload=b"not json")
        out = pt.prompt_tune(action="evaluate", prompt_id=1,
                             dataset_id=1, metric="exact", limit=1,
                             _opener=bad, _db_path=self.db)
        self.assertIn("was not JSON", out)

    def test_unknown_action_refuses(self):
        out = self._call(action="bogus")
        self.assertTrue(out.startswith("refused:"), out)

    # -- registry / catalogue / gates -------------------------------------
    def test_registry_census(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 772)  # INTENTIONAL — R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        mine = [d for d in decls if d["name"] == "prompt_tune"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(mine[0]["parameters"]["properties"]), 14)
        self.assertEqual(
            sum(len(d.get("parameters", {}).get("properties", {}))
                for d in decls), 2126)  # INTENTIONAL — R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_catalogue_mind_group(self):
        self.assertIn("prompt_tune", GROUP_MEMBERS["Mind"])
        gui_text = (TREE / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        self.assertIn('"prompt_tune",', gui_text)

    def test_gates(self):
        self.assertIn("prompt_tune", _CONFIRM_GATE_TOOLS)
        self.assertTrue(_needs_confirmation(
            "prompt_tune", {"action": "evaluate"}))
        self.assertTrue(_needs_confirmation(
            "prompt_tune", {"action": "tune"}))
        self.assertTrue(_needs_confirmation(
            "prompt_tune", {"action": "propose"}))
        self.assertTrue(_needs_confirmation(
            "prompt_tune", {"action": "bogus"}))
        self.assertFalse(_needs_confirmation(
            "prompt_tune", {"action": "prompt", "mode": "add"}))
        self.assertFalse(_needs_confirmation(
            "prompt_tune", {"action": "prompt", "mode": "list"}))
        self.assertFalse(_needs_confirmation(
            "prompt_tune", {"action": "dataset", "mode": "add"}))
        self.assertFalse(_needs_confirmation(
            "prompt_tune", {"action": "variants"}))
        self.assertFalse(_needs_confirmation(
            "prompt_tune", {"action": "status"}))
        self.assertFalse(_needs_confirmation(
            "prompt_tune", {"action": "events"}))
        self.assertTrue(_is_read_action(
            "prompt_tune", {"action": "prompt", "mode": "list"}))
        self.assertTrue(_is_read_action(
            "prompt_tune", {"action": "prompt", "mode": "get"}))
        self.assertTrue(_is_read_action(
            "prompt_tune", {"action": "dataset", "mode": "list"}))
        self.assertTrue(_is_read_action(
            "prompt_tune", {"action": "variants"}))
        self.assertTrue(_is_read_action(
            "prompt_tune", {"action": "status"}))
        self.assertFalse(_is_read_action(
            "prompt_tune", {"action": "evaluate"}))
        self.assertFalse(_is_read_action(
            "prompt_tune", {"action": "tune"}))
        self.assertFalse(_is_read_action(
            "prompt_tune", {"action": "propose"}))
        self.assertFalse(_is_read_action(
            "prompt_tune", {"action": "prompt", "mode": "add"}))

    def test_licence_marker_is_proposed(self):
        src = (TREE / "core" / "prompt_tune.py").read_text(
            encoding="utf-8")
        # Either marker state passes: PROPOSED until the parent rules
        # the licence at verification, Parent-ruled after. (The R17-9
        # wardrobe suite asserted PROPOSED only, and the parent's
        # ruling flip red-ded it in R18-1 neighbours — and the same
        # trap red-ded test_a2a in R18-5 neighbours. This test does
        # not repeat it.)
        self.assertTrue("PROPOSED — parent rules" in src
                        or "Parent-ruled (licence" in src)
        self.assertIn("stanfordnlp/dspy", src)


if __name__ == "__main__":
    unittest.main()
