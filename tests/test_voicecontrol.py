"""Voice-control bug audit + fix batch (2026-11-16) — tests.

Covers the behaviours fixed in this batch, one class per audit item,
so a future regression names the bug it re-opens:

- C3: the three file-writing tools (audio_to_midi, organise_photos,
  sync_folders_two_way) are bound to the Files permission.
- C2: use_toolbox returns the inner funnel dict for every
  non-success outcome (never a success-shaped string).
- B1: continuous send failure arms the Live watchdog.
- B2: the receive loop ends the session after repeated empty
  receive() re-entries instead of busy-spinning on a closed session.
- B3/B4: stop() clears the session reference; _run_session resets
  stale per-session state at entry.
- V1/V2: short utterances flush after 2 s of trailing silence;
  a partial utterance flushes when the loop exits.
- V5: transcribe_with_words passes initial_prompt / thresholds
  through to faster-whisper (the one shared STT call).
- V9: the hallucination gate uses peak-window RMS, not the
  whole-utterance mean.
- O3/O4: odd-length PCM never raises out of enqueue_playback; the
  playback queue is bounded and drops the oldest block.
- O7: the _play_pcm hold ends when the enqueued blocks drain,
  not when the characters-per-second guess expires.
- C4: Live staleness covers raw-point click_anywhere / browser_click;
  a confirmed raw-point click actually clicks.
- P1/P2: the Win32 PTT poll never fires a stop into shutdown and
  never swallows a re-press inside the poll window.
- P6: flipping the PTT mode never strands an open mic.
- Section 10: the OS microphone denial is surfaced (doctor verdict
  os_denied; probe separates "no default" from "enumeration failed").

Qt-gated classes (B9, C5, P6) skip when PySide6 is absent, following
the test_voice_drop.py precedent. The mic-loop harness below is a
minimal copy of tests/test_mic_loops.py's _FakeMic/_FakeSession/
_drive (same chunk counts, same LOUD/QUIET levels) so these tests
stay independent of that file.
"""

import asyncio
import collections
import pathlib
import queue
import sys
import threading
import time
import types as _types_mod
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

# Lab has no PortAudio: stub sounddevice before any core.voice.audio
# import, exactly as tests/test_micfix.py does.
try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

# Synthetic voice_assistant package (the test_livefix /
# test_voice_drop precedent): the tree layout is top-level core/ +
# gui/, so the alias maps voice_assistant -> tree root. The gui
# mixins import voice_assistant.core.gui_facade.
_ROOT = pathlib.Path(__file__).resolve().parent.parent
if "voice_assistant" not in sys.modules:
    _pkg = _types_mod.ModuleType("voice_assistant")
    _pkg.__path__ = [str(_ROOT)]
    sys.modules["voice_assistant"] = _pkg
if "voice_assistant.core" not in sys.modules:
    _core = _types_mod.ModuleType("voice_assistant.core")
    _core.__path__ = [str(_ROOT / "core")]
    sys.modules["voice_assistant.core"] = _core
if "voice_assistant.gui" not in sys.modules:
    _gui = _types_mod.ModuleType("voice_assistant.gui")
    _gui.__path__ = [str(_ROOT / "gui")]
    sys.modules["voice_assistant.gui"] = _gui

from core.config import config
from core.voice import mic_loop
from core.voice.local_client_vad import (
    VAD_MIN_SPEECH_S,
    VAD_TRAILING_SILENCE_S,
    _SHORT_UTTERANCE_SILENCE_S,
)


# ---------------------------------------------------------------------------
# C3: the three file-writing tools are bound to the Files permission
# ---------------------------------------------------------------------------

class TestC3FilesScopeBinding(unittest.TestCase):
    """PF-18 closure: audio_to_midi / organise_photos /
    sync_folders_two_way were registered, confirm-gated and
    file-writing but had no entry in FILES_TOOL_NAMES, so
    check_tool() let them through with no permission at all."""

    def setUp(self):
        import pathlib
        import tempfile

        from core import permission_grants as pg

        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)

    def tearDown(self):
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def test_three_tools_denied_when_files_denied(self):
        self.assertTrue(
            self.pg.set_permission_state("files", "denied"))
        for tool in ("audio_to_midi", "organise_photos",
                     "sync_folders_two_way"):
            allowed, pid = self.pg.check_tool(tool)
            self.assertFalse(allowed, tool)
            self.assertEqual(pid, "files", tool)

    def test_three_tools_allowed_when_files_granted(self):
        self.assertTrue(
            self.pg.set_permission_state("files", "granted"))
        for tool in ("audio_to_midi", "organise_photos",
                     "sync_folders_two_way"):
            allowed, _pid = self.pg.check_tool(tool)
            self.assertTrue(allowed, tool)


# ---------------------------------------------------------------------------
# C2: use_toolbox returns the inner dict, never a success-shaped string
# ---------------------------------------------------------------------------

class _FakeRegistry:
    def __init__(self, outcome):
        self._outcome = outcome

    def get_declarations(self):
        return [{"name": "some_tool"}]

    def execute(self, name, args):
        return dict(self._outcome)


class TestC2ToolboxDemotion(unittest.TestCase):
    """The funnel wraps a string tool return as success, so the old
    \"{name} did not run: ...\" text told Gemini (and the receipt
    ledger) that a denial, validation failure, timeout or handler
    error SUCCEEDED."""

    def _call(self, outcome):
        from core.funnel.toolbox_findcall import _call

        return _call(_FakeRegistry(outcome), "some_tool", {})

    def test_denied_returns_dict(self):
        out = self._call({"status": "error", "kind": "denied",
                          "message": "Files is denied"})
        self.assertIsInstance(out, dict)
        self.assertEqual(out["kind"], "denied")
        self.assertEqual(out["status"], "error")

    def test_validation_error_returns_dict(self):
        out = self._call({"status": "error", "kind": "validation",
                          "message": "bad args",
                          "schema": {"type": "object"}})
        self.assertIsInstance(out, dict)
        self.assertEqual(out["kind"], "validation")
        self.assertEqual(out["schema"], {"type": "object"})

    def test_timeout_and_handler_error_return_dicts(self):
        for kind in ("timeout", "handler_error"):
            out = self._call({"status": "error", "kind": kind,
                              "message": "boom"})
            self.assertIsInstance(out, dict)
            self.assertEqual(out["kind"], kind)

    def test_success_still_returns_text(self):
        out = self._call({"status": "success", "result": "done it"})
        self.assertEqual(out, "done it")

    def test_unknown_tool_returns_error_dict(self):
        from core.funnel.toolbox_findcall import _call

        out = _call(_FakeRegistry({"status": "success"}),
                    "no_such_tool_xyz", {})
        self.assertIsInstance(out, dict)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["kind"], "unknown_tool")


