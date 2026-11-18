"""§7 item 2 — unified perception adapter (existing sources only).

Drives the adapter's backend protocol with a counting fake backend:
proves one capture feeds every section, a shared stamp, staleness
consistent with the item-1 handle rule, independent section
degradation, honest empty snapshots on denied/failed capture, no
new external dependency, and the item-1 engine round-trip.
"""

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from core.desktop import perception_adapter as pa  # noqa: E402  # INTENTIONAL (Phase 3 S7a): perception_adapter moved to core/desktop/
from core.desktop import target_handles as th  # noqa: E402  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/


class FakeBackend:
    """Counting fake: every section reads the same fake capture."""

    def __init__(self, fg=None, bounds=(0, 0, 800, 600),
                 block=None, capture_raises=False, capture_none=False,
                 ocr_raises=False):
        self.fg = fg if fg is not None else {
            "hwnd": 111, "pid": 222, "title": "Notes",
            "proc": "notes.exe"}
        self._bounds = bounds
        self.block = block
        self.capture_raises = capture_raises
        self.capture_none = capture_none
        self.ocr_raises = ocr_raises
        self.calls = {name: 0 for name in (
            "foreground_identity", "bounds", "capture_block_reason",
            "capture", "marks", "controls", "ocr_words", "pixels")}

    def foreground_identity(self):
        self.calls["foreground_identity"] += 1
        return dict(self.fg)

    def bounds(self):
        self.calls["bounds"] += 1
        return self._bounds

    def capture_block_reason(self):
        self.calls["capture_block_reason"] += 1
        return self.block

    def capture(self):
        self.calls["capture"] += 1
        if self.capture_raises:
            raise RuntimeError("fake camera bus error")
        if self.capture_none:
            return None
        return {"fake_image": True}

    def marks(self, capture, fg, ocr_words):
        self.calls["marks"] += 1
        assert capture in ({"fake_image": True}, None)
        return ([{"id": 1, "name": "Save", "control_type": "Button",
                  "rect": (10, 10, 90, 40), "source": "uia"}], "")

    def controls(self, capture, fg):
        self.calls["controls"] += 1
        assert capture in ({"fake_image": True}, None)
        return ([{"id": 1, "name": "Save", "control_type": "Button",
                  "rect": (10, 10, 90, 40), "source": "uia"}], "")

    def ocr_words(self, capture):
        self.calls["ocr_words"] += 1
        if self.ocr_raises:
            raise RuntimeError("fake tesseract crash")
        if capture is None:
            return [], "no captured image — OCR unavailable"
        return ([{"text": "Save", "rect": (10, 10, 90, 40)}], "")

    def pixels(self, capture):
        self.calls["pixels"] += 1
        if capture is None:
            return {}, "no captured image — pixel probes unavailable"
        return ({"centre": (255, 255, 255)}, "")


