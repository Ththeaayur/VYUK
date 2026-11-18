"""P2 speed pass (2026-11-16): the two-second voice loop.

Covers, in order:
  * TurnLatency — the per-turn latency record (fake clock):
    first-sound math, the 500 ms budget boundary, tool waits,
    finalize idempotence, new-turn reset, the ledger.
  * FillerBridge — the gap bridge (fake clock + fake audio
    manager + fake render): fires once per turn past the gap,
    never over model audio, never while muted/paused/playing,
    cut short when model audio lands, a second tool wait gets
    its own chance, render caching, unavailable-engine
    behaviour, the render race.
  * Low-latency streams — PortAudio opens ask for latency="low"
    first and fall back to the default (fake sounddevice).
  * Receive-loop wiring — a fake session's first audio stamps
    the recorder, an interrupted response cuts playback and
    flushes the transcript, a turn_complete finalizes the
    record onto the ledger, the filler watchdog fires the
    bridge, a tool batch stamps its wait.
  * Playback probes — the local path's measured costs with
    generous bounds (the report carries the medians).

Lab note: the server side (model thinking, endpointing) cannot
be measured here — no key, no live session, no audio device.
The probes measure the LOCAL path only; the field test on his
machine is the end-to-end proof. No test here claims otherwise.
"""

import asyncio
import pathlib
import queue
import sys
import tempfile
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

from core.voice import audio as audio_mod
from core.voice import audio_devices_mic as audio_mic_mod
from core.voice import live_client
from core.voice import live_client_receive as receive_mod
from core.voice.live_filler import (
    FILLER_GAP_S,
    FILLER_PHRASES,
    FillerBridge,
)
from core.voice.live_latency import (
    FIRST_SOUND_BUDGET_MS,
    LatencyLedger,
    TurnLatency,
)


class _FakeClock:
    """Monotonic clock the test drives by hand."""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class _FakeAudioManager:
    """Stand-in for the session's AudioManager: records
    enqueues, sentence tracking, skips and interrupts."""

    def __init__(self):
        self.enqueued = []
        self.begun = []
        self.skips = 0
        self.interrupts = 0
        self.is_muted = False
        self.playback_paused = False
        self.is_playing = False

    def enqueue_playback(self, pcm):
        self.enqueued.append(pcm)
        return 2  # two blocks, like a short phrase

    def begin_sentence(self, blocks, index, total, text):
        self.begun.append((blocks, index, total, text))

    def skip_current_sentence(self):
        self.skips += 1
        return True

    def interrupt_playback(self):
        self.interrupts += 1


def _fake_pcm(seed=3, samples=12000):
    rng = np.random.default_rng(seed)
    return rng.integers(-8000, 8000, size=samples,
                        dtype=np.int16).tobytes()


# ------------------------------------------------------------------
# TurnLatency
# ------------------------------------------------------------------