# ---------------------------------------------------------------------------
# V5: STT kwargs pass through the one shared call
# ---------------------------------------------------------------------------

class TestV5SttKwargs(unittest.TestCase):
    """transcribe_with_words is the ONE shared STT call — the live
    sessions' _transcribe methods call it through transcribe_text
    with _transcription_settings(), so the locked language, beam
    and biasing prompt must reach faster-whisper exactly."""

    def test_initial_prompt_and_thresholds_reach_whisper(self):
        from core.voice import asr

        seen = {}

        class _FakeModel:
            def transcribe(self, audio, **kwargs):
                seen.update(kwargs)
                seg = SimpleNamespace(text="hello", start=0.0,
                                      end=0.5, words=[])
                info = SimpleNamespace(language="en")
                return [seg], info

        with mock.patch.object(asr, "faster_whisper_available",
                               return_value=True), \
                mock.patch.object(asr, "get_whisper_model",
                                  return_value=_FakeModel()):
            out = asr.transcribe_with_words(
                b"\x00\x00" * 1600,
                initial_prompt="test prompt",
                no_speech_threshold=0.11,
                log_prob_threshold=-0.22)
        self.assertEqual(out["text"], "hello")
        self.assertEqual(seen["initial_prompt"], "test prompt")
        self.assertEqual(seen["no_speech_threshold"], 0.11)
        self.assertEqual(seen["log_prob_threshold"], -0.22)

    def test_defaults_come_from_module_constants(self):
        from core.voice import asr

        seen = {}

        class _FakeModel:
            def transcribe(self, audio, **kwargs):
                seen.update(kwargs)
                seg = SimpleNamespace(text="hi", start=0.0,
                                      end=0.2, words=[])
                info = SimpleNamespace(language="en")
                return [seg], info

        with mock.patch.object(asr, "faster_whisper_available",
                               return_value=True), \
                mock.patch.object(asr, "get_whisper_model",
                                  return_value=_FakeModel()):
            asr.transcribe_with_words(b"\x00\x00" * 1600)
        self.assertEqual(
            seen["no_speech_threshold"],
            asr.FASTER_WHISPER_NO_SPEECH_THRESHOLD)
        self.assertEqual(
            seen["log_prob_threshold"],
            asr.FASTER_WHISPER_LOG_PROB_THRESHOLD)


# ---------------------------------------------------------------------------
# V9: hallucination gate uses peak-window RMS
# ---------------------------------------------------------------------------

class TestV9MaxChunkRms(unittest.TestCase):
    """A loud word inside a mostly-silent utterance was diluted
    below the gate by the whole-utterance mean and dropped as a
    hallucination. The gate now reads the loudest 64 ms window."""

    def test_loud_word_in_silence_is_not_a_hallucination(self):
        from core.voice.local_client_mic import LocalLiveSessionMicMixin
        from core.voice.local_client_render import (
            _is_probable_hallucination)

        mixin = LocalLiveSessionMicMixin.__new__(
            LocalLiveSessionMicMixin)
        loud = np.full(1024, 3000, dtype=np.int16).tobytes()
        quiet = np.full(1024 * 20, 50, dtype=np.int16).tobytes()
        pcm = loud + quiet
        rms = mixin._max_chunk_rms(pcm)
        self.assertGreater(rms, 1000.0)
        self.assertFalse(_is_probable_hallucination("thank you", rms))

    def test_pure_silence_still_gates(self):
        from core.voice.local_client_mic import LocalLiveSessionMicMixin
        from core.voice.local_client_render import (
            _is_probable_hallucination)

        mixin = LocalLiveSessionMicMixin.__new__(
            LocalLiveSessionMicMixin)
        pcm = np.full(1024 * 10, 50, dtype=np.int16).tobytes()
        rms = mixin._max_chunk_rms(pcm)
        self.assertLess(rms, 200.0)
        self.assertTrue(_is_probable_hallucination("thank you", rms))


class TestM10HallucinationShapes(unittest.TestCase):
    """Field-check fix #11 (M10a): the exact-phrase gate cannot
    catch what Whisper invents from silence/hum — a bare app
    name ("Notepad.com" from room hum at RMS 499.6) or a looping
    app-name list (digital silence). The structural rules catch
    those shapes, still only when the utterance is quiet (peak
    RMS under twice the 400 floor); a real short command with a
    verb still dispatches, quiet or not."""

    def test_looping_app_names_are_a_hallucination(self):
        from core.voice.local_client_render import (
            _is_probable_hallucination)
        text = ("Notepad, Calculator, File Explorer, Notepad, "
                "Calculator, File Explorer, Notepad, Calculator, "
                "File Explorer,")
        self.assertTrue(_is_probable_hallucination(text, 100.0))

    def test_bare_domain_app_name_is_a_hallucination(self):
        from core.voice.local_client_render import (
            _is_probable_hallucination)
        # The field check's hum shape: RMS 499.6, under 2x floor.
        self.assertTrue(_is_probable_hallucination("Notepad.com", 499.6))

    def test_bare_app_name_is_a_hallucination(self):
        from core.voice.local_client_render import (
            _is_probable_hallucination)
        self.assertTrue(_is_probable_hallucination("Notepad", 100.0))
        self.assertTrue(
            _is_probable_hallucination("File Explorer", 100.0))
        self.assertTrue(
            _is_probable_hallucination("Notepad, Calculator", 100.0))

    def test_bare_word_list_is_a_hallucination(self):
        from core.voice.local_client_render import (
            _is_probable_hallucination)
        # The tiny-model silence shape from the field check.
        self.assertTrue(_is_probable_hallucination(
            "VYUK, open, close, screenshot,", 100.0))

    def test_real_short_commands_are_not_hallucinations(self):
        from core.voice.local_client_render import (
            _is_probable_hallucination)
        for text in (
                "open Notepad",
                "what time is it",
                "play music",
                "stop",
                "yes",
                "weather today",
                "set a timer",
                "turn on the lights, play music",
                "remind me to call mum",
        ):
            self.assertFalse(
                _is_probable_hallucination(text, 100.0),
                f"real turn dropped as hallucination: {text!r}")

    def test_loud_app_name_is_not_a_hallucination(self):
        from core.voice.local_client_render import (
            _is_probable_hallucination)
        # The quiet half: a loud "Notepad" is a real turn.
        self.assertFalse(_is_probable_hallucination("Notepad", 1500.0))
        self.assertFalse(_is_probable_hallucination(
            "Notepad, Calculator, File Explorer, Notepad, "
            "Calculator, File Explorer,", 1500.0))


