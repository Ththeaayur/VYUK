"""Regression batch G3 — toolbox router gold queries.

ROUTE-01 (window 3) changed EVERY use_toolbox query's ranking. This
suite pins the gold set through the REAL `_find` over the REAL
registry (keyword-only mode in tests — no embedding model loaded),
so a future ranking change that breaks a working query, or silently
changes an audited abstention, fails here first instead of shipping.

Working queries (must rank the right tool first):
- "convert 5 miles to kilometres" -> unit_convert
- "take a screenshot" -> take_screenshot
- "what is the battery level" -> battery_info
- exact-name "unit_convert" -> unit_convert first + declaration inlined

Audited-changed queries (must abstain with the audit's message, not
hand the model a wrong tool):
- "translate this text to French" (no language translation tool)
- "convert a PDF to Word" (no document-format conversion tool)
- "record my screen" (no video recording tool)
- "screenshot of the second monitor" (no per-monitor capture tool)

Stdlib unittest only.
"""

from __future__ import annotations

import unittest

try:
    from core.funnel.tools_registry import tools as _registry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.funnel import toolbox as _toolbox  # Phase 3 F2a INTENTIONAL: toolbox moved to core/funnel/
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")


def _first_listed(output):
    for line in output.splitlines():
        if line.startswith("- "):
            return line[2:].split(" — ")[0].strip()
    return None


class TestRouterGoldQueries(unittest.TestCase):
    def test_working_queries_rank_right_tool_first(self):
        cases = [
            ("convert 5 miles to kilometres", "unit_convert"),
            ("take a screenshot", "take_screenshot"),
            ("what is the battery level", "battery_info"),
        ]
        for query, expected in cases:
            out = _toolbox._find(_registry, query)
            self.assertEqual(_first_listed(out), expected,
                             f"{query!r} ->\n{out[:400]}")

    def test_exact_name_query_inlines_declaration(self):
        out = _toolbox._find(_registry, "unit_convert")
        self.assertEqual(_first_listed(out), "unit_convert",
                         f"exact-name ->\n{out[:400]}")
        # List-only outputs are short (header + <=8 items); an
        # inlined declaration makes the output substantially longer.
        self.assertGreater(len(out.splitlines()), 20,
                           "exact-name query did not inline the "
                           "declaration")

    def test_audited_queries_abstain(self):
        cases = [
            ("translate this text to French", "translates between languages"),
            ("convert a PDF to Word", "converts document formats"),
            ("record my screen", "records video"),
            ("screenshot of the second monitor", "specific monitor by number"),
        ]
        for query, fragment in cases:
            out = _toolbox._find(_registry, query)
            self.assertTrue(
                out.startswith("No suitable tool for"),
                f"{query!r} did not abstain ->\n{out[:400]}")
            self.assertIn(fragment, out,
                          f"{query!r} abstention lost its message ->\n{out[:400]}")


if __name__ == "__main__":
    unittest.main()
