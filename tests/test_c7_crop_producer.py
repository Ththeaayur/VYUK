"""C7 crop producer tests (2026-11-05, INTENTIONAL).

Producer half of C7 ("Verify the spot, not the snapshot"):
vision retains the SENT image in a single slot, point_crop
cuts region grids from it on demand, and the C3 re-issue
target + pending records attach the crop when the step names
the retained capture and has one primary point.

HONEST SCOPE NOTE (do not "fix" by inventing attribution):
the producer core is built and tested here, but NO live step
carries a source_capture_id today — coordinates arrive from
the model with no tree-owned link to the capture they were
read from, and stamping "latest capture" at generic
construction (from_mapping/new_task) or at dispatch would be
dishonest (routines/defaults/ledger steps are not read from
any capture; dispatch stamping would defeat C7 binding).
The brain-plan stamp point is a brain-session-layer design
decision, owed as future work. Until it lands, live C3
targets and pending records attach nothing (the helper
returns None without an id), and live dialogs render the
fallback — the mechanism lights up automatically once steps
carry ids. Nothing here fabricates a crop, re-captures
silently, or retains a frame history.
"""

import unittest

from core.desktop import point_crop as pc


def _slot(frame_w=10, frame_h=8, cid=7, cropped=False, screen=(10, 8),
          image=None, vb=(0, 0, 9, 7)):
    gray = bytes((y * frame_w + x) % 256
                 for y in range(frame_h) for x in range(frame_w))
    rec = {"cropped": cropped,
           "screen_width": screen[0], "screen_height": screen[1],
           "image_width": image[0] if image else frame_w,
           "image_height": image[1] if image else frame_h,
           "virtual_bounds": vb}
    if cropped:
        rec.update({"crop_left": 2, "crop_top": 1,
                    "crop_width": 6, "crop_height": 4})
    return {"capture_id": cid, "width": frame_w, "height": frame_h,
            "gray": gray, "record": rec}


class CutterTests(unittest.TestCase):
    def test_center_grid_exact(self):
        # INTENTIONAL: norm1000 (500,500) on a 10x8 1:1 record
        # -> ref (0,0,10,8) -> to_pixels (4,4) -> 3x3 at half 1.
        g = pc.cut_region(_slot(), 500, 500, half_side=1)
        self.assertEqual(g, [[33, 34, 35], [43, 44, 45], [53, 54, 55]])

    def test_corner_clamps(self):
        g = pc.cut_region(_slot(), 0, 0, half_side=1)
        self.assertEqual(g, [[0, 1], [10, 11]])

    def test_cropped_record_maps_within_crop(self):
        # INTENTIONAL: cropped 6x4 image of a 10x8 true frame,
        # crop origin (2,1). norm1000 (0,0) -> ref (2,1) ->
        # sent (0,0); the region clamps to the image corner.
        slot = _slot(frame_w=6, frame_h=4, cropped=True)
        g = pc.cut_region(slot, 0, 0, half_side=1)
        self.assertEqual(g, [[0, 1], [6, 7]])

    def test_pixels_mode_maps_directly(self):
        # INTENTIONAL: true pixel (5,3) on a 1:1 record lands
        # on sent (5,3); no norm1000 scaling applies.
        g = pc.cut_region(_slot(), 5, 3, mode="pixels", half_side=0)
        self.assertEqual(g, [[35]])

    def test_outside_point_is_none(self):
        # INTENTIONAL: a point outside the sent image is
        # honest absence, never a neighbouring region. (A
        # norm1000 point clamps inside the reference rect by
        # _norm1000_to_pixels, so use pixels mode past the edge
        # with a record whose image is smaller than the screen.)
        slot = _slot(frame_w=10, frame_h=8, screen=(100, 80),
                     image=(10, 8), vb=None)
        self.assertIsNone(pc.cut_region(slot, 500, 500, mode="pixels",
                                        half_side=1))

    def test_bad_inputs_are_none(self):
        slot = _slot()
        self.assertIsNone(pc.cut_region(None, 500, 500))
        self.assertIsNone(pc.cut_region(slot, 500, 500, mode="bogus"))
        self.assertIsNone(pc.cut_region(slot, "x", 500))
        broken = _slot()
        broken["gray"] = b"\x00\x01"
        self.assertIsNone(pc.cut_region(broken, 500, 500))
        bad_rec = _slot()
        bad_rec["record"] = {"cropped": False}
        self.assertIsNone(pc.cut_region(bad_rec, 500, 500))


