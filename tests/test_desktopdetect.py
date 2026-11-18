"""Desktop-control programme, group (v): layered detection
(audit 8 F-06).

Lab honesty: cursor/vision/UIA code is Windows-gated, so these
tests drive the layering logic with fake elements/windows, and
stub the Windows-only seams the way existing suites do. Real
Windows behaviour — unnamed/custom/canvas/game targets on real
apps, layer disclosure seen in real mark listings, the D-10
matrix — stays field work and is not claimed here.

Taint note (group (i) lesson): this suite never drives
_tool_screen_look or anything wrapping OCR/screen text, so it
needs no injection.clear_taint; it also restores every module
attribute it patches (addCleanup) so it cannot poison later
suites in the same process.

Design under test (core/desktop/uia.py enumerate_marks, F-06 layer 3):
layer 1 = named interactive UIA controls (unchanged); layer 2 =
OCR word boxes merged in (unchanged); layer 3 = NEW — nameless,
usable, bounded interactive-or-Custom controls, appended after
the named pass with continuing ids, source "uia_unnamed", key
"layer" = "unnamed", named by position. Non-interactive
nameless elements earn nothing, and a surface with no UIA
children at all earns no mark from this layer — that is the
honest limit, not a fabricated coordinate. Every mark,
whatever its layer, resolves through click_by_mark's existing
staleness + virtual-bounds checks; no layer bypasses a guard.
"""

import sys
import types
import unittest
from unittest import mock

from core.desktop import uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.desktop import uia_base as _uia_base_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_click as _uia_click_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_discover as _uia_discover_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_marks as _uia_marks_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_probe as _uia_probe_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_send as _uia_send_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_type as _uia_type_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_verify as _uia_verify_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import vision  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/