# ---------------------------------------------------------------------------
# V1/V2: mic-loop utterance flushing (minimal test_mic_loops harness)
# ---------------------------------------------------------------------------

def _pcm(rms_value, n_samples=1024):
    return np.full(n_samples, int(rms_value), dtype=np.int16).tobytes()


class _CountingQueue(queue.Queue):
    def __init__(self, on_take=None):
        super().__init__()
        self._on_take = on_take
        self._takes = 0

    def get_nowait(self):
        item = super().get_nowait()
        self._takes += 1
        if self._on_take is not None:
            self._on_take(self._takes)
        return item


class _FakeMic:
    def __init__(self, chunks, on_take=None):
        self.mic_queue = _CountingQueue(on_take=on_take)
        for c in chunks:
            self.mic_queue.put(c)


class _FakeLoopSession:
    def __init__(self):
        self.speaking = False
        self.interrupts = 0
        self.utterances = []
        self.silences = []
        self._stop = False

    def running(self):
        return not self._stop

    def is_speaking(self):
        return self.speaking

    def set_speaking(self, value):
        self.speaking = bool(value)

    def interrupt(self):
        self.interrupts += 1

    async def on_utterance(self, pcm, trailing_silence_s=0.0):
        self.utterances.append(pcm)
        self.silences.append(trailing_silence_s)

    def stop(self):
        self._stop = True


def _drive_loop(chunks, session, estimator=None, pre_roll=None,
                should_stop=None, settle_s=0.3):
    async def _run():
        task = asyncio.create_task(mic_loop.run_mic_loop(
            _FakeMic(chunks),
            running=session.running,
            is_speaking=session.is_speaking,
            set_speaking=session.set_speaking,
            interrupt_playback=session.interrupt,
            on_utterance=session.on_utterance,
            estimator=estimator, pre_roll=pre_roll,
            should_stop=should_stop))
        await asyncio.sleep(settle_s)
        session.stop()
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(_run())


_VC_LOUD = _pcm(1000)
_VC_QUIET = _pcm(100)
_CHUNK_S = config.chunk_size / config.sample_rate_in


