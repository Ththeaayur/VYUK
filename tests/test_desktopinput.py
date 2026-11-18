"""Desktop-control programme, group (iii): input reliability
(audit 9 D-05 + D-06 + D-07).

Lab honesty: cursor/vision/UIA code is Windows-gated, so these
tests drive the pure logic (tri-state delivery, partial-block
accounting, read-back-before-retry decisions, the failsafe
registration seam, the pilot schema + validator) with fake
elements/windows/mice, and stub the Windows-only seams the way
existing suites do. Real Windows behaviour — SendInput partial
delivery on real hardware, failsafe release on a real interrupt,
pilot plans on real apps, the D-10 matrix — stays field work
and is not claimed here.

Taint note (group (i) lesson): this suite never drives
_tool_screen_look or anything wrapping OCR/screen text, so it
needs no injection.clear_taint; it also restores every module
attribute it patches (addCleanup) so it cannot poison later
suites in the same process.
"""

import unittest
from unittest import mock

from core.desktop import cursor  # Phase 3 D4d INTENTIONAL: cursor hub moved to core/desktop/
from core import skills_window_control  # Phase C C6 INTENTIONAL: hands3 renamed to window_control
from core.desktop import uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.desktop import uia_base as _uia_base_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_click as _uia_click_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_discover as _uia_discover_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_marks as _uia_marks_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_probe as _uia_probe_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_send as _uia_send_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_type as _uia_type_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_verify as _uia_verify_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core import w4_wiring as w4


