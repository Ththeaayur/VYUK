"""Field-check fix #27 (round-2 UI finding 1): learning truncates
abbreviations — "Dr. Rao" was stored as "Dr".

The fix: core/memory/learning.py::_sentences is abbreviation-aware
(the word before the punctuation is a title/honorific, a dotted
abbreviation, or a single initial, the period does not end the
sentence). The word list is verbatim core/summary.py's
_ABBREVIATIONS, the guard mirrors summary.split_sentences. The
store under test is a temp MemoryStore and the learning feed is
redirected to a temp dir, so no test touches the live data/.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory import learning as learn  # noqa: E402
from core.memory.memory import MemoryStore  # noqa: E402


class AbbreviationSentenceTests(unittest.TestCase):
    def test_dr_rao_is_one_sentence(self):
        self.assertEqual(
            learn._sentences("remember that my dentist is Dr. Rao"),
            ["remember that my dentist is Dr. Rao"])

    def test_mr_mrs_ms_prof_st_are_one_sentence(self):
        self.assertEqual(
            learn._sentences("my doctor is Mr. Smith"),
            ["my doctor is Mr. Smith"])
        self.assertEqual(
            learn._sentences("my neighbour is Mrs. Patel"),
            ["my neighbour is Mrs. Patel"])
        self.assertEqual(
            learn._sentences("my aunt is Ms. Shah"),
            ["my aunt is Ms. Shah"])
        self.assertEqual(
            learn._sentences("my teacher is Prof. Rao"),
            ["my teacher is Prof. Rao"])
        self.assertEqual(
            learn._sentences("I live on St. Mary Street"),
            ["I live on St. Mary Street"])

    def test_single_initial_is_one_sentence(self):
        self.assertEqual(
            learn._sentences("my dentist is J. Rao"),
            ["my dentist is J. Rao"])

    def test_dotted_abbreviations_mid_sentence(self):
        self.assertEqual(
            learn._sentences("my dentist is Dr. Rao, e.g. the one on Main St"),
            ["my dentist is Dr. Rao, e.g. the one on Main St"])
        self.assertEqual(
            learn._sentences("the U.S. office opens at 9 a.m. sharp"),
            ["the U.S. office opens at 9 a.m. sharp"])

    def test_real_boundary_after_abbreviation_still_splits(self):
        self.assertEqual(
            learn._sentences("I met Dr. Rao. He is kind."),
            ["I met Dr. Rao.", "He is kind."])

    def test_decimal_is_not_a_boundary(self):
        self.assertEqual(
            learn._sentences("it costs 3.5 dollars. He paid."),
            ["it costs 3.5 dollars.", "He paid."])

    def test_ellipsis_and_exclaim_unchanged(self):
        self.assertEqual(
            learn._sentences("wait... he left"), ["wait...", "he left"])
        self.assertEqual(
            learn._sentences("my dentist is Rao! He is kind."),
            ["my dentist is Rao!", "He is kind."])

    def test_empty_and_plain_text(self):
        self.assertEqual(learn._sentences(""), [])
        self.assertEqual(learn._sentences(None), [])
        self.assertEqual(
            learn._sentences("my dentist is Rao"),
            ["my dentist is Rao"])
        self.assertEqual(
            learn._sentences("my dentist is Rao. He is kind."),
            ["my dentist is Rao.", "He is kind."])


class AbbreviationLearnTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="learning_abbrev_")
        self._orig_store = learn.memory_store
        self._orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = os.path.join(
            self._tmp, "feed")
        learn.memory_store = MemoryStore(
            os.path.join(self._tmp, "assistant.db"))
        learn.take_dropped_at_cap()

    def tearDown(self):
        learn.memory_store = self._orig_store
        if self._orig_feed is None:
            os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
        else:
            os.environ["VYUK_LEARNING_FEED_DIR"] = self._orig_feed
        learn.take_dropped_at_cap()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _values(self):
        return [r["value"] for r in learn.get_learned_facts()]

    def test_dr_rao_note_keeps_the_name(self):
        stored = learn.learn_from_text(
            "remember that my dentist is Dr. Rao")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["value"], "my dentist is Dr. Rao")
        self.assertEqual(stored[0]["display"], "my dentist is Dr. Rao")
        self.assertIn("my dentist is Dr. Rao", self._values())

    def test_mr_smith_attribute_keeps_the_name(self):
        stored = learn.learn_from_text("my doctor is Mr. Smith")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["value"], "Mr. Smith")
        self.assertEqual(stored[0]["display"], "your doctor is Mr. Smith")

    def test_plain_name_still_stores(self):
        stored = learn.learn_from_text("my dentist is Rao")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["value"], "Rao")

    def test_fact_after_a_real_boundary_still_learns(self):
        stored = learn.learn_from_text(
            "I met Dr. Rao. My dentist is Rao.")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["value"], "Rao")

    def test_two_facts_across_abbreviation_and_boundary(self):
        stored = learn.learn_from_text(
            "my dentist is Dr. Rao. My doctor is Mr. Smith.")
        self.assertEqual(len(stored), 2)
        self.assertEqual(
            sorted(v for v in self._values()),
            ["Dr. Rao", "Mr. Smith"])


if __name__ == "__main__":
    unittest.main()
