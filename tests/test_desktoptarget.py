"""Desktop-control programme, group (ii): unified target resolution
+ freshness + outcome separation (audit 8 F-07/F-08, audit 9
D-01/D-02/D-03/D-04).

Lab honesty: cursor/vision/UIA code is Windows-gated, so these
tests drive the pure logic (freshness decision, shared click
pick, outcome trailer, scroll-point choice, block sending) with
fake elements/windows, and stub the Windows-only seams the way
existing suites do. Real Windows behaviour — target resolution
on real apps, freshness against real window changes, the D-10
matrix — stays field work and is not claimed here.

Taint note (group (i) lesson): this suite never drives
_tool_screen_look or anything wrapping OCR/screen text, so it
needs no injection.clear_taint; it also restores every module
attribute it patches (addCleanup) so it cannot poison later
suites in the same process.
"""

import sys
import types
import unittest
from unittest import mock

from core.desktop import control_plus  # Phase 3 D2b INTENTIONAL: control_plus moved to core/desktop/
from core import skills_window_control  # Phase C C6 INTENTIONAL: hands3 renamed to window_control
from core.desktop import uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.desktop import uia_base as _uia_base_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_click as _uia_click_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_discover as _uia_discover_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_marks as uia_marks_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_probe as _uia_probe_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_send as _uia_send_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_type as _uia_type_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_verify as _uia_verify_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = (
            left, top, right, bottom)


class _Info:
    def __init__(self, name, control_type="Button", enabled=True,
                 rect=None):
        self.name = name
        self.class_name = "FakeClass"
        self.control_type = control_type
        self.enabled = enabled
        self.rectangle = rect if rect is not None else _Rect(0, 0, 50, 20)


class _El:
    """A fake UIA wrapper: _element_facts + _control_usable read
    element_info / is_enabled / is_visible; clicks record."""

    def __init__(self, name, control_type="Button", enabled=True):
        self.element_info = _Info(name, control_type, enabled)
        self._enabled = enabled
        self.clicked = []
        self.focused = False
        self.value = ""

    def is_enabled(self):
        return self._enabled

    def is_visible(self):
        return True

    def click_input(self):
        self.clicked.append("click_input")

    def set_focus(self):
        self.focused = True

    def set_edit_text(self, text):
        self.value = text

    @property
    def iface_invoke(self):
        raise AssertionError("Invoke fallback must not run in these tests")


class _Win:
    def __init__(self, elements):
        self._elements = elements

    def descendants(self):
        return list(self._elements)


def _facts(name, control_type="Button"):
    return {"name": name, "class_name": "FakeClass",
            "control_type": control_type, "rect": "(0,0)–(50,20)"}


# Refactor P1 step 4 INTENTIONAL seam migration: core/desktop/uia.py split into
# uia_base/verify/discover/probe/marks/click/send/type. A fake must land
# in every module that binds the name (a function looks its helpers up
# in its own module's namespace), so patching only core.desktop.uia no longer
# reaches the moved functions. Call sites are unchanged.
_UIA_SEAM_MODULES = (uia, _uia_base_mod, _uia_verify_mod,
                     _uia_discover_mod, _uia_probe_mod, uia_marks_mod,
                     _uia_click_mod, _uia_send_mod, _uia_type_mod)


def _patch(testcase, target, value):
    if target.startswith("core.desktop.uia."):  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/; prefix moves with the targets
        name = target[len("core.desktop.uia."):]  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/; prefix moves with the targets
        for mod in _UIA_SEAM_MODULES:
            if hasattr(mod, name):
                patcher = mock.patch.object(mod, name, value)
                patcher.start()
                testcase.addCleanup(patcher.stop)
        return
    patcher = mock.patch(target, value)
    patcher.start()
    testcase.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# F-07 — mark freshness is handle-first, title as fallback