class TestV1ShortUtteranceFlush(unittest.TestCase):
    """A sub-0.35 s utterance ("yes", "stop") used to merge into
    the NEXT turn — it never dispatched on its own. After 2 s of
    trailing silence it now flushes as its own utterance."""

    def test_short_burst_then_silence_dispatches(self):
        session = _FakeLoopSession()
        # 2 loud chunks (~0.13 s, below VAD_MIN_SPEECH_S) then
        # 2.0 s of quiet — the short-utterance flush fires.
        n_quiet = int(-(-_SHORT_UTTERANCE_SILENCE_S // _CHUNK_S)) + 2
        chunks = [_VC_LOUD] * 2 + [_VC_QUIET] * n_quiet
        _drive_loop(chunks, session, settle_s=2.6)
        self.assertEqual(len(session.utterances), 1)
        self.assertGreaterEqual(session.silences[0], 2.0)


class TestV2ExitFlush(unittest.TestCase):
    """A partial utterance in flight when the loop exits was
    dropped silently. The loop now flushes it on exit."""

    def test_partial_utterance_flushes_on_loop_exit(self):
        session = _FakeLoopSession()
        # 6 loud chunks (~0.38 s, at/above VAD_MIN_SPEECH_S) then
        # only 2 quiet chunks — not enough trailing silence to
        # flush during the run, so only the exit flush can save it.
        chunks = [_VC_LOUD] * 6 + [_VC_QUIET] * 2
        _drive_loop(chunks, session, settle_s=0.3)
        self.assertEqual(len(session.utterances), 1)


# ---------------------------------------------------------------------------
# O3/O4: playback queue is bounded and never raises on odd PCM
# ---------------------------------------------------------------------------

class TestO3O4PlaybackQueue(unittest.TestCase):
    """enqueue_playback used to raise out of np.frombuffer on
    odd-length PCM, killing the live receive loop; and the
    playback queue was unbounded, so a fast model could queue
    minutes of audio behind a slow speaker."""

    def _mixin(self):
        from core.voice.audio_streams_playback import (
            AudioStreamsPlaybackMixin)

        m = AudioStreamsPlaybackMixin.__new__(
            AudioStreamsPlaybackMixin)
        m.playback_queue = queue.Queue(maxsize=2)
        m.chunk_size = 1024
        m._sentence_fifo = collections.deque()
        m._playback_odd_chunks = 0
        m.playback_dropped_blocks = 0
        m._playback_log_n = 0
        return m

    def test_odd_length_pcm_does_not_raise(self):
        m = self._mixin()
        blocks = m.enqueue_playback(b"\x01\x02\x03")
        self.assertGreaterEqual(blocks, 0)
        self.assertEqual(m._playback_odd_chunks, 1)

    def test_empty_pcm_returns_zero(self):
        m = self._mixin()
        self.assertEqual(m.enqueue_playback(b""), 0)

    def test_bounded_queue_drops_oldest_and_counts(self):
        m = self._mixin()
        # Each 1024-sample chunk is one block at the test chunk
        # size; three blocks into a maxsize-2 queue drops one.
        one = np.zeros(1024, dtype=np.int16).tobytes()
        m.enqueue_playback(one)
        m.enqueue_playback(one)
        m.enqueue_playback(one)
        self.assertEqual(m.playback_queue.qsize(), 2)
        self.assertEqual(m.playback_dropped_blocks, 1)


# ---------------------------------------------------------------------------
# O7: _play_pcm hold ends when the blocks drain
# ---------------------------------------------------------------------------

class TestO7PlayPcmHold(unittest.TestCase):
    """The _play_pcm hold used to be a characters-per-second guess,
    so LISTENING flipped while she was still speaking. The hold
    now ends when the playback queue drains back to its
    pre-enqueue backlog."""

    def test_hold_ends_when_queue_drains(self):
        from core.voice.local_client_speech import (
            LocalLiveSessionSpeechMixin)

        playback_q = queue.Queue()
        calls = []

        class _FakeAudio:
            def __init__(self):
                self.playback_queue = playback_q
                self.chunk_size = 1024

            def enqueue_playback(self, pcm):
                calls.append(pcm)
                playback_q.put(pcm)
                return 1

        ns = SimpleNamespace(
            on_audio_data=None,
            _backend="builtin",
            _speaking=True,
            _running=True,
            _karaoke_pending=None,
            _audio_manager=_FakeAudio(),
        )

        # A background drainer consumes the queue at the audio
        # clock rate, as the real playback callback does.
        stop = threading.Event()

        def _drain():
            while not stop.is_set():
                try:
                    playback_q.get(timeout=0.01)
                except queue.Empty:
                    pass

        drainer = threading.Thread(target=_drain, daemon=True)
        drainer.start()
        try:
            # 2 s of PCM: the old chars/sec guess would hold the
            # full 2 s; the drain gate ends it once the single
            # enqueued block leaves the queue.
            pcm = b"\x00\x00" * (2 * config.sample_rate_out)
            started = time.monotonic()
            asyncio.run(
                LocalLiveSessionSpeechMixin._play_pcm(ns, pcm))
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            drainer.join(timeout=2.0)
        self.assertEqual(len(calls), 1)
        self.assertLess(elapsed, 1.0)


# ---------------------------------------------------------------------------
# C4: Live staleness covers raw-point actions; the confirm resolver clicks
# ---------------------------------------------------------------------------

class TestC4LiveStaleness(unittest.TestCase):
    """A raw-point click on Live used to bypass the staleness check
    entirely (is_live_point_action covered only cursor_control /
    mouse_control), so the model could click a point from a stale
    screenshot. Named rungs re-resolve against the live view, so
    only the raw rung is in scope."""

    def test_click_anywhere_raw_point_is_live_point_action(self):
        from core.desktop.live_staleness import is_live_point_action

        self.assertTrue(is_live_point_action(
            "click_anywhere",
            {"action": "click", "target": "640,480", "by": "point"}))
        self.assertTrue(is_live_point_action(
            "click_anywhere",
            {"action": "type", "target": "640,480", "by": "point"}))

    def test_click_anywhere_named_rung_is_not(self):
        from core.desktop.live_staleness import is_live_point_action

        self.assertFalse(is_live_point_action(
            "click_anywhere",
            {"action": "click", "target": "Submit", "by": "dom"}))

    def test_browser_click_raw_point_is_live_point_action(self):
        from core.desktop.live_staleness import is_live_point_action

        self.assertTrue(is_live_point_action(
            "browser_click",
            {"by": "point", "target": "100,200", "kind": "hover"}))

    def test_malformed_point_never_matches(self):
        from core.desktop.live_staleness import is_live_point_action

        self.assertFalse(is_live_point_action(
            "click_anywhere",
            {"action": "click", "target": "somewhere", "by": "point"}))
        self.assertFalse(is_live_point_action(
            "click_anywhere",
            {"action": "click", "target": "640.5,480", "by": "point"}))


class TestC4ClickAnywhereConfirm(unittest.TestCase):
    """The live-route point branch used to return {"status": "ask"}
    with no confirm parameter to re-issue with, so the model
    re-issued identically and asked forever. confirm=true now
    performs the visible click."""

    def test_ask_first_then_confirm_clicks(self):
        from core import click_anywhere_tool as _ca

        ask = _ca.click_anywhere(
            "click", target="640,480", by="point", name="Next",
            route="desktop")
        self.assertEqual(ask["status"], "ask")
        self.assertIn("confirm=true", ask.get("report", ""))

        with mock.patch.object(_ca, "visible_click",
                               return_value=(True, {"point": (640, 480)})) as vc:
            done = _ca.click_anywhere(
                "click", target="640,480", by="point", name="Next",
                route="desktop", confirm=True)
        self.assertEqual(done["status"], "clicked")
        vc.assert_called_once()
        self.assertEqual(vc.call_args.args[:2], (640, 480))

    def test_failed_visible_click_reports_error(self):
        from core import click_anywhere_tool as _ca

        with mock.patch.object(_ca, "visible_click",
                               return_value=(False, {
                                   "reason": "no cursor"})):
            done = _ca.click_anywhere(
                "click", target="640,480", by="point", name="Next",
                route="desktop", confirm=True)
        self.assertFalse(done["ok"])
        self.assertIn("visible-click-failed", done.get("rule", ""))

    def test_confirm_string_false_does_not_click(self):
        from core import click_anywhere_tool as _ca

        with mock.patch.object(_ca, "visible_click",
                               return_value=(True, {"point": (640, 480)})) as vc:
            out = _ca.click_anywhere(
                "click", target="640,480", by="point", name="Next",
                route="desktop", confirm="false")
        self.assertEqual(out["status"], "ask")
        vc.assert_not_called()


# ---------------------------------------------------------------------------
# P1/P2: Win32 PTT poll
# ---------------------------------------------------------------------------

class TestP1P2HotkeyPoll(unittest.TestCase):
    """P2: a re-press inside the 50 ms poll window was swallowed —
    WM_HOTKEY saw _ptt_active still True and ignored it, so the
    user held re-pressed keys and got a muted mic until release +
    re-press. P1: the poll could fire on_ptt_stop into shutdown."""

    def _hotkeys(self):
        from core.desktop import hotkeys

        hk = hotkeys.HotkeyDaemon.__new__(hotkeys.HotkeyDaemon)
        hk._running = True
        hk._ptt_active = True
        hk._summon_active = False
        hk._panic_active = False
        hk.on_ptt_start = lambda: None
        hk.on_ptt_stop = lambda: None
        return hk

    def test_repress_inside_poll_window_is_not_swallowed(self):
        import ctypes

        hk = self._hotkeys()
        stops = []
        hk.on_ptt_stop = lambda: stops.append(1)
        # _held() reads GetAsyncKeyState once per VK (Ctrl, Shift,
        # 'N') and all() short-circuits on the first False, so the
        # script is per-VK-call: [not-held (1 call), re-check all
        # three held (3 calls), not-held (1), re-check not-held (1)].
        script = iter([False, True, True, True, False, False])
        calls = []

        class _User32:
            def GetAsyncKeyState(self, vk):
                calls.append(vk)
                return 0x8000 if next(script) else 0

        real_windll = getattr(ctypes, "windll", None)
        ctypes.windll = SimpleNamespace(user32=_User32())
        try:
            hk._poll_ptt_release_win32()
        finally:
            if real_windll is None:
                del ctypes.windll
            else:
                ctypes.windll = real_windll
        # The re-press continued the same hold (no stop fired
        # for it); the final release fired exactly one stop.
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(stops), 1)
        self.assertFalse(hk._ptt_active)

    def test_poll_does_not_fire_stop_after_shutdown(self):
        import ctypes

        hk = self._hotkeys()
        stops = []
        hk.on_ptt_stop = lambda: stops.append(1)

        class _User32:
            def GetAsyncKeyState(self, vk):
                return 0

        real_windll = getattr(ctypes, "windll", None)
        ctypes.windll = SimpleNamespace(user32=_User32())
        try:
            hk._running = False
            hk._poll_ptt_release_win32()
        finally:
            if real_windll is None:
                del ctypes.windll
            else:
                ctypes.windll = real_windll
        self.assertEqual(stops, [])
        self.assertTrue(hk._ptt_active)


# ---------------------------------------------------------------------------
# Section 10: OS microphone denial (doctor + probe separation)
# ---------------------------------------------------------------------------

class TestSection10DoctorOsDenied(unittest.TestCase):
    """The mic doctor used to report the device healthy while
    Windows itself had the microphone off for desktop apps — the
    user heard nothing and VYUK said nothing was wrong. An OS
    denial now judges first, before any device check."""

    def test_os_denied_verdict(self):
        from core.voice.mic_doctor import diagnose

        out = diagnose(os_state="denied")
        self.assertEqual(out["verdict"], "os_denied")
        self.assertIn("Microphone", out["sentence"])

    def test_os_unknown_changes_nothing(self):
        from core.voice.mic_doctor import diagnose

        out = diagnose(os_state="unknown")
        self.assertNotEqual(out["verdict"], "os_denied")

    def test_run_diagnosis_carries_os_microphone_key(self):
        from core.voice.mic_doctor import run_diagnosis

        out = run_diagnosis(os_state="denied")
        self.assertEqual(out["verdict"], "os_denied")
        self.assertEqual(out["os_microphone"], "denied")


class TestSection10ProbeSeparation(unittest.TestCase):
    """probe_microphone_device used to collapse "no default
    microphone configured" and "enumeration failed" into the same
    UNKNOWN — the user could not tell a missing device from a
    broken one. The injected-reader seam still reads UNKNOWN
    (it carries no E_NOTFOUND signal); the mmdevice detail is
    exercised through default_capture_endpoint_detail."""

    def test_injected_reader_none_stays_unknown(self):
        from core.platform_permission_probe import (
            probe_microphone_device)

        out = probe_microphone_device(device_reader=lambda: None)
        self.assertEqual(out["device"], "unknown")

    def test_detail_on_linux_is_unavailable(self):
        # Lab runs on Linux: no mmdevice, so the detail is the
        # honest "unavailable" — the E_NOTFOUND branch is
        # Windows-only and field-owed.
        from core.mmdevice import default_capture_endpoint_detail

        endpoint, detail = default_capture_endpoint_detail()
        self.assertIsNone(endpoint)
        self.assertEqual(detail, "unavailable")


# ---------------------------------------------------------------------------
# B1/B2/B3/B4: Live session loops and stale state
# ---------------------------------------------------------------------------

class _LiveFixBase(unittest.TestCase):
    """Resume-path override + log capture, mirroring
    tests/test_livefix.py's _LiveFixBase (same bindings, both
    restored)."""

    def setUp(self):
        import pathlib
        import shutil
        import tempfile

        from core.voice import live_client
        from core.voice import live_client_lifecycle as _lc
        from core.voice import live_client_resume as _lr

        self._live_client = live_client
        self._tmpdir = tempfile.mkdtemp(prefix="voicecontrol_")
        self.addCleanup(shutil.rmtree, self._tmpdir, True)
        self._resume_file = pathlib.Path(self._tmpdir) / "live_resume.json"
        self._orig_override = live_client._resume_path_override
        live_client._resume_path_override = self._resume_file
        self._orig_override_recv = _lr._resume_path_override
        _lr._resume_path_override = self._resume_file
        self.log_lines = []
        self._log_patcher = mock.patch.object(
            live_client, "_log_line", new=self.log_lines.append)
        self._log_patcher.start()
        self.addCleanup(self._log_patcher.stop)
        self._log_patcher_mixin = mock.patch.object(
            _lc, "_log_line", new=self.log_lines.append)
        self._log_patcher_mixin.start()
        self.addCleanup(self._log_patcher_mixin.stop)
        self.addCleanup(self._restore_override)

    def _restore_override(self):
        self._live_client._resume_path_override = self._orig_override
        from core.voice import live_client_resume as _lr

        _lr._resume_path_override = self._orig_override_recv
        if self._resume_file.exists():
            self._resume_file.unlink()

    def _new_session(self):
        return self._live_client.GeminiLiveSession()


class _EmptyReceiveSession:
    """A session whose receive() yields nothing — the cleanly
    closed shape."""

    async def receive(self):
        if False:
            yield


class TestB1SendFailureWatchdog(_LiveFixBase):
    """The send path used to fail forever without arming any
    watchdog: the voice/audible stamps only fire on a SUCCESSFUL
    send, so a session whose sends all raise stayed \"connected\"
    while the user spoke into a void."""

    def test_continuous_send_failure_ends_session(self):
        async def _run():
            session = self._new_session()
            notes = []
            session.on_status_change = notes.append
            session._running = True
            session.is_connected = True
            session._last_response_at = time.monotonic()
            session._last_voice_at = None
            session._last_audible_at = None
            session._last_send_failure_at = time.monotonic() - 20.0
            session._last_send_success_at = None
            await asyncio.wait_for(
                session._live_watchdog_loop(), timeout=5.0)
            self.assertIn("LIVE_WATCHDOG", notes)

        asyncio.run(_run())

    def test_fresh_failure_does_not_fire(self):
        async def _run():
            session = self._new_session()
            notes = []
            session.on_status_change = notes.append
            session._running = True
            session.is_connected = True
            session._last_response_at = time.monotonic()
            session._last_voice_at = None
            session._last_audible_at = None
            session._last_send_failure_at = time.monotonic()
            session._last_send_success_at = None
            # NOTE: asyncio.wait_for cannot prove "still running"
            # here — the watchdog catches CancelledError by
            # design, and on this Python wait_for then returns
            # the suppressed result instead of raising
            # TimeoutError. A bare task + sleep proves it.
            task = asyncio.create_task(session._live_watchdog_loop())
            await asyncio.sleep(2.5)
            try:
                self.assertFalse(task.done())
                self.assertNotIn("LIVE_WATCHDOG", notes)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run())


