"""Control-existence verifier (C1–C10, 2026-11-05).

One test per adapter: mint/stale -> refuse with the right code
+ re-observe instruction + last-known description; fresh ->
pass. Plus the approval-pause sequence (C3), the capture
binding (C7), the region threshold (C7), the window-identity
floor (C8), the one-action token (C2/C5), the destructive
region gate (C6), the full-shape refusal (C9), and the
family classifier (C1).

Headless by construction: adapters take explicit observations,
so no test needs a screen, a UIA walk, or a live DOM. The
vision singleton's last-capture record is saved/restored
around every test that touches it (test_desktopcoord pattern).
"""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.desktop import control_existence as cev
from core.desktop import target_handles as th
from core.desktop import vision as _vision


def _record(**over):
    record = {
        "screen_width": 1920,
        "screen_height": 1080,
        "image_width": 800,
        "image_height": 600,
        "max_dim": 1024,
        "cropped": True,
        "crop_left": 100,
        "crop_top": 50,
        "crop_width": 800,
        "crop_height": 600,
        "monitor_crop": False,
    }
    record.update(over)
    return record


class _VisionRecordMixin:
    def setUp(self):
        super().setUp()
        self._orig_info = _vision.vision_manager._last_capture_info
        _vision.vision_manager._last_capture_info = None

    def tearDown(self):
        _vision.vision_manager._last_capture_info = self._orig_info
        super().tearDown()


class _HandleMixin:
    def setUp(self):
        super().setUp()
        self._old_session = th.current_session_id()
        th.reset_store()

    def tearDown(self):
        th.reset_store()
        th._session_id = self._old_session
        super().tearDown()


class RefusalShapeTests(unittest.TestCase):
    def test_full_shape_carries_code_reason_last_known_instruction(self):
        r = cev.refusal("target-gone", "the file is gone.",
                        "file '/tmp/x.txt'")
        self.assertEqual(r["code"], "target-gone")
        self.assertIn("the file is gone.", r["reason"])
        self.assertEqual(r["last_known"], "file '/tmp/x.txt'")
        self.assertIn("Re-observe", r["instruction"])
        self.assertIn("file '/tmp/x.txt'", r["text"])
        self.assertIn("Re-observe", r["text"])

    def test_refusal_without_last_known_still_instructs(self):
        r = cev.refusal("target-gone", "the file is gone.")
        self.assertEqual(r["last_known"], "")
        self.assertIn("Re-observe", r["text"])


