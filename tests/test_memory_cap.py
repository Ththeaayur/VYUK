"""Field-check fix #14 (T9): the 101st fact is silently dropped at the
100-fact memory cap.

The fix: a brand-new fact skipped at the cap is recorded for
take_dropped_at_cap() (the chat layer announces it in the moment),
so nothing the user taught vanishes silently. The cap itself is
unchanged — the 101st fact is still not stored; the fix is the
notice, not storage. The store under test is a temp MemoryStore and
the learning feed is redirected to a temp dir, so no test touches
the live data/.
"""
from __future__ import annotations

import itertools
import os
import pathlib
import shutil
import string
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory import learning as learn  # noqa: E402
from core.memory.memory import MemoryStore  # noqa: E402


def _fill_words(n=100):
    return ["f" + "".join(c) for c in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=3), n)]


class MemoryCapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="memory_cap_")
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

    def _fill_to_cap(self):
        stored = 0
        for n, w in enumerate(_fill_words(), start=1):
            stored += len(learn.learn_from_text(f"My {w} is value {n}."))
        self.assertEqual(stored, 100)
        self.assertEqual(len(learn.get_learned_facts()), 100)

    def test_101st_fact_is_reported_not_silently_dropped(self):
        self._fill_to_cap()
        got = learn.learn_from_text("My zzzq is value onezerothree.")
        self.assertEqual(got, [])
        self.assertEqual(len(learn.get_learned_facts()), 100)
        dropped = learn.take_dropped_at_cap()
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["key"], "my_zzzq")
        self.assertIn("onezerothree", dropped[0]["display"])

    def test_drain_returns_each_drop_once(self):
        self._fill_to_cap()
        learn.learn_from_text("My zzzq is value onezerothree.")
        self.assertEqual(len(learn.take_dropped_at_cap()), 1)
        self.assertEqual(learn.take_dropped_at_cap(), [])

    def test_two_drops_in_one_call_are_both_reported(self):
        self._fill_to_cap()
        got = learn.learn_from_text(
            "My zzzq is value one. My zzzr is value two.")
        self.assertEqual(got, [])
        dropped = learn.take_dropped_at_cap()
        self.assertEqual([d["key"] for d in dropped],
                         ["my_zzzq", "my_zzzr"])

    def test_replacement_at_cap_still_stores_and_is_not_a_drop(self):
        self._fill_to_cap()
        got = learn.learn_from_text("My faaa is value ninehundred.")
        self.assertEqual(len(got), 1)
        self.assertEqual(len(learn.get_learned_facts()), 100)
        self.assertEqual(learn.take_dropped_at_cap(), [])

    def test_identical_fact_at_cap_is_not_a_drop(self):
        self._fill_to_cap()
        learn.learn_from_text("My faaa is value ninehundred.")
        learn.take_dropped_at_cap()
        got = learn.learn_from_text("My faaa is value ninehundred.")
        self.assertEqual(got, [])
        self.assertEqual(learn.take_dropped_at_cap(), [])

    def test_below_cap_nothing_is_reported(self):
        got = learn.learn_from_text("My faaa is value one.")
        self.assertEqual(len(got), 1)
        self.assertEqual(learn.take_dropped_at_cap(), [])


if __name__ == "__main__":
    unittest.main()
