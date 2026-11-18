"""B-D5 (2026-11-09) screen capture via DXcam.

Runs against a FAKE DXcam backend (no real screen, no DXcam
on this Linux lab machine) plus a FAKE CLOCK (no real
waiting). The engine-availability tests assert the honest
engine_unavailable degradation on this machine instead of
skipping: a suite that silently skips the capture checks on
an interpreter without the library is a lie, not a pass.

The real-Windows leg — real Desktop Duplication frames at
240 Hz on his machine — is a field leg, owed, not this
batch.
"""

import unittest

import numpy as np

from core.desktop import screen_capture as sc

MONITORS = [
    {"index": 0, "x": 0, "y": 0, "width": 1920, "height": 1080,
     "primary": True},
    {"index": 1, "x": 1920, "y": 0, "width": 1280, "height": 1024,
     "primary": False},
]


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
    grab (filled with a counter so no two frames are equal),
    a threaded-loop shape for latest_frame/collect."""

    backend_name = "dxcam"

    def __init__(self, clock=None):
        self.clock = clock or _FakeClock()
        self.counter = 0
        self.fail_next = None  # "none" | "raise" | None
        self.loop = None

    def outputs(self):
        return [dict(m) for m in MONITORS]

    def _fresh(self, width, height):
        self.counter += 1
        return np.full((height, width, 3),
                       self.counter % 256, dtype=np.uint8)

    def grab_frame(self, output, region):
        if self.fail_next == "none":
            self.fail_next = None
            return None
        if self.fail_next == "raise":
            self.fail_next = None
            raise sc.CaptureFailed("DXGI device lost (fake)")
        left, top, right, bottom = region
        return self._fresh(right - left, bottom - top)

    def start_loop(self, output, region, target_fps):
        if self.loop is not None:
            raise sc.CaptureFailed("a capture loop is already running")
        self.loop = {"output": output, "region": region,
                     "target_fps": target_fps}

    def latest_loop_frame(self):
        if self.loop is None:
            return None
        left, top, right, bottom = self.loop["region"]
        frame = self._fresh(right - left, bottom - top)
        return frame, self.clock.monotonic()

    def stop_loop(self):
        self.loop = None

    def is_loop_running(self):
        return self.loop is not None


def _fresh_backend():
    clock = _FakeClock()
    return _FakeBackend(clock), clock


class _LoopGuard(unittest.TestCase):
    """A loop left running by a failing test would leak into
    the next one (module-level loop state) — stop it always."""

    def tearDown(self):
        sc.stop_capture()
        super().tearDown()


class TestListMonitors(_LoopGuard):
    def test_lists_both_monitors_with_geometry(self):
        be, _ = _fresh_backend()
        listed = sc.list_monitors(backend=be)
        self.assertTrue(listed["ok"], listed["reason"])
        self.assertEqual(len(listed["monitors"]), 2)
        self.assertEqual(listed["monitors"][0]["width"], 1920)
        self.assertEqual(listed["monitors"][1]["x"], 1920)
        self.assertTrue(listed["monitors"][0]["primary"])
        self.assertFalse(listed["monitors"][1]["primary"])


class TestGrab(_LoopGuard):
    def test_grab_returns_frame_timestamp_monitor(self):
        be, clock = _fresh_backend()
        got = sc.grab(monitor=0, backend=be, clock=clock)
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["source"], "fresh-grab")
        self.assertEqual(got["monitor"], 0)
        self.assertEqual(got["region"], (0, 0, 1920, 1080))
        self.assertEqual((got["height"], got["width"]), (1080, 1920))
        self.assertEqual(got["timestamp"], 1000.0)
        self.assertEqual(got["frame"].shape, (1080, 1920, 3))
        self.assertEqual(got["frame"].dtype, np.uint8)
        self.assertEqual(got["backend"], "dxcam")

    def test_grab_region_returns_region_shape(self):
        be, _ = _fresh_backend()
        got = sc.grab(monitor=1, region=(10, 20, 110, 120),
                      backend=be)
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["region"], (10, 20, 110, 120))
        self.assertEqual((got["height"], got["width"]), (100, 100))

    def test_second_grab_is_a_new_frame(self):
        be, clock = _fresh_backend()
        first = sc.grab(monitor=0, backend=be, clock=clock)
        clock.advance(0.5)
        second = sc.grab(monitor=0, backend=be, clock=clock)
        self.assertTrue(first["ok"] and second["ok"])
        self.assertFalse(np.array_equal(first["frame"], second["frame"]))
        self.assertGreater(second["timestamp"], first["timestamp"])
        self.assertIsNot(first["frame"], second["frame"])

    def test_unknown_monitor_refused_never_retargeted(self):
        be, _ = _fresh_backend()
        got = sc.grab(monitor=9, backend=be)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["frame"])
        self.assertIn("No monitor 9", got["reason"])
        self.assertIn("[0, 1]", got["reason"])

    def test_region_partly_off_screen_refused_never_clamped(self):
        be, _ = _fresh_backend()
        got = sc.grab(monitor=0, region=(1800, 900, 2000, 1100),
                      backend=be)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["frame"])
        self.assertIn("outside monitor 0", got["reason"])
        self.assertIn("never clamped", got["reason"])

    def test_region_not_a_rectangle_refused(self):
        be, _ = _fresh_backend()
        for bad in ((100, 100, 100, 200), (100, 200, 50, 300),
                    (-10, 0, 100, 100), (0, 0, 100), "100,100,200,200"):
            got = sc.grab(monitor=0, region=bad, backend=be)
            self.assertFalse(got["ok"], bad)
            self.assertIsNone(got["frame"])

    def test_failed_grab_is_honest_failure_never_pixels(self):
        be, _ = _fresh_backend()
        be.fail_next = "none"
        got = sc.grab(monitor=0, backend=be)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["frame"])
        self.assertIn("no frame", got["reason"])
        be.fail_next = "raise"
        got = sc.grab(monitor=0, backend=be)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["frame"])
        self.assertIn("DXGI device lost", got["reason"])

    def test_black_frame_is_valid_pixels(self):
        be, _ = _fresh_backend()
        be.counter = 255  # next fill is 0 -> an all-black frame
        got = sc.grab(monitor=0, region=(0, 0, 4, 4), backend=be)
        self.assertTrue(got["ok"], got["reason"])
        self.assertTrue(np.all(got["frame"] == 0))

    def test_frame_to_pil_converts_and_rejects(self):
        be, _ = _fresh_backend()
        got = sc.grab(monitor=0, region=(0, 0, 8, 6), backend=be)
        image = sc.frame_to_pil(got["frame"])
        self.assertEqual(image.size, (8, 6))
        self.assertEqual(image.mode, "RGB")
        with self.assertRaises(ValueError):
            sc.frame_to_pil(None)
        with self.assertRaises(ValueError):
            sc.frame_to_pil(np.zeros((4, 4), dtype=np.uint8))


class TestEngineAvailability(_LoopGuard):
    def test_dxcam_unavailable_on_linux(self):
        # GAPS-19: the DXcam engine specifically is still honestly
        # unavailable on this machine (not Windows) — the owner
        # reports it separately from its own availability.
        status = sc.dxcam_available()
        self.assertFalse(status["available"])
        self.assertIsNone(status["backend"])
        self.assertIn("not Windows", status["error"])

    def test_owner_available_via_imagegrab_on_linux(self):
        # GAPS-19: the OWNER is available on Linux through the
        # ImageGrab fallback backend, and says so by name.
        self.assertTrue(sc.engine_available())
        status = sc.engine_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["backend"], "imagegrab")
        self.assertIsNone(status["error"])

    def test_grab_without_display_fails_honestly(self):
        # This lab has no display on either interpreter: a grab
        # with no injected backend fails honestly — never pixels,
        # and never by claiming the engine is missing (the engine
        # exists; the display does not).
        got = sc.grab(monitor=0)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["frame"])
        self.assertIn("Could not read the monitor list", got["reason"])
        self.assertNotIn("engine_unavailable", got["reason"])
        listed = sc.list_monitors()
        self.assertFalse(listed["ok"])
        self.assertEqual(listed["monitors"], [])
        self.assertIsNone(sc.virtual_screen_bounds())


class TestLoop(_LoopGuard):
    def test_latest_without_loop_refused(self):
        got = sc.latest_frame()
        self.assertFalse(got["ok"])
        self.assertIn("no capture loop is running", got["reason"])

    def test_latest_is_labelled_latest_buffered(self):
        be, clock = _fresh_backend()
        started = sc.start_capture(monitor=1, backend=be, clock=clock)
        self.assertTrue(started["ok"], started["reason"])
        got = sc.latest_frame()
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["source"], "latest-buffered")
        self.assertEqual(got["monitor"], 1)
        self.assertEqual(got["timestamp"], 1000.0)

    def test_second_start_refused(self):
        be, _ = _fresh_backend()
        self.assertTrue(sc.start_capture(backend=be)["ok"])
        again = sc.start_capture(backend=be)
        self.assertFalse(again["ok"])
        self.assertIn("already running", again["reason"])

    def test_stop_then_latest_refused(self):
        be, _ = _fresh_backend()
        self.assertTrue(sc.start_capture(backend=be)["ok"])
        stopped = sc.stop_capture()
        self.assertTrue(stopped["ok"])
        self.assertFalse(sc.latest_frame()["ok"])

    def test_start_unknown_monitor_refused(self):
        be, _ = _fresh_backend()
        got = sc.start_capture(monitor=5, backend=be)
        self.assertFalse(got["ok"])
        self.assertIn("No monitor 5", got["reason"])

    def test_start_bad_fps_refused(self):
        be, _ = _fresh_backend()
        for bad in (0, 241, -5):
            got = sc.start_capture(target_fps=bad, backend=be)
            self.assertFalse(got["ok"], bad)
            self.assertIn("Target FPS", got["reason"])

    def test_collect_stops_at_duration_cap(self):
        be, clock = _fresh_backend()
        got = sc.collect_frames(monitor=0, duration_s=1.0,
                                target_fps=10, max_frames=100,
                                backend=be, clock=clock)
        self.assertTrue(got["ok"], got["reason"])
        # 1.0 s at 10 fps, one frame per poll: exactly 10.
        self.assertEqual(got["count"], 10)
        self.assertEqual(len(got["frames"]), 10)
        self.assertTrue(all(f["source"] == "latest-buffered"
                            for f in got["frames"]))
        # The loop is stopped afterwards — latest refuses.
        self.assertFalse(sc.latest_frame()["ok"])

    def test_collect_stops_at_max_frames_cap(self):
        be, clock = _fresh_backend()
        got = sc.collect_frames(monitor=0, duration_s=60.0,
                                target_fps=60, max_frames=5,
                                backend=be, clock=clock)
        self.assertTrue(got["ok"], got["reason"])
        self.assertEqual(got["count"], 5)

    def test_collect_refuses_out_of_range_bounds(self):
        be, _ = _fresh_backend()
        for kwargs in ({"duration_s": 0}, {"duration_s": 3601},
                       {"target_fps": 0}, {"target_fps": 241},
                       {"max_frames": 0}, {"max_frames": 10001}):
            got = sc.collect_frames(backend=be, **kwargs)
            self.assertFalse(got["ok"], kwargs)
            self.assertEqual(got["count"], 0)
            self.assertEqual(got["frames"], [])

    def test_collect_timestamps_advance(self):
        be, clock = _fresh_backend()
        got = sc.collect_frames(monitor=0, duration_s=0.5,
                                target_fps=10, max_frames=100,
                                backend=be, clock=clock)
        stamps = [f["timestamp"] for f in got["frames"]]
        self.assertEqual(len(stamps), 5)
        self.assertEqual(stamps, sorted(stamps))
        self.assertGreater(stamps[-1], stamps[0])


if __name__ == "__main__":
    unittest.main()