# Refactor P1 step 4 INTENTIONAL seam migration: core/desktop/uia.py split into
# uia_base/verify/discover/probe/marks/click/send/type. A fake must land
# in every module that binds the name (a function looks its helpers up
# in its own module's namespace), so patching only core.desktop.uia no longer
# reaches the moved functions. Call sites are unchanged.
_UIA_SEAM_MODULES = (uia, _uia_base_mod, _uia_verify_mod,
                     _uia_discover_mod, _uia_probe_mod, _uia_marks_mod,
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
# D-05 — tri-state delivery + partial-block accounting (pure)
# ---------------------------------------------------------------------------


class DeliveryTests(unittest.TestCase):
    def test_delivery_states(self):
        self.assertEqual(
            uia._SendOutcome(True, False, 5, 5).delivery, "complete")
        self.assertEqual(
            uia._SendOutcome(False, False, 3, 5).delivery, "partial")
        self.assertEqual(
            uia._SendOutcome(False, False, 0, 5).delivery, "failed")
        self.assertEqual(
            uia._SendOutcome(False, True, 2, 5).delivery, "aborted")

    def test_block_progress_matches_chunk_events(self):
        groups = ([(None, "c1"), (None, "c2")]
                  + [(ci, "e%d_%d" % (ci, k))
                     for ci in range(5) for k in range(2)])
        blocks, cpb = uia._chunk_events(groups, chunk_size=4)
        prog = uia._block_char_progress(
            groups, [len(b) for b in blocks])
        self.assertEqual([len(b) for b in blocks], [4, 4, 4])
        self.assertEqual(cpb, [1, 3, 5])
        # Cumulative progress through each block == chars_per_block.
        self.assertEqual(
            [sum(r[-1] for r in prog[:i + 1])
             for i in range(len(prog))], cpb)
        # Clear-prefix events never complete a character.
        self.assertEqual(prog[0][:3], [0, 0, 0])

    def test_straddling_character_counts_only_in_later_block(self):
        # 3 characters x 150 events, chunked 200/200/50 (the group
        # (ii) shape): each character straddles a block boundary.
        groups = [(ci, "e") for ci in range(3) for _ in range(150)]
        blocks, cpb = uia._chunk_events(groups, chunk_size=200)
        prog = uia._block_char_progress(
            groups, [len(b) for b in blocks])
        self.assertEqual([len(b) for b in blocks], [200, 200, 50])
        self.assertEqual(cpb, [1, 2, 3])
        # Block 1's first 149 events complete nothing new (char 1
        # ends at global event 299, the 100th event of block 1);
        # its 100th event completes exactly one character.
        self.assertEqual(prog[1][99], 0)
        self.assertEqual(prog[1][100], 1)

    def test_send_in_blocks_bool_senders_unchanged(self):
        blocks = [["a", "b"], ["c", "d"]]
        cpb = [1, 2]
        self.assertEqual(
            uia._send_in_blocks(blocks, cpb, lambda b: True,
                                lambda: True),
            (True, False, 2))
        self.assertEqual(
            uia._send_in_blocks(blocks, cpb, lambda b: False,
                                lambda: True),
            (False, False, 0))
        self.assertEqual(
            uia._send_in_blocks(blocks, cpb, lambda b: True,
                                lambda: False),
            (False, True, 0))

    def test_send_in_blocks_int_counts(self):
        groups = ([(None, "c1"), (None, "c2")]
                  + [(ci, "e%d_%d" % (ci, k))
                     for ci in range(5) for k in range(2)])
        blocks, cpb = uia._chunk_events(groups, chunk_size=4)
        prog = uia._block_char_progress(
            groups, [len(b) for b in blocks])
        # Block 0 full, block 1 accepts 3 of 4 events: char 1
        # completes inside the accepted prefix, char 2 (which
        # straddles) does not — exactly 2 characters sent.
        calls = []

        def sender(block):
            calls.append(len(block))
            return len(block) if len(calls) == 1 else 3

        self.assertEqual(
            uia._send_in_blocks(blocks, cpb, sender, lambda: True,
                                block_progress=prog),
            (False, False, 2))
        # Counts clamp: negative -> 0, over-long -> full block.
        self.assertEqual(
            uia._send_in_blocks(blocks, cpb, lambda b: -5,
                                lambda: True, block_progress=prog),
            (False, False, 0))
        self.assertEqual(
            uia._send_in_blocks(blocks, cpb, lambda b: 99,
                                lambda: True, block_progress=prog),
            (True, False, 5))
        # Without progress data a short block conservatively
        # contributes nothing beyond fully-sent prior blocks.
        self.assertEqual(
            uia._send_in_blocks(blocks, cpb, lambda b: 2,
                                lambda: True),
            (False, False, 0))


class RetryPlanTests(unittest.TestCase):
    def test_landed(self):
        self.assertEqual(
            uia._retry_plan("hello", "old", "hello"), ("landed", None))

    def test_clean_prefix_remainder(self):
        self.assertEqual(
            uia._retry_plan("hel", "", "hello"), ("remainder", "lo"))

    def test_append_mode_prefix(self):
        # Existing "ab" + typed "cd" of "cdef": remainder "ef".
        self.assertEqual(
            uia._retry_plan("abcd", "ab", "abcdef"), ("remainder", "ef"))

    def test_cleared_but_empty_is_remainder_not_full(self):
        # Replace mode: the clear landed, no characters did — the
        # whole payload is the remainder, and no second clear is
        # needed (a "full" retry would clear an empty field,
        # harmless but not what the plan means).
        self.assertEqual(
            uia._retry_plan("", "old", "hello"), ("remainder", "hello"))

    def test_nothing_landed_is_full(self):
        self.assertEqual(
            uia._retry_plan("old", "old", "hello"), ("full", None))
        self.assertEqual(
            uia._retry_plan("", "", "hello"), ("full", None))

    def test_unexpected_content_is_report(self):
        self.assertEqual(
            uia._retry_plan("h3llo!", "old", "hello"), ("report", None))
        # A longer-than-payload read-back is not a prefix either.
        self.assertEqual(
            uia._retry_plan("hello world", "", "hello"),
            ("report", None))


# ---------------------------------------------------------------------------
# D-05 — caller: _uia_type_text_impl rung 3 / rung 3b
# ---------------------------------------------------------------------------


class _FakeEl:
    """Fake UIA element: rung 2 setters raise (so the ladder
    reaches the keystroke rungs); type_keys records only."""

    def __init__(self):
        self.calls = []

    def set_focus(self):
        pass

    def set_edit_text(self, text):
        raise RuntimeError("no UIA value path in this test")

    @property
    def iface_value(self):
        raise RuntimeError("no ValuePattern in this test")

    def type_keys(self, keys, with_spaces=False, set_focus=False):
        self.calls.append((keys, set_focus))


def _facts(name, control_type="Edit"):
    return {"name": name, "class_name": "FakeClass",
            "control_type": control_type, "rect": "(0,0)-(50,20)"}


class _Win:
    def __init__(self, elements):
        self._elements = elements

    def descendants(self):
        return list(self._elements)


class TypingRetryTests(unittest.TestCase):
    def _impl_setup(self, el, readbacks, sendinput=None, scancode=None,
                    target_ok=True):
        _patch(self, "core.desktop.uia._routed_backend", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._failsafe_block",
               mock.Mock(return_value=None))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._unavailable", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._uia_window",
               lambda title: (_Win([]), "the foreground window"))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._find_matches",
               lambda win, name, *a: ([(el, _facts("Username"))],
                                      1, False, None))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._current_text",
               mock.Mock(side_effect=list(readbacks)))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._safe_snapshot", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._focus_assertion",
               lambda el: ("confirmed", "Username"))  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._probe_type_suffix",
               lambda *a, **k: "")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.input_guard.element_is_password",
               lambda el: False)
        _patch(self, "core.desktop.uia._target_still_current",
               lambda identity: target_ok)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        if sendinput is not None:
            _patch(self, "core.desktop.uia._sendinput_type", sendinput)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        if scancode is not None:
            _patch(self, "core.desktop.uia._scancode_type", scancode)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/

    def _mk_outcome(self, completed, aborted, sent, total):
        return uia._SendOutcome(completed, aborted, sent, total)

    def test_partial_retypes_only_the_remainder(self):
        el = _FakeEl()
        # Append mode reads the field twice before rung 3
        # (value_before, then existing): "", "", then the
        # read-back "hel", then the final "hello".
        self._impl_setup(
            el, ["", "", "hel", "hello"],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 2, 5))
        result = uia._uia_type_text_impl(
            "Username", "hello", clear_first=False)
        self.assertIn("Typed 5 character(s)", result)
        # Exactly the remainder, never the full payload on top.
        self.assertEqual(el.calls, [("lo", False)])

    def test_partial_with_nothing_landed_uses_full_fallback(self):
        el = _FakeEl()
        self._impl_setup(
            el, ["old", "old", "hello"],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 2, 5))
        result = uia._uia_type_text_impl("Username", "hello")
        self.assertIn("Typed 5 character(s)", result)
        self.assertEqual(el.calls,
                         [("^a{BACKSPACE}", True), ("hello", False)])

    def test_partial_with_unexpected_content_never_retries(self):
        el = _FakeEl()
        self._impl_setup(
            el, ["old", "h3llo!"],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 2, 5))
        result = uia._uia_type_text_impl("Username", "hello")
        self.assertIn("stopped rather than retyping", result)
        self.assertEqual(el.calls, [])

    def test_target_changed_means_no_retry(self):
        el = _FakeEl()
        self._impl_setup(
            el, [""],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 2, 5),
            target_ok=False)
        result = uia._uia_type_text_impl("Username", "hello")
        self.assertIn("changed while typing", result)
        self.assertIn("did not retype", result)
        self.assertEqual(el.calls, [])

    def test_fully_failed_uses_full_fallback_when_target_current(self):
        el = _FakeEl()
        self._impl_setup(
            el, ["", "", "", "hello"],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 0, 5))
        result = uia._uia_type_text_impl(
            "Username", "hello", clear_first=False)
        self.assertIn("Typed 5 character(s)", result)
        self.assertEqual(el.calls, [("hello", False)])

    def test_landed_despite_under_report_types_nothing(self):
        el = _FakeEl()
        self._impl_setup(
            el, ["", "", "hello"],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 2, 5))
        result = uia._uia_type_text_impl(
            "Username", "hello", clear_first=False)
        self.assertIn("Typed 5 character(s)", result)
        self.assertEqual(el.calls, [])

    def test_scancode_partial_pastes_only_the_remainder(self):
        el = _FakeEl()
        pastes = []

        def fake_paste(text, paste_fn):
            pastes.append(text)
            paste_fn()
            return True, "note"

        self._impl_setup(
            el, ["", "", "", "hel", "hello"],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 0, 5),
            scancode=lambda text, clear_first=False: self._mk_outcome(
                False, False, 2, 5))
        # INTENTIONAL (Phase 2 B2-1 rename): patch target moved with its module (was "core.desktop_v559.clipboard_paste", then "core.desktop.clipboard_paste" — patch where bound; Phase 3 S7a: desktop.py moved to core/desktop/desktop.py).
        _patch(self, "core.desktop.desktop.clipboard_paste", fake_paste)
        result = uia._uia_type_text_impl("Username", "hello")
        self.assertIn("remainder via clipboard paste", result)
        # The paste carries ONLY the remainder, never the full text.
        self.assertEqual(pastes, ["lo"])
        self.assertIn(("^v", False), el.calls)

    def test_scancode_partial_with_unexpected_content_pastes_nothing(self):
        el = _FakeEl()
        pastes = []

        def fake_paste(text, paste_fn):
            pastes.append(text)
            paste_fn()
            return True, "note"

        self._impl_setup(
            el, ["", "", "", "weird"],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 0, 5),
            scancode=lambda text, clear_first=False: self._mk_outcome(
                False, False, 2, 5))
        # INTENTIONAL (Phase 2 B2-1 rename): patch target moved with its module (was "core.desktop_v559.clipboard_paste", then "core.desktop.clipboard_paste" — patch where bound; Phase 3 S7a: desktop.py moved to core/desktop/desktop.py).
        _patch(self, "core.desktop.desktop.clipboard_paste", fake_paste)
        result = uia._uia_type_text_impl("Username", "hello")
        self.assertIn("stopped rather than pasting", result)
        self.assertEqual(pastes, [])

    def test_failsafe_still_checked_before_keystroke_rungs(self):
        el = _FakeEl()
        self._impl_setup(
            el, ["", "", "hel", "hello"],
            sendinput=lambda text, clear_first=False: self._mk_outcome(
                False, False, 2, 5))
        uia._uia_type_text_impl(
            "Username", "hello", clear_first=False)
        # The harness stub is a Mock: rung 2 (x2 setters) + rung 3.
        self.assertGreaterEqual(
            uia._failsafe_block.call_count, 3)


