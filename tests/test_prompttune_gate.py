"""GAPS-4 held-out gate on prompt_tune (BLOCKS #5, learning-loop half).

Fake Gemini endpoint only: tuning is driven through an injectable
fake opener returning recorded answers, against a temp store DB,
with a fake key. No real LM is ever contacted, no real state is
ever touched, and no real credential is ever used — the 'key'
strings below are obviously fake.

The gate: the evaluation set splits deterministically into a
tuning set and a held-out set; candidates are ranked on the tuning
set only; the winner is promoted only when it beats the base prompt
on the held-out set by at least _MIN_IMPROVEMENT (0.05), with no
regression tolerated. A candidate that merely memorised the tuning
slice is refused, honestly, with the numbers.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import prompt_tune as pt

FAKE_KEY = "test-gate-fake-key-12345"
EXAMPLES = [{"input": "What is the capital of %s?" % c,
             "expected": cap}
            for c, cap in [("France", "Paris"), ("Japan", "Tokyo"),
                           ("Italy", "Rome"), ("Spain", "Madrid"),
                           ("Germany", "Berlin"), ("Canada", "Ottawa"),
                           ("Brazil", "Brasilia"),
                           ("Egypt", "Cairo"),
                           ("India", "New Delhi"),
                           ("China", "Beijing")]]
BY_INPUT = {ex["input"]: ex["expected"] for ex in EXAMPLES}


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
    """Records every request in order. The MEMORISER answers exactly
    on the tuning-slice inputs and wrongly on anything else; the
    GENUINE answers exactly everywhere; anything else (the base)
    answers verbosely, scoring partial f1."""

    def __init__(self, train_inputs):
        self.train_inputs = set(train_inputs)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        body = json.loads(req.data.decode("utf-8"))
        text = body["contents"][0]["parts"][0]["text"]
        inp = (text.split("Input:\n", 1)[1].strip()
               if "Input:\n" in text else text.strip())
        expected = BY_INPUT.get(inp, "?")
        if "MEMORISER" in text:
            out = expected if inp in self.train_inputs else "wrong"
        elif "GENUINE" in text:
            out = expected
        else:
            out = "The answer is %s, as you asked." % expected
        return _Resp(json.dumps(
            {"candidates": [{"content": {"parts": [{"text": out}]}}]}
        ).encode("utf-8"))


def _input_of(req):
    body = json.loads(req.data.decode("utf-8"))
    text = body["contents"][0]["parts"][0]["text"]
    return (text.split("Input:\n", 1)[1].strip()
            if "Input:\n" in text else text.strip())


class PromptTuneGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "prompt_tune.sqlite3")
        self.train, self.held = pt._split_examples(EXAMPLES)
        self.train_inputs = {ex["input"] for ex in self.train}
        self.held_inputs = {ex["input"] for ex in self.held}
        self.fake = _FakeLM(self.train_inputs)
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
        out = self._call(action="prompt", mode="add", name="base",
                         text="Answer the question.")
        self.assertTrue(out.startswith("Added prompt #1"), out)
        out = self._call(action="dataset", mode="add", name="capitals",
                         examples=json.dumps(EXAMPLES))
        self.assertTrue(out.startswith("Added dataset #1"), out)

    def _runs(self):
        conn = sqlite3.connect(self.db)
        rows = conn.execute(
            "SELECT kind, candidates_json, winner_id "
            "FROM prompt_tune_runs ORDER BY id").fetchall()
        conn.close()
        return [(k, json.loads(c), w) for k, c, w in rows]

    def _events(self):
        conn = sqlite3.connect(self.db)
        rows = conn.execute(
            "SELECT kind, detail FROM prompt_tune_events ORDER BY id"
            ).fetchall()
        conn.close()
        return rows

    # -- the split ------------------------------------------------------
    def test_split_is_deterministic(self):
        self.assertEqual(pt._split_examples(EXAMPLES),
                         pt._split_examples(EXAMPLES))

    def test_split_is_disjoint_and_covers(self):
        train_inputs = {ex["input"] for ex in self.train}
        held_inputs = {ex["input"] for ex in self.held}
        self.assertFalse(train_inputs & held_inputs)
        self.assertEqual(train_inputs | held_inputs,
                         {ex["input"] for ex in EXAMPLES})

    def test_split_has_both_sides(self):
        self.assertGreaterEqual(len(self.train), 1)
        self.assertGreaterEqual(len(self.held), 1)

    def test_split_of_one_has_no_held_out(self):
        train, held = pt._split_examples(EXAMPLES[:1])
        self.assertEqual(len(train), 1)
        self.assertEqual(held, [])

    def test_split_is_content_based_not_position_based(self):
        # Prepending an example must not reshuffle the split: the
        # assignment follows the example's content, so results stay
        # comparable when the dataset grows.
        extra = {"input": "What is the capital of Peru?",
                 "expected": "Lima"}
        train2, held2 = pt._split_examples([extra] + EXAMPLES)
        self.assertEqual({ex["input"] for ex in train2},
                         self.train_inputs | {extra["input"]})
        self.assertEqual({ex["input"] for ex in held2},
                         self.held_inputs)

    # -- the gate decision ----------------------------------------------
    def test_gate_refuses_regression(self):
        promote, reason = pt._gate_decision(0.0, 0.25)
        self.assertFalse(promote)
        self.assertIn("regressed", reason)

    def test_gate_refuses_a_gain_below_threshold(self):
        promote, reason = pt._gate_decision(0.28, 0.25)
        self.assertFalse(promote)
        self.assertIn("below the 0.050", reason)

    def test_gate_promotes_a_genuine_improvement(self):
        promote, _reason = pt._gate_decision(1.0, 0.25)
        self.assertTrue(promote)

    def test_gate_thresholds_are_the_documented_ones(self):
        self.assertEqual(pt._MIN_IMPROVEMENT, 0.05)
        self.assertEqual(pt._MAX_REGRESSION, 0.0)
        self.assertEqual(pt._HELD_OUT_EVERY, 5)

    # -- tuning through the gate ----------------------------------------
    def test_memoriser_is_refused_with_the_numbers(self):
        self._seed()
        out = self._call(action="tune", prompt_id=1, dataset_id=1,
                         candidates=json.dumps(
                             ["MEMORISER: Answer the question. Give only "
                              "the answer, nothing else."]),
                         metric="f1", limit=10)
        self.assertTrue(out.startswith("Tuning refused:"), out)
        self.assertIn("regressed on the held-out set by", out)
        self.assertIn("winner 0.000 vs base", out)
        self.assertIn("min-improvement 0.050", out)
        self.assertIn("Nothing was stored, nothing was made current.",
                      out)
        out = self._call(action="prompt", mode="list")
        self.assertNotIn("MEMORISER", out)
        self.assertNotIn("(current)", out)
        self.assertIn("not evaluated yet", out)

    def test_held_out_is_never_tuned_on(self):
        self._seed()
        self._call(action="tune", prompt_id=1, dataset_id=1,
                   candidates=json.dumps(
                       ["MEMORISER: Answer the question. Give only "
                        "the answer, nothing else."]),
                   metric="f1", limit=10)
        inputs = [_input_of(r) for r in self.fake.requests]
        n_rank = 2 * len(self.train)
        # Every ranking call carries a tuning input.
        self.assertTrue(
            all(i in self.train_inputs for i in inputs[:n_rank]),
            inputs[:n_rank])
        # Held-out inputs appear only after ranking, once per
        # gate-scored prompt (winner + base).
        self.assertEqual(set(inputs[n_rank:]), self.held_inputs)
        self.assertEqual(len(inputs), n_rank + 2 * len(self.held))

    def test_refusal_is_recorded_with_the_numbers(self):
        self._seed()
        self._call(action="tune", prompt_id=1, dataset_id=1,
                   candidates=json.dumps(
                       ["MEMORISER: Answer the question. Give only "
                        "the answer, nothing else."]),
                   metric="f1", limit=10)
        runs = self._runs()
        self.assertEqual(len(runs), 1)
        kind, record, winner_id = runs[0]
        self.assertEqual(kind, "tune")
        self.assertIsNone(winner_id)
        gate = record["gate"]
        self.assertIs(gate["promoted"], False)
        self.assertEqual(gate["winner_held_out_mean"], 0.0)
        self.assertEqual(gate["train_examples"], len(self.train))
        self.assertEqual(gate["held_out_examples"], len(self.held))
        self.assertEqual(gate["min_improvement"], 0.05)
        self.assertIn("regressed", gate["reason"])
        self.assertTrue(any(k == "tune_refused" and "regressed" in d
                            for k, d in self._events()))

    def test_genuine_improvement_is_promoted(self):
        self._seed()
        out = self._call(action="tune", prompt_id=1, dataset_id=1,
                         candidates=json.dumps(
                             ["GENUINE: Answer the question. Give only "
                              "the answer, nothing else."]),
                         metric="f1", limit=10)
        self.assertIn("Winner: prompt #2", out)
        self.assertIn("now current", out)
        self.assertIn("Held-out gate: PASSED", out)
        self.assertIn("winner 1.000 vs base", out)
        out = self._call(action="prompt", mode="list")
        self.assertIn("#2", out)
        self.assertIn("(current)", out)
        # The stored best score is the held-out mean, not the
        # tuning mean.
        self.assertIn("best 1.00 on dataset 1", out)
        runs = self._runs()
        self.assertEqual(len(runs), 1)
        _kind, record, winner_id = runs[0]
        self.assertEqual(winner_id, 2)
        gate = record["gate"]
        self.assertIs(gate["promoted"], True)
        self.assertEqual(gate["winner_held_out_mean"], 1.0)
        self.assertGreaterEqual(gate["delta"], 0.05)
        self.assertTrue(any(k == "tune" and "held-out 1.000" in d
                            for k, d in self._events()))

    def test_too_small_slice_refuses_before_any_call(self):
        self._seed()
        before = len(self.fake.requests)
        out = self._call(action="tune", prompt_id=1, dataset_id=1,
                         candidates=json.dumps(["GENUINE: Answer."]),
                         metric="f1", limit=1)
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("too small to split", out)
        self.assertEqual(len(self.fake.requests), before)
        self.assertEqual(self._runs(), [])

    def test_base_win_promotes_nothing(self):
        self._seed()
        out = self._call(action="tune", prompt_id=1, dataset_id=1,
                         candidates=json.dumps(
                             ["Answer the question, please be brief and "
                              "clear."]),
                         metric="exact", limit=10)
        self.assertIn("nothing promoted, current unchanged", out)
        out = self._call(action="prompt", mode="list")
        self.assertNotIn("(current)", out)
        runs = self._runs()
        self.assertEqual(len(runs), 1)
        gate = runs[0][1]["gate"]
        self.assertIs(gate["promoted"], False)
        self.assertIn("base prompt", gate["reason"])

    def test_status_names_the_gate(self):
        self._seed()
        out = self._call(action="status")
        self.assertIn("held-out gate: 1 in 5 examples held out", out)
        self.assertIn("promote needs +0.05 on held-out", out)


if __name__ == "__main__":
    unittest.main()