class PerceptionAdapterTests(unittest.TestCase):

    def test_single_capture_shared_stamp(self):
        be = FakeBackend()
        snap = pa.observe(backend=be)
        self.assertEqual(be.calls["capture"], 1)
        for name in ("marks", "controls", "ocr_words", "pixels"):
            self.assertEqual(be.calls[name], 1, name)
        self.assertEqual(snap["capture_status"], "ok")
        self.assertTrue(snap["stamp"])
        self.assertEqual(len(snap["marks"]), 1)
        self.assertEqual(len(snap["controls"]), 1)
        self.assertEqual(len(snap["ocr_words"]), 1)
        self.assertEqual(snap["pixels"], {"centre": (255, 255, 255)})
        self.assertEqual(snap["foreground_identity"]["hwnd"], 111)

    def test_snapshot_reuse_does_not_reobserve(self):
        be = FakeBackend()
        snap = pa.observe(backend=be)
        counts_after_observe = dict(be.calls)
        _ = (snap["marks"], snap["controls"], snap["ocr_words"],
             snap["pixels"], snap["stamp"])
        self.assertEqual(be.calls, counts_after_observe)

    def test_fresh_observe_mints_fresh_stamp(self):
        be = FakeBackend()
        first = pa.observe(backend=be)
        second = pa.observe(backend=be)
        self.assertNotEqual(first["stamp"], second["stamp"])
        self.assertEqual(be.calls["capture"], 2)

    def test_stale_hwnd_change_same_title(self):
        be = FakeBackend()
        snap = pa.observe(backend=be)
        reason = pa.is_stale(
            snap, ({"hwnd": 999, "pid": 222, "title": "Notes"}, (0, 0, 800, 600)))
        self.assertIsNotNone(reason)
        self.assertIn("hwnd", reason)

    def test_title_edit_same_hwnd_is_fresh(self):
        # Window-level title survival, consistent with item 1: a
        # renamed document under the same hwnd is the same target.
        be = FakeBackend()
        snap = pa.observe(backend=be)
        reason = pa.is_stale(
            snap, ({"hwnd": 111, "pid": 222, "title": "Notes — modified"},
                   (0, 0, 800, 600)))
        self.assertIsNone(reason)

    def test_stale_title_fallback_when_hwnd_unknown(self):
        be = FakeBackend(fg={"hwnd": None, "pid": None, "title": "Notes"})
        snap = pa.observe(backend=be)
        reason = pa.is_stale(
            snap, ({"hwnd": None, "pid": None, "title": "Calc"}, (0, 0, 800, 600)))
        self.assertIsNotNone(reason)
        self.assertIn("title", reason)

    def test_unreadable_title_never_counts_as_change(self):
        be = FakeBackend(fg={"hwnd": None, "pid": None, "title": None})
        snap = pa.observe(backend=be)
        reason = pa.is_stale(
            snap, ({"hwnd": None, "pid": None, "title": "Calc"},
                   (0, 0, 800, 600)))
        self.assertIsNone(reason)

    def test_stale_bounds_change(self):
        be = FakeBackend()
        snap = pa.observe(backend=be)
        reason = pa.is_stale(
            snap, ({"hwnd": 111, "pid": 222, "title": "Notes"},
                   (0, 0, 1920, 1080)))
        self.assertIsNotNone(reason)
        self.assertIn("bounds", reason)

    def test_ocr_failure_degrades_one_section_only(self):
        be = FakeBackend(ocr_raises=True)
        snap = pa.observe(backend=be)
        self.assertEqual(snap["capture_status"], "ok")
        self.assertTrue(snap["stamp"])
        self.assertEqual(snap["ocr_words"], [])
        self.assertTrue(snap["notes"]["ocr_words"])
        self.assertEqual(len(snap["marks"]), 1)
        self.assertEqual(len(snap["controls"]), 1)
        self.assertEqual(snap["pixels"], {"centre": (255, 255, 255)})

    def test_capture_denied_empty_and_no_sections_run(self):
        be = FakeBackend(block="capture refused — denylisted foreground")
        snap = pa.observe(backend=be)
        self.assertEqual(snap["capture_status"], "denied")
        self.assertIn("denylisted", snap["capture_reason"])
        self.assertEqual(snap["marks"], [])
        self.assertEqual(snap["controls"], [])
        self.assertEqual(snap["ocr_words"], [])
        self.assertEqual(snap["pixels"], {})
        self.assertEqual(be.calls["capture"], 0)
        for name in ("marks", "controls", "ocr_words", "pixels"):
            self.assertEqual(be.calls[name], 0, name)

    def test_capture_failure_empty_and_never_recycled(self):
        be = FakeBackend(capture_raises=True)
        snap = pa.observe(backend=be)
        self.assertEqual(snap["capture_status"], "failed")
        self.assertIn("capture failed", snap["capture_reason"])
        self.assertEqual(snap["marks"], [])
        self.assertEqual(snap["pixels"], {})
        be.capture_raises = False
        again = pa.observe(backend=be)
        self.assertEqual(again["capture_status"], "ok")
        self.assertEqual(len(again["marks"]), 1)

    def test_missing_image_degrades_image_sections_only(self):
        be = FakeBackend(capture_none=True)
        snap = pa.observe(backend=be)
        self.assertEqual(snap["capture_status"], "ok")
        self.assertEqual(snap["ocr_words"], [])
        self.assertEqual(snap["pixels"], {})
        self.assertTrue(snap["notes"]["ocr_words"])
        self.assertTrue(snap["notes"]["pixels"])
        # UIA sections still run against the same foreground window.
        self.assertEqual(len(snap["marks"]), 1)
        self.assertEqual(len(snap["controls"]), 1)

    def test_no_new_external_dependency(self):
        path = os.path.join(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))),
            "core", "desktop", "perception_adapter.py")  # INTENTIONAL (Phase 3 S7a): perception_adapter moved to core/desktop/
        tree = ast.parse(open(path, encoding="utf-8").read())
        stdlib = {"hashlib", "itertools", "threading", "datetime"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], stdlib,
                                  alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    # Relative import: the tree's own modules, imported
                    # lazily inside backend methods — not a new dep.
                    continue
                self.assertIn((node.module or "").split(".")[0], stdlib,
                              node.module)

    def test_snapshot_window_identity_drops_unknown_fields(self):
        be = FakeBackend(fg={"hwnd": 111, "pid": None, "title": "Notes"})
        snap = pa.observe(backend=be)
        identity = pa.snapshot_window_identity(snap)
        self.assertEqual(identity, {"hwnd": 111, "title": "Notes"})

    def test_describe_carries_stamp_and_counts(self):
        be = FakeBackend()
        snap = pa.observe(backend=be)
        line = pa.describe(snap)
        self.assertIn(snap["stamp"][:12], line)
        self.assertIn("Notes", line)
        self.assertIn("1 marks", line)

    def test_item1_engine_roundtrip(self):
        th.reset_store()
        be = FakeBackend()
        snap = pa.observe(backend=be)
        handle = th.mint_window(pa.snapshot_window_identity(snap))
        ok = th.resolve(handle["id"], {
            "identity": pa.snapshot_window_identity(snap)})
        self.assertTrue(ok["ok"])
        refused = th.resolve(handle["id"], {
            "identity": {"hwnd": 999, "pid": 222, "title": "Notes"}})
        self.assertFalse(refused["ok"])
        self.assertTrue(refused["refusal"]["reason"])


if __name__ == "__main__":
    unittest.main()
