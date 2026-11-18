"""R9-1 RapidFuzz PORT: the palette's typo lane (tests).

The finding was RapidFuzz (fast fuzzy matching, C++ core) for the
palette's subsequence-only scorer. The batch PORTs the behaviour
instead of adopting the package: the tree already has a plain
Levenshtein (core/skill_gate_admit.py:131, stdlib), and the palette
is tens to hundreds of short rows — the pure-Python distance is
fast enough there (see test_typo_lane_is_fast_enough). No RapidFuzz
dependency is added: no ``import rapidfuzz`` anywhere in ``core/``
and nothing in requirements.txt.

Gap, proven on backup_rapidfuzz_pre/ (the scout's example was
wrong — "chrme" DOES match "Chrome" as a subsequence, 48.5):
transpositions and wrong letters scored nothing — "chorme",
"crhome", "chrmoe" all returned None. They now match in the typo
band (25–35), below every clean lane and above a description
subsequence (20).
"""

import time
import unittest

from core import command_palette as palette
from core.skill_gate_admit import levenshtein


class TypoLaneTests(unittest.TestCase):
    def test_transpositions_now_match(self):
        for needle in ("chorme", "crhome", "chrmoe"):
            score = palette.score_match(needle, "Chrome")
            self.assertIsNotNone(score, needle)
            self.assertGreaterEqual(score, 25.0)
            self.assertLessEqual(score, 35.0)

    def test_substitution_typos_now_match(self):
        self.assertIsNotNone(palette.score_match("settnigs", "Settings"))
        self.assertIsNotNone(palette.score_match("calcualtor", "Calculator"))

    def test_multiword_label_matches_on_a_word(self):
        # "chorme" is a typo of the word "Chrome" inside a longer label.
        self.assertIsNotNone(
            palette.score_match("chorme", "Google Chrome"))

    def test_parent_corrected_case_unchanged(self):
        # "chrme" is a subsequence of "chrome" — it matched before
        # the batch (48.5) and still matches through the same lane,
        # not the typo lane.
        self.assertEqual(palette.score_match("chrme", "Chrome"), 48.5)

    def test_typo_ranks_below_clean_subsequence(self):
        # A clean subsequence match (40+) outranks a typo match.
        # "chorme" is a subsequence of "Chore Me" (48.3) but only a
        # typo of "Chrome" (31.7).
        entries = [
            {"label": "Chrome", "description": ""},
            {"label": "Chore Me", "description": ""},
        ]
        ranked = palette.rank_entries(entries, "chorme")
        self.assertEqual([e["label"] for e in ranked],
                         ["Chore Me", "Chrome"])

    def test_typo_label_ranks_above_description_subsequence(self):
        # A label with a typo is still a label hit: it outranks a
        # match on another entry's description text (20.0).
        entries = [
            {"label": "Unrelated", "description": "chore meter"},
            {"label": "Chrome", "description": ""},
        ]
        ranked = palette.rank_entries(entries, "chorme")
        self.assertEqual(ranked[0]["label"], "Chrome")

    def test_no_match_still_none(self):
        self.assertIsNone(palette.score_match("zzz", "Globe"))
        self.assertIsNone(palette.score_match("zzzqqq", "Settings"))

    def test_short_needles_cannot_typo_match(self):
        # A 2-letter needle with 1 wrong letter is ratio 0.5 — below
        # the 0.6 bar, so it stays a non-match.
        self.assertIsNone(palette.score_match("ab", "ax"))

    def test_threshold_boundary(self):
        # Distance 2 on a 6-letter word (ratio 0.667) matches;
        # distance 3 does not.
        self.assertIsNotNone(palette.score_match("chorme", "Chrome"))
        self.assertIsNone(palette.score_match("chxrmx", "Chrome"))

    def test_deterministic_order(self):
        entries = [
            {"label": "Chrome", "description": ""},
            {"label": "Chromium", "description": ""},
            {"label": "Settings", "description": ""},
        ]
        first = palette.rank_entries(entries, "chorme")
        second = palette.rank_entries(entries, "chorme")
        self.assertEqual([e["label"] for e in first],
                         [e["label"] for e in second])

    def test_typo_lane_is_fast_enough(self):
        # The port's whole justification: pure-Python Levenshtein
        # over 500 palette rows must stay interactive. A C++ core
        # would be faster, but 500 rows in well under a second needs
        # no compiled extension.
        entries = [{"label": f"Settings Panel {i}", "description": ""}
                   for i in range(500)]
        start = time.monotonic()
        ranked = palette.rank_entries(entries, "settnigs")
        elapsed = time.monotonic() - start
        self.assertEqual(len(ranked), 500)
        self.assertLess(elapsed, 1.0, f"500-row typo query took {elapsed:.3f}s")

    def test_reuses_tree_levenshtein(self):
        # The scorer uses the tree's own function, not a second copy.
        self.assertIs(palette.levenshtein, levenshtein)

    def test_no_rapidfuzz_import_anywhere(self):
        # No adoption: nothing in core/ or requirements.txt may
        # import or declare RapidFuzz.
        import os
        import re
        root = os.path.join(os.path.dirname(__file__), "..")
        core = os.path.join(root, "core")
        hits = []
        for dirpath, _dirnames, filenames in os.walk(core):
            if "__pycache__" in dirpath:
                continue
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                text = open(path, encoding="utf-8").read()
                if re.search(r"^\s*(from|import)\s+rapidfuzz\b",
                             text, re.M):
                    hits.append(path)
        req = open(os.path.join(root, "requirements.txt"),
                   encoding="utf-8").read()
        self.assertNotIn("rapidfuzz", req.lower())
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
