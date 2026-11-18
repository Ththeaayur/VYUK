"""Field-check fix #16 (T8) — four silent losses, each surfaced.

#24 Activity feed omits broken sources: a source that is present
but unreadable (garbage chain file, corrupt store or cost
ledger, unreadable log) now contributes one "unavailable"
marker row instead of silently contributing nothing. A missing
source is healthy and still contributes nothing.
#25 "stop everything" becomes a normal turn when the cancel
predicate raises: the error now reads as a cancel attempt —
stop playback, say "Stopped." — never a normal turn.
#26 mic-watch tick gives up silently when mic_status() raises:
consecutive failures are counted, the first and the threshold
crossing are logged to the sendable log, and a hint appears
after 3 consecutive failures.
#27 browser-permission refusal names only Settings: it now names
the one-tap way back ("Connect my browser"), as the no-session
refusal already does. Other permissions read exactly as before.
"""
import asyncio
import io
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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

from core import activity_feed as af  # noqa: E402
from core import audit_chain as chain_mod  # noqa: E402
from core import record_family as rf_mod  # noqa: E402

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


def _unavailable(rows):
    return [r for r in rows if r.get("kind") == "unavailable"]


class ActivityUnavailableTests(unittest.TestCase):
    """#24: broken sources surface as marker rows."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        chain_mod.configure(data_dir=self.tmp)
        rf_mod.configure(data_dir=self.tmp)
        self.addCleanup(rf_mod.reset_configuration)
        self.addCleanup(chain_mod.reset_configuration)

    def tearDown(self):
        self._tmp.cleanup()

    def test_chain_garbage_gets_marker(self):
        (self.tmp / "records.jsonl").write_text("{{{not json\n",
                                                encoding="utf-8")
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _unavailable(rows)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].get("source"), "activity")
        self.assertIn("unavailable", markers[0]["title"].lower())
        self.assertIn("records", markers[0]["title"].lower())

    def test_chain_missing_gets_no_marker(self):
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        self.assertEqual(_unavailable(rows), [])

    def test_corrupt_store_gets_marker(self):
        (self.tmp / "translations.sqlite3").write_bytes(
            b"not a sqlite file")
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _unavailable(rows)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].get("source"), "ledger")
        self.assertIn("translations", markers[0]["title"])

    def test_corrupt_cost_ledger_gets_marker(self):
        (self.tmp / "cost_ledger.sqlite3").write_bytes(
            b"not a sqlite file")
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _unavailable(rows)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].get("source"), "cost")
        self.assertIn("cost", markers[0]["title"].lower())

    def test_healthy_feed_has_no_unavailable_markers(self):
        rf_mod.log_activity_human("t8 healthy line")
        (self.tmp / "activity.log").write_text(
            "2026-01-01T00:00:00 | get_current_time_date | success\n",
            encoding="utf-8")
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        self.assertEqual(_unavailable(rows), [])
        self.assertTrue(any(e.get("source") == "activity"
                            for e in rows))

    def test_marker_sorts_last(self):
        # A dated log entry outranks the undated marker.
        (self.tmp / "records.jsonl").write_text("{{{not json\n",
                                                encoding="utf-8")
        (self.tmp / "activity.log").write_text(
            "2026-01-01T00:00:00 | get_current_time_date | success\n",
            encoding="utf-8")
        rows = af.read_unified(limit=50, data_dir=self.tmp)
        markers = _unavailable(rows)
        self.assertEqual(len(markers), 1)
        self.assertEqual(rows[-1], markers[0])
        self.assertEqual(rows[0].get("source"), "log")


class CancelAllPredicateTests(unittest.TestCase):
    """#25: a raising cancel predicate still stops."""

    def setUp(self):
        from core import failsafe as _fs
        from core.voice import local_client_mic as lcm

        self._fs = _fs
        self._lcm = lcm
        self._orig_pred = _fs.is_cancel_all_utterance
        self._orig_log = lcm.memory_store.log_message
        lcm.memory_store.log_message = lambda *a, **k: None
        self.addCleanup(self._restore)

    def _restore(self):
        self._fs.is_cancel_all_utterance = self._orig_pred
        self._lcm.memory_store.log_message = self._orig_log

    def _mixin(self):
        _m = object.__new__(self._lcm.LocalLiveSessionMicMixin)
        self.spoken = []
        _m.on_transcript = lambda role, text: self.spoken.append(
            (role, text))
        _m._speaking = True
        _m._interrupt_playback = lambda: None
        _m._drain_speech_queue = lambda: None
        _m._notify_status = lambda s: None

        async def _speak(text):
            self.spoken.append(("spoken", text))

        _m._speak = _speak
        return _m

    def test_predicate_error_still_stops(self):
        def _boom(text):
            raise RuntimeError("injected")

        self._fs.is_cancel_all_utterance = _boom
        got = asyncio.run(
            self._mixin()._handle_cancel_all("stop everything"))
        self.assertIs(got, True)
        self.assertIn(("assistant", "Stopped."), self.spoken)

    def test_predicate_error_is_logged(self):
        def _boom(text):
            raise RuntimeError("injected")

        self._fs.is_cancel_all_utterance = _boom
        buf = io.StringIO()
        with redirect_stdout(buf):
            asyncio.run(
                self._mixin()._handle_cancel_all("stop everything"))
        self.assertIn("cancel-all check failed", buf.getvalue())
        self.assertIn("treating the utterance as a stop",
                      buf.getvalue())

    def test_predicate_false_is_not_a_cancel(self):
        self._fs.is_cancel_all_utterance = lambda text: False
        got = asyncio.run(
            self._mixin()._handle_cancel_all("what time is it"))
        self.assertIs(got, False)
        self.assertEqual(self.spoken, [])

    def test_predicate_true_stops(self):
        self._fs.is_cancel_all_utterance = lambda text: True
        got = asyncio.run(
            self._mixin()._handle_cancel_all("stop everything"))
        self.assertIs(got, True)
        self.assertIn(("assistant", "Stopped."), self.spoken)