# ---------------------------------------------------------------------------
# D-06 — _drag_hold registers with the failsafe registry
# ---------------------------------------------------------------------------


class _FakeButton:
    left = "LEFT"


class _FakeMouse:
    def __init__(self):
        self.calls = []
        self.release_raises = False

    def press(self, button):
        self.calls.append(("press", button))

    def release(self, button):
        self.calls.append(("release", button))
        if self.release_raises:
            raise RuntimeError("release failed")


class DragHoldTests(unittest.TestCase):
    def _setup(self, mouse, follow_result=None):
        _patch(self, "core.desktop.cursor._failsafe_tripped", lambda: False)  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        _patch(self, "core.desktop.cursor._mouse_controller",
               lambda: (mouse, _FakeButton, None))  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        _patch(self, "core.desktop.cursor._fence_point", lambda x, y: (x, y))  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        _patch(self, "core.desktop.cursor._set_position", lambda m, x, y: None)  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        _patch(self, "core.desktop.cursor._plan_path",
               lambda x1, y1, x2, y2, d: [(x1, y1, 0), (x2, y2, 0)])  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        _patch(self, "core.desktop.cursor._follow_path",
               lambda m, path: follow_result)  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        pressed = mock.Mock()
        released = mock.Mock()
        _patch(self, "core.desktop.cursor._note_input_pressed", pressed)  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        _patch(self, "core.desktop.cursor._note_input_released", released)  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        return pressed, released

    def test_hold_registers_and_unregisters(self):
        mouse = _FakeMouse()
        pressed, released = self._setup(mouse)
        result = skills_window_control._drag_hold([(10, 10), (20, 20)], 1.0)
        self.assertIsNone(result)
        self.assertEqual(mouse.calls,
                         [("press", "LEFT"), ("release", "LEFT")])
        pressed.assert_called_once_with("button", "LEFT")
        released.assert_called_once_with("button", "LEFT")

    def test_abort_still_releases_and_unregisters(self):
        mouse = _FakeMouse()
        pressed, released = self._setup(mouse, follow_result="takeover!")
        result = skills_window_control._drag_hold([(10, 10), (20, 20)], 1.0)
        self.assertEqual(result, "takeover!")
        self.assertEqual(mouse.calls,
                         [("press", "LEFT"), ("release", "LEFT")])
        pressed.assert_called_once_with("button", "LEFT")
        released.assert_called_once_with("button", "LEFT")

    def test_failed_release_stays_tracked(self):
        mouse = _FakeMouse()
        mouse.release_raises = True
        pressed, released = self._setup(mouse)
        result = skills_window_control._drag_hold([(10, 10), (20, 20)], 1.0)
        self.assertIsNone(result)
        # Mirror the canonical drag: a failed release leaves the
        # hold tracked so a failsafe trip can still release it.
        pressed.assert_called_once_with("button", "LEFT")
        released.assert_not_called()

    def test_cursor_registration_delegates_to_failsafe(self):
        noted = []
        _patch(self, "core.failsafe.note_input_pressed",
               lambda kind, ident, obj=None: noted.append((kind, ident)))
        _patch(self, "core.failsafe.input_ident", lambda obj: "button:LEFT")
        cursor._note_input_pressed("button", "LEFT")
        self.assertEqual(noted, [("button", "button:LEFT")])


