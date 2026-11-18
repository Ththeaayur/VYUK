"""Mic starvation signal consumer (issue-hunt #5) — tests for the
deaf hint firing on a starving session and for mic_status()
carrying the signal.

The write phase exposed `_mic_starved_seconds` on the session
and mirrored it onto the audio manager, but nothing read it — a
deaf connected session still looked connected. The consumer:
mic_status() carries `mic_starved_seconds` in both branches, and
_mic_watch_tick shows the deaf hint at once when the session is
connected and starved (the 20 s wait is already inside the
signal), with the muted wording when the mic is muted.

Harness: the MicOffHintTests pattern from
test_mic_first_use_ask.py (stub host over
SessionConnectionMixin, stubbed audio_manager + live_session,
temp permission store) for the GUI half; a real AudioManager
for the mic_status() half (construction opens no device).
"""

import os
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    import types as _types_mod

    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

from core import permission_grants as pg  # noqa: E402

try:
    from PySide6.QtCore import Qt as _Qt  # noqa: F401
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None


def setUpModule():
    """Mirror test_c7_dialog_crop's voice_assistant.* alias."""
    global _APP, _VA_SNAPSHOT
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    import types as _types_mod

    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


class _FakeCaption:
    def __init__(self, text=""):
        self._t = text

    def text(self):
        return self._t

    def setText(self, t):
        self._t = t


