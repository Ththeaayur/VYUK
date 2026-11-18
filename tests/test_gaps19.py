"""GAPS-19 (review flaws D-7 + D-8, §6.6): one capture owner.

The DXcam engine in core/desktop/screen_capture.py is now the ONE
capture owner: every frame in the product comes through it — the
vision path, the skills capture tools, the hands seam, the
social-send guard read-back and the display-size probes. The owner
selects DXcam, else the Pillow ImageGrab fallback backend (same
protocol, same frame-dict contract). grab_virtual is the
virtual-screen entry every caller uses: a bbox inside one monitor
goes through the selected backend, a bbox spanning monitors goes
through the ImageGrab backend, and a bbox outside the virtual
screen is refused, never clamped.

Runs against a FAKE DXcam-shaped backend and a FAKE ImageGrab
module (no real screen, no DXcam on this Linux lab machine) plus a
FAKE CLOCK (no real waiting, except the loop poll). The real
Windows leg — real Desktop Duplication frames on his machine — is
a field leg, owed, not this batch.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from core.desktop import screen_capture as sc

MONITORS = [
    {"index": 0, "x": 0, "y": 0, "width": 1920, "height": 1080,
     "primary": True},
    {"index": 1, "x": 1920, "y": 0, "width": 1280, "height": 1024,
     "primary": False},
]
VIRTUAL = (0, 0, 3200, 1080)


class _FakeClock:
    """monotonic()/sleep() with no real waiting."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def advance(self, seconds):
        self.t += seconds


class _FakeBackend:
    """A DXcam-shaped backend: two monitors, a fresh array per
    grab (filled with a counter so no two frames are equal)."""

    backend_name = "dxcam"

    def __init__(self, clock=None):
        self.clock = clock or _FakeClock()
        self.counter = 0
        self.calls = []

    def outputs(self):
        return [dict(m) for m in MONITORS]

    def grab_frame(self, output, region):
        self.calls.append((output, tuple(region)))
        left, top, right, bottom = region
        self.counter += 1
        return np.full((bottom - top, right - left, 3),
                       self.counter % 256, dtype=np.uint8)

    def start_loop(self, output, region, target_fps):
        raise AssertionError("not used by this suite")

    def latest_loop_frame(self):
        return None

    def stop_loop(self):
        pass

    def is_loop_running(self):
        return False