class PrimaryPointTests(unittest.TestCase):
    def test_cursor_xy_norm1000_default(self):
        self.assertEqual(
            pc.primary_point_for_step(
                "cursor_control", {"x": 500, "y": 250}),
            (500.0, 250.0, "norm1000"))

    def test_cursor_xy_pixels_mode(self):
        self.assertEqual(
            pc.primary_point_for_step(
                "cursor_control",
                {"x": 12, "y": 34, "coords": "pixels"}),
            (12.0, 34.0, "pixels"))

    def test_cursor_unknown_mode_is_none(self):
        self.assertIsNone(pc.primary_point_for_step(
            "cursor_control", {"x": 1, "y": 2, "coords": "bogus"}))

    def test_cursor_single_pair_is_pixels(self):
        self.assertEqual(
            pc.primary_point_for_step(
                "cursor_control", {"points": [[7, 8]]}),
            (7.0, 8.0, "pixels"))

    def test_cursor_path_is_none(self):
        # INTENTIONAL: waypoint paths (schema: at least two
        # pairs, true pixels) name a path, not a spot.
        self.assertIsNone(pc.primary_point_for_step(
            "cursor_control", {"points": [[1, 2], [3, 4]]}))

    def test_cursor_drag_xy2_is_none(self):
        # INTENTIONAL: a drag names two spots (start + end);
        # one region cannot honestly stand for it.
        self.assertIsNone(pc.primary_point_for_step(
            "cursor_control", {"x": 1, "y": 2, "x2": 5, "y2": 6}))

    def test_mouse_xy_is_pixels(self):
        self.assertEqual(
            pc.primary_point_for_step("mouse_control", {"x": 9, "y": 8}),
            (9.0, 8.0, "pixels"))

    def test_window_control_has_no_point(self):
        self.assertIsNone(pc.primary_point_for_step(
            "window_control", {"title_substring": "x"}))

    def test_other_tools_have_no_point(self):
        self.assertIsNone(pc.primary_point_for_step(
            "menu_action", {"action": "list"}))


class StoredCropForStepTests(unittest.TestCase):
    def test_attaches_on_matching_id(self):
        slot = _slot()
        args = {"source_capture_id": 7, "x": 500, "y": 500}
        grid = pc.stored_crop_for_step("cursor_control", args, slot)
        self.assertIsNotNone(grid)
        self.assertEqual(grid[0][0], 0)  # region includes origin corner
        self.assertTrue(all(isinstance(v, int) and 0 <= v <= 255
                            for row in grid for v in row))

    def test_no_id_means_no_crop(self):
        self.assertIsNone(pc.stored_crop_for_step(
            "cursor_control", {"x": 500, "y": 500}, _slot()))

    def test_id_mismatch_means_no_crop(self):
        self.assertIsNone(pc.stored_crop_for_step(
            "cursor_control",
            {"source_capture_id": 99, "x": 500, "y": 500}, _slot()))

    def test_no_slot_means_no_crop(self):
        self.assertIsNone(pc.stored_crop_for_step(
            "cursor_control",
            {"source_capture_id": 7, "x": 500, "y": 500}, None))

    def test_string_id_matches(self):
        # INTENTIONAL: ids compare as strings (the verifier's
        # binding does the same), so int/str drift can't fake
        # a mismatch — or a match.
        slot = _slot()
        args = {"source_capture_id": "7", "x": 500, "y": 500}
        self.assertIsNotNone(
            pc.stored_crop_for_step("cursor_control", args, slot))