# ---------------------------------------------------------------------------


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        # Refactor P1 step 4 INTENTIONAL: marks state lives in
        # core/desktop/uia_marks.py now; save/restore it there (the uia
        # module only re-exports an import-time snapshot).
        self._saved = (uia_marks_mod._last_marks,
                       uia_marks_mod._last_marks_fg,
                       uia_marks_mod._last_marks_hwnd)
        _patch(self, "core.desktop.uia.foreground_title", lambda: "Notes")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: 111)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/

    def tearDown(self):
        (uia_marks_mod._last_marks, uia_marks_mod._last_marks_fg,
         uia_marks_mod._last_marks_hwnd) = self._saved

    def _stamp(self, title="Notes", hwnd=111):
        uia._set_last_marks(
            [{"id": 1, "name": "Save", "control_type": "Button",
              "rect": (0, 0, 100, 100)}], title, hwnd)

    def test_same_title_different_hwnd_is_stale(self):
        self._stamp("Notes", 111)
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: 222)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        reason = uia._marks_stale_reason(1)
        self.assertIsNotNone(reason)
        self.assertIn("stale", reason)
        self.assertIn("different window", reason)

    def test_same_title_same_hwnd_is_fresh(self):
        self._stamp("Notes", 111)
        self.assertIsNone(uia._marks_stale_reason(1))

    def test_different_title_is_stale_when_hwnd_unknown(self):
        self._stamp("Notes", None)
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_title", lambda: "Browser")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        reason = uia._marks_stale_reason(1)
        self.assertIsNotNone(reason)
        self.assertIn("Notes", reason)
        self.assertIn("Browser", reason)

    def test_same_title_unknown_hwnd_is_fresh(self):
        self._stamp("Notes", None)
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        self.assertIsNone(uia._marks_stale_reason(1))

    def test_unreadable_title_never_counts_as_change(self):
        self._stamp("Notes", None)
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_title", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        self.assertIsNone(uia._marks_stale_reason(1))

    def test_click_by_mark_refuses_on_hwnd_change(self):
        self._stamp("Notes", 111)
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: 222)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._failsafe_block", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        result = uia.click_by_mark(1)
        self.assertIn("stale", result)
        self.assertNotIn("Clicked mark", result)

    def test_restamp_without_hwnd_keeps_stamp(self):
        self._stamp("Notes", 111)
        uia._set_last_marks([{"id": 1}], "Notes")
        self.assertEqual(uia.last_marks_foreground_hwnd(), 111)
        uia.clear_last_marks()
        self.assertIsNone(uia.last_marks_foreground_hwnd())
        self.assertEqual(uia.last_marks(), [])


# ---------------------------------------------------------------------------
# F-08 — dispatch, verified landing, and failure stay distinct
# ---------------------------------------------------------------------------


class OutcomeTests(unittest.TestCase):
    def test_trailer_appended_to_clicked_and_invoked(self):
        for text in ("Clicked 'Save' (Button) at (0,0)–(50,20).",
                     "Invoked 'Save' (Button) at (0,0)–(50,20)."):
            out = uia._with_dispatch_trailer(text)
            self.assertTrue(out.startswith(text))
            self.assertIn("landing not verified", out)
            self.assertIn("no expectation was given", out)

    def test_trailer_not_appended_to_failures(self):
        for text in ("Could not click it: boom",
                     "No control with a name containing 'X' was found.",
                     "Found 2 control(s) matching 'X' in Y, but none "
                     "could be clicked: 'A' (disabled). Nothing was "
                     "clicked."):
            self.assertEqual(uia._with_dispatch_trailer(text), text)

    def test_wrapper_no_expect_gets_trailer(self):
        _patch(self, "core.desktop.uia._uia_click_control_impl",
               lambda *a, **k: "Clicked 'Save' (Button) at (0,0)–(50,20).")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        result = uia.uia_click_control("Save")
        self.assertIn("Clicked 'Save'", result)
        self.assertIn("landing not verified", result)

    def test_wrapper_expect_gets_verdict_not_trailer(self):
        _patch(self, "core.desktop.uia._uia_click_control_impl",
               lambda *a, **k: "Clicked 'Save' (Button) at (0,0)–(50,20).")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._safe_snapshot", lambda: {"fg": "Notes"})  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.wait_for_effect", lambda expect: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.verify_expectation",
               lambda *a, **k: ("met", "a window titled 'Save As' appeared."))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        result = uia.uia_click_control(
            "Save", expect="a dialog titled Save As appears")
        self.assertIn("Expectation 'a dialog titled Save As appears': met",
                      result)
        self.assertNotIn("landing not verified", result)


# ---------------------------------------------------------------------------
# D-01 — one click policy in both registered paths
# ---------------------------------------------------------------------------


