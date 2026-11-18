"""B18 — picker history window: summarised full history, not just tail 8.

The picker was handed only ``list(history)[-8:]`` (both text brains).
These tests prove the shared helper (core/brain/picker_history.py)
and that both pick paths use it. Fakes only — no cloud calls.
"""

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_PKGROOT = ROOT / ".pkgroot"
if _PKGROOT.is_dir() and str(_PKGROOT) not in sys.path:
    sys.path.insert(0, str(_PKGROOT))

from core.brain import picker_history as ph  # noqa: E402
from core.brain.picker_history import (  # noqa: E402
    MAX_SUMMARY_CHARS,
    render_picker_history,
    summarise_history,
)


def _old_render(history):
    """The pre-B18 rendering, verbatim — the short-goal oracle."""
    if not history:
        return "(no actions taken yet)"
    lines = []
    for record in list(history)[-8:]:
        try:
            lines.append(json.dumps(record, default=str)[:400])
        except (TypeError, ValueError):
            lines.append(str(record)[:400])
    return "\n".join(lines)


def _long_history(n_steps=12):
    """A realistic long goal, using run_goal's real event shapes."""
    history = [{"event": "playbook", "steps": 2, "proof_job_id": "job-7"}]
    for step in range(1, n_steps + 1):
        if step == 3:
            history.append({"event": "retry", "step": step,
                            "tool": "click_text", "reason": "no change",
                            "retry": True, "kind": "transient"})
            history.append({"event": "completed", "step": step,
                            "tool": "click_text", "result": "ok",
                            "expect_verdict": "met"})
        elif step == 5:
            history.append({"event": "step_failed", "step": step,
                            "tool": "open_url", "reason": "timeout",
                            "strikes": 1})
        elif step == 7:
            history.append({"event": "halted", "step": step,
                            "tool": "type_text", "reason": "gated"})
        else:
            history.append({"event": "completed", "step": step,
                            "tool": "open_url", "result": "ok",
                            "expect_verdict": "met"})
    return history


class ShortGoalTests(unittest.TestCase):
    def test_empty_history(self):
        self.assertEqual(render_picker_history([]), "(no actions taken yet)")
        self.assertEqual(render_picker_history(None), "(no actions taken yet)")

    def test_short_goal_byte_identical_to_old_render(self):
        for n in (1, 7, 8):
            history = _long_history(n)[: n + 1]  # playbook + n steps
            # Trim to exactly n records total.
            history = history[:n]
            self.assertEqual(render_picker_history(history),
                             _old_render(history),
                             f"n={n}: short goal must render as before")

    def test_short_goal_with_playbook_event_stays_verbatim(self):
        history = _long_history(3)[:4]
        self.assertEqual(render_picker_history(history),
                         _old_render(history))


class LongGoalTests(unittest.TestCase):
    def test_summary_covers_whole_history_plus_verbatim_tail(self):
        history = _long_history(12)
        out = render_picker_history(history)
        # Every step's tool + outcome + verdict in compressed form.
        for step in (1, 2, 4, 6, 8, 9, 10, 11, 12):
            self.assertIn(f"step {step}: open_url — completed [expect met]",
                          out)
        self.assertIn("step 3: click_text — retried (transient) then "
                      "completed [expect met]", out)
        self.assertIn("step 5: open_url — failed (timeout; strikes 1)", out)
        self.assertIn("step 7: type_text — halted (gated)", out)
        # The full last 8 records verbatim, as today.
        tail = _old_render(history)
        self.assertIn(tail, out)
        self.assertIn("Latest 8 (verbatim):", out)
        self.assertIn("Earlier steps (summary of all", out)

    def test_unknown_outcome_stays_unknown_never_succeeded(self):
        history = _long_history(9)
        # Step 10: only a retry, no outcome ever recorded (crash).
        history.append({"event": "retry", "step": 10, "tool": "click_text",
                        "reason": "no change", "retry": True,
                        "kind": "transient"})
        out = render_picker_history(history)
        self.assertIn("step 10: click_text — no outcome recorded", out)
        self.assertNotIn("step 10: click_text — completed", out)
        self.assertNotIn("step 10: click_text — succeeded", out)

    def test_summary_rebuilt_on_every_pick(self):
        history = _long_history(9)
        first = render_picker_history(history)
        self.assertNotIn("step 12:", first)
        history.extend([
            {"event": "completed", "step": 12, "tool": "write_file",
             "result": "ok", "expect_verdict": "met"},
        ])
        second = render_picker_history(history)
        self.assertIn("step 12: write_file — completed [expect met]", second)

    def test_resumed_goal_pre_stop_steps_in_summary(self):
        # B11: a resumed goal's history carries its pre-stop steps.
        history = [
            {"event": "completed", "step": 1, "tool": "open_url",
             "result": "ok", "expect_verdict": "met"},
            {"event": "interrupted", "step": 2,
             "report": "budget spent", "state": "paused"},
            {"event": "resumed", "step": 2, "report": "resumed"},
            {"event": "completed", "step": 2, "tool": "click_text",
             "result": "ok", "expect_verdict": "met"},
        ]
        for step in range(3, 11):
            history.append({"event": "completed", "step": step,
                            "tool": "open_url", "result": "ok",
                            "expect_verdict": "met"})
        out = render_picker_history(history)
        self.assertIn("step 1: open_url — completed [expect met]", out)
        self.assertIn("step 2: click_text — completed [expect met]", out)

    def test_interrupted_step_with_no_outcome(self):
        history = _long_history(9)
        history.append({"event": "interrupted", "step": 11,
                        "report": "budget spent", "state": "paused"})
        out = render_picker_history(history)
        self.assertIn("step 11: unknown tool — interrupted (budget spent)",
                      out)