class VerifierConsumesCropTests(unittest.TestCase):
    def test_c6_gate_compares_attached_crop(self):
        # INTENTIONAL: a destructive point WITH an attached
        # crop runs the region compare (not skipped): a
        # matching fresh crop passes, a changed one refuses
        # region-changed. Without the crop the gate refuses
        # region-unobserved instead.
        from core.desktop import control_existence as cev
        grid = [[10] * 4 for _ in range(4)]
        target = {"kind": "point", "description": "spot",
                  "source_capture_id": 7, "stored_crop": grid,
                  "destructive": True}
        fresh_same = {"capture_info": {"capture_id": 7},
                      "fresh_crop": [row[:] for row in grid]}
        ok = cev.verify(target, fresh_same)
        self.assertTrue(ok["ok"], ok.get("refusal"))
        fresh_changed = {"capture_info": {"capture_id": 7},
                         "fresh_crop": [[200] * 4 for _ in range(4)]}
        bad = cev.verify(target, fresh_changed)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["refusal"]["code"], "region-changed")
        bare = {"kind": "point", "description": "spot",
                "source_capture_id": 7, "destructive": True}
        missing = cev.verify(bare, {"capture_info": {"capture_id": 7}})
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["refusal"]["code"], "region-unobserved")


class SlotTests(unittest.TestCase):
    def _patched_manager(self):
        from PIL import Image
        from core.desktop import vision_manager as vm
        mgr = vm.VisionManager()
        frame = Image.new("RGB", (200, 100))
        frame.putdata([(x % 256, y % 256, (x + y) % 256)
                       for y in range(100) for x in range(200)])
        old_routed = vm._routed_backend
        old_raw = vm._backend_raw_full_frame
        vm._routed_backend = lambda: object()
        vm._backend_raw_full_frame = lambda backend: (frame.copy(), None)
        return mgr, old_routed, old_raw

    def test_capture_fills_single_slot_and_overwrites(self):
        # INTENTIONAL: capture_screen retains the SENT image
        # (grayscale thumbnail) + id + record in ONE slot;
        # a second capture overwrites it — no history.
        from core.desktop import vision_manager as vm
        mgr, old_routed, old_raw = self._patched_manager()
        try:
            mgr.capture_screen(marks=False)
            slot1 = mgr.last_sent_frame()
            self.assertIsNotNone(slot1)
            self.assertEqual(slot1["width"], 200)
            self.assertEqual(slot1["height"], 100)
            self.assertEqual(len(slot1["gray"]), 200 * 100)
            self.assertEqual(slot1["capture_id"],
                             slot1["record"]["capture_id"])
            mgr.capture_screen(marks=False)
            slot2 = mgr.last_sent_frame()
            self.assertEqual(slot2["capture_id"],
                             slot1["capture_id"] + 1)
            # The old id no longer serves: no history to cut from.
            args = {"source_capture_id": slot1["capture_id"],
                    "x": 500, "y": 500}
            self.assertIsNone(pc.stored_crop_for_step(
                "cursor_control", args, slot2))
            args["source_capture_id"] = slot2["capture_id"]
            grid = pc.stored_crop_for_step(
                "cursor_control", args, slot2)
            self.assertIsNotNone(grid)
            self.assertTrue(all(isinstance(v, int)
                                for row in grid for v in row))
        finally:
            vm._routed_backend = old_routed
            vm._backend_raw_full_frame = old_raw

    def test_slot_copy_cannot_mutate(self):
        from core.desktop import vision_manager as vm
        mgr, old_routed, old_raw = self._patched_manager()
        try:
            mgr.capture_screen(marks=False)
            slot = mgr.last_sent_frame()
            slot["record"]["capture_id"] = 999
            self.assertNotEqual(
                mgr.last_sent_frame()["record"]["capture_id"], 999)
        finally:
            vm._routed_backend = old_routed
            vm._backend_raw_full_frame = old_raw