class UnifiedPickTests(unittest.TestCase):
    def test_exact_beats_substring(self):
        save_as = _El("Save As")
        save = _El("Save")
        chosen, note = uia._pick_click_target(
            [(save_as, _facts("Save As")), (save, _facts("Save"))],
            "Save", "the foreground window")
        self.assertIs(chosen[0], save)
        self.assertIn("exact name match", note)
        self.assertIn("were not clicked", note)
        self.assertIn("Save As", note)

    def test_ambiguous_exact_refuses(self):
        a = _El("Save")
        b = _El("Save")
        chosen, refusal = uia._pick_click_target(
            [(a, _facts("Save")), (b, _facts("Save"))],
            "Save", "the foreground window")
        self.assertIsNone(chosen)
        self.assertIn("won't guess", refusal)
        self.assertEqual(a.clicked, [])
        self.assertEqual(b.clicked, [])

    def test_ambiguous_substring_refuses(self):
        a = _El("Save As")
        b = _El("Save All")
        chosen, refusal = uia._pick_click_target(
            [(a, _facts("Save As")), (b, _facts("Save All"))],
            "Save", "the foreground window")
        self.assertIsNone(chosen)
        self.assertIn("won't guess", refusal)

    def test_single_substring_clicks_with_partial_note(self):
        el = _El("Save As")
        chosen, note = uia._pick_click_target(
            [(el, _facts("Save As"))], "Save", "the foreground window")
        self.assertIs(chosen[0], el)
        self.assertIn("partial-name", note)

    def test_unusable_exact_does_not_fall_through_to_substring(self):
        exact = _El("Save", enabled=False)
        substr = _El("Save As")
        chosen, refusal = uia._pick_click_target(
            [(exact, _facts("Save")), (substr, _facts("Save As"))],
            "Save", "the foreground window")
        self.assertIsNone(chosen)
        self.assertIn("none could be clicked", refusal)
        self.assertEqual(substr.clicked, [])

    def test_truncation_disclosed_in_note(self):
        el = _El("Save As")
        _chosen, note = uia._pick_click_target(
            [(el, _facts("Save As"))], "Save", "the foreground window",
            truncated=True)
        self.assertIn("scan stopped early", note)

    def _impl_setup(self, elements):
        _patch(self, "core.desktop.uia._routed_backend", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._failsafe_block", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._unavailable", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._uia_window",
               lambda title: (_Win(elements), "the foreground window"))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._control_state", lambda el: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._safe_snapshot", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._probe_click_suffix", lambda *a, **k: "")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/

    def test_impl_clicks_exact_not_first_substring(self):
        save_as = _El("Save As")
        save = _El("Save")
        self._impl_setup([save_as, save])
        result = uia._uia_click_control_impl("Save")
        self.assertIn("Clicked 'Save'", result)
        self.assertIn("exact name match", result)
        self.assertEqual(save.clicked, ["click_input"])
        self.assertEqual(save_as.clicked, [])

    def test_impl_ambiguous_exact_refuses(self):
        a = _El("Save")
        b = _El("Save")
        self._impl_setup([a, b])
        result = uia._uia_click_control_impl("Save")
        self.assertIn("won't guess", result)
        self.assertEqual(a.clicked, [])
        self.assertEqual(b.clicked, [])

    def test_click_element_uses_shared_pick_and_trailer(self):
        save_as = _El("Save As")
        save = _El("Save")
        fake_pywinauto = types.ModuleType("pywinauto")
        with mock.patch.dict(sys.modules, {"pywinauto": fake_pywinauto}):
            _patch(self, "core.desktop.control_plus._is_windows", lambda: True)  # Phase 3 D2b INTENTIONAL: patch target moved with module
            _patch(self, "core.desktop.control_plus._input_blocked", lambda: None)  # Phase 3 D2b INTENTIONAL: patch target moved with module
            _patch(self, "core.desktop.control_plus._uia_window",
                   lambda title: (_Win([save_as, save]),
                                  "the foreground window"))  # Phase 3 D2b INTENTIONAL: patch target moved with module
            _patch(self, "core.desktop.uia._descendants",
                   lambda win: (win.descendants(), None))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
            _patch(self, "core.desktop.uia._safe_snapshot", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
            _patch(self, "core.desktop.uia._control_state", lambda el: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
            _patch(self, "core.desktop.uia._probe_click_suffix",
                   lambda *a, **k: "")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
            result = control_plus.click_element("Save")
        # T-4 (debug pass): click_element is declared -> str but the
        # success path returns an Outcome (hands_landing_check) —
        # assert on its .message, the plain-words sentence the brain
        # speaks from (the base message is preserved verbatim).
        self.assertIn("Clicked 'Save'", result.message)
        self.assertIn("exact name match", result.message)
        self.assertIn("landing not verified", result.message)
        self.assertEqual(save.clicked, ["click_input"])
        self.assertEqual(save_as.clicked, [])


# ---------------------------------------------------------------------------
# D-02 — a truncated scan never types silently
# ---------------------------------------------------------------------------


class TypingTruncationTests(unittest.TestCase):
    def _impl_setup(self, matches, truncated):
        _patch(self, "core.desktop.uia._routed_backend", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._failsafe_block", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._unavailable", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._uia_window",
               lambda title: (_Win([]), "the foreground window"))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._find_matches",
               lambda win, name, *a: (matches, 5, truncated, None))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/

    def test_truncated_scan_with_match_refuses(self):
        el = _El("Username", control_type="Edit")
        self._impl_setup([(el, _facts("Username", "Edit"))], True)
        result = uia._uia_type_text_impl("Username", "hello")
        self.assertIn("stopped scanning", result)
        self.assertIn("didn't type", result)
        self.assertEqual(el.value, "")

    def test_truncated_scan_without_match_refuses(self):
        self._impl_setup([], True)
        result = uia._uia_type_text_impl("Username", "hello")
        self.assertIn("may exist deeper", result)
        self.assertIn("didn't type", result)

    def test_untruncated_scan_reaches_the_ladder(self):
        el = _El("Username", control_type="Edit")
        self._impl_setup([(el, _facts("Username", "Edit"))], False)
        _patch(self, "core.desktop.uia._current_text",
               mock.Mock(side_effect=["", "hello"]))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._safe_snapshot", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._focus_assertion",
               lambda el: ("confirmed", "Username"))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.input_guard.element_is_password",
               lambda el: False)
        result = uia._uia_type_text_impl("Username", "hello")
        self.assertIn("Typed 5 character(s)", result)
        self.assertNotIn("stopped scanning", result)
        self.assertEqual(el.value, "hello")