class LessonTests(unittest.TestCase):
    def test_b17_lesson_is_first_line(self):
        out = render_picker_history(_long_history(12))
        first = out.splitlines()[0]
        self.assertTrue(first.startswith("Lesson (proven playbook, "
                                         "proof job job-7): 2 proven "
                                         "step(s) run first."), first)

    def test_no_playbook_event_means_no_lesson_line(self):
        history = [r for r in _long_history(12)
                   if r.get("event") != "playbook"]
        out = render_picker_history(history)
        self.assertNotIn("Lesson (proven playbook", out)

    def test_lesson_without_proof_job_id(self):
        history = [{"event": "playbook", "steps": 3,
                    "proof_job_id": None}]
        history.extend(_long_history(9)[1:])
        out = render_picker_history(history)
        self.assertIn("Lesson (proven playbook, proof job unrecorded): "
                      "3 proven step(s) run first.", out)


class BoundTests(unittest.TestCase):
    def test_summary_is_bounded(self):
        history = _long_history(60)
        out = render_picker_history(history)
        # Summary block capped; verbatim tail is 8 x 400 max.
        self.assertLessEqual(len(out), MAX_SUMMARY_CHARS + 8 * 400 + 500)

    def test_failures_never_dropped_when_block_shrinks(self):
        history = []
        for step in range(1, 61):
            if step == 2:
                history.append({"event": "step_failed", "step": step,
                                "tool": "open_url", "reason": "timeout",
                                "strikes": 1})
            elif step == 3:
                history.append({"event": "halted", "step": step,
                                "tool": "type_text", "reason": "gated"})
            else:
                history.append({"event": "completed", "step": step,
                                "tool": "open_url", "result": "ok",
                                "expect_verdict": "met"})
        summary = summarise_history(history)
        self.assertLessEqual(len(summary), MAX_SUMMARY_CHARS + 200)
        self.assertIn("step 2: open_url — failed (timeout; strikes 1)",
                      summary)
        self.assertIn("step 3: type_text — halted (gated)", summary)
        self.assertIn("omitted from this summary", summary)

    def test_short_summary_never_truncated(self):
        self.assertNotIn("omitted from this summary",
                         summarise_history(_long_history(12)))


class WiringTests(unittest.TestCase):
    def test_both_pick_paths_use_the_shared_helper(self):
        from core.voice import cloud_text_goal
        from core.voice import local_client_plangoal
        self.assertIs(cloud_text_goal.render_picker_history,
                      render_picker_history)
        self.assertIs(local_client_plangoal.render_picker_history,
                      render_picker_history)

    def test_both_prompts_describe_the_new_shape(self):
        import inspect
        from core.voice import cloud_text_goal
        from core.voice import local_client_plangoal
        for module in (cloud_text_goal, local_client_plangoal):
            source = inspect.getsource(module)
            self.assertIn("summary of all steps so far", source)
            self.assertNotIn("list(history)[-8:]", source)

    def test_helper_is_stdlib_only(self):
        import inspect
        source = inspect.getsource(ph)
        self.assertNotIn("voice_assistant", source)


if __name__ == "__main__":
    unittest.main()