try:
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False


import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_APP = None
_VA_SNAPSHOT = None


def setUpModule():
    """Mirror test_c7_dialog_crop's voice_assistant.* alias + stubs.

    gui/window_services_confirm.py imports voice_assistant.core.*
    (pre-split paths); the tree layout is top-level core/ +
    gui/, so the alias maps voice_assistant -> tree root,
    .core -> tree/core. The audio/brain stubs keep PortAudio
    out of this VM (window_widgets_shell imports audio_manager
    at module level). Restored on teardown.
    """
    global _APP, _VA_SNAPSHOT
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = types.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    audio = types.ModuleType("voice_assistant.core.voice.audio")
    audio.audio_manager = types.SimpleNamespace()
    brain = types.ModuleType("voice_assistant.core.brain.brain")
    brain.live_session = types.SimpleNamespace()
    for name, mod in (("voice_assistant", pkg),
                      ("voice_assistant.core", core),
                      ("voice_assistant.core.voice.audio", audio),
                      ("voice_assistant.core.brain.brain", brain)):
        sys.modules[name] = mod


def tearDownModule():
    global _APP, _VA_SNAPSHOT
    if _VA_SNAPSHOT is None:
        return
    for key in list(sys.modules):
        if key == "voice_assistant" or key.startswith("voice_assistant."):
            del sys.modules[key]
    sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


import types  # noqa: E402 — after setUpModule def, mirrors dialog suite


@unittest.skipUnless(_QT, "dialog feed needs PySide6 (Qt interpreter)")
class DialogFeedTests(unittest.TestCase):
    def _pump(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def test_producer_crop_reaches_dialog(self):
        # INTENTIONAL end-to-end (producer -> pending record
        # -> dialog slot): a helper-produced grid placed on a
        # pending record renders as the dialog's crop label
        # with pixels == the grid. The threading itself is the
        # C7 dialog batch's proven path; this proves the
        # producer feeds it.
        from types import MethodType
        from unittest import mock
        from PySide6.QtWidgets import QDialog, QLabel, QWidget
        from gui import pending_approval as pa
        from gui.window_services import ServicesMixin
        slot = _slot()
        args = {"source_capture_id": 7, "x": 500, "y": 500}
        grid = pc.stored_crop_for_step("cursor_control", args, slot)
        self.assertIsNotNone(grid)
        fake = QWidget()
        fake.show()
        self._pump()
        fake._build_confirm_dialog = MethodType(
            ServicesMixin._build_confirm_dialog, fake)
        fake._build_plain_confirm_dialog = MethodType(
            ServicesMixin._build_plain_confirm_dialog, fake)
        captured = []

        def _fake_exec(self):
            captured.append(self)
            return QDialog.DialogCode.Rejected

        with mock.patch.object(QDialog, "exec", _fake_exec):
            pa.present_pending_dialog(
                fake, {"tool": "cursor_control", "label": "click spot",
                       "stored_crop": grid}, timeout_s=0.05)
        self.assertEqual(len(captured), 1)
        label = captured[0].findChild(QLabel, "c7CropLabel")
        self.assertIsNotNone(label, "producer crop not fed to dialog")
        from PySide6.QtGui import QImage, qGray
        image = label.pixmap().toImage().convertToFormat(
            QImage.Format.Format_Grayscale8)
        vals = [qGray(image.pixel(x, y))
                for y in range(image.height())
                for x in range(image.width())]
        self.assertEqual((image.width(), image.height()), (10, 8))
        self.assertEqual(vals, [v for row in grid for v in row])
        fake.close()


if __name__ == "__main__":
    unittest.main()