class TurnLatencyTests(unittest.TestCase):
    def test_first_sound_within_budget(self):
        clock = _FakeClock()
        rec = TurnLatency(clock=clock)
        rec.note_voice()
        clock.advance(0.320)
        rec.note_first_audio()
        clock.advance(0.002)
        rec.note_first_enqueue()
        s = rec.summary()
        self.assertAlmostEqual(s["first_sound_ms"], 320.0)
        self.assertAlmostEqual(s["local_ms"], 2.0)
        self.assertTrue(s["within_budget"])

    def test_budget_boundary_500ms_is_within(self):
        clock = _FakeClock()
        rec = TurnLatency(clock=clock)
        rec.note_voice()
        clock.advance(0.500)
        rec.note_first_audio()
        self.assertTrue(rec.summary()["within_budget"])

    def test_over_budget(self):
        clock = _FakeClock()
        rec = TurnLatency(clock=clock)
        rec.note_voice()
        clock.advance(0.900)
        rec.note_first_audio()
        s = rec.summary()
        self.assertAlmostEqual(s["first_sound_ms"], 900.0)
        self.assertFalse(s["within_budget"])

    def test_no_voice_means_no_first_sound(self):
        rec = TurnLatency(clock=_FakeClock())
        rec.note_first_audio()
        s = rec.summary()
        self.assertIsNone(s["first_sound_ms"])
        self.assertIsNone(s["within_budget"])

    def test_first_audio_first_wins(self):
        clock = _FakeClock()
        rec = TurnLatency(clock=clock)
        rec.note_voice()
        clock.advance(0.300)
        rec.note_first_audio()
        clock.advance(0.400)
        rec.note_first_audio()
        self.assertAlmostEqual(
            rec.summary()["first_sound_ms"], 300.0)

    def test_tool_wait_and_first_sound_after_tools(self):
        clock = _FakeClock()
        rec = TurnLatency(clock=clock)
        rec.note_voice()
        clock.advance(0.200)
        rec.note_tool_batch_start()
        clock.advance(1.500)
        rec.note_tool_results()
        clock.advance(0.400)
        rec.note_first_audio()
        s = rec.summary()
        self.assertAlmostEqual(s["tool_wait_ms"], 1500.0)
        self.assertAlmostEqual(
            s["first_sound_after_tools_ms"], 400.0)

    def test_finalize_idempotent(self):
        clock = _FakeClock()
        rec = TurnLatency(clock=clock)
        rec.note_voice()
        clock.advance(0.300)
        rec.note_first_audio()
        first = rec.note_turn_complete()
        self.assertIsNotNone(first)
        self.assertIsNone(rec.note_turn_complete())
        self.assertTrue(rec.finalized)

    def test_voice_after_finalize_opens_new_turn(self):
        clock = _FakeClock()
        rec = TurnLatency(clock=clock)
        rec.note_voice()
        clock.advance(0.300)
        rec.note_first_audio()
        rec.note_turn_complete()
        clock.advance(5.0)
        rec.note_voice()
        clock.advance(0.100)
        rec.note_first_audio()
        self.assertAlmostEqual(
            rec.summary()["first_sound_ms"], 100.0)

    def test_filler_and_interrupted_recorded(self):
        rec = TurnLatency(clock=_FakeClock())
        rec.note_filler("On it.")
        rec.note_interrupted()
        s = rec.summary()
        self.assertEqual(s["filler"], "On it.")
        self.assertTrue(s["interrupted"])

    def test_ledger_recent_worst_over_budget(self):
        ledger = LatencyLedger(maxlen=3)
        clock = _FakeClock()
        for wait_s in (0.2, 0.9, 0.4):
            rec = TurnLatency(clock=clock)
            rec.note_voice()
            clock.advance(wait_s)
            rec.note_first_audio()
            ledger.add(rec.note_turn_complete())
            clock.advance(1.0)
        self.assertEqual(len(ledger.recent()), 3)
        self.assertAlmostEqual(
            ledger.worst_first_sound_ms(), 900.0)
        self.assertEqual(ledger.over_budget_count(), 1)
        # The ring is bounded: a fourth turn drops the oldest.
        rec = TurnLatency(clock=clock)
        rec.note_voice()
        clock.advance(0.1)
        rec.note_first_audio()
        ledger.add(rec.note_turn_complete())
        self.assertEqual(len(ledger.recent()), 3)
        self.assertAlmostEqual(
            ledger.worst_first_sound_ms(), 900.0)


# ------------------------------------------------------------------
# FillerBridge
# ------------------------------------------------------------------

