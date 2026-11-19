"""GAPS-20 (review flaws D-9 + D-10, §6.6): one UIA engine + loopback
wired to the meeting session.

D-9: the one UIA engine interface (core/desktop/uia_engine.py) —
pywinauto first, the easy-uiauto pilot only on an empty pywinauto
result, and the Electron wall named (with the app-adapter route
when one is registered) instead of a bare not-found. Runs against
FAKE engines and FAKE windows (no Windows, no pywinauto, no
easy-uiauto on this Linux lab machine).

D-10: loopback capture wired to the meeting session — the worker
`capture` command (NDJSON frames), the loopback capture session
(both backends, 16 kHz mono int16 normalisation, caps), the
meeting's opt-in system-audio track (second WAV, two-track
transcript + notes), the "system_audio" capture-guard kind, and
the front-end status slot reading the live capture state. Runs
against a FAKE pyaudiowpatch, a FAKE sounddevice, and a FAKE
worker script (no WASAPI on this Linux lab machine). The real
Windows legs — a real loopback stream on his machine, a real
Electron window, a real meeting with the other side audible —
are field legs, owed, not this batch.

Census (GAPS-20): total 754, params 2064 (start_meeting gains
system_audio), grouped 752, closed 753, AUDIT-TOTALS
754/382/372/164/306.
"""

import ast
import base64
import io
import json
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import capture_guard
from core import loopback as lb
from core import meeting as meeting_mod
from core.catalogue_snapshot import GROUP_MEMBERS
from core.desktop import app_adapters as aa
from core.desktop import uia_engine as ue
from core.desktop.vscode_adapter import register_vscode_adapter
from core.funnel.tools_registry import tools as _registry
from core.voice.audio_streams_playback import AudioStreamsPlaybackMixin

TREE = Path(__file__).resolve().parent.parent

_FAKE_WIN = object()  # the window object; identity comes from the override


class _FakeEngine:
    """A configurable UIA engine: canned results + call records."""

    def __init__(self, name, capabilities, find_result=None,
                 tree_result=None, point=None, focus=None,
                 available=(True, "")):
        self.name = name
        self.capabilities = frozenset(capabilities)
        self._find = find_result
        self._tree = tree_result
        self._point = point
        self._focus = focus
        self._available = available
        self.find_calls = []
        self.tree_calls = []
        self.point_calls = []
        self.focus_calls = 0

    def available(self):
        return self._available

    def find(self, name, window_title="", control_type=""):
        self.find_calls.append((name, window_title, control_type))
        if not self._available[0]:
            return {"status": "unavailable", "report": self._available[1],
                    "matches": [], "scanned": 0, "truncated": False,
                    "window": None, "window_desc": ""}
        if self._find is not None:
            return dict(self._find)
        return {"status": "not-found", "report": None, "matches": [],
                "scanned": 0, "truncated": False, "window": _FAKE_WIN,
                "window_desc": "window matching 'Code'"}

    def describe_point(self, x, y):
        self.point_calls.append((x, y))
        return self._point

    def read_tree(self, window_title="", max_depth=3, max_nodes=80):
        self.tree_calls.append((window_title, max_depth, max_nodes))
        if self._tree is not None:
            return dict(self._tree)
        return {"status": "empty",
                "report": "Could not read any controls from window matching 'Code'.",
                "window": _FAKE_WIN, "window_desc": "window matching 'Code'"}

    def focus_label(self):
        self.focus_calls += 1
        return self._focus


def _not_found(scanned=12, truncated=False):
    return {"status": "not-found", "report": None, "matches": [],
            "scanned": scanned, "truncated": truncated,
            "window": _FAKE_WIN, "window_desc": "window matching 'Code'"}