class TestB2EmptyReceiveEndsSession(_LiveFixBase):
    """A cleanly closed session makes receive() return at once
    with zero yields — the old loop re-entered it forever with
    no sleep and no guard, a busy spin that left is_connected
    True."""

    def test_three_empty_receives_end_the_loop(self):
        async def _run():
            session = self._new_session()
            session._running = True
            session.is_connected = True
            session._session = _EmptyReceiveSession()
            await asyncio.wait_for(
                session._receive_loop(SimpleNamespace()),
                timeout=2.0)

        asyncio.run(_run())


class TestB3StopClearsSession(_LiveFixBase):
    """A stopped session's reference used to survive, so later
    code could mistake it for alive."""

    def test_stop_clears_session_reference(self):
        session = self._new_session()
        session._session = _EmptyReceiveSession()
        session._running = True
        session.stop()
        self.assertIsNone(session._session)
        self.assertFalse(session._running)


class TestB4RunSessionResetsStaleState(_LiveFixBase):
    """Stale per-session state leaked across reconnects: a stale
    IN_PROGRESS vetoed the LISTENING flip, a stale cancelled-id
    set grew unboundedly, and a partial output-transcript buffer
    flushed as a stray bubble into the next session."""

    def test_stale_state_reset_at_session_entry(self):
        async def _run():
            session = self._new_session()
            notes = []
            session.on_status_change = notes.append
            session._running = True
            session._interaction_status = "SPEAKING"
            session._cancelled_call_ids = {"dead-call"}
            session._output_transcript_buf = "stray partial"
            session._last_send_failure_at = 12345.0
            session._last_send_success_at = 12345.0
            fake_audio = SimpleNamespace(mic_queue=queue.Queue())
            await asyncio.wait_for(
                session._run_session(_EmptyReceiveSession(), fake_audio),
                timeout=5.0)
            self.assertIsNone(session._interaction_status)
            self.assertEqual(session._cancelled_call_ids, set())
            self.assertEqual(session._output_transcript_buf, "")
            self.assertIsNone(session._last_send_failure_at)
            self.assertIsNone(session._last_send_success_at)
            self.assertIn("LISTENING", notes)

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# W1 (wake detector) / W2: generation retirement + surviving spotter errors
# ---------------------------------------------------------------------------