class FillerBridgeTests(unittest.TestCase):
    def _bridge(self, **kw):
        clock = _FakeClock()
        am = _FakeAudioManager()
        renders = []
        render = kw.pop("render", None) or (lambda text: _fake_pcm())
        bridge = FillerBridge(am, render=render, clock=clock,
                              **kw)
        return bridge, clock, am, renders

    def test_not_due_before_gap(self):
        bridge, clock, _am, _r = self._bridge()
        bridge.note_voice()
        clock.advance(FILLER_GAP_S - 0.1)
        self.assertFalse(bridge.due())

    def test_due_after_gap_and_fires_once(self):
        async def _run():
            bridge, clock, am, _r = self._bridge()
            bridge.note_voice()
            clock.advance(FILLER_GAP_S + 0.1)
            spoken = await bridge.tick()
            self.assertEqual(spoken, FILLER_PHRASES[0])
            self.assertEqual(len(am.enqueued), 1)
            self.assertEqual(am.begun[0][3], FILLER_PHRASES[0])
            # A second tick in the same turn never fires again.
            clock.advance(1.0)
            self.assertIsNone(await bridge.tick())
            self.assertEqual(len(am.enqueued), 1)
        asyncio.run(_run())

    def test_no_fire_after_model_audio(self):
        async def _run():
            bridge, clock, am, _r = self._bridge()
            bridge.note_voice()
            clock.advance(FILLER_GAP_S + 0.1)
            bridge.note_model_audio()
            self.assertIsNone(await bridge.tick())
            self.assertEqual(am.enqueued, [])
        asyncio.run(_run())

    def test_no_fire_when_muted_paused_or_playing(self):
        async def _run():
            for attr in ("is_muted", "playback_paused", "is_playing"):
                bridge, clock, am, _r = self._bridge()
                setattr(am, attr, True)
                bridge.note_voice()
                clock.advance(FILLER_GAP_S + 0.5)
                self.assertIsNone(
                    await bridge.tick(),
                    f"fired while {attr}")
                self.assertEqual(am.enqueued, [])
        asyncio.run(_run())

    def test_model_audio_cuts_playing_filler(self):
        async def _run():
            bridge, clock, am, _r = self._bridge()
            bridge.note_voice()
            clock.advance(FILLER_GAP_S + 0.1)
            await bridge.tick()
            self.assertEqual(len(am.enqueued), 1)
            bridge.note_model_audio()
            self.assertEqual(am.skips, 1)
        asyncio.run(_run())

    def test_second_tool_wait_gets_its_own_filler(self):
        async def _run():
            bridge, clock, am, _r = self._bridge()
            bridge.note_voice()
            clock.advance(FILLER_GAP_S + 0.1)
            first = await bridge.tick()
            self.assertIsNotNone(first)
            bridge.note_model_audio()  # the model answered
            clock.advance(0.2)
            bridge.arm_tools()  # ... then asked for tools again
            clock.advance(FILLER_GAP_S + 0.1)
            second = await bridge.tick()
            self.assertIsNotNone(second)
            self.assertNotEqual(second, first)  # rotation, no repeat
            self.assertEqual(len(am.enqueued), 2)
        asyncio.run(_run())

    def test_render_cached_across_turns(self):
        calls = []

        def _render(text):
            calls.append(text)
            return _fake_pcm()

        async def _run():
            bridge, clock, am, _r = self._bridge(render=_render)
            for _ in range(2):
                bridge.note_voice()
                clock.advance(FILLER_GAP_S + 0.1)
                await bridge.tick()
                clock.advance(0.5)
            # Two turns, two phrases, one render each — the
            # first turn's phrase is not re-rendered.
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(set(calls)), 2)
        asyncio.run(_run())

    def test_no_renderer_means_unavailable_not_broken(self):
        async def _run():
            bridge, clock, am, _r = self._bridge(render=lambda t: None)
            bridge.note_voice()
            clock.advance(FILLER_GAP_S + 0.5)
            self.assertIsNone(await bridge.tick())
            self.assertEqual(am.enqueued, [])
            status = bridge.status()
            self.assertFalse(status["available"])
            self.assertIn("Piper", status["disabled_reason"])
            # Later ticks in the same session do not retry.
            clock.advance(2.0)
            self.assertIsNone(await bridge.tick())
        asyncio.run(_run())

    def test_render_race_model_audio_wins(self):
        async def _run():
            def _slow_render(text):
                time.sleep(0.05)
                return _fake_pcm()

            bridge, clock, am, _r = self._bridge(render=_slow_render)
            bridge.note_voice()
            clock.advance(FILLER_GAP_S + 0.1)
            loop = asyncio.get_running_loop()
            loop.call_later(
                0.02, lambda: bridge.note_model_audio())
            spoken = await bridge.tick()
            self.assertIsNone(spoken)
            self.assertEqual(am.enqueued, [])
        asyncio.run(_run())

    def test_cancel_clears_the_wait(self):
        async def _run():
            bridge, clock, am, _r = self._bridge()
            bridge.note_voice()
            clock.advance(FILLER_GAP_S + 0.1)
            await bridge.tick()
            bridge.cancel()
            self.assertEqual(am.skips, 1)
            self.assertFalse(bridge.due())
        asyncio.run(_run())

    def test_render_filler_pcm_without_piper(self):
        # The default render never raises: where Piper (or its
        # voice file) is absent it reports None; where Piper
        # IS present it returns PCM. This VM has no piper
        # package, so the lab half of the test is the None
        # branch — the PCM half is the field leg.
        from core.voice.live_filler import render_filler_pcm
        try:
            import piper  # noqa: F401
            have_piper = True
        except ImportError:
            have_piper = False
        out = render_filler_pcm("On it.")
        if have_piper:
            self.assertIsInstance(out, (bytes, type(None)))
        else:
            self.assertIsNone(out)