# ---------------------------------------------------------------------------
# D-03 — scroll where the inspection happened
# ---------------------------------------------------------------------------


class ScrollPointTests(unittest.TestCase):
    def test_words_bbox_centre(self):
        words = [{"left": 100, "top": 200, "right": 300, "bottom": 400},
                 {"left": 0, "top": 0, "right": 100, "bottom": 100}]
        self.assertEqual(skills_window_control._inspected_scroll_point(words),
                         (150, 200))

    def test_crop_centre_fallback(self):
        _patch(self, "core.desktop.vision.last_capture_info",
               lambda: {"cropped": True, "crop_left": 800, "crop_top": 100,
                        "crop_width": 800, "crop_height": 600})  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/
        self.assertEqual(skills_window_control._inspected_scroll_point([]),
                         (1200, 400))

    def test_pointer_fallback_when_nothing_on_record(self):
        _patch(self, "core.desktop.vision.last_capture_info", lambda: None)  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/
        self.assertIsNone(skills_window_control._inspected_scroll_point([]))

    def test_malformed_words_fall_through_to_crop(self):
        _patch(self, "core.desktop.vision.last_capture_info",
               lambda: {"cropped": True, "crop_left": 0, "crop_top": 0,
                        "crop_width": 400, "crop_height": 200})  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/
        words = [{"text": "x"},  # no box at all
                 {"left": 10, "top": 10, "right": 10, "bottom": 50}]
        self.assertEqual(skills_window_control._inspected_scroll_point(words),
                         (200, 100))

    def test_scroll_until_found_scrolls_at_inspected_point(self):
        words = [{"text": "hello", "left": 100, "top": 100,
                  "right": 300, "bottom": 200}]
        calls = []
        # SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL: scroll_until_found reads _ocr_words/_input_blocked from the capture-scroll module's globals now, so these patches target that module, not the facade.
        _patch(self, "core.skills_hands3_capture_scroll._ocr_words",
               lambda: (words, None))
        _patch(self, "core.skills_hands3_capture_scroll._input_blocked", lambda: None)
        _patch(self, "core.desktop.cursor.scroll_cursor",
               lambda amount, **kw: calls.append((amount, kw))
               or "Scrolled by 5 step(s).")  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        with mock.patch("time.sleep", lambda s: None):
            result = skills_window_control.scroll_until_found("missing phrase",
                                                      max_attempts=1)
        self.assertIn("stopped moving", result)
        self.assertEqual(len(calls), 1)
        amount, kw = calls[0]
        self.assertEqual(amount, -5)
        self.assertEqual((kw["x"], kw["y"]), (200, 150))

    def test_scroll_until_found_pointer_fallback(self):
        # SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL: scroll_until_found reads _ocr_words/_input_blocked from the capture-scroll module's globals now, so these patches target that module, not the facade.
        _patch(self, "core.skills_hands3_capture_scroll._ocr_words", lambda: ([], None))
        _patch(self, "core.skills_hands3_capture_scroll._input_blocked", lambda: None)
        _patch(self, "core.desktop.vision.last_capture_info", lambda: None)  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/
        calls = []
        _patch(self, "core.desktop.cursor.scroll_cursor",
               lambda amount, **kw: calls.append((amount, kw))
               or "Scrolled by 5 step(s).")  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        with mock.patch("time.sleep", lambda s: None):
            skills_window_control.scroll_until_found("missing phrase",
                                             max_attempts=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], (-5, {}))