class _StoreIsolation(unittest.TestCase):
    """Temp store path + clean session state per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)
        pg.clear_session_grants()
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        pg.clear_path_override()
        pg.clear_session_grants()
        pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()


def _make_manager():
    from core.voice import audio as audio_mod

    m = audio_mod.AudioManager()
    m.mic_gain = 1.0
    m.is_muted = False
    return m


class MicStatusStarvedTests(unittest.TestCase):
    """mic_status() carries mic_starved_seconds in both branches."""

    def test_no_stream_branch_carries_starved_seconds(self):
        m = _make_manager()
        m._mic_starved_seconds = 25.0
        status = m.mic_status()
        self.assertEqual(status["state"], "no_stream")
        self.assertEqual(status["mic_starved_seconds"], 25.0)

    def test_no_stream_branch_defaults_to_zero(self):
        m = _make_manager()
        status = m.mic_status()
        self.assertEqual(status["mic_starved_seconds"], 0.0)

    def test_open_branch_carries_starved_seconds(self):
        m = _make_manager()
        # A stream open 30 s ago, frames flowing, no voice yet —
        # state reads "silent", but the test only asserts the
        # starved field rides the dict.
        m._in_stream = object()
        m._input_opened_at = time.monotonic() - 30.0
        m._opened_device_name = "Fake Mic"
        m._frames_received = 10
        m._mic_starved_seconds = 25.0
        status = m.mic_status()
        self.assertEqual(status["mic_starved_seconds"], 25.0)


@unittest.skipUnless(_QT, "PySide6 needed for the starved-hint tests")
class StarvedHintTests(_StoreIsolation):
    """_mic_watch_tick shows the deaf hint on a starving session."""

    def setUp(self):
        super().setUp()
        # The mixin reads permission_grants via the
        # voice_assistant.core alias — a DIFFERENT module object
        # from core.permission_grants, so the override + grant
        # must land on that copy too (the MicOffHintTests
        # pattern in test_mic_first_use_ask.py). Without this,
        # the mixin reads the real (missing) store, where a
        # never-decided mic reads as denied and the mic-off
        # branch fires instead of the starvation branch.
        from voice_assistant.core import permission_grants as _pg_a
        self.pga = _pg_a
        self.pga.set_path_override(self._store)
        self.pga.clear_session_grants()

    def tearDown(self):
        self.pga.clear_path_override()
        self.pga.clear_session_grants()
        super().tearDown()

    def _host(self, mic_state, connected=True):
        import gui.window_session_connection as wsc

        class _Worker:
            def is_alive(self):
                return True

        class _Timer:
            def stop(self):
                pass

        class _Host(wsc.SessionConnectionMixin):
            pass

        host = _Host()
        host.worker = _Worker()
        host._mic_watch_timer = _Timer()
        host.caption_label = _FakeCaption("Listening")
        host._deaf_since = None
        host._deaf_hint_active = False
        host._deaf_hint_text = ""
        host._deaf_saved_caption = None
        host._mic_stall_noted = False

        class _AM:
            @staticmethod
            def mic_status():
                return dict(mic_state)

        self._orig_am = wsc.audio_manager
        wsc.audio_manager = _AM()
        self._orig_ls = wsc.live_session
        wsc.live_session = SimpleNamespace(is_connected=connected)
        self._wsc = wsc
        return host

    def _restore(self):
        self._wsc.audio_manager = self._orig_am
        self._wsc.live_session = self._orig_ls

    def test_starved_ok_session_shows_device_hint_at_once(self):
        # The case the signal exists for: state reads "ok" on
        # energy (or anything but "silent"), yet no mic audio
        # has reached the session for 20 s. The hint fires at
        # once — no _deaf_since wait, the 20 s is inside the
        # signal already.
        host = self._host({"state": "ok", "device": "Fake Mic",
                           "level": 0.1, "muted": False,
                           "mic_starved_seconds": 25.0})
        try:
            host._mic_watch_tick()
            self.assertIn("Fake Mic", host.caption_label.text())
            self.assertIn("can't hear anything",
                          host.caption_label.text())
        finally:
            self._restore()

    def test_starved_and_muted_names_the_mute(self):
        # Mute gates the queue, not the RMS stamps — a muted
        # stream reads "ok" while starving. The hint must say
        # muted, not send the user to Settings for a device
        # that is fine.
        host = self._host({"state": "ok", "device": "Fake Mic",
                           "level": 0.1, "muted": True,
                           "mic_starved_seconds": 25.0})
        try:
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(),
                             self._wsc._MIC_MUTED_HINT)
        finally:
            self._restore()

    def test_starved_no_stream_unknown_open_error_shows_hint(self):
        # Issue-hunt #3, partially covered: the mic never
        # opened for an unknown reason (not VYUK's permission,
        # not an OS denial) — the silent branch never sees
        # "no_stream", so without the starvation consumer the
        # session sat deaf with no hint at all.
        self.assertTrue(self.pga.set_permission_state(
            "microphone", "granted", via="settings"))
        host = self._host({"state": "no_stream", "device": None,
                           "level": 0.0, "muted": False,
                           "open_error": "Error querying device -1",
                           "mic_starved_seconds": 25.0})
        try:
            host._mic_watch_tick()
            self.assertIn("can't hear anything",
                          host.caption_label.text())
        finally:
            self._restore()

    def test_not_connected_starved_value_ignored(self):
        # A stale starved value left over from a previous
        # session must never fire during a fresh connect.
        host = self._host({"state": "ok", "device": "Fake Mic",
                           "level": 0.1, "muted": False,
                           "mic_starved_seconds": 25.0},
                          connected=False)
        try:
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(), "Listening")
        finally:
            self._restore()

    def test_no_starvation_no_hint(self):
        # Control: a healthy delivering session shows nothing.
        host = self._host({"state": "ok", "device": "Fake Mic",
                           "level": 0.1, "muted": False,
                           "mic_starved_seconds": 0.0})
        try:
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(), "Listening")
        finally:
            self._restore()

    def test_hint_clears_when_frames_return(self):
        host = self._host({"state": "ok", "device": "Fake Mic",
                           "level": 0.1, "muted": False,
                           "mic_starved_seconds": 25.0})
        try:
            host._mic_watch_tick()
            self.assertIn("can't hear anything",
                          host.caption_label.text())
            # Frames flow again: the mirror clears, the next
            # tick restores the caption.
            self._wsc.audio_manager = type("AM", (), {
                "mic_status": staticmethod(lambda: {
                    "state": "ok", "device": "Fake Mic",
                    "level": 0.1, "muted": False,
                    "mic_starved_seconds": 0.0})})()
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(), "Listening")
        finally:
            self._restore()


if __name__ == "__main__":
    unittest.main()