# ------------------------------------------------------------------
# Low-latency streams
# ------------------------------------------------------------------

class _RecordingStream:
    """sd stream fake: records kwargs; optionally fails when
    latency='low' is requested (construction or start)."""

    instances = []
    fail_low_construct = False
    fail_low_start = False

    def __init__(self, *args, **kwargs):
        if (self.fail_low_construct
                and kwargs.get("latency") == "low"):
            raise RuntimeError("PortAudio: latency not supported")
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        _RecordingStream.instances.append(self)

    def start(self):
        if (self.fail_low_start
                and self.kwargs.get("latency") == "low"):
            raise RuntimeError("PortAudio: start failed on low")
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class _FakeSD:
    InputStream = _RecordingStream
    OutputStream = _RecordingStream
    default = SimpleNamespace(device=(0, 0))

    @staticmethod
    def query_devices(kind=None):
        return [{"name": "Fake Mic", "max_input_channels": 2},
                {"name": "Fake Speaker", "max_input_channels": 0}]


# If sounddevice was stubbed above (lab: no PortAudio), give the
# stub the same fake attributes — core.audio holds the same module
# object, so its runtime sd.* lookups resolve.
if "sounddevice" in sys.modules and not hasattr(
        sys.modules["sounddevice"], "InputStream"):
    _sdmod = sys.modules["sounddevice"]
    _sdmod.InputStream = _RecordingStream
    _sdmod.OutputStream = _RecordingStream
    _sdmod.query_devices = _FakeSD.query_devices
    _sdmod.default = _FakeSD.default


