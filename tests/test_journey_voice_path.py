"""Journey 1 — voice path (whole-path phase).

A spoken-style goal travels goal text -> toolbox selection -> real
funnel action -> receipt. This suite proves the lab-provable half:

- "what time is it" selects get_current_time_date first, runs it
  through the REAL funnel, succeeds, and leaves exactly one new
  SUCCEEDED receipt attempt for that tool in the receipt store;
- "take a screenshot" selects take_screenshot first (selection
  works; executing a capture headless is NOT asserted — capture
  needs a real display, so that execution half is field-owed).

NOT proven here (stated honestly, field-owed, never claimed):
- the offered-declaration half is covered by
  tests.test_defectbatch_live_schema (run in verification, not
  duplicated here);
- real Live audio (mic -> Gemini Live -> voice reply) cannot run
  in this lab — no audio device, no Live session. The funnel path
  below is the same path a Live tool call takes after selection,
  but the audio half is owed on the owner's machine.

Stdlib unittest only.
"""

from __future__ import annotations

import unittest

try:
    from core import tool_receipts as receipts_mod
    from core.funnel import tools_registry as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.funnel import toolbox as toolbox_mod  # Phase 3 F2a INTENTIONAL: toolbox moved to core/funnel/
    from tests._journey_base import FunnelBase
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")


def _first_listed(output):
    for line in output.splitlines():
        if line.startswith("- "):
            return line[2:].split(" — ")[0].strip()
    return None


class TestVoicePath(FunnelBase):
    def test_goal_to_selection_to_action_to_receipt(self):
        goal = "what time is it"
        listing = toolbox_mod._find(tools_mod.tools, goal)
        self.assertEqual(_first_listed(listing), "get_current_time_date",
                         f"goal selection broke ->\n{listing[:400]}")
        store = receipts_mod._get_store()
        before = {a["receipt_id"] for a in store.attempts()
                  if a.get("tool") == "get_current_time_date"}
        out = self.call("get_current_time_date", {})
        self.assertEqual(out.get("status"), "success", str(out))
        result_text = str(out.get("result", ""))
        self.assertTrue(result_text.strip(), "empty result text")
        self.assertTrue(any(ch.isdigit() for ch in result_text),
                        f"result has no time/date digits: {result_text!r}")
        after = [a for a in store.attempts()
                 if a.get("tool") == "get_current_time_date"
                 and a["receipt_id"] not in before]
        self.assertEqual(len(after), 1,
                         f"expected exactly one new receipt, got {len(after)}")
        self.assertEqual(after[0].get("status"), "succeeded",
                         f"receipt status dishonest: {after[0]}")

    def test_selection_only_for_headless_capture(self):
        listing = toolbox_mod._find(tools_mod.tools, "take a screenshot")
        self.assertEqual(_first_listed(listing), "take_screenshot",
                         f"capture selection broke ->\n{listing[:400]}")
        # Execution deliberately NOT asserted: a screen capture
        # needs a real display. Field-owed, not lab-proven.


if __name__ == "__main__":
    unittest.main()