# ---------------------------------------------------------------------------
# Fakes (mirroring tests/test_desktoptarget.py)
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
    element_info / is_enabled / is_visible."""

    def __init__(self, name, control_type="Button", enabled=True,
                 rect=None):
        self.element_info = _Info(name, control_type, enabled, rect)
        self._enabled = enabled

    def is_enabled(self):
        return self._enabled

    def is_visible(self):
        return True


class _Win:
    def __init__(self, elements):
        self._elements = elements

    def descendants(self):
        return list(self._elements)


# Refactor P1 step 4 INTENTIONAL seam migration: core/desktop/uia.py split into
# uia_base/verify/discover/probe/marks/click/send/type. A fake must land
# in every module that binds the name (a function looks its helpers up
# in its own module's namespace), so patching only core.desktop.uia no longer
# reaches the moved functions. Call sites are unchanged.
_UIA_SEAM_MODULES = (uia, _uia_base_mod, _uia_verify_mod,
                     _uia_discover_mod, _uia_probe_mod, _uia_marks_mod,
                     _uia_click_mod, _uia_send_mod, _uia_type_mod)


def _patch(test, target, value):
    if target.startswith("core.desktop.uia."):  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/; prefix moves with the targets
        name = target[len("core.desktop.uia."):]  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/; prefix moves with the targets
        for mod in _UIA_SEAM_MODULES:
            if hasattr(mod, name):
                patcher = mock.patch.object(mod, name, value)
                patcher.start()
                test.addCleanup(patcher.stop)
        return
    patcher = mock.patch(target, value)
    patcher.start()
    test.addCleanup(patcher.stop)


def _enum_setup(test, elements):
    """Stub the Windows-only seams so enumerate_marks runs its
    UIA passes on fake elements."""
    _patch(test, "core.desktop.uia._unavailable", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
    _patch(test, "core.desktop.uia._uia_window",
           lambda title: (_Win(elements), "the foreground window"))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
    _patch(test, "core.desktop.uia._marks_capture_block", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
    _patch(test, "core.desktop.uia.foreground_title", lambda: "the foreground window")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
    _patch(test, "core.desktop.uia.foreground_hwnd", lambda: 1111)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/


# ---------------------------------------------------------------------------
# F-06 layer 3 — unnamed marks
# ---------------------------------------------------------------------------


class UnnamedLayerTests(unittest.TestCase):
    def test_named_layer_unchanged(self):
        """Named marks keep their exact pre-F-06 shape: ids in
        walk order, source "uia", no "layer" key, no unnamed
        suffix — the named layer is not reworded by this batch."""
        save = _El("Save", rect=_Rect(0, 0, 80, 30))
        edit = _El("Search", "Edit", rect=_Rect(0, 40, 200, 70))
        _enum_setup(self, [save, edit])
        marks, note = uia.enumerate_marks()
        self.assertEqual(note, "")
        self.assertEqual(len(marks), 2)
        self.assertEqual(marks[0]["id"], 1)
        self.assertEqual(marks[0]["name"], "Save")
        self.assertEqual(marks[0]["source"], "uia")
        self.assertNotIn("layer", marks[0])
        self.assertEqual(marks[1]["id"], 2)
        self.assertEqual(marks[1]["name"], "Search")
        self.assertEqual(marks[1]["source"], "uia")
        self.assertNotIn("layer", marks[1])

    def test_unnamed_interactive_gets_positional_mark(self):
        """A nameless usable bounded Button yields a mark AFTER
        the named marks, with continuing id, positional name,
        source "uia_unnamed", and layer "unnamed"."""
        save = _El("Save", rect=_Rect(0, 0, 80, 30))
        mystery = _El("", "Button", rect=_Rect(100, 100, 150, 130))
        _enum_setup(self, [save, mystery])
        marks, _note = uia.enumerate_marks()
        self.assertEqual(len(marks), 2)
        unnamed = marks[1]
        self.assertEqual(unnamed["id"], 2)
        self.assertEqual(unnamed["name"], "Unnamed Button at (125,115)")
        self.assertEqual(unnamed["control_type"], "Button")
        self.assertEqual(unnamed["rect"], (100, 100, 150, 130))
        self.assertEqual(unnamed["source"], "uia_unnamed")
        self.assertEqual(unnamed["layer"], "unnamed")

    def test_unnamed_custom_control_gets_mark(self):
        """Custom is the "custom target" case: a nameless Custom
        control with a real rect earns an unnamed mark. (A NAMED
        Custom control still earns no named mark — the named
        layer's type set is unchanged.)"""
        named_custom = _El("Widget", "Custom", rect=_Rect(0, 0, 60, 60))
        bare_custom = _El("", "Custom", rect=_Rect(200, 200, 300, 260))
        _enum_setup(self, [named_custom, bare_custom])
        marks, _note = uia.enumerate_marks()
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["name"], "Unnamed Custom at (250,230)")
        self.assertEqual(marks[0]["source"], "uia_unnamed")

    def test_unnamed_disabled_or_rectless_or_duplicate_earns_nothing(self):
        """Usability and geometry rules apply to the unnamed
        layer exactly as to the named layer: disabled, degenerate
        rect, and same-rect-as-a-named-mark controls yield no
        unnamed mark."""
        save = _El("Save", rect=_Rect(0, 0, 80, 30))
        disabled = _El("", "Button", enabled=False,
                       rect=_Rect(100, 100, 150, 130))
        flat = _El("", "Button", rect=_Rect(200, 200, 200, 230))
        shadow = _El("", "Button", rect=_Rect(0, 0, 80, 30))
        _enum_setup(self, [save, disabled, flat, shadow])
        marks, _note = uia.enumerate_marks()
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["name"], "Save")

    def test_non_interactive_nameless_earns_nothing(self):
        """Pane / Text / Window without a name are not targets:
        the unnamed layer covers interactive-or-Custom types
        only, so these yield no mark."""
        pane = _El("", "Pane", rect=_Rect(0, 0, 400, 300))
        text = _El("", "Text", rect=_Rect(10, 10, 100, 30))
        _enum_setup(self, [pane, text])
        marks, note = uia.enumerate_marks()
        self.assertEqual(marks, [])
        self.assertEqual(note, "")

    def test_bare_canvas_surface_earns_no_fabricated_mark(self):
        """Honest limit: a surface exposing no UIA children at
        all (a bare canvas, a game frame) yields NO mark from
        this layer — never a fabricated coordinate mark."""
        _enum_setup(self, [])
        marks, note = uia.enumerate_marks()
        self.assertEqual(marks, [])
        self.assertEqual(note, "")

    def test_unnamed_marks_respect_cap(self):
        """The unnamed pass stops at the same cap as the named
        pass — a window full of nameless buttons cannot flood
        the table past it."""
        elements = [_El("", "Button", rect=_Rect(i * 10, 0, i * 10 + 5, 20))
                    for i in range(100)]
        _enum_setup(self, elements)
        marks, _note = uia.enumerate_marks(cap=5)
        self.assertEqual(len(marks), 5)
        self.assertTrue(all(m["source"] == "uia_unnamed" for m in marks))

    def test_ocr_word_inside_unnamed_rect_is_dropped(self):
        """OCR merge runs after the unnamed pass, so a word whose
        centre falls inside an unnamed control's rect is dropped
        — UIA grounds that spot, same as for named rects."""
        mystery = _El("", "Button", rect=_Rect(100, 100, 150, 130))
        _enum_setup(self, [mystery])
        words = [{"text": "play", "left": 110, "top": 105,
                  "right": 140, "bottom": 125}]
        marks, _note = uia.enumerate_marks(ocr_words=words)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["source"], "uia_unnamed")
        words_far = [{"text": "play", "left": 300, "top": 300,
                      "right": 340, "bottom": 320}]
        marks2, _note2 = uia.enumerate_marks(ocr_words=words_far)
        self.assertEqual(len(marks2), 2)
        self.assertEqual(marks2[1]["source"], "ocr")