class _FakeCaption:
    def __init__(self, text=""):
        self._t = text

    def text(self):
        return self._t

    def setText(self, t):
        self._t = t


@unittest.skipUnless(_QT, "PySide6 needed for the mic-watch tests")
class MicWatchFailureTests(unittest.TestCase):
    """#26: mic_status() failures are counted, logged, hinted."""

    def _host(self, fail_first=99):
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
        host._mic_status_failures = 0
        host._mic_permission_blocked = lambda: False
        host._note_mic_stall = lambda s: None
        self.log_lines = []
        host._log_drop_line = lambda line: self.log_lines.append(line)

        class _AM:
            def __init__(self):
                self.calls = 0

            def mic_status(self):
                self.calls += 1
                if self.calls <= fail_first:
                    raise RuntimeError("injected mic_status failure")
                return {"state": "ok", "callback_stalled": False,
                        "mic_starved_seconds": 0.0}

        self._wsc = wsc
        self._orig_am = wsc.audio_manager
        wsc.audio_manager = _AM()
        self._orig_ls = wsc.live_session
        wsc.live_session = SimpleNamespace(is_connected=True)
        self.addCleanup(self._restore)
        return host

    def _restore(self):
        self._wsc.audio_manager = self._orig_am
        self._wsc.live_session = self._orig_ls

    def test_repeated_failures_show_hint(self):
        host = self._host()
        for _ in range(5):
            host._mic_watch_tick()
        self.assertIn("can't check whether the microphone",
                      host.caption_label.text())
        self.assertGreaterEqual(len(self.log_lines), 1)

    def test_single_failure_logs_but_shows_no_hint(self):
        host = self._host()
        host._mic_watch_tick()
        self.assertEqual(host.caption_label.text(), "Listening")
        self.assertEqual(len(self.log_lines), 1)
        self.assertIn("mic_status_failed", self.log_lines[0])

    def test_hint_clears_when_status_recovers(self):
        host = self._host()
        for _ in range(3):
            host._mic_watch_tick()
        self.assertIn("can't check whether the microphone",
                      host.caption_label.text())
        # Recovery: the same host with a healthy audio manager.
        self._wsc.audio_manager = SimpleNamespace(
            mic_status=lambda: {"state": "ok",
                                "callback_stalled": False,
                                "mic_starved_seconds": 0.0})
        host._mic_watch_tick()
        self.assertEqual(host.caption_label.text(), "Listening")
        self.assertEqual(host._mic_status_failures, 0)

    def test_success_resets_the_count(self):
        # Two failures, one healthy tick, two more failures: the
        # count restarted, so the hint (threshold 3) never fires.
        host = self._host(fail_first=2)
        for _ in range(2):
            host._mic_watch_tick()
        self.assertEqual(host.caption_label.text(), "Listening")
        host._mic_watch_tick()  # healthy: resets the count
        self.assertEqual(host._mic_status_failures, 0)
        for _ in range(2):
            host._mic_watch_tick()
        self.assertEqual(host.caption_label.text(), "Listening")


class BrowserRefusalTests(unittest.TestCase):
    """#27: the browser refusal names the one-tap way back."""

    def setUp(self):
        from core import permission_grants as _pg
        from core.funnel import tools_registry_exec_gates as _gates

        self._pg = _pg
        self._orig_check = _pg.check_tool
        gates = object.__new__(_gates.ToolRegistryExecGatesMixin)
        gates._log_activity = lambda *a, **k: None
        self.gates = gates
        self.addCleanup(self._restore)

    def _restore(self):
        self._pg.check_tool = self._orig_check

    def _refuse(self, pid):
        self._pg.check_tool = lambda name: (False, pid)
        ctx = SimpleNamespace(
            name="browser_navigate",
            _audit_gate={"value": "free", "request_id": None},
            _audited=lambda result: result)
        return self.gates._exec_permission(ctx)

    def test_browser_refusal_names_connect(self):
        result = self._refuse("browser")
        self.assertEqual(result.get("kind"), "refused")
        self.assertIn("Connect my browser", result["message"])
        self.assertIn("Settings", result["message"])

    def test_camera_refusal_unchanged(self):
        result = self._refuse("camera")
        self.assertEqual(
            result["message"],
            "Camera is switched off in Settings, so this capability "
            "was refused — nothing was run. To use it again, switch "
            "camera back on in Settings.")
        self.assertNotIn("Connect my browser", result["message"])

    def test_allowed_tool_passes_through(self):
        self._pg.check_tool = lambda name: (True, None)
        ctx = SimpleNamespace(
            name="browser_navigate",
            _audit_gate={"value": "free", "request_id": None},
            _audited=lambda result: result)
        self.assertIsNone(self.gates._exec_permission(ctx))


if __name__ == "__main__":
    unittest.main()