class HandleAdapterTests(_HandleMixin, unittest.TestCase):
    def test_window_reproves_on_matching_identity(self):
        handle = th.mint_window({"hwnd": 123, "title": "Doc"},
                                "window 'Doc'")
        result = cev.verify(
            {"kind": "handle", "handle_id": handle["id"]},
            {"observation": {"identity": {"hwnd": 123,
                                           "title": "Doc"}}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["label"], "identity-verified")
        self.assertEqual(result["resolved"]["handle_id"], handle["id"])

    def test_window_title_edit_survives_on_stable_field(self):
        handle = th.mint_window({"hwnd": 123, "title": "Doc"},
                                "window 'Doc'")
        result = cev.verify(
            {"kind": "handle", "handle_id": handle["id"]},
            {"observation": {"identity": {"hwnd": 123,
                                           "title": "Doc — modified"}}})
        self.assertTrue(result["ok"])

    def test_hwnd_contradiction_refuses_naming_the_field(self):
        handle = th.mint_window({"hwnd": 123, "title": "Doc"},
                                "window 'Doc'")
        result = cev.verify(
            {"kind": "handle", "handle_id": handle["id"],
             "description": "window 'Doc'"},
            {"observation": {"identity": {"hwnd": 999,
                                           "title": "Doc"}}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "identity-contradiction")
        self.assertIn("hwnd", result["refusal"]["reason"])
        self.assertIn("Re-observe", result["refusal"]["text"])
        self.assertIn("Doc", result["refusal"]["last_known"])

    def test_unknown_handle_refuses_never_raises(self):
        result = cev.verify({"kind": "handle", "handle_id": "th-nope"},
                            {"observation": {}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-handle")

    def test_missing_handle_id_refuses(self):
        result = cev.verify({"kind": "handle"}, {"observation": {}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-handle")

    def test_foreign_session_handle_refuses(self):
        handle = th.mint_window({"hwnd": 7, "title": "W"}, "window 'W'")
        th.begin_session()
        try:
            result = cev.verify(
                {"kind": "handle", "handle_id": handle["id"]},
                {"observation": {"identity": {"hwnd": 7,
                                               "title": "W"}}})
            self.assertFalse(result["ok"])
            self.assertEqual(result["refusal"]["code"],
                             "foreign-session")
        finally:
            th._session_id = self._old_session

    def test_revoked_handle_refuses(self):
        handle = th.mint_window({"hwnd": 7, "title": "W"}, "window 'W'")
        th.revoke(handle["id"])
        result = cev.verify(
            {"kind": "handle", "handle_id": handle["id"]},
            {"observation": {"identity": {"hwnd": 7, "title": "W"}}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-handle")

    def test_file_handle_gone_refuses(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        handle = th.mint_file(path)
        os.unlink(path)
        result = cev.verify({"kind": "handle",
                             "handle_id": handle["id"]}, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-gone")


class MenuAdapterTests(unittest.TestCase):
    TREE = [["File", "Edit"], ["New", "Open", "Save"]]

    def test_exact_walk_passes(self):
        result = cev.verify(
            {"kind": "menu_item", "path": ["File", "Open"]},
            {"tree": self.TREE, "tree_stamp": 3, "listed_stamp": 3})
        self.assertTrue(result["ok"])
        self.assertEqual(result["resolved"]["path"], ["File", "Open"])

    def test_missing_item_refuses_naming_siblings_no_substitution(self):
        result = cev.verify(
            {"kind": "menu_item", "path": ["File", "Opne"]},
            {"tree": self.TREE, "tree_stamp": 3, "listed_stamp": 3})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-not-found")
        self.assertIn("Open", result["refusal"]["reason"])
        self.assertNotIn("Save As", result["refusal"]["reason"])

    def test_rebuilt_tree_refuses_before_walking(self):
        result = cev.verify(
            {"kind": "menu_item", "path": ["File", "Open"]},
            {"tree": self.TREE, "tree_stamp": 4, "listed_stamp": 3})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-rebuilt")

    def test_no_fresh_tree_refuses_never_trusts_listing(self):
        result = cev.verify({"kind": "menu_item", "path": ["File"]},
                            {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-unavailable")

    def test_empty_path_refuses(self):
        result = cev.verify({"kind": "menu_item", "path": []},
                            {"tree": self.TREE})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-not-found")


class BrowserAdapterTests(unittest.TestCase):
    def test_present_visible_same_url_passes(self):
        result = cev.verify(
            {"kind": "browser_element", "ref": "e12",
             "snapshot_url": "https://example.test/a"},
            {"present": True, "visible": True,
             "url": "https://example.test/a"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["resolved"]["ref"], "e12")

    def test_gone_element_refuses(self):
        result = cev.verify({"kind": "browser_element", "ref": "e12"},
                            {"present": False, "visible": False})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-gone")

    def test_invisible_element_refuses(self):
        result = cev.verify({"kind": "browser_element", "ref": "e12"},
                            {"present": True, "visible": False})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-unusable")

    def test_url_change_refuses(self):
        result = cev.verify(
            {"kind": "browser_element", "ref": "e12",
             "snapshot_url": "https://example.test/a"},
            {"present": True, "visible": True,
             "url": "https://example.test/b"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "url-changed")


class FileAdapterTests(unittest.TestCase):
    def test_existing_file_passes_with_canonical_path(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        try:
            result = cev.verify({"kind": "file", "path": path}, {})
            self.assertTrue(result["ok"])
            self.assertEqual(result["resolved"]["path"],
                             os.path.realpath(path))
        finally:
            os.unlink(path)

    def test_missing_file_refuses(self):
        result = cev.verify({"kind": "file",
                             "path": "/tmp/cev-definitely-missing-xyz"},
                            {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-gone")

    def test_empty_path_refuses(self):
        result = cev.verify({"kind": "file", "path": "  "}, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "invalid-target")


class PointAdapterTests(_VisionRecordMixin, unittest.TestCase):
    def test_no_capture_on_record_is_unverified_not_refused(self):
        result = cev.verify({"kind": "point", "x": 500, "y": 500},
                            {"capture_info": None})
        self.assertTrue(result["ok"])
        self.assertEqual(result["label"], "geometry-unverified")

    def test_capture_binding_match_passes(self):
        _vision.vision_manager._last_capture_info = _record(
            cropped=False, virtual_bounds=None)
        result = cev.verify(
            {"kind": "point", "source_capture_id": 7},
            {"capture_info": {"capture_id": 7, "cropped": False}})
        self.assertTrue(result["ok"])

    def test_capture_binding_mismatch_refuses(self):
        _vision.vision_manager._last_capture_info = _record(
            cropped=False, virtual_bounds=None)
        result = cev.verify(
            {"kind": "point", "source_capture_id": 7,
             "description": "Save button"},
            {"capture_info": {"capture_id": 9, "cropped": False}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "capture-mismatch")
        self.assertIn("7", result["refusal"]["reason"])
        self.assertIn("9", result["refusal"]["reason"])
        self.assertIn("Save button", result["refusal"]["last_known"])

    def test_binding_skipped_when_either_id_unknown(self):
        _vision.vision_manager._last_capture_info = _record(
            cropped=False, virtual_bounds=None)
        result = cev.verify(
            {"kind": "point", "source_capture_id": 7},
            {"capture_info": {"cropped": False}})
        self.assertTrue(result["ok"])

    def test_floor_verdict_code_flows_through(self):
        # Cropped record, live rect differs -> stale-foreground
        # with the C9 code, not just a string.
        _vision.vision_manager._last_capture_info = _record()
        with mock.patch.object(
                _vision, "foreground_window_rect",
                return_value=(0, 0, 1920, 1080)):
            result = cev.verify(
                {"kind": "point"},
                {"capture_info": _vision.vision_manager
                    ._last_capture_info})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "stale-foreground")
        self.assertIn("Re-observe", result["refusal"]["text"])


class WindowIdentityFloorTests(_VisionRecordMixin, unittest.TestCase):
    def _stale_rect(self):
        return (0, 0, 1920, 1080)

    def test_same_rect_different_hwnd_refuses_c8(self):
        _vision.vision_manager._last_capture_info = _record(
            foreground_hwnd=111, foreground_title="Doc")
        with mock.patch.object(
                _vision, "foreground_window_rect",
                return_value=(100, 50, 900, 650)), \
            mock.patch("core.skills_window_control._foreground_hwnd",
                       return_value=222), \
            mock.patch("core.vision_exclusions.foreground_probe",
                       return_value=("app", "Doc")):
            result = cev.verify(
                {"kind": "point"},
                {"capture_info": _vision.vision_manager
                    ._last_capture_info})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "stale-foreground")
        self.assertIn("different window", result["refusal"]["reason"])

    def test_same_rect_same_hwnd_passes_c8(self):
        _vision.vision_manager._last_capture_info = _record(
            foreground_hwnd=111, foreground_title="Doc")
        with mock.patch.object(
                _vision, "foreground_window_rect",
                return_value=(100, 50, 900, 650)), \
            mock.patch("core.skills_window_control._foreground_hwnd",
                       return_value=111), \
            mock.patch("core.vision_exclusions.foreground_probe",
                       return_value=("app", "Doc")):
            result = cev.verify(
                {"kind": "point"},
                {"capture_info": _vision.vision_manager
                    ._last_capture_info})
        self.assertTrue(result["ok"])

    def test_title_fallback_when_hwnd_unknown_both_sides(self):
        _vision.vision_manager._last_capture_info = _record(
            foreground_hwnd=None, foreground_title="Doc")
        with mock.patch.object(
                _vision, "foreground_window_rect",
                return_value=(100, 50, 900, 650)), \
            mock.patch("core.skills_window_control._foreground_hwnd",
                       return_value=None), \
            mock.patch("core.vision_exclusions.foreground_probe",
                       return_value=("app", "Other")):
            result = cev.verify(
                {"kind": "point"},
                {"capture_info": _vision.vision_manager
                    ._last_capture_info})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "stale-foreground")

    def test_unknown_identity_never_invents_a_refusal(self):
        _vision.vision_manager._last_capture_info = _record(
            foreground_hwnd=None, foreground_title=None)
        with mock.patch.object(
                _vision, "foreground_window_rect",
                return_value=(100, 50, 900, 650)), \
            mock.patch("core.skills_window_control._foreground_hwnd",
                       return_value=None), \
            mock.patch("core.vision_exclusions.foreground_probe",
                       return_value=(None, None)):
            result = cev.verify(
                {"kind": "point"},
                {"capture_info": _vision.vision_manager
                    ._last_capture_info})
        self.assertTrue(result["ok"])

    def test_verdict_and_refusal_wrapper_agree(self):
        from core.desktop import cursor_geometry as _geom
        _vision.vision_manager._last_capture_info = _record()
        with mock.patch.object(
                _vision, "foreground_window_rect",
                return_value=(0, 0, 1920, 1080)):
            code, message = _geom._capture_freshness_verdict()
            text = _geom._capture_freshness_refusal()
        self.assertEqual(code, "stale-foreground")
        self.assertEqual(message, text)
        self.assertIn("Re-observe", text)


class FreshLookupAdapterTests(unittest.TestCase):
    def test_exactly_one_hit_passes(self):
        result = cev.verify(
            {"kind": "fresh_lookup", "tool": "click_text"},
            {"candidates": [{"name": "Save", "rect": (10, 10, 60, 30)}]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["label"], "identity-verified")
        self.assertEqual(result["resolved"]["name"], "Save")

    def test_zero_hits_refuses(self):
        result = cev.verify({"kind": "fresh_lookup"},
                            {"candidates": []})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-not-present")

    def test_ambiguous_scan_refuses_naming_candidates(self):
        result = cev.verify(
            {"kind": "fresh_lookup"},
            {"candidates": [{"name": "Save"}, {"name": "Save As"}],
             "ambiguous": True})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "ambiguous-target")
        self.assertIn("Save As", result["refusal"]["reason"])

    def test_two_candidates_refuse_even_without_flag(self):
        result = cev.verify(
            {"kind": "fresh_lookup"},
            {"candidates": [{"name": "A"}, {"name": "B"}]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "ambiguous-target")


class RegionCompareTests(unittest.TestCase):
    def test_identical_grids_match(self):
        grid = [[10, 20], [30, 40]]
        compared = cev.compare_region(grid, [row[:] for row in grid])
        self.assertTrue(compared["match"])
        self.assertEqual(compared["mean_abs_diff"], 0.0)

    def test_small_local_animation_matches(self):
        stored = [[100] * 20 for _ in range(20)]
        fresh = [row[:] for row in stored]
        for i in range(20):  # 5% of pixels move a lot (blink)
            fresh[i][0] = 200
        compared = cev.compare_region(stored, fresh)
        self.assertTrue(compared["match"])
        self.assertLessEqual(compared["mean_abs_diff"], 12.0)
        self.assertLessEqual(compared["frac_large"], 0.10)

    def test_full_motion_video_refuses(self):
        stored = [[100] * 20 for _ in range(20)]
        fresh = [[200] * 20 for _ in range(20)]
        compared = cev.compare_region(stored, fresh)
        self.assertFalse(compared["match"])
        self.assertEqual(compared["mean_abs_diff"], 100.0)
        self.assertEqual(compared["frac_large"], 1.0)

    def test_dimension_mismatch_is_a_mismatch_not_a_crash(self):
        compared = cev.compare_region([[1, 2]], [[1, 2, 3]])
        self.assertFalse(compared["match"])

    def test_unusable_grid_is_a_mismatch_not_a_crash(self):
        compared = cev.compare_region([], [[1]])
        self.assertFalse(compared["match"])
        compared = cev.compare_region(None, [[1]])
        self.assertFalse(compared["match"])

    def test_threshold_constants_are_the_decided_values(self):
        self.assertEqual(cev.MEAN_ABS_DIFF_MAX, 12.0)
        self.assertEqual(cev.LARGE_DIFF, 48)
        self.assertEqual(cev.FRAC_LARGE_MAX, 0.10)


class DestructiveRegionGateTests(_VisionRecordMixin, unittest.TestCase):
    GRID = [[100] * 10 for _ in range(10)]

    def test_destructive_point_without_crops_refuses(self):
        result = cev.verify_for_action(
            {"kind": "point", "description": "Delete button"},
            {"capture_info": None}, destructive=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "region-unobserved")

    def test_destructive_point_with_matching_crops_passes(self):
        result = cev.verify_for_action(
            {"kind": "point", "stored_crop": self.GRID},
            {"capture_info": None, "fresh_crop": self.GRID},
            destructive=True)
        self.assertTrue(result["ok"])
        self.assertIn("region", result["resolved"])

    def test_destructive_point_with_changed_crops_refuses(self):
        changed = [[200] * 10 for _ in range(10)]
        result = cev.verify_for_action(
            {"kind": "point", "stored_crop": self.GRID},
            {"capture_info": None, "fresh_crop": changed},
            destructive=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "region-changed")

    def test_cheap_point_without_crops_passes_identity_only(self):
        result = cev.verify_for_action(
            {"kind": "point"}, {"capture_info": None},
            destructive=False)
        self.assertTrue(result["ok"])

    def test_destructive_handle_without_crop_passes_on_reproof(self):
        handle = th.mint_window({"hwnd": 5, "title": "W"}, "window 'W'")
        try:
            result = cev.verify_for_action(
                {"kind": "handle", "handle_id": handle["id"]},
                {"observation": {"identity": {"hwnd": 5,
                                               "title": "W"}}},
                destructive=True)
            self.assertTrue(result["ok"])
        finally:
            th.reset_store()

    def test_destructive_handle_with_changed_crop_refuses(self):
        handle = th.mint_window({"hwnd": 5, "title": "W"}, "window 'W'")
        try:
            changed = [[200] * 10 for _ in range(10)]
            result = cev.verify_for_action(
                {"kind": "handle", "handle_id": handle["id"],
                 "stored_crop": self.GRID},
                {"observation": {"identity": {"hwnd": 5,
                                               "title": "W"}},
                 "fresh_crop": changed},
                destructive=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["refusal"]["code"],
                             "region-changed")
        finally:
            th.reset_store()


class TokenTests(unittest.TestCase):
    def test_token_consumes_once_then_raises(self):
        result = cev.verify(
            {"kind": "fresh_lookup"},
            {"candidates": [{"name": "Save", "rect": (0, 0, 9, 9)}]})
        self.assertTrue(result["ok"])
        token = result["resolved"]["token"]
        self.assertFalse(token.consumed)
        resolved = token.consume()
        self.assertEqual(resolved["name"], "Save")
        self.assertTrue(token.consumed)
        with self.assertRaises(ValueError):
            token.consume()

    def test_refusal_carries_no_token(self):
        result = cev.verify({"kind": "fresh_lookup"},
                            {"candidates": []})
        self.assertFalse(result["ok"])
        self.assertIsNone(result["resolved"])


class BeforeConfirmTests(unittest.TestCase):
    def test_ok_proceeds(self):
        proceed, refusal = cev.verify_before_confirm(
            {"kind": "fresh_lookup"},
            {"candidates": [{"name": "Save"}]})
        self.assertTrue(proceed)
        self.assertIsNone(refusal)

    def test_refusal_blocks_the_dialog(self):
        proceed, refusal = cev.verify_before_confirm(
            {"kind": "fresh_lookup"}, {"candidates": []})
        self.assertFalse(proceed)
        self.assertEqual(refusal["code"], "target-not-present")
        self.assertIn("Re-observe", refusal["text"])


class FamilyTests(unittest.TestCase):
    def test_family_a_tools(self):
        for tool in ("uia_click_control", "click_element", "click_text"):
            self.assertEqual(cev.family_of(tool), "A")

    def test_family_b_tools(self):
        for tool in ("menu_action", "file_dialog_action",
                     "system_surface_action", "drag_drop_action",
                     "context_menu_action", "click_by_mark"):
            self.assertEqual(cev.family_of(tool), "B")

    def test_family_c_tools(self):
        for tool in ("cursor_control", "mouse_control", "window_control"):
            self.assertEqual(cev.family_of(tool), "C")

    def test_unknown_tool_is_never_guessed_into_a_family(self):
        self.assertEqual(cev.family_of("definitely_not_a_tool"),
                         "unknown")
        self.assertEqual(cev.family_of(""), "unknown")


class InvalidInputTests(unittest.TestCase):
    def test_non_dict_target_refuses_without_raising(self):
        result = cev.verify("a string", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "invalid-target")

    def test_none_target_refuses_without_raising(self):
        result = cev.verify(None, None)
        self.assertFalse(result["ok"])

    def test_unknown_kind_refuses(self):
        result = cev.verify({"kind": "teleport"}, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-kind")


class ApprovalPauseC3Tests(_VisionRecordMixin, unittest.TestCase):
    def _coordinator_with_paused_cursor_step(self):
        from core.brain import task_coordinator as tc
        from core.brain.task_coordinator_steps import StepDef
        coord = tc.TurnCoordinator()
        step = StepDef(tool="cursor_control",
                       args={"act": "move", "coords": "norm1000",
                             "x": 500, "y": 500},
                       label="Move to Save")
        from core.brain.task_coordinator_task import Task
        task = Task("T", "test", [step])
        task.state = "paused"
        task._pending_approval = {
            "step_index": 0, "tool": "cursor_control",
            "label": "Move to Save", "sentence": "approve?",
            "confirm_arg": "confirmed"}
        coord._tasks[task.id] = task
        return coord, task

    def test_approved_reissue_refuses_when_grid_moved(self):
        coord, task = self._coordinator_with_paused_cursor_step()
        _vision.vision_manager._last_capture_info = _record()
        with mock.patch.object(
                _vision, "foreground_window_rect",
                return_value=(0, 0, 1920, 1080)):
            receipt = coord.resolve_pending(task.id, approved=True)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.state, "failed")
        kinds = [s["kind"] for s in receipt.steps]
        self.assertIn("target-stale", kinds)
        refused = [v for v in receipt.verification
                   if v.get("verdict") == "refused"]
        self.assertTrue(refused)
        self.assertIn("Re-observe", refused[0]["reason"])

    def test_approved_reissue_passes_c3_when_nothing_moved(self):
        coord, task = self._coordinator_with_paused_cursor_step()
        receipt = coord.resolve_pending(task.id, approved=True)
        self.assertIsNotNone(receipt)
        kinds = [s["kind"] for s in receipt.steps]
        self.assertNotIn("target-stale", kinds)
        refused = [v for v in receipt.verification
                   if v.get("verdict") == "refused"
                   and "Re-observe" in str(v.get("reason", ""))]
        self.assertFalse(refused)


if __name__ == "__main__":
    unittest.main()
