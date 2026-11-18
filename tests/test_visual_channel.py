"""U6 — Visual channel: the tutor can show, not just tell.

Lab leg only. The real-Windows field leg — a real MA 105 lesson on
his machine, the panel showing the bite and the board drawing the
region progressively, never showing the answer — is OWED, not this
batch.

Gap proved first (audit T6): the chat is text-only (ChatBubble
takes text; _split_segments returns prose/code only; the only
QPixmap in the chat path is the static Aura avatar chip), there is
no lesson component anywhere in gui/, no board component, and no
KaTeX/MathJax anywhere in gui/.

What this suite proves: the lesson panel renders a bite (bite text
+ check question + ladder level + mastery bar) without the gold
anywhere in the rendered output; the chat renders an image message
with caption + source attribution; the board accumulates steps
(step 2's picture contains step 1's elements; stepping back shows
the earlier picture); a board step never erases an earlier step; a
leaky bite renders no gold in the panel, the caption, or any board
label (gold-containment, U3's rule, on every rendered string); the
panel reads mastery but never writes it (the panel path holds no
model write handle — the state is built from a plain record dict);
the panel fits the settled home (no new top-level window — the
panel is a child of the existing layout).

Harness: the logic tests need no Qt and run on both interpreters.
The GUI tests need PySide6 and skip without it (test_journey_gui_path
precedent); they install the voice_assistant package shim the same
way that suite does. No real cloud calls, no real waiting.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.brain import visual_channel as vc
from core.brain.lesson_planner import Bite

try:
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QApplication as _QApp
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt optional
    _QT_AVAILABLE = False

SKILL = "ma105-surfaces-flux"
GOLD = "7/3"
RUBRIC = ("The answer must show the integrated value from "
          "integrating term by term. The flux must follow the "
          "surface, not the usual range.")
BITE_TEXT = ("Draw the region first: the surface is the graph of "
             "the function over the region in the plane.")
CHECK = "What is the value of the integral?"
SOURCE = "MA 105 L13 slides, p. 4"

# A bite whose bite text AND check contain the gold value — the
# guards must fall back to generic lines, never leak it.
LEAKY_BITE = ("Draw the region to get 7/3: the surface is the "
              "graph of the function over the region.")
LEAKY_CHECK = "What is 7/3 here?"


def _bite(**overrides):
    fields = {
        "skill": SKILL,
        "bite": BITE_TEXT,
        "check": CHECK,
        "gold": GOLD,
        "rubric": RUBRIC,
        "requires": (),
        "reinforce": "ma105-regions-and-limits",
    }
    fields.update(overrides)
    return Bite(**fields)


def _board_three_steps(gold=None):
    board = vc.ProgressiveBoard()
    board.add_step("Region outline", [
        vc.BoardElement("ellipse", 10, 10, 90, 60)], gold=gold)
    board.add_step("Boundary", [
        vc.BoardElement("line", 10, 10, 90, 10)], gold=gold)
    board.add_step("Normal arrows", [
        vc.BoardElement("arrow", 50, 35, 50, 10, label="n")],
        gold=gold)
    return board


class TestVisualChannelLogic(unittest.TestCase):
    def test_panel_renders_bite_without_gold(self):
        state = vc.build_panel_state(
            _bite(), ladder_level=1,
            mastery={"p": 0.45, "transfers": 1}, source=SOURCE)
        self.assertEqual(state.bite_text, BITE_TEXT)
        self.assertEqual(state.check, CHECK)
        self.assertEqual(state.ladder_level, 1)
        self.assertEqual(state.ladder_max, 3)
        self.assertAlmostEqual(state.mastery_p, 0.45)
        self.assertEqual(state.transfers, 1)
        self.assertFalse(state.mastered)
        self.assertEqual(state.source, SOURCE)
        for field in (state.bite_text, state.check, state.source):
            self.assertNotIn(GOLD, field)

    def test_leaky_bite_renders_no_gold_anywhere(self):
        # The bite text, the check, a board label, an image caption
        # and an image source all contain the gold — none of them
        # may reach a rendered string.
        state = vc.build_panel_state(
            _bite(bite=LEAKY_BITE, check=LEAKY_CHECK),
            ladder_level=0, mastery={"p": 0.30, "transfers": 0},
            source="notes with 7/3 in them")
        for field in (state.bite_text, state.check, state.source):
            self.assertNotIn(GOLD, field)
        board = vc.ProgressiveBoard()
        board.add_step("The step gives 7/3", [
            vc.BoardElement("label", 50, 50, label="answer 7/3")],
            gold=GOLD)
        for label in board.current_labels():
            self.assertNotIn(GOLD, label)
        msg = vc.build_image_message(
            "The diagram shows 7/3", "figure with 7/3", b"bytes",
            gold=GOLD)
        self.assertNotIn(GOLD, msg.caption)
        self.assertNotIn(GOLD, msg.source)
        self.assertEqual(msg.image_ref, b"bytes")

    def test_board_accumulates_and_steps_back(self):
        board = _board_three_steps()
        self.assertEqual(board.step_count, 3)
        self.assertEqual(board.position, 3)
        self.assertEqual(len(board.current_elements()), 3)
        # Step 3's picture contains step 1's elements.
        kinds = [e.kind for e in board.current_elements()]
        self.assertEqual(kinds, ["ellipse", "line", "arrow"])
        self.assertTrue(board.step_back())
        self.assertEqual(board.position, 2)
        self.assertEqual(len(board.current_elements()), 2)
        self.assertTrue(board.step_back())
        self.assertEqual(len(board.current_elements()), 1)
        self.assertFalse(board.step_back())
        self.assertTrue(board.step_forward())
        self.assertEqual(len(board.current_elements()), 2)

    def test_board_step_never_erases(self):
        board = vc.ProgressiveBoard()
        first = vc.BoardElement("ellipse", 10, 10, 90, 60)
        board.add_step("Region outline", [first])
        board.add_step("Boundary", [vc.BoardElement("line", 0, 0, 100, 0)])
        self.assertIn(first, board.current_elements())
        board.step_back()
        board.step_forward()
        self.assertIn(first, board.current_elements())

    def test_board_drops_unknown_kind_and_clamps_coords(self):
        board = vc.ProgressiveBoard()
        board.add_step("Mixed", [
            vc.BoardElement("sparkle", 0, 0, 10, 10),
            vc.BoardElement("point", float("nan"), -50, 500, 0),
            "not an element",
        ])
        elements = board.current_elements()
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0].kind, "point")
        self.assertEqual(elements[0].x0, 0.0)
        self.assertEqual(elements[0].y0, 0.0)

    def test_image_message_keeps_image_ref(self):
        msg = vc.build_image_message(
            "The region and its boundary", SOURCE, b"\x89PNG",
            gold=GOLD)
        self.assertEqual(msg.caption, "The region and its boundary")
        self.assertEqual(msg.source, SOURCE)
        self.assertEqual(msg.image_ref, b"\x89PNG")

    def test_panel_reads_mastery_never_writes(self):
        calls = []
        record = {"skill_id": SKILL, "p": 0.45, "transfers": 1,
                  "evidence": 5}
        before = dict(record)

        class _FakeModel:
            def get_skill(self, skill_id):
                calls.append(("get_skill", skill_id))
                return dict(record)

            def record_graded(self, *args, **kwargs):
                calls.append(("record_graded", args, kwargs))

        class _FakeSession:
            def ladder_level(self):
                return 2

        state = vc.panel_state_from_session(
            _bite(), _FakeSession(), _FakeModel(), source=SOURCE)
        self.assertEqual(state.ladder_level, 2)
        self.assertAlmostEqual(state.mastery_p, 0.45)
        self.assertEqual(record, before)
        self.assertEqual(
            [c for c in calls if c[0] == "record_graded"], [])
        # The state holds no model and no gold — by construction.
        self.assertFalse(hasattr(state, "model"))
        self.assertFalse(hasattr(state, "gold"))
        self.assertFalse(hasattr(state, "bite"))

    def test_panel_state_mastered_at_two_transfers(self):
        self.assertFalse(vc.build_panel_state(
            _bite(), mastery={"p": 0.8, "transfers": 1}).mastered)
        self.assertTrue(vc.build_panel_state(
            _bite(), mastery={"p": 0.9, "transfers": 2}).mastered)

    def test_board_snapshot_round_trip(self):
        board = _board_three_steps()
        board.step_back()
        restored = vc.ProgressiveBoard.from_snapshot(board.snapshot())
        self.assertEqual(restored.step_count, 3)
        self.assertEqual(restored.position, 2)
        self.assertEqual(restored.current_elements(),
                         board.current_elements())
        self.assertEqual(
            vc.ProgressiveBoard.from_snapshot({"nope": 1}).step_count, 0)


def _install_shim():
    """The voice_assistant package shim (test_journey_gui_path
    precedent): the gui modules import voice_assistant.* absolute,
    and in the dev tree that package is a shim over the tree root."""
    tree = str(ROOT)
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    sys.modules["voice_assistant"] = pkg
    core_pkg = types.ModuleType("voice_assistant.core")
    core_pkg.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant.core"] = core_pkg
    gui_pkg = types.ModuleType("voice_assistant.gui")
    gui_pkg.__path__ = [os.path.join(tree, "gui")]
    sys.modules["voice_assistant.gui"] = gui_pkg


def _ensure_app():
    app = _QApp.instance()
    if app is None:
        app = _QApp([])
    return app


@unittest.skipUnless(_QT_AVAILABLE, "PySide6 unavailable")
class TestVisualChannelGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_shim()
        cls.app = _ensure_app()
        from gui.lesson_panel import BoardWidget, LessonPanel
        from gui.window_widgets_bubble import ChatBubble
        cls.LessonPanel = LessonPanel
        cls.BoardWidget = BoardWidget
        cls.ChatBubble = ChatBubble

    def test_lesson_panel_renders_state_no_gold(self):
        state = vc.build_panel_state(
            _bite(bite=LEAKY_BITE, check=LEAKY_CHECK),
            ladder_level=2, mastery={"p": 0.6, "transfers": 1},
            source=SOURCE)
        panel = self.LessonPanel()
        panel.show_state(state)
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.mastery_bar.value(), 60)
        self.assertIn("1 clean transfer", panel.mastery_lbl.text())
        self.assertIn("Hint level 2 of 3", panel.ladder_lbl.text())
        for text in panel.rendered_texts():
            self.assertNotIn(GOLD, text)
        self.assertFalse(hasattr(panel, "gold"))
        self.assertIs(panel.state(), state)

    def test_lesson_panel_is_child_not_window(self):
        parent = QWidget()
        panel = self.LessonPanel(parent)
        self.assertFalse(panel.isWindow())
        self.assertIs(panel.parent(), parent)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        panel2 = self.LessonPanel()
        layout.addWidget(panel2)
        self.assertFalse(panel2.isWindow())
        self.assertIs(panel2.parent(), holder)

    def test_lesson_panel_clear_hides(self):
        panel = self.LessonPanel()
        panel.show_state(vc.build_panel_state(_bite()))
        self.assertTrue(panel.isVisible())
        panel.clear()
        self.assertFalse(panel.isVisible())
        self.assertIsNone(panel.state())

    def test_board_widget_paints_and_steps(self):
        widget = self.BoardWidget()
        widget.resize(320, 200)
        widget.set_board(_board_three_steps())
        self.assertEqual(len(widget.current_elements()), 3)
        self.assertTrue(widget.step_back())
        self.assertEqual(len(widget.current_elements()), 2)
        image = widget.to_image()
        self.assertIsInstance(image, QImage)
        self.assertFalse(image.isNull())
        self.assertEqual(image.width(), 320)

    def test_chat_bubble_image_caption_source(self):
        pixmap = QPixmap(200, 100)
        pixmap.fill()
        bubble = self.ChatBubble(
            "VYUK", "Here is the region.", False,
            image=pixmap, caption="The region and its boundary",
            source=SOURCE)
        self.assertIsNotNone(bubble.image_lbl)
        self.assertFalse(bubble.image_lbl.pixmap().isNull())
        self.assertEqual(bubble.caption_lbl.text(),
                         "The region and its boundary")
        self.assertIn(SOURCE, bubble.source_lbl.text())
        self.assertIn("The region and its boundary",
                      bubble._plain_text)

    def test_chat_bubble_without_image_unchanged(self):
        bubble = self.ChatBubble("VYUK", "Just words.", False)
        self.assertIsNone(bubble.image_lbl)
        self.assertIsNone(bubble.caption_lbl)
        self.assertIsNone(bubble.source_lbl)
        self.assertEqual(bubble._plain_text, "Just words.")


if __name__ == "__main__":
    unittest.main()