def _found(report):
    return {"status": "found", "report": report, "matches": [("w", {})],
            "scanned": 5, "truncated": False, "window": _FAKE_WIN,
            "window_desc": "window matching 'Code'"}


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        # The tools_registry import populates the adapter registry
        # at load time; each test starts from an empty one.
        aa.reset_registry()
        self._py = _FakeEngine("pywinauto", ue.ENGINE_CAPABILITIES["pywinauto"])
        self._pilot = _FakeEngine("easy-uiauto",
                                  ue.ENGINE_CAPABILITIES["easy-uiauto"])
        ue.set_engine_override("pywinauto", self._py)
        ue.set_engine_override("easy-uiauto", self._pilot)
        ue.set_window_identity_override(
            lambda w: ("Chrome_WidgetWin_1", "Code.exe"))

    def tearDown(self):
        ue.reset_engine_overrides()
        ue.reset_window_identity_override()
        aa.reset_registry()

    def test_engine_names_and_capabilities(self):
        self.assertEqual(ue.ENGINE_NAMES, ("pywinauto", "easy-uiauto"))
        self.assertEqual(ue.ENGINE_CAPABILITIES["pywinauto"],
                         frozenset({"find", "describe_point", "read_tree", "focus"}))
        self.assertEqual(ue.ENGINE_CAPABILITIES["easy-uiauto"],
                         frozenset({"find"}))
        self.assertEqual(self._py.capabilities,
                         ue.ENGINE_CAPABILITIES["pywinauto"])
        self.assertEqual(self._pilot.capabilities,
                         ue.ENGINE_CAPABILITIES["easy-uiauto"])

    def test_get_engine_unknown_raises_naming_known(self):
        with self.assertRaises(KeyError) as ctx:
            ue.get_engine("not-an-engine")
        self.assertIn("pywinauto", str(ctx.exception))
        self.assertIn("easy-uiauto", str(ctx.exception))

    def test_real_pywinauto_engine_unavailable_on_linux(self):
        engine = ue._PywinautoEngine()
        ok, reason = engine.available()
        self.assertFalse(ok)
        self.assertIn("isn't available", reason)

    def test_real_easy_uiauto_engine_unavailable_when_absent(self):
        engine = ue._EasyUiautoEngine()
        ok, reason = engine.available()
        self.assertFalse(ok)
        self.assertIn("not installed", reason)
        # The pilot declares no point/tree/focus capability: those
        # methods never consult the pilot library.
        self.assertIsNone(engine.describe_point(10, 10))
        self.assertEqual(engine.read_tree()["status"], "unsupported")
        self.assertIsNone(engine.focus_label())

    def test_engines_status_reports_both(self):
        status = ue.engines_status()
        self.assertEqual(sorted(status), ["easy-uiauto", "pywinauto"])
        self.assertTrue(status["pywinauto"]["available"])  # the fake
        self.assertEqual(status["pywinauto"]["capabilities"],
                         ["describe_point", "find", "focus", "read_tree"])
        self.assertEqual(status["easy-uiauto"]["capabilities"], ["find"])

    def test_find_pywinauto_first_pilot_never_shadows(self):
        self._py._find = _found("Found 1 control(s) matching 'Save' in window matching 'Code':\n• Save (Button) at (1,2)–(3,4)")
        self.assertIn("Save (Button)", ue.find_control("Save", "Code"))
        self.assertEqual(self._pilot.find_calls, [])

    def test_find_pilot_fallback_only_on_empty_pywinauto(self):
        self._py._find = _not_found()
        self._pilot._find = {"status": "found",
                             "report": "PILOT: 'Save' (Button) at (10,20) — easy-uiauto pilot"}
        result = ue.find_control("Save", "Code")
        self.assertIn("easy-uiauto pilot", result)
        self.assertEqual(len(self._py.find_calls), 1)
        self.assertEqual(len(self._pilot.find_calls), 1)
        self.assertEqual(self._pilot.find_calls[0][0], "Save")

    def test_find_scan_failure_never_retried_by_pilot(self):
        self._py._find = {"status": "failed",
                          "report": "Could not search window matching 'Code': the target window went away",
                          "matches": [], "scanned": 0, "truncated": False,
                          "window": _FAKE_WIN,
                          "window_desc": "window matching 'Code'"}
        result = ue.find_control("Save", "Code")
        self.assertIn("Could not search", result)
        self.assertNotIn("No control", result)
        self.assertEqual(self._pilot.find_calls, [])

    def test_find_unavailable_returns_without_pilot(self):
        self._py._available = (False, "UI Automation isn't available here.")
        result = ue.find_control("Save", "Code")
        self.assertIn("isn't available", result)
        self.assertEqual(self._pilot.find_calls, [])

    def test_find_empty_name(self):
        self.assertIn("No control name given",
                      ue.find_control("   ", "Code"))
        self.assertEqual(self._py.find_calls, [])
        self.assertEqual(self._pilot.find_calls, [])

    def test_find_truncated_note_survives(self):
        self._py._find = _not_found(scanned=2000, truncated=True)
        result = ue.find_control("Save", "Code")
        self.assertIn("scanned 2000 elements", result)
        self.assertIn("stopped at 2000 elements", result)

    def test_electron_wall_named_no_adapter(self):
        self._py._find = _not_found()
        result = ue.find_control("Save", "Code")
        self.assertIn("No control with a name containing 'Save'", result)
        self.assertIn("Electron app (Code.exe)", result)
        self.assertIn("accessibility tree is built lazily", result)
        self.assertIn("No app adapter covers this app yet", result)

    def test_electron_wall_adapter_named_when_registered(self):
        register_vscode_adapter()
        self._py._find = _not_found()
        result = ue.find_control("Save", "Code")
        self.assertIn("The vscode app adapter covers this app", result)

    def test_electron_wall_chrome_points_at_browser_control(self):
        ue.set_window_identity_override(
            lambda w: ("Chrome_WidgetWin_1", "chrome.exe"))
        self._py._find = _not_found()
        result = ue.find_control("Save", "Code")
        self.assertIn("this is Chrome", result)
        self.assertIn("browser control", result)
        self.assertNotIn("Electron app", result)

    def test_electron_wall_unknown_proc_named_honestly(self):
        ue.set_window_identity_override(
            lambda w: ("Chrome_WidgetWin_1", None))
        self._py._find = _not_found()
        result = ue.find_control("Save", "Code")
        self.assertIn("process could not be read", result)
        self.assertIn("Chrome (drive it through the browser control)", result)

    def test_electron_wall_not_chromium_no_note(self):
        ue.set_window_identity_override(lambda w: ("Notepad", "notepad.exe"))
        self._py._find = _not_found()
        result = ue.find_control("Save", "Code")
        self.assertIn("No control with a name containing 'Save'", result)
        self.assertNotIn("Electron", result)
        self.assertNotIn("Chrome", result)

    def test_electron_wall_detector_kinds(self):
        ue.set_window_identity_override(
            lambda w: ("Chrome_WidgetWin_1", "Code.exe"))
        wall = ue.electron_wall(_FAKE_WIN)
        self.assertEqual(wall["kind"], "electron")
        ue.set_window_identity_override(
            lambda w: ("Chrome_WidgetWin_1", "chrome.exe"))
        self.assertEqual(ue.electron_wall(_FAKE_WIN)["kind"], "chrome")
        ue.set_window_identity_override(
            lambda w: ("Chrome_WidgetWin_1", None))
        self.assertEqual(ue.electron_wall(_FAKE_WIN)["kind"],
                         "chromium-unknown")
        ue.set_window_identity_override(lambda w: ("Notepad", "notepad.exe"))
        self.assertIsNone(ue.electron_wall(_FAKE_WIN))
        self.assertIsNone(ue.electron_wall(None))

    def test_read_tree_empty_names_wall(self):
        self._py._tree = {"status": "empty",
                          "report": "Could not read any controls from window matching 'Code'.",
                          "window": _FAKE_WIN,
                          "window_desc": "window matching 'Code'"}
        result = ue.read_tree("Code")
        self.assertIn("Could not read any controls", result)
        self.assertIn("Electron app (Code.exe)", result)

    def test_read_tree_ok_no_wall_note(self):
        self._py._tree = {"status": "ok",
                          "report": "Control tree of window matching 'Code' — 2 node(s) shown (depth ≤ 3, cap 80):\n• Save [Button]",
                          "window": _FAKE_WIN,
                          "window_desc": "window matching 'Code'"}
        result = ue.read_tree("Code")
        self.assertIn("Control tree", result)
        self.assertNotIn("Electron", result)

    def test_describe_point_routes_pywinauto_only(self):
        self._py._point = '"Save" (Button) in Code'
        self.assertEqual(ue.describe_point(10, 20), '"Save" (Button) in Code')
        self.assertEqual(self._py.point_calls, [(10, 20)])
        self.assertEqual(self._pilot.point_calls, [])

    def test_focus_label_routes_pywinauto_only(self):
        self._py._focus = '"Save" (Button)'
        self.assertEqual(ue.focus_label(), '"Save" (Button)')
        self.assertEqual(self._py.focus_calls, 1)
        self.assertEqual(self._pilot.focus_calls, 0)

    def test_uia_facade_reexports_engine(self):
        from core.desktop import uia

        for name in ("find_control", "read_tree", "focus_label",
                     "electron_wall", "engines_status", "get_engine",
                     "ENGINE_NAMES", "ENGINE_CAPABILITIES", "wall_note"):
            self.assertTrue(hasattr(uia, name), name)
        self.assertEqual(uia.ENGINE_NAMES, ("pywinauto", "easy-uiauto"))


