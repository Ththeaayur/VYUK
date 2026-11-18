"""Mic-input recovery (v5.95 micfix) — tests for the silent,
recurring "can't hear me but chat works" failure.

Root cause fixed here: an input stream whose PortAudio
callback stopped firing (device glitch, exclusive-mode grab,
callback failure) had NO recovery — the silence guard fires
once per start, the device watcher is event-driven, session
reconnects never restart audio streams, and mic_status could
not tell a dead callback from a quiet room. The session stayed
"connected" and deaf while typed chat kept working, and a
restart (fresh stream) was the only cure — hence recurring.

Also fixed: a hung realtime send could stall the audio sender
forever (the watchdog only fires after voice WAS sent, so a
hung FIRST send left a deaf zombie with working text); and a
wake-window timeout could clobber a PTT toggle (toggle is a
click, not a hold, so isDown() is False even when toggled on).

Lab notes: sounddevice/PortAudio are absent here, so the
audio tests run against a stubbed sounddevice module (the
manager's logic, not real hardware). The wake test needs
PySide6 and skips without it. The send test stubs the
google-genai types module, so it runs with or without the SDK.
"""

import asyncio
import queue
import sys
import time
import types as _types_mod
import unittest
from pathlib import Path
from types import SimpleNamespace

TREE_ROOT = Path(__file__).resolve().parent.parent
if str(TREE_ROOT) not in sys.path:
    sys.path.insert(0, str(TREE_ROOT))
if str(TREE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(TREE_ROOT.parent))

import numpy as np

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

from core.voice import audio as audio_mod  # Phase 3 V1a-1 INTENTIONAL: audio moved to core/voice/; from core import audio would bind the package
from core.voice import audio_devices_mic as audio_mic_mod  # B33 INTENTIONAL: devices-mic mixin holds _log_mic_drops + _mic_watchdog_loop, whose DATA_DIR / watchdog-constant bindings the facade patch alone no longer steers (snapshot copies, B16 pattern) — patch both bindings below
from core import permission_grants as pg  # B9: the session-grant seam start() consults (same module object start() imports — verified by probe, no dual-import trap on this path)