class _FakeGrabModule:
    """PIL.ImageGrab stand-in at the owner's seam: grab() returns
    a real PIL image sized by the bbox (or the 1920x1080 virtual
    screen for a full grab), recording every call. fail=True
    stands in for a host with no display."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def grab(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail:
            raise OSError("X connection failed: error 5")
        box = kwargs.get("bbox")
        if box is None:
            size = (1920, 1080)
        else:
            size = (int(box[2]) - int(box[0]),
                    int(box[3]) - int(box[1]))
        return Image.new("RGB", size, (10, 20, 30))


def _fresh_backend():
    clock = _FakeClock()
    return _FakeBackend(clock), clock


class _OwnerGuard(unittest.TestCase):
    """Stop any loop and restore the ImageGrab seam after each
    test — a leaked loop or a left-behind fake would poison the
    next test in this process."""

    def tearDown(self):
        try:
            sc.stop_capture()
        finally:
            sc._imagegrab_module = _ORIG_LOADER
        super().tearDown()


_ORIG_LOADER = sc._imagegrab_module


class TestOwnerSelection(_OwnerGuard):
    def test_injected_backend_wins_over_real_selection(self):
        be, clock = _fresh_backend()
        got = sc.grab(monitor=0, backend=be, clock=clock)
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["backend"], "dxcam")
        self.assertEqual(got["monitor"], 0)

    def test_dxcam_engine_unavailable_on_linux(self):
        status = sc.dxcam_available()
        self.assertFalse(status["available"])
        self.assertIsNone(status["backend"])
        self.assertIn("not Windows", status["error"])

    def test_owner_available_via_imagegrab_on_linux(self):
        self.assertTrue(sc.engine_available())
        status = sc.engine_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["backend"], "imagegrab")
        self.assertIsNone(status["error"])


class TestGrabVirtual(_OwnerGuard):
    def test_single_monitor_bbox_uses_selected_backend(self):
        be, clock = _fresh_backend()
        got = sc.grab_virtual(bbox=(100, 50, 300, 250), backend=be,
                              clock=clock)
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["backend"], "dxcam")
        self.assertEqual(got["monitor"], 0)
        self.assertEqual(got["region"], (100, 50, 300, 250))
        self.assertEqual((got["height"], got["width"]), (200, 200))
        self.assertEqual(got["timestamp"], 1000.0)
        # The backend saw the monitor-relative region, not the
        # virtual bbox verbatim.
        self.assertEqual(be.calls, [(0, (100, 50, 300, 250))])

    def test_second_monitor_bbox_translates_to_monitor_relative(self):
        be, _ = _fresh_backend()
        got = sc.grab_virtual(bbox=(2000, 100, 2200, 300), backend=be)
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["backend"], "dxcam")
        self.assertEqual(got["monitor"], 1)
        self.assertEqual(got["region"], (80, 100, 280, 300))
        self.assertEqual(be.calls, [(1, (80, 100, 280, 300))])

    def test_full_virtual_desktop_goes_through_imagegrab(self):
        # Two monitors (injected backend) + the fake ImageGrab
        # module: the full virtual desktop spans monitors, so the
        # ImageGrab backend serves it, not the DXcam-shaped one.
        be, _ = _fresh_backend()
        fake = _FakeGrabModule()
        sc._imagegrab_module = lambda: fake
        got = sc.grab_virtual(bbox=None, backend=be)
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["backend"], "imagegrab")
        self.assertIsNone(got["monitor"])
        self.assertEqual(got["region"], VIRTUAL)
        self.assertEqual((got["height"], got["width"]), (1080, 3200))
        pixel = [c for c in fake.calls
                 if c[1].get("all_screens") is True
                 and c[1].get("bbox") == VIRTUAL]
        self.assertEqual(len(pixel), 1)
        self.assertEqual(be.calls, [])

    def test_spanning_bbox_goes_through_imagegrab(self):
        be, _ = _fresh_backend()
        fake = _FakeGrabModule()
        sc._imagegrab_module = lambda: fake
        got = sc.grab_virtual(bbox=(1800, 0, 2200, 500), backend=be)
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["backend"], "imagegrab")
        self.assertIsNone(got["monitor"])
        self.assertEqual(got["region"], (1800, 0, 2200, 500))
        self.assertEqual((got["height"], got["width"]), (500, 400))
        self.assertEqual(be.calls, [])

    def test_bbox_outside_virtual_screen_refused_never_clamped(self):
        be, _ = _fresh_backend()
        got = sc.grab_virtual(bbox=(0, 0, 4000, 1080), backend=be)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["frame"])
        self.assertIn("outside the virtual screen", got["reason"])
        self.assertIn("never clamped", got["reason"])
        self.assertEqual(be.calls, [])

    def test_bbox_not_a_rectangle_refused(self):
        be, _ = _fresh_backend()
        for bad in ((100, 100, 100, 200), (-10, 0, 100, 100),
                    (0, 0, 100), "100,100,200,200"):
            got = sc.grab_virtual(bbox=bad, backend=be)
            self.assertFalse(got["ok"], bad)
            self.assertIsNone(got["frame"])
        self.assertEqual(be.calls, [])

    def test_virtual_bounds_is_the_union_of_the_outputs(self):
        be, _ = _fresh_backend()
        self.assertEqual(sc.virtual_screen_bounds(backend=be), VIRTUAL)

    def test_virtual_bounds_none_without_a_display(self):
        sc._imagegrab_module = lambda: _FakeGrabModule(fail=True)
        self.assertIsNone(sc.virtual_screen_bounds())


class TestGrabImageDelegation(_OwnerGuard):
    def test_grab_image_returns_pil_of_the_virtual_size(self):
        from core.desktop import vision_capture as vc

        sc._imagegrab_module = lambda: _FakeGrabModule()
        image = vc._grab_image()
        self.assertEqual(image.size, (1920, 1080))
        self.assertEqual(image.mode, "RGB")

    def test_grab_image_bbox_returns_the_bbox_size(self):
        from core.desktop import vision_capture as vc

        sc._imagegrab_module = lambda: _FakeGrabModule()
        image = vc._grab_image(bbox=(10, 20, 110, 70))
        self.assertEqual(image.size, (100, 50))

    def test_grab_image_raises_runtime_error_without_a_display(self):
        from core.desktop import vision_capture as vc

        sc._imagegrab_module = lambda: _FakeGrabModule(fail=True)
        with self.assertRaises(RuntimeError) as ctx:
            vc._grab_image()
        self.assertIn("Could not read the monitor list",
                      str(ctx.exception))


class TestImageGrabLoop(_OwnerGuard):
    def test_loop_runs_through_the_imagegrab_backend(self):
        sc._imagegrab_module = lambda: _FakeGrabModule()
        started = sc.start_capture(monitor=0, target_fps=30)
        self.assertTrue(started["ok"], started["reason"])
        try:
            got = None
            for _ in range(50):
                got = sc.latest_frame()
                if got["ok"]:
                    break
                time.sleep(0.02)
            self.assertTrue(got["ok"], got["reason"])
            self.assertEqual(got["source"], "latest-buffered")
            self.assertEqual(got["backend"], "imagegrab")
            self.assertEqual((got["height"], got["width"]),
                             (1080, 1920))
        finally:
            stopped = sc.stop_capture()
            self.assertTrue(stopped["ok"], stopped["reason"])

    def test_start_without_a_display_refused_honestly(self):
        sc._imagegrab_module = lambda: _FakeGrabModule(fail=True)
        started = sc.start_capture(monitor=0, target_fps=30)
        self.assertFalse(started["ok"])
        self.assertIn("Could not read the monitor list",
                      started["reason"])


class TestSkillsDelegation(_OwnerGuard):
    def test_grab_and_save_goes_through_the_owner(self):
        from core import skills_screen_capture as ssc

        sc._imagegrab_module = lambda: _FakeGrabModule()
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "shot.png")
            got = ssc._grab_and_save((0, 0, 200, 100), dest, "region")
            self.assertIsInstance(got, tuple)
            self.assertEqual(got[1], (200, 100))
            self.assertTrue(Path(got[0]).exists())
            self.assertGreater(got[2], 0)

    def test_grab_and_save_without_a_display_is_an_error_string(self):
        from core import skills_screen_capture as ssc

        sc._imagegrab_module = lambda: _FakeGrabModule(fail=True)
        with tempfile.TemporaryDirectory() as tmp:
            got = ssc._grab_and_save((0, 0, 200, 100),
                                     os.path.join(tmp, "shot.png"),
                                     "region")
            self.assertIsInstance(got, str)
            self.assertIn("Could not capture the screen", got)

    def test_pixel_colour_reads_through_the_owner(self):
        from core import skills_screen_capture as ssc

        sc._imagegrab_module = lambda: _FakeGrabModule()
        got = ssc.get_pixel_color(10, 20)
        self.assertIn("#0A141E", got)

    def test_media_region_goes_through_the_owner(self):
        from core import skills_media_capture as mc

        sc._imagegrab_module = lambda: _FakeGrabModule()
        with tempfile.TemporaryDirectory() as tmp:
            saved = {}

            def _fake_save(image, kind):
                dest = Path(tmp) / "region.png"
                image.save(dest, "PNG")
                saved["size"] = image.size
                return dest

            orig_save = mc._save_screenshot
            mc._save_screenshot = _fake_save
            try:
                got = mc.screenshot_region(0, 0, 200, 100)
            finally:
                mc._save_screenshot = orig_save
            self.assertIn("Captured the region at (0, 0), 200×100", got)
            self.assertEqual(saved["size"], (200, 100))

    def test_media_region_outside_the_virtual_screen_refused(self):
        from core import skills_media_capture as mc

        sc._imagegrab_module = lambda: _FakeGrabModule()
        got = mc.screenshot_region(0, 0, 5000, 100)
        self.assertIn("doesn't fit on the screen", got)
        self.assertIn("1920×1080", got)

    def test_display_info_names_the_primary_monitor(self):
        from core.desktop import control_plus_desktop as cpd

        sc._imagegrab_module = lambda: _FakeGrabModule()
        got = cpd.display_control("info")
        self.assertEqual(got,
                         "Primary display resolution: 1920 x 1080 pixels.")


if __name__ == "__main__":
    unittest.main()