class LowLatencyStreamTests(unittest.TestCase):
    def setUp(self):
        self._orig = (audio_mic_mod.sd.InputStream,
                      audio_mic_mod.sd.OutputStream)
        audio_mic_mod.sd.InputStream = _RecordingStream
        audio_mic_mod.sd.OutputStream = _RecordingStream
        _RecordingStream.instances.clear()
        _RecordingStream.fail_low_construct = False
        _RecordingStream.fail_low_start = False
        self._tmpdir = tempfile.mkdtemp(prefix="speedpass_")
        self._orig_data_dir = audio_mod.DATA_DIR
        audio_mod.DATA_DIR = pathlib.Path(self._tmpdir)
        self._orig_mic_data_dir = audio_mic_mod.DATA_DIR
        audio_mic_mod.DATA_DIR = pathlib.Path(self._tmpdir)

    def tearDown(self):
        audio_mic_mod.sd.InputStream, audio_mic_mod.sd.OutputStream = \
            self._orig
        audio_mod.DATA_DIR = self._orig_data_dir
        audio_mic_mod.DATA_DIR = self._orig_mic_data_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _manager(self):
        m = audio_mod.AudioManager()
        m.mic_gain = 1.0
        m.is_muted = False
        return m

    def test_output_asks_low_first(self):
        m = self._manager()
        stream, err = m._open_output_stream(None)
        self.assertIsNone(err)
        self.assertIsNotNone(stream)
        self.assertEqual(stream.kwargs.get("latency"), "low")
        self.assertEqual(m._output_latency_mode, "low")

    def test_input_asks_low_first(self):
        m = self._manager()
        stream, err = m._open_input_stream(None, "Fake Mic")
        self.assertIsNone(err)
        self.assertIsNotNone(stream)
        self.assertEqual(stream.kwargs.get("latency"), "low")
        self.assertEqual(m._input_latency_mode, "low")

    def test_low_construct_failure_falls_back(self):
        _RecordingStream.fail_low_construct = True
        m = self._manager()
        stream, err = m._open_output_stream(None)
        self.assertIsNone(err)
        self.assertIsNotNone(stream)
        self.assertNotIn("latency", stream.kwargs)
        self.assertEqual(m._output_latency_mode, "default")

    def test_low_start_failure_does_not_reconstruct(self):
        # A .start() failure is the DEVICE, not the buffer: even
        # when only the low attempt fails to start, there is no
        # second construction — the half-open stream is closed
        # and the error returned. (The mic remake's
        # test_half_open_stream_is_cleaned pins the instance
        # count; a retry here would double it.)
        _RecordingStream.fail_low_start = True
        try:
            m = self._manager()
            stream, err = m._open_input_stream(None, "Fake Mic")
            self.assertIsNone(stream)
            self.assertIn("start failed", err)
            self.assertEqual(len(_RecordingStream.instances), 1)
            self.assertTrue(_RecordingStream.instances[0].closed)
            self.assertIsNone(m._input_latency_mode)
        finally:
            _RecordingStream.fail_low_start = False

    def test_mic_status_carries_latency_modes(self):
        m = self._manager()
        status = m.mic_status()
        self.assertIn("input_latency", status)
        self.assertIn("output_latency", status)


# ------------------------------------------------------------------
# Receive-loop wiring (fake session, test_voicecontrol pattern)
# ------------------------------------------------------------------