class _FakeStream:
    """Stand-in for sd.InputStream/OutputStream: records
    instances so a test can count (re)opens."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        _FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class _FlakyStream(_FakeStream):
    """InputStream that fails for chosen devices — on
    construction (fail_for: the -9996 open failure) or on
    .start() (fail_start_for: the half-open shape, where
    construction succeeds and .start() raises). Class-level
    sets so a test can flip the fault mid-run (the device
    recovers). A failing construction never reaches
    _FakeStream.__init__, so it is not counted as an
    instance; a failing .start() IS (it was constructed)."""

    fail_for = set()
    fail_start_for = set()

    def __init__(self, *args, **kwargs):
        if kwargs.get("device") in _FlakyStream.fail_for:
            raise RuntimeError("PortAudio error: -9996")
        super().__init__(*args, **kwargs)

    def start(self):
        if self.kwargs.get("device") in _FlakyStream.fail_start_for:
            raise RuntimeError("PortAudio error: -9996")
        super().start()


class _FakeDefault:
    device = (0, 0)


class _FakeSD:
    InputStream = _FakeStream
    OutputStream = _FakeStream
    default = _FakeDefault()

    @staticmethod
    def query_devices(kind=None):
        devs = [{"name": "Fake Mic", "max_input_channels": 2},
                {"name": "Fake Speaker", "max_input_channels": 0}]
        if kind == "input":
            return {"name": "Fake Mic"}
        return devs


# If sounddevice was stubbed above (lab: no PortAudio), give
# the stub the same fake attributes — core.audio holds the
# same module object, so its runtime sd.* lookups resolve.
if "sounddevice" in sys.modules and not hasattr(
        sys.modules["sounddevice"], "InputStream"):
    _sdmod = sys.modules["sounddevice"]
    _sdmod.InputStream = _FakeStream
    _sdmod.OutputStream = _FakeStream
    _sdmod.query_devices = _FakeSD.query_devices
    _sdmod.default = _FakeDefault()


def _make_manager():
    m = audio_mod.AudioManager()
    m.mic_gain = 1.0
    m.is_muted = False
    return m


class MicCallbackTests(unittest.TestCase):
    def setUp(self):
        self._orig = (audio_mod.sd.InputStream,
                      audio_mod.sd.OutputStream,
                      audio_mod.sd.query_devices,
                      audio_mod.sd.default)
        audio_mod.sd.InputStream = _FakeStream
        audio_mod.sd.OutputStream = _FakeStream
        audio_mod.sd.query_devices = _FakeSD.query_devices
        audio_mod.sd.default = _FakeDefault()
        _FakeStream.instances.clear()
        self._orig_data_dir = audio_mod.DATA_DIR
        audio_mod.DATA_DIR = Path(__file__).resolve().parent / "_micfix_tmp"
        self._orig_stall = audio_mod._MIC_CALLBACK_STALL_S
        self._orig_tick = audio_mod._MIC_WATCHDOG_TICK_S
        # B33 INTENTIONAL: _log_mic_drops + _mic_watchdog_loop resolve
        # DATA_DIR / the constants from the mixin module's globals, so
        # save + redirect those bindings too (same values, same tmp dir).
        self._orig_mic_data_dir = audio_mic_mod.DATA_DIR
        audio_mic_mod.DATA_DIR = Path(__file__).resolve().parent / "_micfix_tmp"
        self._orig_mic_stall = audio_mic_mod._MIC_CALLBACK_STALL_S
        self._orig_mic_tick = audio_mic_mod._MIC_WATCHDOG_TICK_S

    def tearDown(self):
        (audio_mod.sd.InputStream,
         audio_mod.sd.OutputStream,
         audio_mod.sd.query_devices,
         audio_mod.sd.default) = self._orig
        audio_mod.DATA_DIR = self._orig_data_dir
        audio_mod._MIC_CALLBACK_STALL_S = self._orig_stall
        audio_mod._MIC_WATCHDOG_TICK_S = self._orig_tick
        # B33 INTENTIONAL: restore the mixin bindings too (see setUp).
        audio_mic_mod.DATA_DIR = self._orig_mic_data_dir
        audio_mic_mod._MIC_CALLBACK_STALL_S = self._orig_mic_stall
        audio_mic_mod._MIC_WATCHDOG_TICK_S = self._orig_mic_tick

    def test_callback_error_counted_and_stream_survives(self):
        m = _make_manager()
        # A frame the processor cannot handle must not raise
        # out of the PortAudio callback (that used to abort
        # the stream silently).
        m._mic_callback(None, 0, None, None)
        self.assertEqual(m._mic_callback_errors, 1)
        self.assertIsNotNone(m._last_callback_at)
        # And the next good frame still flows to the queue.
        frames = np.zeros((160, 1), dtype=np.int16)
        m._mic_callback(frames, 160, None, None)
        chunk = m.mic_queue.get_nowait()
        self.assertEqual(len(chunk), 320)

    def test_muted_callback_still_marks_alive(self):
        # Mute gates the QUEUE, never the callback: a muted
        # but healthy stream must not look stalled.
        m = _make_manager()
        m.is_muted = True
        frames = np.zeros((160, 1), dtype=np.int16)
        m._mic_callback(frames, 160, None, None)
        self.assertIsNotNone(m._last_callback_at)
        with self.assertRaises(queue.Empty):
            m.mic_queue.get_nowait()

    def test_stalled_stream_reopens(self):
        # No callbacks after start -> the watchdog reopens the
        # stream (new stream instances appear) and recording
        # continues. Short stall/tick so the test stays fast.
        audio_mod._MIC_CALLBACK_STALL_S = 0.2
        audio_mod._MIC_WATCHDOG_TICK_S = 0.05
        # B33 INTENTIONAL: the watchdog loop reads the constants from
        # the mixin module's globals, so set both bindings.
        audio_mic_mod._MIC_CALLBACK_STALL_S = 0.2
        audio_mic_mod._MIC_WATCHDOG_TICK_S = 0.05
        m = _make_manager()
        # B9: the start() gate (PF-18 D2 first-run consequence) reads
        # microphone denied on a fresh lab tree with no grants store,
        # so input never opens and the watchdog has nothing to reopen
        # (pre-fix: 1 stream instance, output only). The precondition
        # is the production "Allow once" session grant (first-use mic
        # ask, D3/D7) — in-memory only, never persisted — set here and
        # cleared in the finally below, so it is scoped to this one
        # test and cannot leak. The gate itself still runs
        # (is_mic_denied() stays True); only the session grant lets
        # the stream open. Rejected: a temp-store path override
        # (heavier, file write, broader blast radius — the funnel
        # would pass too); patching is_mic_denied (stubs the gate
        # under test — a fake pass); an honest skip (unnecessary —
        # the real path CAN run in the lab with the session grant).
        self.assertTrue(pg.grant_session_only("microphone"))
        try:
            m.start()
            self.assertEqual(len(_FakeStream.instances), 2)
            deadline = time.monotonic() + 3.0
            reopened = False
            while time.monotonic() < deadline:
                if len(_FakeStream.instances) >= 4:
                    reopened = True
                    break
                time.sleep(0.05)
            self.assertTrue(reopened,
                            "watchdog did not reopen a stalled stream")
            self.assertTrue(m.is_recording)
            self.assertIsNotNone(m._in_stream)
        finally:
            m.stop()
            pg.clear_session_grants()

    def test_start_refuses_input_without_session_grant(self):
        # B9 control: without the session grant the start() gate
        # refuses input on a fresh lab tree (no grants store, mic
        # reads denied) — output opens, input does not, and the
        # watchdog has nothing to reopen. This pins the gate the
        # test above relies on: the session grant is load-bearing,
        # not a no-op, and the denied behaviour is unchanged.
        m = _make_manager()
        try:
            m.start()
            self.assertEqual(len(_FakeStream.instances), 1)
            self.assertFalse(m.is_recording)
            self.assertIsNone(m._in_stream)
        finally:
            m.stop()

    def test_mic_status_reports_stall(self):
        # A stalled stream still reads "silent" (state values
        # unchanged — the deaf hint still keys on it), but the
        # distinguishing facts ride alongside it.
        m = _make_manager()
        m._in_stream = _FakeStream()
        m._input_opened_at = time.monotonic() - 100.0
        m._last_voice_at = None
        m._last_callback_at = time.monotonic() - 100.0
        m._mic_callback_errors = 3
        st = m.mic_status()
        self.assertEqual(st["state"], "silent")
        self.assertTrue(st["callback_stalled"])
        self.assertGreaterEqual(st["callback_age_s"], 90.0)
        self.assertEqual(st["callback_errors"], 3)

    # --- Mic remake (v6.3.x): open failure / retry / guard ---

    def _grant(self):
        self.assertTrue(pg.grant_session_only("microphone"))

    def test_open_failure_records_error_and_does_not_raise(self):
        # -9996 on every candidate (resolved + both fallbacks):
        # start() must NOT raise, must leave _in_stream None /
        # is_recording False, and must record _open_error for
        # mic_status() + the watchdog retry. The old code let
        # the exception escape start() and every caller
        # swallowed it — a permanently deaf session.
        audio_mod.sd.InputStream = _FlakyStream
        _FlakyStream.fail_for = {0, None}
        m = _make_manager()
        self._grant()
        try:
            m.start()  # must not raise
            self.assertIsNone(m._in_stream)
            self.assertFalse(m.is_recording)
            self.assertIsNotNone(m._open_error)
            self.assertIn("-9996", m._open_error)
            st = m.mic_status()
            self.assertEqual(st["state"], "no_stream")
            self.assertEqual(st["open_error"], m._open_error)
        finally:
            m.stop()
            pg.clear_session_grants()
            _FlakyStream.fail_for = set()
            audio_mod.sd.InputStream = _FakeStream

    def test_open_falls_back_to_portaudio_default(self):
        # The resolved device (index 0) fails, the system
        # default IS index 0, so the chain's last link —
        # PortAudio's own default (device=None) — must open.
        audio_mod.sd.InputStream = _FlakyStream
        _FlakyStream.fail_for = {0}
        m = _make_manager()
        self._grant()
        try:
            m.start()
            self.assertIsNotNone(m._in_stream)
            self.assertTrue(m.is_recording)
            self.assertIsNone(m._open_error)
            self.assertIsNone(m._opened_device_index)
            self.assertIsNone(m._opened_device_name)
        finally:
            m.stop()
            pg.clear_session_grants()
            _FlakyStream.fail_for = set()
            audio_mod.sd.InputStream = _FakeStream

    def test_half_open_stream_is_cleaned(self):
        # Construction succeeds but .start() raises: the old
        # code assigned _in_stream before .start(), so the
        # half-open stream poisoned every later start().
        # The remake closes it and forgets it — a second
        # start() with a healthy device opens cleanly. Both
        # the resolved device AND the PortAudio-default
        # fallback must fail on .start() here, or the fallback
        # (working as designed) opens and masks the case.
        audio_mod.sd.InputStream = _FlakyStream
        _FlakyStream.fail_start_for = {0, None}
        m = _make_manager()
        self._grant()
        try:
            m.start()
            self.assertIsNone(m._in_stream)
            self.assertIsNotNone(m._open_error)
            self.assertEqual(len(_FakeStream.instances), 3)
            # The half-open input instance was closed, not left
            # dangling (the old code never closed it).
            self.assertTrue(_FakeStream.instances[0].closed)
            _FlakyStream.fail_start_for = set()
            m.start()
            self.assertIsNotNone(m._in_stream)
            self.assertTrue(m.is_recording)
            self.assertIsNone(m._open_error)
            self.assertEqual(m._opened_device_name, "Fake Mic")
        finally:
            m.stop()
            pg.clear_session_grants()
            _FlakyStream.fail_start_for = set()
            audio_mod.sd.InputStream = _FakeStream

    def test_watchdog_retries_failed_open(self):
        # A failed open arms the watchdog, which retries the
        # open (throttled) while the session is live — the
        # old watchdog skipped forever on _in_stream None.
        audio_mod.sd.InputStream = _FlakyStream
        _FlakyStream.fail_for = {0, None}
        audio_mic_mod._MIC_WATCHDOG_TICK_S = 0.05
        audio_mod._MIC_WATCHDOG_TICK_S = 0.05
        _orig_retry = audio_mic_mod._MIC_OPEN_RETRY_S
        audio_mic_mod._MIC_OPEN_RETRY_S = 0.1
        m = _make_manager()
        self._grant()
        try:
            m.start()
            self.assertIsNone(m._in_stream)
            self.assertIsNotNone(m._mic_watchdog_thread)
            self.assertTrue(m._mic_watchdog_thread.is_alive())
            # The device recovers; backdate the last attempt
            # past the throttle so the next tick retries.
            _FlakyStream.fail_for = set()
            m._open_error_at = time.monotonic() - 10.0
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if m._in_stream is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(m._in_stream,
                                 "watchdog did not retry a failed open")
            self.assertTrue(m.is_recording)
            self.assertIsNone(m._open_error)
        finally:
            m.stop()
            pg.clear_session_grants()
            _FlakyStream.fail_for = set()
            audio_mic_mod._MIC_OPEN_RETRY_S = _orig_retry
            audio_mod.sd.InputStream = _FakeStream

    def test_mic_status_stalled_with_recent_voice_is_silent(self):
        # Negative control for the old behaviour: voice crossed
        # 10 s ago, callbacks stopped since — the old code
        # read "quiet" (peak < 0.02, last voice within 20 s)
        # while no frames arrived at all. A stall is silent.
        m = _make_manager()
        m._in_stream = _FakeStream()
        m._input_opened_at = time.monotonic() - 100.0
        m._last_voice_at = time.monotonic() - 10.0
        m._last_callback_at = time.monotonic() - 100.0
        m._frames_received = 50
        st = m.mic_status()
        self.assertEqual(st["state"], "silent")
        self.assertTrue(st["callback_stalled"])

    def test_mic_status_no_frames_is_silent(self):
        # A stream opened 2 s ago that never delivered a
        # frame: the old code read "ok" for its first 6 s
        # (no voice yet, not silent yet) — exactly the window
        # a fresh deaf session hid in.
        m = _make_manager()
        m._in_stream = _FakeStream()
        m._input_opened_at = time.monotonic() - 2.0
        m._last_voice_at = None
        m._last_callback_at = time.monotonic()
        m._frames_received = 0
        st = m.mic_status()
        self.assertEqual(st["state"], "silent")

    def test_mic_status_reports_muted_field(self):
        # Mute gates the queue, not the RMS stamps — a muted
        # stream can read "ok"/"quiet" on energy while nothing
        # is transmitted, so callers must read the field.
        m = _make_manager()
        m._in_stream = _FakeStream()
        m._input_opened_at = time.monotonic()
        m._last_voice_at = time.monotonic()
        m._last_callback_at = time.monotonic()
        m._frames_received = 5
        m.is_muted = True
        st = m.mic_status()
        self.assertTrue(st["muted"])
        m.is_muted = False
        self.assertFalse(m.mic_status()["muted"])
        # And the no-stream branch carries it too.
        m2 = _make_manager()
        m2.is_muted = True
        self.assertTrue(m2.mic_status()["muted"])
        self.assertEqual(m2.mic_status()["state"], "no_stream")

    def test_silence_guard_ignores_quiet_user(self):
        # Nonzero frames are arriving (room noise / quiet
        # voice below the floor): the guard must NOT fire —
        # the old guard fired on mic_status() == "silent"
        # whenever the user had not spoken in 6.5 s.
        m = _make_manager()
        stream = _FakeStream()
        m._in_stream = stream
        m._input_opened_at = time.monotonic() - 100.0
        m._last_nonzero_at = time.monotonic() - 1.0
        m._last_voice_at = None
        m._frames_received = 100
        before = len(_FakeStream.instances)
        m._silence_guard_fired(stream, 0)
        self.assertEqual(len(_FakeStream.instances), before)
        self.assertEqual(m._silence_fallback_note, "")
        self.assertIs(m._in_stream, stream)

    def test_silence_guard_diagnoses_silent_default_without_hopping(self):
        # Exact-zero frames for 20 s on the system default:
        # diagnose once, stay on the device — the old code
        # fell through to the next enumerated input (a webcam
        # mic / dead device on the strength of a quiet room).
        m = _make_manager()
        stream = _FakeStream()
        m._in_stream = stream
        m._opened_device_name = "Fake Mic"
        m._input_opened_at = time.monotonic() - 100.0
        m._last_nonzero_at = None
        m._frames_received = 50
        before = len(_FakeStream.instances)
        m._silence_guard_fired(stream, 0)
        self.assertEqual(len(_FakeStream.instances), before)
        self.assertIs(m._in_stream, stream)
        self.assertIn("delivers silence", m._silence_fallback_note)

    def test_silence_guard_falls_back_to_default_once(self):
        # Exact-zero frames on a NON-default pick (index 1):
        # reopen once on the system default (index 0).
        m = _make_manager()
        stream = _FakeStream()
        m._in_stream = stream
        m._opened_device_name = "Fake Speaker"
        m._input_opened_at = time.monotonic() - 100.0
        m._last_nonzero_at = None
        m._frames_received = 50
        self._grant()
        try:
            m._silence_guard_fired(stream, 1)
            self.assertIsNotNone(m._in_stream)
            self.assertEqual(m._opened_device_index, 0)
            self.assertFalse(m._force_default_once)
            self.assertIn("default input", m._silence_fallback_note)
        finally:
            m.stop()
            pg.clear_session_grants()

    def test_start_registers_mic_teardown_hook(self):
        # Any mic→denied flip must tear the stream down, not
        # only flips through apply_permission_changes().
        m = _make_manager()
        try:
            self.assertIn(m.stop, pg._mic_teardown_hooks)
        finally:
            pg.unregister_mic_teardown(m.stop)


class WakeTimeoutTests(unittest.TestCase):
    def test_wake_timeout_respects_ptt_toggle(self):
        try:
            from PySide6 import QtWidgets  # noqa: F401
            # The working copy's top dir is "voice-ai-assistant"
            # (hyphens); the shipped layout renames it to
            # "voice_assistant" (underscores), which is what
            # window_session imports. Alias it in-process so
            # the import resolves here without touching the tree.
            if "voice_assistant" not in sys.modules:
                _pkg = _types_mod.ModuleType("voice_assistant")
                _pkg.__path__ = [str(TREE_ROOT)]
                sys.modules["voice_assistant"] = _pkg
            import gui.window_session as ws
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"GUI import unavailable: {e}")
        calls = []

        class FakeBtn:
            def isDown(self):
                return False

            def setProperty(self, name, value):
                calls.append((name, value))

            def setStyle(self, style):
                pass

            def style(self):
                return None

        stub_audio = SimpleNamespace(is_muted=False)
        # GUI split #11 batch 1 INTENTIONAL: _on_wake_timeout moved verbatim
        # to the suggestions module, so patch audio_manager where it is
        # bound (patching the facade binding would no longer reach it).
        import gui.window_session_suggestions as wss
        orig_audio = wss.audio_manager
        wss.audio_manager = stub_audio
        try:
            # Toggle ON: the wake window must not mute.
            stub_self = SimpleNamespace(btn_mic=FakeBtn(),
                                        _ptt_toggled_on=True)
            ws.SessionMixin._on_wake_timeout(stub_self)
            self.assertFalse(stub_audio.is_muted)
            self.assertNotIn(("active", "false"), calls)
            # Toggle OFF / hold mode: existing behavior kept.
            calls.clear()
            stub_self = SimpleNamespace(btn_mic=FakeBtn(),
                                        _ptt_toggled_on=False)
            ws.SessionMixin._on_wake_timeout(stub_self)
            self.assertTrue(stub_audio.is_muted)
            self.assertIn(("active", "false"), calls)
        finally:
            wss.audio_manager = orig_audio

    def test_wake_timeout_does_not_mute_live_session(self):
        # Mic remake (v6.3.x): the 6 s wake window is for the
        # ambient/idle state — a LIVE session owns the mic, so
        # the timeout must not re-mute mid-conversation. The
        # old code muted unconditionally 6 s after every
        # "Hey VYUK", so any utterance starting later, or any
        # pause over 6 s, silently re-muted.
        try:
            from PySide6 import QtWidgets  # noqa: F401
            if "voice_assistant" not in sys.modules:
                _pkg = _types_mod.ModuleType("voice_assistant")
                _pkg.__path__ = [str(TREE_ROOT)]
                sys.modules["voice_assistant"] = _pkg
            import gui.window_session as ws
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"GUI import unavailable: {e}")
        calls = []

        class FakeBtn:
            def isDown(self):
                return False

            def setProperty(self, name, value):
                calls.append((name, value))

            def setStyle(self, style):
                pass

            def style(self):
                return None

        class FakeWorker:
            def __init__(self, alive):
                self._alive = alive

            def is_alive(self):
                return self._alive

        stub_audio = SimpleNamespace(is_muted=False)
        import gui.window_session_suggestions as wss
        orig_audio = wss.audio_manager
        wss.audio_manager = stub_audio
        try:
            # Live worker: the timeout is a no-op.
            stub_self = SimpleNamespace(
                btn_mic=FakeBtn(), _ptt_toggled_on=False,
                worker=FakeWorker(alive=True))
            ws.SessionMixin._on_wake_timeout(stub_self)
            self.assertFalse(stub_audio.is_muted)
            self.assertNotIn(("active", "false"), calls)
            # Dead worker: the old behaviour stands (mutes).
            calls.clear()
            stub_self = SimpleNamespace(
                btn_mic=FakeBtn(), _ptt_toggled_on=False,
                worker=FakeWorker(alive=False))
            ws.SessionMixin._on_wake_timeout(stub_self)
            self.assertTrue(stub_audio.is_muted)
            self.assertIn(("active", "false"), calls)
        finally:
            wss.audio_manager = orig_audio


class SendTimeoutTests(unittest.TestCase):
    def test_hung_send_does_not_stall_draining(self):
        from core.voice import live_client as lc  # Phase 3 V1b INTENTIONAL: moved to core/voice/
        from core.voice import live_client_receive as lc_recv  # B47 INTENTIONAL: receive mixin binding for dual patches

        class Blob:
            def __init__(self, data=None, mime_type=None):
                self.data = data
                self.mime_type = mime_type

        orig_types = lc.types
        orig_timeout = lc._SEND_REALTIME_TIMEOUT_S
        lc.types = SimpleNamespace(Blob=Blob)
        lc._SEND_REALTIME_TIMEOUT_S = 0.05
        # INTENTIONAL (skills split B47): _send_audio_loop moved to
        # the receive mixin and reads types + _SEND_REALTIME_TIMEOUT_S
        # from its own globals — patch both bindings (B16 pattern,
        # same fakes, both restored).
        orig_types_recv = lc_recv.types
        orig_timeout_recv = lc_recv._SEND_REALTIME_TIMEOUT_S
        lc_recv.types = SimpleNamespace(Blob=Blob)
        lc_recv._SEND_REALTIME_TIMEOUT_S = 0.05
        try:
            async def hanging_send(audio=None):
                await asyncio.sleep(3600)

            fake_session = SimpleNamespace(
                send_realtime_input=hanging_send)
            fake_audio = SimpleNamespace(
                mic_queue=queue.Queue())
            for _ in range(3):
                fake_audio.mic_queue.put(b"\x00\x02" * 320)
            stub_self = SimpleNamespace(_running=True,
                                        _session=fake_session,
                                        _last_voice_at=None)

            async def run():
                task = asyncio.create_task(
                    lc.GeminiLiveSession._send_audio_loop(
                        stub_self, fake_audio))
                await asyncio.sleep(0.9)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            asyncio.run(run())
            # All three chunks were attempted-and-dropped on
            # timeout instead of the first one hanging the loop.
            self.assertTrue(fake_audio.mic_queue.empty())
        finally:
            lc.types = orig_types
            lc._SEND_REALTIME_TIMEOUT_S = orig_timeout
            lc_recv.types = orig_types_recv
            lc_recv._SEND_REALTIME_TIMEOUT_S = orig_timeout_recv

    def test_failed_send_counts_drops_and_drains_stale_backlog(self):
        # Mic remake (v6.3.x): a failed send used to drop one
        # chunk silently and keep going — a 15 s stall left
        # ~8 s of speech lost with no count, no log, no
        # resync. The remake counts every dropped chunk and
        # drains the stale backlog down to the last ~16 chunks
        # (~1 s) so the sender resyncs to live audio.
        from core.voice import live_client as lc
        from core.voice import live_client_receive as lc_recv

        class Blob:
            def __init__(self, data=None, mime_type=None):
                self.data = data
                self.mime_type = mime_type

        orig_types = lc.types
        orig_timeout = lc._SEND_REALTIME_TIMEOUT_S
        lc.types = SimpleNamespace(Blob=Blob)
        lc._SEND_REALTIME_TIMEOUT_S = 0.05
        orig_types_recv = lc_recv.types
        orig_timeout_recv = lc_recv._SEND_REALTIME_TIMEOUT_S
        lc_recv.types = SimpleNamespace(Blob=Blob)
        lc_recv._SEND_REALTIME_TIMEOUT_S = 0.05
        try:
            async def hanging_send(audio=None):
                await asyncio.sleep(3600)

            fake_session = SimpleNamespace(
                send_realtime_input=hanging_send)
            fake_audio = SimpleNamespace(
                mic_queue=queue.Queue())
            for _ in range(20):
                fake_audio.mic_queue.put(b"\x00\x02" * 320)
            stub_self = SimpleNamespace(_running=True,
                                        _session=fake_session,
                                        _last_voice_at=None)

            async def run():
                task = asyncio.create_task(
                    lc.GeminiLiveSession._send_audio_loop(
                        stub_self, fake_audio))
                await asyncio.sleep(0.9)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            asyncio.run(run())
            # The first failed send drained the backlog to 16;
            # later failures consumed chunks one by one. The
            # queue must never hold a stale backlog again, and
            # the drop count must be nonzero and honest.
            self.assertLessEqual(fake_audio.mic_queue.qsize(), 16)
            self.assertGreaterEqual(
                getattr(stub_self, "_send_drop_count", 0), 4)
        finally:
            lc.types = orig_types
            lc._SEND_REALTIME_TIMEOUT_S = orig_timeout
            lc_recv.types = orig_types_recv
            lc_recv._SEND_REALTIME_TIMEOUT_S = orig_timeout_recv


class _OutFakeDefault:
    device = (0, 1)  # (default input, default output: "Speakers A")


class _OutFakeSD:
    """Two speakers + one mic, in sounddevice order (field-check
    fix #12, M11a): the picker/reroute tests need a second
    output the saved name can have moved to."""

    DEVS = [
        {"name": "Fake Mic", "max_input_channels": 2,
         "max_output_channels": 0},
        {"name": "Speakers A", "max_input_channels": 0,
         "max_output_channels": 2},
        {"name": "Speakers B", "max_input_channels": 0,
         "max_output_channels": 2},
    ]

    @staticmethod
    def query_devices(kind=None):
        if kind == "input":
            return {"name": "Fake Mic"}
        if kind == "output":
            return {"name": "Speakers A"}
        return [dict(d) for d in _OutFakeSD.DEVS]


class OutputDeviceTests(unittest.TestCase):
    """Field-check fix #12 (M11a/M11c): the speaker picker —
    resolve by name, open the resolved device, and say plainly
    that the front-end processors do not touch the mic audio."""

    def setUp(self):
        self._orig = (audio_mod.sd.InputStream,
                      audio_mod.sd.OutputStream,
                      audio_mod.sd.query_devices,
                      audio_mod.sd.default)
        audio_mod.sd.InputStream = _FakeStream
        audio_mod.sd.OutputStream = _FakeStream
        audio_mod.sd.query_devices = _OutFakeSD.query_devices
        audio_mod.sd.default = _OutFakeDefault()
        _FakeStream.instances.clear()
        _FlakyStream.fail_for = set()
        _FlakyStream.fail_start_for = set()

    def tearDown(self):
        (audio_mod.sd.InputStream,
         audio_mod.sd.OutputStream,
         audio_mod.sd.query_devices,
         audio_mod.sd.default) = self._orig
        _FakeStream.instances.clear()
        _FlakyStream.fail_for = set()
        _FlakyStream.fail_start_for = set()

    def _manager(self):
        m = audio_mod.AudioManager()
        m.mic_gain = 1.0
        m.is_muted = False
        return m

    def test_resolve_output_intact_pick(self):
        m = self._manager()
        index, name, note = m.resolve_saved_output(2, "Speakers B")
        self.assertEqual((index, name, note), (2, "Speakers B", ""))

    def test_resolve_output_moved_name(self):
        m = self._manager()
        index, name, note = m.resolve_saved_output(0, "Speakers B")
        self.assertEqual(index, 2)
        self.assertEqual(name, "Speakers B")
        self.assertIn("moved", note)

    def test_resolve_output_gone_pick_falls_back_to_default(self):
        m = self._manager()
        index, name, note = m.resolve_saved_output(9, "Gone Speaker")
        self.assertEqual(index, 1)
        self.assertEqual(name, "Speakers A")
        self.assertIn("not available", note)

    def test_resolve_output_no_pick_is_portaudio_default(self):
        m = self._manager()
        index, name, note = m.resolve_saved_output(None, None)
        self.assertEqual((index, name, note), (None, None, ""))

    def test_try_open_output_reresolves_stale_index_by_name(self):
        m = self._manager()
        m.output_device = 0
        m.output_device_name = "Speakers B"
        self.assertTrue(m._try_open_output())
        self.assertEqual(_FakeStream.instances[-1].kwargs.get("device"), 2)
        self.assertEqual(m.output_device, 2)
        self.assertEqual(m.output_device_name, "Speakers B")

    def test_try_open_output_no_pick_opens_default(self):
        m = self._manager()
        m.output_device = None
        m.output_device_name = None
        self.assertTrue(m._try_open_output())
        self.assertIsNone(_FakeStream.instances[-1].kwargs.get("device"))

    def test_try_open_output_failed_pick_falls_back_to_default(self):
        m = self._manager()
        audio_mod.sd.OutputStream = _FlakyStream
        _FlakyStream.fail_for = {2}
        m.output_device = 2
        m.output_device_name = "Speakers B"
        self.assertTrue(m._try_open_output())
        self.assertIsNone(_FakeStream.instances[-1].kwargs.get("device"))

    def test_front_end_status_says_processors_do_not_touch_mic(self):
        m = self._manager()
        status = m.front_end_status()
        aec = status["aec"]
        self.assertIs(aec["applied_to_mic"], False)
        self.assertIn("not running", aec["note"])
        ns = status["noise_suppression"]
        self.assertIs(ns["applied_to_mic"], False)
        self.assertIn("raw mic signal", ns["note"])
        # The tapped branch says the same thing: the reference
        # is collected, the mic audio is still never cleaned.
        m.tap_render_reference(
            (np.zeros(4800, dtype=np.int16)).tobytes())
        tapped = m.front_end_status()["aec"]
        self.assertIs(tapped["applied_to_mic"], False)
        self.assertIn("not running", tapped["note"])

    def test_mic_audio_byte_identical_with_aec_tap(self):
        rng = np.random.default_rng(7)
        pcm = rng.integers(-20000, 20000, 29760, dtype=np.int16)

        def _feed(tap):
            m = self._manager()
            if tap:
                m.tap_render_reference(
                    (np.zeros(4800, dtype=np.int16)).tobytes())
            for i in range(0, len(pcm) - 1023, 1024):
                chunk = pcm[i:i + 1024].reshape(-1, 1)
                m._mic_callback_impl(chunk, 1024, None, None)
            out = b""
            while not m.mic_queue.empty():
                out += m.mic_queue.get_nowait()
            return out

        plain = _feed(False)
        tapped = _feed(True)
        self.assertTrue(len(plain) > 0)
        self.assertEqual(plain, tapped)


if __name__ == "__main__":
    unittest.main()