# ---------------------------------------------------------------------------
# D-10: loopback worker + capture session
# ---------------------------------------------------------------------------

_FAKE_WORKER_SCRIPT = '''
import sys, json, base64, struct
print(json.dumps({"type": "hello", "device": "Fake Loopback", "index": 7, "rate": 48000, "channels": 2}))
chunk = struct.pack("<4096f", *([0.25, 0.25] * 2048))
for i in range(3):
    print(json.dumps({"type": "frame", "pcm_b64": base64.b64encode(chunk).decode(), "rate": 48000, "channels": 2, "frames": 2048, "t": 1000.0 + i}))
print(json.dumps({"type": "end", "reason": "ended", "frames": 6144, "dropped_chunks": 0}))
'''


class _FakeLoopbackStream:
    """A PyAudioWPatch stream that feeds canned chunks synchronously."""

    def __init__(self, callback, chunks):
        self._callback = callback
        self._chunks = chunks
        self.stopped = False

    def start_stream(self):
        for data, count in self._chunks:
            self._callback(data, count, None, None)

    def stop_stream(self):
        self.stopped = True

    def close(self):
        pass


class _FakePyAudio:
    paFloat32 = 1
    paContinue = 0

    def __init__(self, chunks=None):
        self._devs = [
            {"index": 0, "name": "Speakers (Realtek)",
             "isLoopbackDevice": False, "defaultSampleRate": 48000.0,
             "maxInputChannels": 0},
            {"index": 1, "name": "Speakers (Realtek)",
             "isLoopbackDevice": True, "defaultSampleRate": 48000.0,
             "maxInputChannels": 2},
            {"index": 2, "name": "Headphones",
             "isLoopbackDevice": True, "defaultSampleRate": 44100.0,
             "maxInputChannels": 2},
        ]
        self._chunks = chunks

    def get_device_count(self):
        return len(self._devs)

    def get_device_info_by_index(self, i):
        return dict(self._devs[i])

    def get_default_output_device_info(self):
        return dict(self._devs[0])

    def open(self, **kw):
        if self._chunks is None:
            chunk = struct.pack("<4096f", *([0.5, -0.5] * 2048))
            self._chunks = [(chunk, 2048)] * 3
        return _FakeLoopbackStream(kw["stream_callback"], self._chunks)

    def terminate(self):
        pass