# ---------------------------------------------------------------------------
# D-07 — cursor pilot schema + validator
# ---------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, **kwargs):
        self.tools[kwargs["name"]] = kwargs


class PilotSchemaTests(unittest.TestCase):
    def test_schema_declares_all_pilot_fields(self):
        registry = _FakeRegistry()
        cursor.register_all(registry)
        props = registry.tools["cursor_control"]["parameters"]["properties"]
        for field, ftype in (("x2", "integer"), ("y2", "integer"),
                             ("points", "array"), ("pause_s", "number")):
            self.assertIn(field, props)
            self.assertEqual(props[field]["type"], ftype)
        self.assertIn("ole_drag_drop", props["x2"]["description"])
        self.assertIn("cursor_waypoint_glide",
                      props["points"]["description"])
        self.assertIn("cursor_drag_with_hover_pause",
                      props["pause_s"]["description"])
        # Only the action itself is globally required; pilot
        # fields are per-action required (validator's job).
        self.assertEqual(
            registry.tools["cursor_control"]["parameters"]["required"],
            ["action"])

    def test_validator_per_action_requirements(self):
        v = w4.validate_cursor_pilot_args
        self.assertIn("needs x, y",
                      v("ole_drag_drop", x=1, y=2))
        self.assertIn("needs x, y",
                      v("ole_drag_drop", x=1, y=2, x2=3))
        self.assertIsNone(
            v("ole_drag_drop", x=1, y=2, x2=3, y2=4))
        self.assertIn("at least two",
                      v("cursor_waypoint_glide", points=[(0, 0)]))
        self.assertIn("at least two",
                      v("cursor_waypoint_glide", points="nope"))
        self.assertIsNone(
            v("cursor_waypoint_glide", points=[(0, 0), (1, 1)]))
        self.assertIsNone(
            v("cursor_waypoint_glide", points="[[0, 0], [1, 1]]"))
        self.assertIn("needs x, y",
                      v("cursor_drag_with_hover_pause", x=1, y=2))
        self.assertIsNone(
            v("cursor_drag_with_hover_pause", x=1, y=2, x2=3, y2=4))
        # pause_s/duration are optional, never required.
        self.assertIsNone(
            v("cursor_drag_with_hover_pause", x=1, y=2, x2=3, y2=4))
        # Unknown actions are the dispatcher's job, not the validator's.
        self.assertIsNone(v("move", x=1))

    def test_dispatcher_validates_before_dispatch(self):
        calls = {}

        def stub_drag(x, y, x2, y2, duration=0.8):
            calls["drag"] = (x, y, x2, y2, duration)
            return "drag-ok"

        def stub_glide(pts, duration=0.6):
            calls["glide"] = (pts, duration)
            return "glide-ok"

        def stub_hover(x, y, x2, y2, pause_s=0.5, duration=0.8):
            calls["hover"] = (x, y, x2, y2, pause_s, duration)
            return "hover-ok"

        pilots = {"ole_drag_drop": stub_drag,
                  "cursor_waypoint_glide": stub_glide,
                  "cursor_drag_with_hover_pause": stub_hover}
        # Complete plans dispatch with the pilot fields.
        result = w4.cursor_pilot_action(
            "ole_drag_drop", x=10, y=20, x2=30, y2=40,
            coords="pixels", _pilots=pilots)
        self.assertEqual(result, "drag-ok")
        self.assertEqual(calls["drag"], (10, 20, 30, 40, 0.8))
        result = w4.cursor_pilot_action(
            "cursor_waypoint_glide", points=[(0, 0), (5, 5)],
            coords="pixels", _pilots=pilots)
        self.assertEqual(result, "glide-ok")
        self.assertEqual(calls["glide"], ([(0, 0), (5, 5)], 0.6))
        result = w4.cursor_pilot_action(
            "cursor_drag_with_hover_pause", x=1, y=2, x2=3, y2=4,
            pause_s=1.5, coords="pixels", _pilots=pilots)
        self.assertEqual(result, "hover-ok")
        self.assertEqual(calls["hover"], (1, 2, 3, 4, 1.5, 0.8))
        # Incomplete plans are refused and the pilot never runs.
        calls.clear()
        result = w4.cursor_pilot_action(
            "ole_drag_drop", x=10, y=20, coords="pixels",
            _pilots=pilots)
        self.assertIn("needs x, y", result)
        self.assertEqual(calls, {})
        result = w4.cursor_pilot_action(
            "cursor_waypoint_glide", points=[(0, 0)],
            coords="pixels", _pilots=pilots)
        self.assertIn("at least two", result)
        self.assertEqual(calls, {})
        # Validation fires before conversion: an incomplete plan
        # in norm1000 space gets the validation refusal, not a
        # conversion error, and no pilot runs.
        result = w4.cursor_pilot_action(
            "ole_drag_drop", x=10, y=20, coords="norm1000",
            _pilots=pilots)
        self.assertIn("needs x, y", result)
        self.assertEqual(calls, {})


if __name__ == "__main__":
    unittest.main()