# ---------------------------------------------------------------------------
# D-04 — per-batch revalidation, exact partial reporting
# ---------------------------------------------------------------------------


class BatchSendTests(unittest.TestCase):
    def test_all_blocks_sent(self):
        blocks = [["a", "b"], ["c"]]
        completed, aborted, sent = uia._send_in_blocks(
            blocks, [1, 2], lambda b: True, lambda: True)
        self.assertEqual((completed, aborted, sent), (True, False, 2))

    def test_target_change_aborts_remaining_blocks(self):
        blocks = [["a"], ["b"], ["c"]]
        seen = []
        target = [True, False]  # current before block 1, changed after

        def target_ok():
            return target.pop(0) if target else False

        completed, aborted, sent = uia._send_in_blocks(
            blocks, [1, 2, 3],
            lambda b: seen.append(b) or True, target_ok)
        self.assertEqual((completed, aborted, sent), (False, True, 1))
        self.assertEqual(len(seen), 1)

    def test_send_failure_is_not_an_abort(self):
        blocks = [["a"], ["b"]]
        completed, aborted, sent = uia._send_in_blocks(
            blocks, [1, 2], lambda b: b != ["b"], lambda: True)
        self.assertEqual((completed, aborted, sent), (False, False, 1))

    def test_empty_send_completes(self):
        self.assertEqual(
            uia._send_in_blocks([], [], lambda b: True, lambda: True),
            (True, False, 0))

    def test_chunk_events_counts_and_straddles(self):
        groups = [(i, f"e{i}") for i in range(225) for _ in (0, 1)]
        # 225 chars x 2 events = 450 events -> 200/200/50
        blocks, per_block = uia._chunk_events(groups)
        self.assertEqual([len(b) for b in blocks], [200, 200, 50])
        self.assertEqual(per_block, [100, 200, 225])

    def test_chunk_events_straddling_char_counts_late(self):
        groups = [(0, "a"), (0, "b"), (0, "c"), (0, "d"),
                  (1, "e"), (1, "f")]
        blocks, per_block = uia._chunk_events(groups, chunk_size=5)
        self.assertEqual([len(b) for b in blocks], [5, 1])
        self.assertEqual(per_block, [1, 2])

    def test_chunk_events_clear_prefix_not_counted(self):
        groups = [(None, "c") for _ in range(6)]
        groups += [(0, "a"), (0, "b"), (1, "c"), (1, "d")]
        _blocks, per_block = uia._chunk_events(groups)
        self.assertEqual(per_block, [2])

    def test_partial_report_wording(self):
        report = uia._partial_type_report(_facts("Username", "Edit"),
                                          "the foreground window", 2, 5)
        self.assertIn("Typed 2 of 5 character(s)", report)
        self.assertIn("remaining 3 character(s) were NOT typed", report)
        self.assertIn("Username", report)

    def test_non_windows_engines_report_failure_not_abort(self):
        # The lab is Linux: both engines stop before any SendInput.
        self.assertEqual(uia._sendinput_type("abc"),
                         uia._SendOutcome(False, False, 0, 3))
        self.assertEqual(uia._scancode_type("abc"),
                         uia._SendOutcome(False, False, 0, 3))

    def test_target_still_current_rules(self):
        self.assertTrue(uia._target_still_current(None))
        _patch(self, "core.desktop.uia._foreground_identity",
               lambda: (111, 1000))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        self.assertTrue(uia._target_still_current((111, 1000)))
        self.assertFalse(uia._target_still_current((222, 1000)))
        self.assertFalse(uia._target_still_current((111, 2000)))
        _patch(self, "core.desktop.uia._foreground_identity", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        self.assertFalse(uia._target_still_current((111, 1000)))


if __name__ == "__main__":
    unittest.main()