class TestW1WakeGeneration(unittest.TestCase):
    """stop() -> start() used to run two detector threads: the old
    thread had not yet observed the stop when the new start set
    _running True again. The loop now exits when its generation
    is retired, whatever _running says."""

    def test_retired_generation_exits_despite_running_true(self):
        from core.voice.wake_word import WakeWordDetector

        det = WakeWordDetector()
        det.enabled = False  # loop idles without touching audio
        det._running = True
        det._wake_gen = 1
        thread = threading.Thread(
            target=det._detection_loop, args=(1,), daemon=True)
        thread.start()
        time.sleep(0.3)
        self.assertTrue(thread.is_alive())
        det._wake_gen = 2  # a stop() that timed out on the join
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())

    def test_same_generation_keeps_running(self):
        from core.voice.wake_word import WakeWordDetector

        det = WakeWordDetector()
        det.enabled = False
        det._running = True
        det._wake_gen = 1
        thread = threading.Thread(
            target=det._detection_loop, args=(1,), daemon=True)
        thread.start()
        try:
            time.sleep(0.3)
            self.assertTrue(thread.is_alive())
        finally:
            det._running = False
            thread.join(timeout=2.0)


class TestW2SpotterErrorSurvived(unittest.TestCase):
    """One Vosk exception used to kill the detector thread
    silently while the UI still said wake was On."""

    def test_raising_spotter_does_not_kill_detector(self):
        from core.voice.wake_word import WakeWordDetector

        det = WakeWordDetector()
        det.enabled = True
        calls = {"accept": 0, "reset": 0}

        class _RaisingSpotter:
            def accept(self, pcm):
                calls["accept"] += 1
                raise RuntimeError("vosk boom")

            def reset(self):
                calls["reset"] += 1

        det._make_spotter = lambda: (_RaisingSpotter(), "fake model")
        det._arm_spotter_state = None
        chunk = np.zeros(1024, dtype=np.int16)
        n = {"i": 0}

        class _FakeAudio:
            sample_rate_in = 16000

            def get_recent_mic_audio(self, seconds=2.5):
                n["i"] += 1
                return [chunk] * n["i"]

        det.start(_FakeAudio())
        try:
            deadline = time.monotonic() + 3.0
            while (time.monotonic() < deadline
                   and calls["accept"] < 2):
                time.sleep(0.05)
            self.assertGreaterEqual(calls["accept"], 2)
            self.assertTrue(det._thread is not None
                            and det._thread.is_alive())
            self.assertGreaterEqual(calls["reset"], 1)
        finally:
            det.stop()


