"""Fix 6 — performance: pay nothing before first paint, measure honestly.

Covers core/perf.py (own-process load, log rotation, load-line text),
the deferred integrity check in main.py, Doctor's process-load check
ordering, the orb idle interval, and the briefing-line wiring pin.
No real network, no wall-clock timing assertions (fake clock /
injected values only); Qt tests run offscreen.
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

# Synthetic voice_assistant package (the test_livefix precedent):
# the tree layout is top-level core/ + gui/, so the alias maps
# voice_assistant -> tree root.
import types as _types_mod

if "voice_assistant" not in sys.modules:
    _pkg = _types_mod.ModuleType("voice_assistant")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _pkg

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    sys.modules["sounddevice"] = _types_mod.ModuleType("sounddevice")

from core import perf  # noqa: E402


def _make_log(tmpdir, n_lines, line="x" * 72 + "\n"):
    path = Path(tmpdir) / "activity.log"
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(n_lines):
            fh.write(f"{i:06d} | test | {line}")
    return path


def _has_psutil():
    try:
        import psutil  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class TestProcessLoad(unittest.TestCase):
    def test_keys_present(self):
        load = perf.process_load()
        self.assertEqual(
            set(load),
            {"psutil_available", "process_rss_mb", "process_cpu_percent",
             "machine_cpu_percent", "machine_mem_percent"})

    @unittest.skipUnless(_has_psutil(), "psutil not installed here")
    def test_real_rss_and_machine_mem(self):
        load = perf.process_load()
        # psutil is installed in the Qt interpreter.
        self.assertTrue(load["psutil_available"])
        self.assertIsInstance(load["process_rss_mb"], float)
        self.assertGreater(load["process_rss_mb"], 0.0)
        # Machine memory is a real percent on every call.
        self.assertIsInstance(load["machine_mem_percent"], float)
        self.assertGreaterEqual(load["machine_mem_percent"], 0.0)
        self.assertLessEqual(load["machine_mem_percent"], 100.0)

    @unittest.skipUnless(not _has_psutil(),
                         "psutil is installed here; see test_real_rss_and_machine_mem")
    def test_no_psutil_reports_honest_none(self):
        load = perf.process_load()
        self.assertFalse(load["psutil_available"])
        self.assertIsNone(load["process_rss_mb"])
        self.assertIsNone(load["process_cpu_percent"])
        self.assertIsNone(load["machine_cpu_percent"])
        self.assertIsNone(load["machine_mem_percent"])

    def test_cpu_percent_is_none_or_a_percent(self):
        # First non-blocking call primes (None is honest); later
        # calls report a real percent. Never a fabricated value.
        load = perf.process_load()
        cpu = load["process_cpu_percent"]
        self.assertTrue(cpu is None or
                        (isinstance(cpu, float) and 0.0 <= cpu <= 100.0))
        load2 = perf.process_load()
        cpu2 = load2["process_cpu_percent"]
        self.assertTrue(cpu2 is None or
                        (isinstance(cpu2, float) and 0.0 <= cpu2 <= 100.0))

    @unittest.skipUnless(_has_psutil(), "psutil not installed here")
    def test_blocking_interval_returns_real_cpu(self):
        load = perf.process_load(cpu_interval=0.05)
        self.assertIsInstance(load["process_cpu_percent"], float)
        self.assertGreaterEqual(load["process_cpu_percent"], 0.0)

    def test_no_psutil_is_honest_not_fake(self):
        real = perf._psutil
        try:
            perf._PSUTIL = None
            perf._PSUTIL_TRIED = True
            # Force the lookup to fail by hiding the module.
            orig_import = __import__

            def _blocked(name, *a, **k):
                if name == "psutil":
                    raise ImportError("psutil blocked for test")
                return orig_import(name, *a, **k)

            import builtins
            old = builtins.__import__
            builtins.__import__ = _blocked
            try:
                perf._PSUTIL_TRIED = False
                load = perf.process_load()
            finally:
                builtins.__import__ = old
        finally:
            perf._PSUTIL_TRIED = False
            perf._PSUTIL = real() if callable(real) else None
            # Re-prime the cache for the other tests.
            perf._psutil()
        self.assertFalse(load["psutil_available"])
        self.assertIsNone(load["process_rss_mb"])
        self.assertIsNone(load["process_cpu_percent"])
        self.assertIsNone(load["machine_cpu_percent"])
        self.assertIsNone(load["machine_mem_percent"])

    def test_never_raises(self):
        # Even with a broken psutil object, no raise.
        saved = perf._PSUTIL, perf._PSUTIL_TRIED
        try:
            perf._PSUTIL = object()
            perf._PSUTIL_TRIED = True
            load = perf.process_load()
            self.assertIsInstance(load, dict)
        finally:
            perf._PSUTIL, perf._PSUTIL_TRIED = saved


class TestFormatLoadLine(unittest.TestCase):
    def test_full_line(self):
        load = {"psutil_available": True, "process_rss_mb": 412.4,
                "process_cpu_percent": 3.0, "machine_cpu_percent": 12.0,
                "machine_mem_percent": 61.0}
        frame = {"ema_ms": 1.4, "quality": "full"}
        self.assertEqual(perf.format_load_line(load, frame),
                         ["VYUK 412 MB", "orb 1.4 ms full"])

    def test_missing_pieces_are_omitted_not_invented(self):
        load = {"psutil_available": True, "process_rss_mb": None,
                "process_cpu_percent": None, "machine_cpu_percent": None,
                "machine_mem_percent": 61.0}
        self.assertEqual(perf.format_load_line(load, None), [])
        self.assertEqual(
            perf.format_load_line(load, {"ema_ms": None, "quality": "full"}),
            ["orb full"])
        self.assertEqual(
            perf.format_load_line({"process_rss_mb": 100.0},
                                  {"ema_ms": 31.0, "quality": None}),
            ["VYUK 100 MB", "orb 31.0 ms"])

    def test_empty(self):
        self.assertEqual(perf.format_load_line({}), [])
        self.assertEqual(perf.format_load_line(None), [])


class TestRotateActivityLog(unittest.TestCase):
    def test_under_cap_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_log(tmp, 10)
            before = path.read_bytes()
            result = perf.rotate_activity_log(path)
            self.assertFalse(result["rotated"])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(result["before_bytes"], len(before))
            self.assertEqual(result["after_bytes"], len(before))

    def test_over_cap_keeps_newest_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_log(tmp, 15000)
            before = path.stat().st_size
            self.assertGreater(before, perf.LOG_MAX_BYTES)
            result = perf.rotate_activity_log(path)
            self.assertTrue(result["rotated"])
            self.assertLessEqual(result["after_bytes"], perf.LOG_MAX_BYTES)
            kept = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(kept), perf.LOG_KEEP_LINES)
            # Newest line survives; oldest kept line is 13001st (1-based).
            self.assertIn("014999 | test |", kept[-1])
            self.assertIn("013000 | test |", kept[0])
            self.assertEqual(result["kept_lines"], perf.LOG_KEEP_LINES)

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = perf.rotate_activity_log(Path(tmp) / "nope.log")
            self.assertFalse(result["rotated"])
            self.assertEqual(result["before_bytes"], 0)

    def test_never_raises_on_unwritable(self):
        result = perf.rotate_activity_log(Path("/nonexistent-dir-xyz/x.log"))
        self.assertFalse(result["rotated"])


class TestMainDeferredIntegrity(unittest.TestCase):
    def test_integrity_not_called_before_first_paint(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        show_at = src.index("window.show()")
        # The check runs only inside the deferred thread, after show.
        self.assertIn("check_and_log()", src)
        self.assertLess(show_at, src.index("check_and_log()"))
        self.assertIn("_deferred_startup_checks", src)
        self.assertIn('name="vyuk-startup-checks"', src)
        self.assertIn("daemon=True", src)
        # Rotation rides the same deferred pass.
        self.assertIn("rotate_activity_log()", src)
        # No blocking call remains before show: the only
        # check_and_log occurrence is the deferred one.
        self.assertEqual(src.count("check_and_log()"), 1)

    def test_updater_still_runs_before_window(self):
        # apply_staged must stay on the launch path (it stages
        # updates); only the integrity hash moved.
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertLess(src.index("apply_staged()"),
                        src.index("window.show()"))


class TestDoctorProcessLoad(unittest.TestCase):
    def test_check_defined_and_before_compile(self):
        src = (ROOT / "doctor.py").read_text(encoding="utf-8")
        self.assertIn("def check_process_load(", src)
        tree = ast.parse(src)
        checks = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_CHECKS":
                        checks = [e.id for e in node.value.elts
                                  if isinstance(e, ast.Name)]
        self.assertIsNotNone(checks)
        self.assertIn("check_process_load", checks)
        self.assertLess(checks.index("check_process_load"),
                        checks.index("check_compile"))
        # The check separates own-process from machine numbers.
        self.assertIn("process_rss_mb", src)
        self.assertIn("machine", src)

    def test_check_runs(self):
        import doctor  # noqa: PLC0415 — run the check in-process
        seen = []
        orig = doctor.report
        try:
            doctor.report = lambda status, label, detail="", fix="": seen.append(
                (status, label, detail))
            doctor.check_process_load()
        finally:
            doctor.report = orig
        names = [n for (_s, n, _m) in seen]
        self.assertIn("VYUK process load", names)
        load_msg = [m for (_s, n, m) in seen if n == "VYUK process load"][0]
        if _has_psutil():
            self.assertIn("Activity log", names)
            self.assertIn("RSS", load_msg)
            self.assertIn("machine", load_msg)
        else:
            # No psutil here: the check says so honestly and stops.
            self.assertIn("psutil", load_msg)


class TestOrbIntervalAndFrameReport(unittest.TestCase):
    def test_idle_interval_is_50_live_is_16(self):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            self.skipTest("Qt unavailable")
        app = QApplication.instance() or QApplication([])
        from gui.visualizer import OrbVisualizer
        w = OrbVisualizer()
        for state, want in (("IDLE", 50), ("DISCONNECTED", 50),
                            ("LISTENING", 16), ("SPEAKING", 16),
                            ("THINKING", 16)):
            w.set_state(state)
            self.assertEqual(w._desired_interval_ms(), want, state)
            self.assertEqual(w.timer.interval(), want, state)

    def test_frame_report(self):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            self.skipTest("Qt unavailable")
        app = QApplication.instance() or QApplication([])
        from gui.visualizer import OrbVisualizer
        w = OrbVisualizer()
        rep = w.frame_report()
        self.assertIsInstance(rep, dict)
        self.assertEqual(rep["quality"], "full")
        # No frames fed yet: ema is honestly None, not invented.
        self.assertIsNone(rep["ema_ms"])


class TestBriefingWiring(unittest.TestCase):
    def test_vitals_block_uses_perf_load_line(self):
        src = (ROOT / "gui" / "window_services_speech.py").read_text(
            encoding="utf-8")
        # T-7 (debug pass): the perf import moved to the facade
        # (GAPS-15 layering) — `from voice_assistant.core import
        # perf` is the stale spelling.
        self.assertIn(
            "from voice_assistant.core.gui_facade import perf as _perf",
            src)
        self.assertIn("_perf.format_load_line(", src)
        self.assertIn("_perf.process_load()", src)
        self.assertIn(".frame_report()", src)


if __name__ == "__main__":
    unittest.main()
