"""Section D fix D-F1 (2026-11-20): remember_user_fact bypassed the
100-fact memory cap entirely.

The cap lived only in learn_from_text() for category "learned";
the funnel tool wrote any category with no check (a capped-out
fact landed anyway, total 225 in the section-D probe), and the
same fact could sit in two categories at once (the live model
files what it hears under "preferences" while the learn path
files the same utterance under "learned").

The fix: the cap is a combined 100-row bound across the
user-fact categories {"learned", "general", "preferences"},
enforced in BOTH paths — the tool refuses a new key at cap with
the fix-#14 "Memory is full" wording (raised, so the funnel
reports handler_error, never a false "Saved to memory"), a key
already kept in another user-fact category is reported as already
remembered instead of filed twice, and the learn path counts the
combined total and never announces a loss for a fact kept
elsewhere. Replacements still work at cap; forget + re-teach
still works; internal skill categories are untouched (save_fact
itself stays uncapped).

Stdlib unittest only. The store under test is a temp MemoryStore
rebound into both module identities (learning + the funnel
handler), and the learning feed is redirected to a temp dir, so
no test touches the live data/.
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
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory import learning as learn  # noqa: E402
from core.memory.memory import MemoryStore  # noqa: E402
import core.funnel.tools_handlers_command_url as _cmdurl  # noqa: E402
from core.funnel.tools_handlers_command_url import (  # noqa: E402
    ToolHandlersCommandUrlMixin)


def _words(n=120):
    return ["f" + "".join(c) for c in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=3), n)]


class MemcapBypassTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="r3df1_memcap_")
        self._orig_learn_store = learn.memory_store
        self._orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = os.path.join(
            self._tmp, "feed")
        self.store = MemoryStore(os.path.join(self._tmp, "assistant.db"))
        learn.memory_store = self.store
        self._cmd_patch = mock.patch.object(
            _cmdurl, "memory_store", self.store)
        self._cmd_patch.start()
        learn.take_dropped_at_cap()

    def tearDown(self):
        self._cmd_patch.stop()
        learn.memory_store = self._orig_learn_store
        if self._orig_feed is None:
            os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
        else:
            os.environ["VYUK_LEARNING_FEED_DIR"] = self._orig_feed
        learn.take_dropped_at_cap()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- helpers ------------------------------------------------------
    def _remember(self, key, value, category=None):
        h = ToolHandlersCommandUrlMixin.__new__(ToolHandlersCommandUrlMixin)
        if category is None:
            return h._tool_remember_fact(key, value)
        return h._tool_remember_fact(key, value, category)

    def _fill_learned(self, n):
        stored = 0
        for i, w in enumerate(_words(), start=1):
            stored += len(learn.learn_from_text(f"My {w} is value {i}."))
            if stored >= n:
                break
        self.assertEqual(stored, n)
        return stored

    def _total(self):
        return len(self.store.get_all_facts())

    def _assert_refused(self, fn):
        with self.assertRaises(RuntimeError) as ctx:
            fn()
        self.assertIn("Memory is full", str(ctx.exception))
        self.assertIn("100 facts", str(ctx.exception))

    # -- the bypass, closed -------------------------------------------
    def test_tool_write_learned_at_cap_refused(self):
        self._fill_learned(100)
        self._assert_refused(
            lambda: self._remember("my_zzzq", "value 103", "learned"))
        self.assertEqual(len(learn.get_learned_facts()), 100)
        self.assertEqual(self._total(), 100)

    def test_tool_write_general_at_cap_refused(self):
        self._fill_learned(100)
        self._assert_refused(lambda: self._remember("my_zzzq", "value 103"))
        self.assertEqual(self._total(), 100)

    def test_tool_write_preferences_new_key_at_cap_refused(self):
        self._fill_learned(100)
        self._assert_refused(
            lambda: self._remember("my_zzzq", "value 103", "preferences"))
        self.assertEqual(self._total(), 100)

    def test_tool_replacement_at_cap_works(self):
        self._fill_learned(100)
        out = self._remember("my_faaa", "value ninehundred", "learned")
        self.assertTrue(out.startswith("Saved to memory"), out)
        self.assertEqual(len(learn.get_learned_facts()), 100)
        self.assertEqual(self._total(), 100)
        self.assertEqual(
            self.store.get_fact("learned", "my_faaa")["value"],
            "value ninehundred")

    def test_tool_already_kept_elsewhere_says_so(self):
        self._fill_learned(99)
        out = self._remember("my_fadw", "101", "preferences")
        self.assertTrue(out.startswith("Saved to memory"), out)
        self.assertEqual(self._total(), 100)
        out2 = self._remember("my_fadw", "101", "general")
        self.assertTrue(out2.startswith("Already remembered"), out2)
        self.assertNotIn("Saved to memory", out2)
        self.assertEqual(self._total(), 100)

    def test_learn_drop_not_recorded_when_kept_elsewhere(self):
        self._fill_learned(99)
        out = self._remember("my_fadw", "101", "preferences")
        self.assertTrue(out.startswith("Saved to memory"), out)
        got = learn.learn_from_text("My fadw is value 101.")
        self.assertEqual(got, [])
        self.assertEqual(learn.take_dropped_at_cap(), [])

    def test_learn_blocked_when_general_full(self):
        for i, w in enumerate(_words(100), start=1):
            out = self._remember(f"my_{w}", f"value {i}")
            self.assertTrue(out.startswith("Saved to memory"), out)
        self.assertEqual(self._total(), 100)
        self.assertEqual(len(learn.get_learned_facts()), 0)
        # NB: my_zzzq, not my_faaa — the fill already filed my_faaa
        # in "general", so a my_faaa candidate would be (correctly)
        # treated as kept elsewhere, not dropped.
        got = learn.learn_from_text("My zzzq is value 1.")
        self.assertEqual(got, [])
        dropped = learn.take_dropped_at_cap()
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["key"], "my_zzzq")

    def test_forget_reteach_works(self):
        self._fill_learned(100)
        row = self.store.get_fact("learned", "my_faaa")
        self.assertTrue(learn.forget_learned_fact(row["id"]))
        self.assertEqual(len(learn.get_learned_facts()), 99)
        got = learn.learn_from_text("My faaa is value 1.")
        self.assertEqual(len(got), 1)
        self.assertEqual(len(learn.get_learned_facts()), 100)
        self._assert_refused(lambda: self._remember("my_zzzq", "value 103"))

    # -- controls: the cap changes nothing below it --------------------
    def test_healthy_store_no_cap_interference(self):
        self.assertTrue(
            self._remember("my_aa", "1").startswith("Saved to memory"))
        self.assertTrue(
            self._remember("my_bb", "2", "learned").startswith(
                "Saved to memory"))
        self.assertTrue(
            self._remember("my_cc", "3", "preferences").startswith(
                "Saved to memory"))
        self.assertEqual(
            len(learn.learn_from_text("My dd is value 4.")), 1)
        self.assertEqual(self._total(), 4)

    def test_non_capped_category_uncapped(self):
        # Only the user-fact categories are capped; a tool write to
        # any other category (internal/app-state use) is unaffected.
        self._fill_learned(100)
        out = self._remember("my_zzzq", "value 103", "custom")
        self.assertTrue(out.startswith("Saved to memory"), out)
        self.assertEqual(self._total(), 101)

    def test_count_facts_helper(self):
        self._fill_learned(3)
        self._remember("my_g1", "1")
        self.assertEqual(
            self.store.count_facts({"learned", "general", "preferences"}),
            4)
        self.assertEqual(self.store.count_facts({"learned"}), 3)
        self.assertEqual(self.store.count_facts(), 4)


if __name__ == "__main__":
    unittest.main()