# ---------------------------------------------------------------------------
# W1 (mic watchdog) / O1 / O10: AudioManager lifecycle
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# W1 (mic watchdog) / O1 / O10: AudioManager lifecycle
# ---------------------------------------------------------------------------

class _VCFakeStream:
    """Stand-in for sd.InputStream/OutputStream (same shape as
    tests/test_micfix.py's _FakeStream): records instances so a
    test can count (re)opens."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        _VCFakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class _VCFakeDefault:
    device = (0, 0)


class _VCFakeSD:
    InputStream = _VCFakeStream
    OutputStream = _VCFakeStream
    default = _VCFakeDefault()

    @staticmethod
    def query_devices(kind=None):
        devs = [{"name": "Fake Mic", "max_input_channels": 2},
                {"name": "Fake Speaker", "max_input_channels": 0}]
        if kind == "input":
            return {"name": "Fake Mic"}
        return devs


class _AudioManagerBase(unittest.TestCase):
    """Mirrors tests/test_micfix.py's setUp: fake PortAudio, temp
    DATA_DIR on both the facade and the mixin (B33 pattern), fast
    watchdog tick."""

    def setUp(self):
        import pathlib

        from core import permission_grants as pg
        from core.voice import audio as audio_mod
        from core.voice import audio_devices_mic as audio_mic_mod

        self._pg = pg
        # The start() gate refuses input while the mic reads
        # denied (fresh lab tree, no grants store) — the
        # production "Allow once" session grant (first-use mic
        # ask, D3/D7), in-memory only, never persisted, scoped
        # to this test (test_micfix.py's B9 pattern).
        self.assertTrue(pg.grant_session_only("microphone"))
        self._audio_mod = audio_mod
        self._audio_mic_mod = audio_mic_mod
        self._orig = (getattr(audio_mod.sd, "InputStream", None),
                      getattr(audio_mod.sd, "OutputStream", None),
                      getattr(audio_mod.sd, "query_devices", None),
                      getattr(audio_mod.sd, "default", None))
        audio_mod.sd.InputStream = _VCFakeStream
        audio_mod.sd.OutputStream = _VCFakeStream
        audio_mod.sd.query_devices = _VCFakeSD.query_devices
        audio_mod.sd.default = _VCFakeDefault()
        _VCFakeStream.instances.clear()
        self._tmpdir = pathlib.Path(__file__).resolve().parent / "_vc_tmp"
        self._tmpdir.mkdir(exist_ok=True)
        self._orig_data_dir = audio_mod.DATA_DIR
        audio_mod.DATA_DIR = self._tmpdir
        self._orig_mic_data_dir = audio_mic_mod.DATA_DIR
        audio_mic_mod.DATA_DIR = self._tmpdir
        self._orig_tick = audio_mic_mod._MIC_WATCHDOG_TICK_S
        audio_mic_mod._MIC_WATCHDOG_TICK_S = 0.05

    def tearDown(self):
        audio_mod = self._audio_mod
        audio_mic_mod = self._audio_mic_mod
        self._pg.clear_session_grants()
        (audio_mod.sd.InputStream,
         audio_mod.sd.OutputStream,
         audio_mod.sd.query_devices,
         audio_mod.sd.default) = self._orig
        audio_mod.DATA_DIR = self._orig_data_dir
        audio_mic_mod.DATA_DIR = self._orig_mic_data_dir
        audio_mic_mod._MIC_WATCHDOG_TICK_S = self._orig_tick

    def _make_manager(self):
        m = self._audio_mod.AudioManager()
        m.mic_gain = 1.0
        m.is_muted = False
        return m

    def _watchdogs(self):
        return [t for t in threading.enumerate()
                if t.name == "MicWatchdog" and t.is_alive()]


class TestW1MicWatchdogSingleThread(_AudioManagerBase):
    """stop() + start() used to run two watchdog threads: stop()
    set the stop event, start() cleared it and spawned a second
    thread while the old one still slept out its tick. The old
    generation now retires, so the old thread exits and never
    comes back."""

    def test_rapid_stop_start_runs_one_watchdog(self):
        m = self._make_manager()
        m.start()
        try:
            m.stop()
            m.start()
            time.sleep(0.6)
            alive = self._watchdogs()
            self.assertEqual(len(alive), 1)
            self.assertIs(alive[0], m._mic_watchdog_thread)
        finally:
            m.stop()
        time.sleep(0.3)
        self.assertEqual(self._watchdogs(), [])


class TestO1OutputOpenFailure(_AudioManagerBase):
    """A failed output open used to raise out of start() and leave
    a half-open stream behind. start() now records _output_error
    (with a fallback to the default device) and keeps the mic."""

    def test_output_failure_recorded_not_raised(self):
        class _DeadOutput(_VCFakeStream):
            def start(self):
                raise RuntimeError("PortAudio error: -9999")

        self._audio_mod.sd.OutputStream = _DeadOutput
        m = self._make_manager()
        try:
            m.start()  # must not raise
            self.assertIsNotNone(m._output_error)
            self.assertIn("RuntimeError", m._output_error)
            dead = [s for s in _VCFakeStream.instances
                    if isinstance(s, _DeadOutput)]
            self.assertTrue(dead)
            self.assertTrue(all(s.closed for s in dead))
            # The mic itself still opened.
            self.assertIsNotNone(m._in_stream)
        finally:
            m.stop()


class TestO10RaisingClose(_AudioManagerBase):
    """A raising stream close used to skip the watchdog stop in
    stop(), leaving a zombie watchdog behind. The stop cleanup
    now lives in a finally."""

    def test_raising_close_does_not_skip_watchdog_stop(self):
        class _RaisingClose(_VCFakeStream):
            def close(self):
                raise RuntimeError("close boom")

        self._audio_mod.sd.InputStream = _RaisingClose
        self._audio_mod.sd.OutputStream = _RaisingClose
        m = self._make_manager()
        gen_before = getattr(m, "_mic_watchdog_gen", 0)
        m.start()
        try:
            m.stop()  # must not raise
        except RuntimeError:
            self.fail("stop() raised on a failing close")
        self.assertTrue(m._mic_watchdog_stop.is_set())
        self.assertGreater(m._mic_watchdog_gen, gen_before)
        time.sleep(0.3)
        self.assertEqual(self._watchdogs(), [])


# ---------------------------------------------------------------------------
# Qt-gated: B9 liveness tick, C5 routine fallback, P6 PTT mode flip
# ---------------------------------------------------------------------------

try:
    import PySide6  # noqa: F401
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _QT_AVAILABLE = False


@unittest.skipUnless(_QT_AVAILABLE, "PySide6 not available")
class TestB9LivenessTickOrder(unittest.TestCase):
    """B9 investigated, reorder tried and REVERTED (same batch):
    moving the reconnect-timer check before the dead-worker
    declaration broke test_voice_drop_silent's inside-episode
    test — a dead worker during backoff IS a silent death (the
    episode keeps its first cause, the death gets its own log
    line, no budget burn: first cause wins and the
    duplicate-drop branch holds the presentation). The timer
    check's job is the probe path only: an ALIVE worker whose
    attempt is still scheduled must not be probed as wedged.
    These tests pin that order."""

    def _namespace(self, **overrides):
        ns = SimpleNamespace(
            _intentional_disconnect=False,
            _reconnect_exhausted=False,
            worker=SimpleNamespace(is_alive=lambda: False),
            _connected=False,
            _reconnect_pending=True,
            _liveness_awaiting_first_status=False,
            _session_liveness_timer=SimpleNamespace(
                isActive=lambda: False, stop=lambda: None),
            _reconnect_timer=SimpleNamespace(
                isActive=lambda: True, stop=lambda: None),
            _silent_death_declared=False,
            _declare_silent_worker_death=lambda *a: None,
        )
        ns.__dict__.update(overrides)
        return ns

    def test_dead_worker_during_backoff_is_still_declared(self):
        from gui.window_session_status import SessionStatusMixin

        deaths = []
        ns = self._namespace(
            _declare_silent_worker_death=lambda *a: deaths.append(1))
        SessionStatusMixin._liveness_tick(ns)
        self.assertEqual(deaths, [1])

    def test_alive_worker_with_scheduled_attempt_is_not_probed(self):
        from gui.window_session_status import SessionStatusMixin

        deaths = []
        probes = []
        ns = self._namespace(
            worker=SimpleNamespace(
                is_alive=lambda: True,
                loop=SimpleNamespace(
                    call_soon_threadsafe=lambda *a: probes.append(1),
                    is_closed=lambda: False)),
            _declare_silent_worker_death=lambda *a: deaths.append(1))
        SessionStatusMixin._liveness_tick(ns)
        self.assertEqual(deaths, [])
        self.assertEqual(probes, [])

    def test_tick_while_connected_still_declares_death(self):
        # Same declaration, connected shape: an ERROR drop
        # schedules a retry without clearing _connected, but
        # the worker is the live one — if it dies with no
        # status, the watch must still catch it
        # (test_voice_drop_silent's inside-episode test).
        from gui.window_session_status import SessionStatusMixin

        deaths = []
        ns = self._namespace(
            _connected=True,
            _declare_silent_worker_death=lambda *a: deaths.append(1))
        SessionStatusMixin._liveness_tick(ns)
        self.assertEqual(deaths, [1])


@unittest.skipUnless(_QT_AVAILABLE, "PySide6 not available")
class TestP6PttModeFlip(unittest.TestCase):
    """Flipping hold -> toggle mid-hold used to strand the mic
    open: the hold flag survived the flip and release became a
    no-op in toggle mode. The flip now resets both PTT flags
    and fails closed to muted."""

    def test_mode_flip_resets_ptt_state_and_mutes(self):
        from gui import window_services_audio as _wsa

        calls = []
        fake_audio = SimpleNamespace(is_muted=False)
        ns = SimpleNamespace(
            settings=None,
            _ptt_toggled_on=True,
            _ptt_hold_active=True,
            ptt_mode_buttons={},
            btn_mic=SimpleNamespace(
                setProperty=lambda *a: calls.append(("prop",) + a),
                setStyle=lambda *a: calls.append(("style",) + a),
                style=lambda: ""),
        )
        with mock.patch.object(_wsa, "qsettings_set",
                               lambda *a, **k: calls.append(
                                   ("qset",) + a)), \
                mock.patch.object(_wsa, "qsettings_get",
                                  return_value="toggle"), \
                mock.patch.object(_wsa, "audio_manager", fake_audio):
            # Bind the real button-sync collaborator (it only
            # iterates ptt_mode_buttons, empty here) instead of
            # stubbing it — the method under test is the flip.
            import types as _types

            ns._sync_ptt_mode_buttons = _types.MethodType(
                _wsa.ServicesAudioMixin._sync_ptt_mode_buttons, ns)
            _wsa.ServicesAudioMixin._on_ptt_mode_selected(ns, "toggle")
        self.assertTrue(fake_audio.is_muted)
        self.assertFalse(ns._ptt_toggled_on)
        self.assertFalse(ns._ptt_hold_active)
        self.assertTrue(any(c[1] == "active" and c[2] == "false"
                            for c in calls if c[0] == "prop"))


@unittest.skipUnless(_QT_AVAILABLE, "PySide6 not available")
class TestC5RoutineDueFallback(unittest.TestCase):
    """A scheduled routine due while connected used to drop its
    send_text future on the floor: send_text silently no-ops
    when disconnected, so the routine never ran and nobody was
    told. A failed or dropped send now falls back to the
    headless runner with a system line."""

    def test_failed_live_send_falls_back_headless(self):
        from gui import window_memory_routines as _wmr

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            fired = threading.Event()

            async def _raising_send_text(text):
                raise RuntimeError("socket closed")

            fake_live = SimpleNamespace(
                is_connected=True, send_text=_raising_send_text)
            ns = SimpleNamespace(
                worker=SimpleNamespace(loop=loop),
                _note_self_sent=lambda text: None,
                _add_system=lambda *a: None,
                signals=SimpleNamespace(
                    routine_result=SimpleNamespace(
                        emit=lambda *a: None)),
                _run_routine_headless=lambda *a: fired.set(),
            )
            with mock.patch.object(_wmr, "live_session", fake_live):
                _wmr.MemoryRoutinesMixin._on_routine_due(
                    ns, {"id": "r1", "name": "test routine"})
            self.assertTrue(fired.wait(timeout=5.0))
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5.0)
            loop.close()