# ---------------------------------------------------------------------------
# Brain-facing disclosure (vision.marks_table_text)
# ---------------------------------------------------------------------------


class DisclosureTests(unittest.TestCase):
    def test_unnamed_mark_carries_layer_suffix(self):
        text = vision.marks_table_text([
            {"id": 1, "name": "Save", "control_type": "Button",
             "rect": (0, 0, 80, 30), "source": "uia"},
            {"id": 2, "name": "Unnamed Button at (125,115)",
             "control_type": "Button", "rect": (100, 100, 150, 130),
             "source": "uia_unnamed", "layer": "unnamed"},
        ])
        self.assertIn("[1] 'Save' (Button) at (0,0)\u2013(80,30)", text)
        self.assertIn("[2] 'Unnamed Button at (125,115)' (Button) "
                      "at (100,100)\u2013(150,130) "
                      "[unnamed control \u2014 position only, no label]",
                      text)

    def test_named_and_ocr_marks_gain_no_suffix(self):
        text = vision.marks_table_text([
            {"id": 1, "name": "Save", "control_type": "Button",
             "rect": (0, 0, 80, 30), "source": "uia"},
            {"id": 2, "name": "play", "control_type": "OCRText",
             "rect": (300, 300, 340, 320), "source": "ocr"},
        ])
        self.assertNotIn("unnamed control", text)

    def test_empty_table_is_empty_string(self):
        self.assertEqual(vision.marks_table_text([]), "")


# ---------------------------------------------------------------------------
# Guards apply to every layer (click_by_mark path, unchanged)
# ---------------------------------------------------------------------------


class GuardThroughAllLayersTests(unittest.TestCase):
    def _click_setup(self, elements, hwnd_now=1111, bounds=None):
        _enum_setup(self, elements)
        uia.enumerate_marks()
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: hwnd_now)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._failsafe_block", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._safe_snapshot", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._probe_click_suffix", lambda *a, **k: "")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.cursor._virtual_bounds",
               lambda: bounds if bounds is not None else (0, 0, 1919, 1079))  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        self.clicks = []
        _patch(self, "core.desktop.cursor.click_cursor",
               lambda button="left", count=1, x=None, y=None, **k:
               self.clicks.append((x, y)) or "Clicked.")  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)

    def test_unnamed_mark_click_goes_through_staleness_check(self):
        """A stale table refuses an unnamed mark exactly as it
        refuses a named one — the handle-first F-07 check runs
        before any click, for every layer."""
        mystery = _El("", "Button", rect=_Rect(100, 100, 150, 130))
        self._click_setup([mystery], hwnd_now=2222)
        result = uia.click_by_mark(1)
        self.assertIn("stale", result)
        self.assertEqual(self.clicks, [])

    def test_unnamed_mark_click_goes_through_bounds_check(self):
        """A centre outside the current virtual bounds refuses
        an unnamed mark exactly as it refuses a named one."""
        mystery = _El("", "Button", rect=_Rect(5000, 5000, 5050, 5030))
        self._click_setup([mystery], bounds=(0, 0, 1919, 1079))
        result = uia.click_by_mark(1)
        self.assertIn("no longer on-screen", result)
        self.assertEqual(self.clicks, [])

    def test_unnamed_mark_click_lands_when_fresh_and_in_bounds(self):
        """Fresh table + in-bounds centre: the unnamed mark
        clicks at its rect centre through the normal path."""
        mystery = _El("", "Button", rect=_Rect(100, 100, 150, 130))
        self._click_setup([mystery])
        result = uia.click_by_mark(1)
        self.assertIn("Clicked mark 1", result)
        self.assertIn("Unnamed Button at (125,115)", result)
        self.assertEqual(self.clicks, [(125, 115)])


if __name__ == "__main__":
    unittest.main()