class _FakeReceiveSession:
    """Yields canned responses, then nothing (the cleanly-closed
    shape — the loop ends after three empty re-entries)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._sent_tool = []

    async def receive(self):
        if self._responses:
            yield self._responses.pop(0)
        return
        yield  # pragma: no cover - never reached

    async def send_tool_response(self, function_responses):
        self._sent_tool.append(function_responses)


def _audio_response(pcm=None, text=None, interrupted=False,
                    turn_complete=False):
    part = SimpleNamespace(
        inline_data=(SimpleNamespace(data=pcm) if pcm is not None
                     else None),
        text=text)
    sc = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[part]),
        output_transcription=None,
        turn_complete=turn_complete,
        interrupted=interrupted)
    return SimpleNamespace(
        server_content=sc,
        session_resumption_update=None,
        go_away=None,
        tool_call=None,
        tool_call_cancellation=None)


def _new_session():
    return live_client.GeminiLiveSession()


class ReceiveLoopSpeedTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="speedpass_")
        self._log_lines = []
        self._log_patcher = mock.patch.object(
            receive_mod, "_log_line", new=self._log_lines.append)
        self._log_patcher.start()
        self.addCleanup(self._log_patcher.stop)
        # Transcript flushes log to memory_store — stub it so
        # these tests never touch data/.
        self._mem_patcher = mock.patch.object(
            receive_mod, "memory_store",
            new=SimpleNamespace(log_message=lambda *a, **k: None))
        self._mem_patcher.start()
        self.addCleanup(self._mem_patcher.stop)
        # A turn_complete also writes a cost-ledger row — stub
        # it the same way (the ledger is not what these tests
        # prove).
        self._cost_lines = []
        self._cost_patcher = mock.patch.object(
            receive_mod, "_cost_record_live",
            new=lambda *a, **k: self._cost_lines.append(a))
        self._cost_patcher.start()
        self.addCleanup(self._cost_patcher.stop)
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self._tmpdir, True))

    def _session_with(self, responses, am=None):
        session = _new_session()
        session._running = True
        session.is_connected = True
        session._session = _FakeReceiveSession(responses)
        session.on_audio_data = None
        session.on_transcript = None
        session._output_transcript_buf = ""
        session._interaction_status = None
        session._cancelled_call_ids = set()
        session._tool_queue = None
        session._ensure_speed_pass(am or _FakeAudioManager())
        return session

    def test_first_audio_stamps_the_recorder(self):
        async def _run():
            am = _FakeAudioManager()
            session = self._session_with(
                [_audio_response(pcm=_fake_pcm(samples=6000))], am)
            # A fake clock on the recorder, so the first-sound
            # number is exact: voice at 1000.0, audio at 1000.35.
            clock = _FakeClock(1000.0)
            session._turn_latency = TurnLatency(clock=clock)
            session._turn_latency.note_voice()
            clock.advance(0.350)
            await asyncio.wait_for(
                session._receive_loop(am), timeout=5.0)
            rec = session._turn_latency
            self.assertTrue(rec.audio_seen)
            self.assertAlmostEqual(
                rec.summary()["first_sound_ms"], 350.0)
            self.assertIsNotNone(rec.summary()["local_ms"])
            self.assertEqual(len(am.enqueued), 1)
        asyncio.run(_run())

    def test_interrupted_cuts_playback_and_flushes(self):
        async def _run():
            am = _FakeAudioManager()
            notes = []
            transcripts = []
            session = self._session_with(
                [_audio_response(
                    pcm=_fake_pcm(samples=6000),
                    interrupted=True,
                    turn_complete=True)], am)
            session.on_status_change = notes.append
            session.on_transcript = lambda kind, text: transcripts.append(
                (kind, text))
            # A partial transcribed reply waiting to flush.
            session._output_transcript_buf = "half a sentence"
            await asyncio.wait_for(
                session._receive_loop(am), timeout=5.0)
            self.assertEqual(am.interrupts, 1)
            self.assertIn("LISTENING", notes)
            self.assertIn(("assistant", "half a sentence"), transcripts)
            self.assertTrue(session._turn_latency.summary()["interrupted"])
        asyncio.run(_run())

    def test_turn_complete_finalizes_onto_ledger(self):
        async def _run():
            am = _FakeAudioManager()
            session = self._session_with(
                [_audio_response(
                    pcm=_fake_pcm(samples=6000),
                    turn_complete=True)], am)
            clock = _FakeClock(1000.0)
            session._turn_latency = TurnLatency(clock=clock)
            session._turn_latency.note_voice()
            clock.advance(0.350)
            await asyncio.wait_for(
                session._receive_loop(am), timeout=5.0)
            ledger = session._latency_ledger
            self.assertEqual(len(ledger.recent()), 1)
            self.assertAlmostEqual(
                ledger.recent()[0]["first_sound_ms"], 350.0)
            self.assertTrue(
                ledger.recent()[0]["within_budget"])
            self.assertTrue(any(
                "Live turn latency" in line
                for line in self._log_lines))
        asyncio.run(_run())

    def test_filler_watchdog_fires_the_bridge(self):
        async def _run():
            am = _FakeAudioManager()
            session = _new_session()
            session._running = True
            session.is_connected = True
            session._ensure_speed_pass(am)
            # The lab has no Piper: inject the render, so this
            # tests the watchdog + bridge, not the engine.
            session._filler_bridge._render = lambda text: _fake_pcm()
            # A turn waiting past the gap, no model audio.
            session._filler_bridge.note_voice()
            task = asyncio.create_task(
                session._filler_watchdog_loop())
            await asyncio.sleep(0.9)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(len(am.enqueued), 1)
            self.assertEqual(
                session._turn_latency.summary()["filler"],
                FILLER_PHRASES[0])
        asyncio.run(_run())

    def test_tool_batch_stamps_its_wait(self):
        async def _run():
            from core.funnel import tools_handlers_limits as _limits

            am = _FakeAudioManager()
            session = _new_session()
            session._running = True
            session.is_connected = True
            session._session = _FakeReceiveSession([])
            session._ensure_speed_pass(am)
            # The batch's one call is cooling down from a recent
            # timeout, so nothing dispatches — the wait is still
            # stamped and the bridge still armed.
            tool = "speed_pass_probe_tool"
            _limits.note_tool_timeout(tool, {})
            fc = SimpleNamespace(name=tool, args={}, id="probe-1")
            # Spy on the arm: the batch must arm the bridge, and
            # the delivery must disarm it again.
            armed = []
            bridge = session._filler_bridge
            orig_arm = bridge.arm_tools
            bridge.arm_tools = lambda *a, **k: (
                armed.append(True), orig_arm(*a, **k))
            # Fake the genai types seam (sys python has none).
            orig_types = receive_mod.types
            receive_mod.types = SimpleNamespace(
                FunctionResponse=lambda **kw: SimpleNamespace(**kw))
            try:
                await asyncio.wait_for(
                    session._run_tool_batch([fc]), timeout=5.0)
            finally:
                receive_mod.types = orig_types
            rec = session._turn_latency
            summary = rec.summary()
            self.assertEqual(armed, [True])
            self.assertIsNotNone(summary["tool_wait_ms"])
            self.assertGreaterEqual(summary["tool_wait_ms"], 0.0)
            self.assertIsNone(bridge._tools_since)
            self.assertEqual(
                len(session._session._sent_tool), 1)
        asyncio.run(_run())


# ------------------------------------------------------------------
# Playback probes (generous bounds; the report carries medians)
# ------------------------------------------------------------------

class PlaybackProbeTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="speedpass_")
        self._orig_data_dir = audio_mod.DATA_DIR
        audio_mod.DATA_DIR = pathlib.Path(self._tmpdir)
        self._orig_mic_data_dir = audio_mic_mod.DATA_DIR
        audio_mic_mod.DATA_DIR = pathlib.Path(self._tmpdir)

    def tearDown(self):
        audio_mod.DATA_DIR = self._orig_data_dir
        audio_mic_mod.DATA_DIR = self._orig_mic_data_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _manager(self):
        m = audio_mod.AudioManager()
        m.mic_gain = 1.0
        m.is_muted = False
        return m

    def test_enqueue_is_fast(self):
        m = self._manager()
        out = np.zeros((m.chunk_size, 1), dtype=np.int16)
        pcm = _fake_pcm(samples=6000)  # 0.25 s
        times = []
        for _ in range(5):
            t0 = time.monotonic()
            m.enqueue_playback(pcm)
            times.append(time.monotonic() - t0)
            while not m.playback_queue.empty():
                m._playback_callback(out, m.chunk_size, None, None)
        # Measured median ~0.2 ms in the lab; 50 ms is a
        # generous bound for a loaded CI box.
        self.assertLess(max(times) * 1000.0, 50.0)

    def test_callback_tick_is_fast(self):
        m = self._manager()
        out = np.zeros((m.chunk_size, 1), dtype=np.int16)
        m.enqueue_playback(_fake_pcm(samples=24000))
        # One warm-up tick first: the first FFT/spectrum path
        # pays import warm-up (~25 ms under Qt), which is not
        # the tick's steady-state cost.
        m._playback_callback(out, m.chunk_size, None, None)
        times = []
        for _ in range(5):
            t0 = time.monotonic()
            m._playback_callback(out, m.chunk_size, None, None)
            times.append(time.monotonic() - t0)
        # Measured ~0.15 ms steady-state; the tick budget is
        # the 42.7 ms block period, so 10 ms is generous.
        self.assertLess(max(times) * 1000.0, 10.0)

    def test_interrupt_cut_is_instant(self):
        m = self._manager()
        out = np.zeros((m.chunk_size, 1), dtype=np.int16)
        m.enqueue_playback(_fake_pcm(samples=24000))
        t0 = time.monotonic()
        m.interrupt_playback()
        m._playback_callback(out, m.chunk_size, None, None)
        dt = time.monotonic() - t0
        self.assertLess(dt * 1000.0, 10.0)
        self.assertTrue(m.playback_queue.empty())
        self.assertFalse(bool(np.any(out != 0)))

    # Field-check fix #13 (M13c): the interrupt used to drain the
    # WHOLE queue on the next callback tick, so audio enqueued
    # after the interrupt (the next turn's first chunks) was
    # discarded as stale too. interrupt_playback() now snapshots
    # the queue depth, and the callback drains only those blocks.
    def test_interrupt_keeps_audio_enqueued_after_it(self):
        m = self._manager()
        out = np.zeros((m.chunk_size, 1), dtype=np.int16)
        n_old = m.enqueue_playback(_fake_pcm(samples=24000))
        m.begin_sentence(n_old, 0, 1, "old turn")
        m.interrupt_playback()
        n_new = m.enqueue_playback(_fake_pcm(samples=24000))
        m.begin_sentence(n_new, 0, 1, "new turn")
        self.assertEqual(m.playback_queue.qsize(), n_old + n_new)
        m._playback_callback(out, m.chunk_size, None, None)
        # The new turn survives the tick, whole.
        self.assertEqual(m.playback_queue.qsize(), n_new)
        self.assertFalse(m._interrupt_event.is_set())
        self.assertTrue(m.is_playing)
        self.assertFalse(bool(np.any(out != 0)))
        # And its sentence record survives with its blocks (the
        # callback no longer clears the FIFO — the stale records
        # were cleared at interrupt time).
        self.assertEqual(m.sentences_left, 1)
        head = m.speech_sentence
        self.assertIsNotNone(head)
        self.assertEqual(head[2], "new turn")

    def test_second_interrupt_cuts_the_new_turn_too(self):
        m = self._manager()
        out = np.zeros((m.chunk_size, 1), dtype=np.int16)
        m.enqueue_playback(_fake_pcm(samples=24000))
        m.interrupt_playback()
        m.enqueue_playback(_fake_pcm(samples=24000))
        # A second interrupt: the new turn was queued AT this
        # interrupt, so it is stale now and must be cut.
        m.interrupt_playback()
        m._playback_callback(out, m.chunk_size, None, None)
        self.assertTrue(m.playback_queue.empty())
        self.assertFalse(m.is_playing)

    def test_interrupt_on_full_queue_keeps_the_fresh_block(self):
        m = self._manager()
        out = np.zeros((m.chunk_size, 1), dtype=np.int16)
        maxsize = m.playback_queue.maxsize
        # Fill past the cap with silence, so the queue is full.
        m.enqueue_playback(
            (np.zeros(m.chunk_size * (maxsize + 50),
                      dtype=np.int16)).tobytes())
        self.assertEqual(m.playback_queue.qsize(), maxsize)
        m.interrupt_playback()
        # One fresh marker block: the full queue drops the oldest
        # stale block for it, so the drain must leave exactly it.
        marker = (np.full(m.chunk_size, 1000,
                          dtype=np.int16)).tobytes()
        m.enqueue_playback(marker)
        m._playback_callback(out, m.chunk_size, None, None)
        self.assertEqual(m.playback_queue.qsize(), 1)
        remaining = m.playback_queue.get_nowait()
        self.assertTrue(bool(np.all(
            np.asarray(remaining).ravel() == 1000)))


if __name__ == "__main__":
    unittest.main()