def _fake_pyaudiowpatch_module(chunks=None):
    import types

    mod = types.ModuleType("pyaudiowpatch")
    mod.PyAudio = lambda: _FakePyAudio(chunks)
    mod.paFloat32 = 1
    mod.paContinue = 0
    return mod


class _FakeSoundDeviceStream:
    def __init__(self, callback):
        self.callback = callback
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        pass


class _FakeSoundDevice:
    """A sounddevice module with one loopback device."""

    def __init__(self):
        self._devs = [
            {"index": 0, "name": "Speakers",
             "isLoopbackDevice": False, "default_samplerate": 48000.0,
             "max_input_channels": 0},
            {"index": 1, "name": "Speakers",
             "isLoopbackDevice": True, "default_samplerate": 48000.0,
             "max_input_channels": 2},
        ]
        self.last_stream = None
        self.opened_kwargs = None

    def query_devices(self, index=None):
        if index is None:
            return [dict(d) for d in self._devs]
        return dict(self._devs[index])

    def InputStream(self, **kw):
        self.opened_kwargs = dict(kw)
        self.last_stream = _FakeSoundDeviceStream(kw["callback"])
        return self.last_stream


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self._old = sys.modules.pop("pyaudiowpatch", None)

    def tearDown(self):
        if self._old is not None:
            sys.modules["pyaudiowpatch"] = self._old
        else:
            sys.modules.pop("pyaudiowpatch", None)

    def _install_fake(self, chunks=None):
        sys.modules["pyaudiowpatch"] = _fake_pyaudiowpatch_module(chunks)

    def test_capture_hello_frame_end(self):
        from core import loopback_worker as lw

        self._install_fake()
        buf = io.StringIO()
        rc = lw.capture(device="default", seconds=5.0, max_frames=4096,
                        out=buf)
        lines = [json.loads(l) for l in buf.getvalue().splitlines()]
        self.assertEqual(rc, 0)
        self.assertEqual([l["type"] for l in lines],
                         ["hello", "frame", "frame", "end"])
        self.assertEqual(lines[0]["device"], "Speakers (Realtek)")
        self.assertEqual(lines[0]["index"], 1)
        self.assertEqual((lines[0]["rate"], lines[0]["channels"]),
                         (48000, 2))
        raw = base64.b64decode(lines[1]["pcm_b64"])
        # 2048 frames x 2 channels x 4 bytes (float32)
        self.assertEqual(len(raw), 16384)
        self.assertEqual(struct.unpack("<4f", raw[:16]),
                         (0.5, -0.5, 0.5, -0.5))
        self.assertEqual(lines[-1]["reason"], "frames cap reached")
        self.assertEqual(lines[-1]["frames"], 4096)
        self.assertEqual(lines[-1]["dropped_chunks"], 0)

    def test_capture_device_by_name_and_index(self):
        from core import loopback_worker as lw

        self._install_fake()
        for device, want in (("headphones", "Headphones"), ("2", "Headphones"),
                             ("Speakers (Realtek)", "Speakers (Realtek)")):
            buf = io.StringIO()
            rc = lw.capture(device=device, seconds=5.0, max_frames=1024,
                            out=buf)
            lines = [json.loads(l) for l in buf.getvalue().splitlines()]
            self.assertEqual(rc, 0, device)
            self.assertEqual(lines[0]["device"], want, device)

    def test_capture_unknown_device_refuses_with_candidates(self):
        from core import loopback_worker as lw

        self._install_fake()
        buf = io.StringIO()
        rc = lw.capture(device="No Such Device", seconds=5.0, out=buf)
        lines = [json.loads(l) for l in buf.getvalue().splitlines()]
        self.assertEqual(rc, 1)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["type"], "error")
        self.assertIn("No Such Device", lines[0]["reason"])
        self.assertIn("Headphones", lines[0]["reason"])

    def test_capture_no_module_refuses(self):
        from core import loopback_worker as lw

        buf = io.StringIO()
        rc = lw.capture(device="default", seconds=1.0, out=buf)
        self.assertEqual(rc, 1)
        line = json.loads(buf.getvalue().strip())
        self.assertEqual(line["type"], "error")
        self.assertIn("pyaudiowpatch is not available", line["reason"])

    def test_capture_seconds_cap(self):
        from core import loopback_worker as lw

        class _QuietStream:
            def start_stream(self):
                pass

            def stop_stream(self):
                pass

            def close(self):
                pass

        class _QuietPA(_FakePyAudio):
            def open(self, **kw):
                return _QuietStream()

        import types

        mod = types.ModuleType("pyaudiowpatch")
        mod.PyAudio = _QuietPA
        mod.paFloat32 = 1
        mod.paContinue = 0
        sys.modules["pyaudiowpatch"] = mod
        buf = io.StringIO()
        rc = lw.capture(device="default", seconds=0.05, out=buf)
        lines = [json.loads(l) for l in buf.getvalue().splitlines()]
        self.assertEqual(rc, 0)
        self.assertEqual([l["type"] for l in lines], ["hello", "end"])
        self.assertEqual(lines[-1]["reason"], "seconds cap reached")
        self.assertEqual(lines[-1]["frames"], 0)

    def test_probe_unchanged_without_module(self):
        from core import loopback_worker as lw

        report = lw.probe()
        self.assertFalse(report["pyaudiowpatch_available"])
        self.assertFalse(report["wasapi_loopback"])
        self.assertEqual(report["devices"], [])

    def test_main_dispatch(self):
        from core import loopback_worker as lw

        self.assertEqual(lw.main([]), 2)
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            self.assertEqual(lw.main(["bogus"]), 2)
        self.assertIn("usage", buf.getvalue())
        self._install_fake()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = lw.main(["capture", "--device", "default", "--seconds",
                          "5", "--max-frames", "1024"])
        self.assertEqual(rc, 0)
        lines = [json.loads(l) for l in buf.getvalue().splitlines()]
        self.assertEqual(lines[0]["type"], "hello")
        self.assertEqual(lines[-1]["type"], "end")

    def test_docstring_points_at_recorded_licence(self):
        text = Path(__file__).resolve().parent.parent.joinpath(
            "core", "loopback_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("was not verified in this wave", text)
        self.assertIn("Apache-2.0", text)
        self.assertIn("THIRD_PARTY_LICENSES.md", text)


class ConversionTestCase(unittest.TestCase):
    def test_to_meeting_pcm_full_scale(self):
        data = struct.pack("<96f", *([1.0, 1.0] * 48))
        pcm = lb._to_meeting_pcm(data, 2, 48000)
        self.assertEqual(len(pcm) // 2, 16)
        self.assertTrue(all(v == 32767 for v in struct.unpack("<16h", pcm)))

    def test_to_meeting_pcm_downmix_cancels(self):
        data = struct.pack("<96f", *([0.5, -0.5] * 48))
        pcm = lb._to_meeting_pcm(data, 2, 48000)
        self.assertEqual(set(struct.unpack("<16h", pcm)), {0})

    def test_to_meeting_pcm_mono_passthrough(self):
        data = struct.pack("<4f", 100 / 32767, -100 / 32767,
                           200 / 32767, -200 / 32767)
        pcm = lb._to_meeting_pcm(data, 1, 16000)
        self.assertEqual(struct.unpack("<4h", pcm), (100, -100, 200, -200))

    def test_to_meeting_pcm_nan_is_silence(self):
        data = struct.pack("<2f", float("nan"), 1.0)
        pcm = lb._to_meeting_pcm(data, 1, 16000)
        self.assertEqual(struct.unpack("<2h", pcm), (0, 32767))

    def test_to_meeting_pcm_empty(self):
        self.assertEqual(lb._to_meeting_pcm(b"", 2, 48000), b"")


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._script = Path(self._tmp.name) / "fake_worker.py"
        self._script.write_text(_FAKE_WORKER_SCRIPT, encoding="utf-8")
        lb.set_worker_command([sys.executable, str(self._script)])
        self._old_sd = sys.modules.pop("sounddevice", None)

    def tearDown(self):
        lb.reset_capture()
        lb.reset_worker_command()
        if self._old_sd is not None:
            sys.modules["sounddevice"] = self._old_sd
        else:
            sys.modules.pop("sounddevice", None)
        self._tmp.cleanup()

    def _wait_frames(self, session, want, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if session.status()["frames"] >= want:
                return True
            time.sleep(0.05)
        return False

    def test_session_subprocess_fake_worker(self):
        ok, info = lb.start_capture(device="default")
        self.assertTrue(ok, info)
        self.assertEqual(info["backend"], "pyaudiowpatch-subprocess")
        self.assertEqual(info["device"], "Fake Loopback")
        self.assertEqual((info["rate"], info["channels"]), (48000, 2))
        session = lb.get_capture()
        self.assertTrue(self._wait_frames(session, 3 * 683))
        frames = []
        for _ in range(4):
            got = session.read(timeout=1.0)
            if got is not None:
                frames.append(got)
        self.assertEqual(len(frames), 3)
        self.assertTrue(all(len(p) // 2 == 683 for p, _ in frames))
        # 0.25 * 32767 = 8191.75 -> 8192
        self.assertEqual(struct.unpack("<h", frames[0][0][:2])[0], 8192)
        end = lb.stop_capture()
        self.assertEqual(end["frames"], 3 * 683)
        self.assertEqual(end["backend"], "pyaudiowpatch-subprocess")
        self.assertEqual(end["device"], "Fake Loopback")
        self.assertFalse(lb.capture_status()["active"])

    def test_session_second_start_refuses(self):
        ok, _ = lb.start_capture(device="default")
        self.assertTrue(ok)
        ok2, reason = lb.start_capture(device="default")
        self.assertFalse(ok2)
        self.assertIn("already active", reason)
        lb.stop_capture()

    def test_session_max_frames_cap(self):
        session = lb.LoopbackCapture()
        ok, info = session.start(device="default", max_frames=700)
        self.assertTrue(ok, info)
        self.assertTrue(self._wait_frames(session, 700))
        # The pump stops at the cap with the reason recorded.
        deadline = time.monotonic() + 5.0
        while session.active and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(session.active)
        self.assertEqual(session.status()["end_reason"], "frames cap reached")
        self.assertEqual(session.status()["frames"], 700)
        session.stop()

    def test_status_reports_live_capture(self):
        before = lb.status(probe_subprocess=False)
        self.assertFalse(before["capture_active"])
        ok, _ = lb.start_capture(device="default")
        self.assertTrue(ok)
        during = lb.status(probe_subprocess=False)
        self.assertTrue(during["capture_active"])
        self.assertTrue(during["capture"]["active"])
        lb.stop_capture()
        after = lb.status(probe_subprocess=False)
        self.assertFalse(after["capture_active"])

    def test_session_sounddevice_fake(self):
        fake_sd = _FakeSoundDevice()
        sys.modules["sounddevice"] = fake_sd
        ok, info = lb.start_capture(device="default",
                                    backend="sounddevice-wasapi-default")
        self.assertTrue(ok, info)
        self.assertEqual(info["backend"], "sounddevice-wasapi-default")
        self.assertEqual(info["device"], "Speakers")
        # Drive the callback with 2048 frames of stereo float32.
        chunk = struct.pack("<4096f", *([0.25, 0.25] * 2048))
        fake_sd.last_stream.callback(chunk, 2048, None, None)
        got = lb.get_capture().read(timeout=2.0)
        self.assertIsNotNone(got)
        pcm, _ = got
        self.assertEqual(len(pcm) // 2, 683)
        self.assertEqual(struct.unpack("<h", pcm[:2])[0], 8192)
        end = lb.stop_capture()
        self.assertEqual(end["backend"], "sounddevice-wasapi-default")

    def test_session_sounddevice_unknown_device_refuses(self):
        fake_sd = _FakeSoundDevice()
        sys.modules["sounddevice"] = fake_sd
        ok, reason = lb.start_capture(device="No Such Device",
                                      backend="sounddevice-wasapi-default")
        self.assertFalse(ok)
        self.assertIn("No Such Device", reason)

    def test_session_no_backend_refuses_both_rungs(self):
        lb.reset_worker_command()  # the real worker: no pyaudiowpatch here
        ok, reason = lb.start_capture(device="default")
        self.assertFalse(ok)
        self.assertIn("pyaudiowpatch is not available", reason)
        # The sounddevice rung's refusal is interpreter-dependent:
        # sys python3 has no sounddevice at all; the Qt venv has
        # it (via the syslibs PortAudio) but enumerates no loopback
        # device on Linux. Either way the rung refuses honestly.
        self.assertTrue(
            "sounddevice is not available" in reason
            or "no WASAPI loopback device enumerated" in reason, reason)

    def test_session_int_device_no_backend_refuses(self):
        ok, reason = lb.start_capture(device="3")
        self.assertFalse(ok)
        self.assertIn("backend-specific", reason)

    def test_only_worker_imports_pyaudiowpatch(self):
        """The isolation rule, by AST: loopback_worker.py is the
        only module allowed to import pyaudiowpatch."""
        import ast as _ast

        offenders = []
        for p in list((TREE / "core").rglob("*.py")) + list(
                (TREE / "gui").rglob("*.py")):
            try:
                tree = _ast.parse(p.read_text(errors="replace"))
            except SyntaxError:
                continue
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Import):
                    if any(a.name.split(".")[0] == "pyaudiowpatch"
                           for a in node.names):
                        offenders.append(str(p.relative_to(TREE)))
                elif isinstance(node, _ast.ImportFrom):
                    if (node.module or "").split(".")[0] == "pyaudiowpatch":
                        offenders.append(str(p.relative_to(TREE)))
        self.assertEqual(sorted(set(offenders)),
                         ["core/loopback_worker.py"])


# ---------------------------------------------------------------------------
# D-10: meeting system track + guard kind + front-end slot + census
# ---------------------------------------------------------------------------

class _ManualBackend:
    """A mic backend that feeds canned PCM synchronously on open."""

    def __init__(self):
        self.opened = False
        self._cb = None

    def open(self, on_chunk):
        self.opened = True
        self._cb = on_chunk
        on_chunk(struct.pack("<8000h", *([1000] * 8000)))

    def close(self):
        self.opened = False

    @property
    def overflows(self):
        return 0


class MeetingSystemTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._script = Path(self._tmp.name) / "fake_worker.py"
        self._script.write_text(_FAKE_WORKER_SCRIPT, encoding="utf-8")
        lb.set_worker_command([sys.executable, str(self._script)])
        self._data_patch = mock.patch.object(
            meeting_mod, "_DATA_DIR", self._tmp_path)
        self._data_patch.start()
        self._guard_patch = mock.patch.object(
            capture_guard, "ensure_capture_allowed",
            return_value=(True, "ok"))
        self._guard_patch.start()
        self._started_patch = mock.patch.object(
            capture_guard, "note_capture_started")
        self._started = self._started_patch.start()
        self._ended_patch = mock.patch.object(
            capture_guard, "note_capture_ended")
        self._ended = self._ended_patch.start()
        self._session = meeting_mod.get_session()
        self._backend = _ManualBackend()
        meeting_mod.set_capture_backend(self._backend)

        def _fake_transcribe(pcm_bytes):
            return {"engine": "faster-whisper",
                    "text": "mic words here", "segments": [],
                    "language": "en", "model_arg": "small",
                    "elapsed_s": 0.01, "error": None}

        self._transcribe_patch = mock.patch.object(
            meeting_mod, "_transcribe_pcm", side_effect=_fake_transcribe)
        self._transcribe_patch.start()

    def tearDown(self):
        if self._session.state == "recording":
            try:
                self._session.stop()
            except Exception:  # noqa: BLE001 — tearDown is best-effort
                pass
        self._transcribe_patch.stop()
        meeting_mod.reset_seams()
        lb.reset_capture()
        lb.reset_worker_command()
        self._ended_patch.stop()
        self._started_patch.stop()
        self._guard_patch.stop()
        self._data_patch.stop()
        self._tmp.cleanup()

    def _wait_system_frames(self, want, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._session.status()["system_frames"] >= want:
                return True
            time.sleep(0.05)
        return False

    def test_meeting_system_track_end_to_end(self):
        import wave

        ok, message = self._session.start("smoke", system_audio="default")
        self.assertTrue(ok, message)
        self.assertIn("Fake Loopback", message)
        status = self._session.status()
        self.assertEqual(status["system_device"], "Fake Loopback")
        self.assertEqual(status["system_backend"],
                         "pyaudiowpatch-subprocess")
        self.assertTrue(self._wait_system_frames(3 * 683))
        ok, result = self._session.stop()
        self.assertTrue(ok)
        self.assertIsNone(result["error"])
        self.assertEqual(result["system_frames"], 3 * 683)
        self.assertEqual(result["system_device"], "Fake Loopback")
        self.assertEqual(result["system_backend"],
                         "pyaudiowpatch-subprocess")
        self.assertIn("[microphone]", result["transcript"])
        self.assertIn("[system audio]", result["transcript"])
        self.assertEqual(result["system_transcript"], "mic words here")
        self.assertIsNotNone(result["notes"])
        for key, nframes in (("wav_path", 8000),
                             ("system_wav_path", 3 * 683)):
            path = Path(result[key])
            self.assertTrue(path.is_file(), key)
            self.assertGreater(path.stat().st_size, 0)
            with wave.open(str(path), "rb") as wav:
                self.assertEqual(
                    (wav.getnchannels(), wav.getframerate(),
                     wav.getnframes()), (1, 16000, nframes), key)
        for key in ("transcript_path", "system_transcript_path",
                    "notes_path"):
            path = Path(result[key])
            self.assertTrue(path.is_file(), key)
            self.assertGreater(path.stat().st_size, 0)
        transcript = Path(result["transcript_path"]).read_text(
            encoding="utf-8")
        self.assertIn("[microphone]", transcript)
        self.assertIn("[system audio]", transcript)
        self.assertIn("two sections", transcript)
        started = [c.args for c in self._started.call_args_list]
        ended = [c.args for c in self._ended.call_args_list]
        self.assertIn(("microphone",), started)
        self.assertIn(("system_audio",), started)
        self.assertIn(("microphone",), ended)
        self.assertIn(("system_audio",), ended)

    def test_meeting_system_guard_refusal_starts_nothing(self):
        with mock.patch.object(
                capture_guard, "ensure_capture_allowed",
                side_effect=lambda kind: (
                    (False, "System audio was not confirmed")
                    if kind == "system_audio" else (True, "ok"))):
            ok, message = self._session.start("smoke",
                                              system_audio="default")
        self.assertFalse(ok)
        self.assertIn("not confirmed", message)
        self.assertFalse(self._backend.opened)
        self.assertEqual(self._session.status()["state"], "idle")
        self.assertEqual(list(self._tmp_path.rglob("*.wav")), [])

    def test_meeting_system_open_failure_tears_down_mic(self):
        lb.reset_worker_command()  # the real worker: no backend here
        ok, message = self._session.start("smoke", system_audio="default")
        self.assertFalse(ok)
        self.assertIn("Could not open system audio", message)
        self.assertFalse(self._backend.opened)
        self.assertEqual(self._session.status()["state"], "idle")
        self.assertEqual(list(self._tmp_path.rglob("*.wav")), [])
        self.assertFalse(lb.capture_status()["active"])

    def test_meeting_mic_only_unchanged(self):
        ok, message = self._session.start("plain")
        self.assertTrue(ok, message)
        self.assertNotIn("System audio", message)
        ok, result = self._session.stop()
        self.assertTrue(ok)
        self.assertIsNone(result["system_wav_path"])
        self.assertEqual(result["transcript"], "mic words here")
        self.assertEqual(result["system_transcript"], "")
        self.assertIsNone(result["system_error"])

    def test_meeting_status_system_fields(self):
        ok, _ = self._session.start("smoke", system_audio="default")
        self.assertTrue(ok)
        status = self._session.status()
        self.assertEqual(status["system_device"], "Fake Loopback")
        self.assertEqual(status["system_backend"],
                         "pyaudiowpatch-subprocess")
        self.assertGreaterEqual(status["system_frames"], 0)
        self.assertFalse(status["system_cap_reached"])

    def test_tool_start_meeting_params(self):
        decls = _registry.get_declarations()
        start = next(d for d in decls if d["name"] == "start_meeting")
        self.assertEqual(sorted(start["parameters"]["properties"]),
                         ["system_audio", "title"])
        self.assertEqual(start["parameters"]["required"], [])


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        capture_guard.note_new_session()

    def tearDown(self):
        capture_guard.note_new_session()

    def test_guard_system_audio_kind(self):
        self.assertEqual(
            capture_guard._CAPTURE_KIND_LABELS["system_audio"],
            "System audio")
        self.assertIn("start_meeting",
                      capture_guard._CAPTURE_KIND_TOOLS["system_audio"])

    def test_guard_system_audio_ask_flow(self):
        # No confirmation handler on this VM: the ask refuses with
        # the kind's own label, never a generic message.
        allowed, message = capture_guard.ensure_capture_allowed(
            "system_audio")
        self.assertFalse(allowed)
        self.assertIn("System audio capture was not confirmed", message)

    def test_guard_system_audio_ask_once_remembers(self):
        calls = []

        def _ask(kind):
            calls.append(kind)
            return True

        with mock.patch.object(capture_guard, "_ask_human",
                               side_effect=_ask):
            allowed, _ = capture_guard.ensure_capture_allowed("system_audio")
            self.assertTrue(allowed)
            allowed, _ = capture_guard.ensure_capture_allowed("system_audio")
            self.assertTrue(allowed)
        self.assertEqual(calls, ["system_audio"])
        self.assertTrue(capture_guard.is_capture_approved("system_audio"))


class FrontEndTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._script = Path(self._tmp.name) / "fake_worker.py"
        self._script.write_text(_FAKE_WORKER_SCRIPT, encoding="utf-8")
        lb.set_worker_command([sys.executable, str(self._script)])

    def tearDown(self):
        lb.reset_capture()
        lb.reset_worker_command()
        self._tmp.cleanup()

    def test_front_end_status_loopback_live(self):
        class _Host(AudioStreamsPlaybackMixin):
            _aec = None

        host = _Host()
        idle = host.front_end_status()["loopback"]
        self.assertFalse(idle["capture_active"])
        self.assertFalse(idle["probed"])
        ok, _ = lb.start_capture(device="default")
        self.assertTrue(ok)
        live = host.front_end_status()["loopback"]
        self.assertTrue(live["capture_active"])
        self.assertEqual(live["capture"]["backend"],
                         "pyaudiowpatch-subprocess")
        lb.stop_capture()
        after = host.front_end_status()["loopback"]
        self.assertFalse(after["capture_active"])


class CensusTestCase(unittest.TestCase):
    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 758, len(decls))  # INTENTIONAL — GAPS-20 loopback-meeting pack (2026-11-13): total unchanged (no new tool); Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2079, total_params)  # INTENTIONAL — GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 755, grouped)  # INTENTIONAL — GAPS-20 loopback-meeting pack (2026-11-13): grouped unchanged (no new tool); field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing
        closed = sum(
            1 for d in decls
            if d.get("parameters", {}).get("additionalProperties") is False)
        self.assertEqual(closed, 757, closed)  # INTENTIONAL — GAPS-20 loopback-meeting pack (2026-11-13): closed unchanged (no new tool); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing

    def test_audit_totals(self):
        import json as _json
        import re as _re

        text = (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 758', '"read": 383',  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing
                       '"effectful": 375', '"confirm_gated": 166',  # INTENTIONAL — Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
                       '"already_verified_in_handler": 309'):  # INTENTIONAL — Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
            self.assertIn(needle, text, needle)
        totals = _json.loads(_re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 758)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(totals["read"], 383)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing
        self.assertEqual(totals["effectful"], 375)  # INTENTIONAL — Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan
        self.assertEqual(totals["confirm_gated"], 166)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
        self.assertEqual(totals["already_verified_in_handler"], 309)  # INTENTIONAL — Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan


if __name__ == "__main__":
    unittest.main()
