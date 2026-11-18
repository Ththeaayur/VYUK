"""Live reconnect fix (field failure 2026-10-30): the 1006 drop, the
two different 1008s, and the reconnect paths around them.

What this suite proves (simulated sessions only — no live Gemini,
no real audio, no real Qt; the GUI button press itself is
field-owed on real Windows):
(a) the classifier separates "already connected" from stale-handle
    1008s, including the field error text that carries BOTH the
    1008 code and the already-connected phrase;
(b) an already-connected resume failure backs off (brief, bounded,
    deadline-capped) before its cold retry and is never logged as
    stale-handle; a plain stale failure sleeps not at all;
(c) a handle cleared during a pass is never re-offered — not to a
    later candidate in the same pass, and not by a second pass
    reusing a stale in-memory config (the proxy retry shape);
(d) a cold attempt that hits already-connected fails honestly
    (raises into the GUI's reconnect episode) instead of looping;
(e) a fresh start-config built after the handle is cleared carries
    no handle (the exhausted-episode press-Connect path goes cold);
(f) the microphone send loop rebinds to a new session exactly the
    way _run_session replaces it (old loop cancelled, new loop
    bound to the new session) — the mic itself is not the bug;
(g) the GUI's single-flight guard (_start_connection refuses when
    a worker is alive; the toggle cancels in-flight attempts)
    still exists, pinned so it cannot silently regress.

Harness: the real core.live_client module is imported (it guards
its google-genai import itself). The resume file is pointed at a
temp file, _log_line is recorded instead of written, and the
backoff sleep is faked (records the delay, returns at once) — no
real-time sleeps, no writes to data/. The mic test needs SDK types
(types.Blob) and skips where the SDK is absent (venv-qt has it,
system python does not) — the textreplies precedent.
"""

from __future__ import annotations

import asyncio
import ast
import queue
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "voice_assistant" not in sys.modules:
    _pkg = types.ModuleType("voice_assistant")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _pkg
if "voice_assistant.core" not in sys.modules:
    _core = types.ModuleType("voice_assistant.core")
    _core.__path__ = [str(ROOT / "core")]
    sys.modules["voice_assistant.core"] = _core

from voice_assistant.core.voice import live_client  # noqa: E402  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from voice_assistant.core.voice import live_client_lifecycle as live_client_lifecycle_mod  # noqa: E402  # B47 INTENTIONAL: lifecycle mixin binding for dual patches
from voice_assistant.core.voice import live_client_resume as live_client_resume_mod  # noqa: E402  # B47 INTENTIONAL: resume module binding for dual patches

HANDLE = "test-handle-livefix"
FIELD_ALREADY = (
    "apierror: 1008 none. bidigeneratecontent session is already "
    "connected to an existing client"
)


class _ApiErr(Exception):
    """An error carrying a numeric code, like the SDK's APIError."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class _FakeCM:
    """Scripted async context manager for live.connect()."""

    def __init__(self, outcomes: list):
        self._outcomes = outcomes

    async def __aenter__(self):
        kind, value = self._outcomes.pop(0)
        if kind == "raise":
            raise value
        return value

    async def __aexit__(self, *exc):
        return False


class _FakeLive:
    """Records every offered config's handle; serves scripted CMs."""

    def __init__(self, outcomes: list):
        self._outcomes = outcomes
        self.offered: list = []  # (model_id, handle-or-None) per connect

    def connect(self, model, config):
        self.offered.append(
            (model, live_client._config_resume_handle(config)))
        return _FakeCM(self._outcomes)


def _resume_config():
    return SimpleNamespace(
        session_resumption=SimpleNamespace(handle=HANDLE, transparent=False),
        realtime_input_config=None)


def _cold_config():
    return SimpleNamespace(
        session_resumption=SimpleNamespace(handle=None, transparent=False),
        realtime_input_config=None)


class _LiveFixBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="livefix_")
        self.addCleanup(shutil.rmtree, self._tmpdir, True)
        self._resume_file = Path(self._tmpdir) / "live_resume.json"
        self._orig_override = live_client._resume_path_override
        live_client._resume_path_override = self._resume_file
        # INTENTIONAL (skills split B47): _resume_path moved to the
        # resume module and _try_candidates to the lifecycle mixin —
        # both read their binding from their own module's globals,
        # so the facade patch alone no longer steers them (B16
        # pattern: patch where bound, same fakes, both restored).
        self._orig_override_recv = live_client_resume_mod._resume_path_override
        live_client_resume_mod._resume_path_override = self._resume_file
        self.log_lines: list[str] = []
        self._log_patcher = mock.patch.object(
            live_client, "_log_line", new=self.log_lines.append)
        self._log_patcher.start()
        self.addCleanup(self._log_patcher.stop)
        self._log_patcher_mixin = mock.patch.object(
            live_client_lifecycle_mod, "_log_line",
            new=self.log_lines.append)
        self._log_patcher_mixin.start()
        self.addCleanup(self._log_patcher_mixin.stop)
        self.addCleanup(self._restore_override)

    def _restore_override(self):
        live_client._resume_path_override = self._orig_override
        live_client_resume_mod._resume_path_override = self._orig_override_recv
        if self._resume_file.exists():
            self._resume_file.unlink()

    def _session_with(self, live):
        session = live_client.GeminiLiveSession()
        session._client = SimpleNamespace(
            aio=SimpleNamespace(live=live))
        session._adopt_working_model = lambda model_id: None

        async def _noop_run(session_obj, audio_manager):
            return None

        session._run_session = _noop_run
        return session

    def _run_candidates(self, session, live, candidates, config, deadline):
        sleeps: list[float] = []

        async def _fake_sleep(delay):
            sleeps.append(delay)

        with mock.patch.object(asyncio, "sleep", new=_fake_sleep):
            result = asyncio.run(session._try_candidates(
                candidates, config, SimpleNamespace(), deadline=deadline))
        return result, sleeps


class TestClassifier(_LiveFixBase):
    def test_already_connected_field_text(self):
        err = Exception(FIELD_ALREADY)
        self.assertTrue(live_client._is_already_connected_close(err))

    def test_already_connected_phrase_without_code(self):
        err = Exception("session already connected")
        self.assertTrue(live_client._is_already_connected_close(err))
        self.assertFalse(live_client._is_stale_handle_close(err))

    def test_already_connected_not_stale_by_phrase(self):
        # The field error carries BOTH the 1008 code and the phrase:
        # the classifier must name it already-connected, and the
        # _try_candidates branch must check it before stale (the
        # branch test below pins the log consequence).
        err = Exception(FIELD_ALREADY)
        self.assertTrue(live_client._is_already_connected_close(err))

    def test_stale_close_unchanged(self):
        err = _ApiErr("connection closed", code=1008)
        self.assertTrue(live_client._is_stale_handle_close(err))
        self.assertFalse(live_client._is_already_connected_close(err))

    def test_plain_1008_text_still_stale(self):
        err = Exception("1008 policy violation")
        self.assertTrue(live_client._is_stale_handle_close(err))
        self.assertFalse(live_client._is_already_connected_close(err))

    def test_unrelated_error_is_neither(self):
        err = Exception("connection reset by peer")
        self.assertFalse(live_client._is_stale_handle_close(err))
        self.assertFalse(live_client._is_already_connected_close(err))


class TestResumePaths(_LiveFixBase):
    def test_stale_resume_cold_retry_no_sleep(self):
        live_client._save_resume_handle(HANDLE)
        live = _FakeLive([
            ("raise", Exception("1008 policy violation")),
            ("ok", SimpleNamespace()),
        ])
        session = self._session_with(live)
        (connected, _timed, _rej), sleeps = self._run_candidates(
            session, live, ["gemini-3.8-live"], _resume_config(),
            deadline=time.monotonic() + 60.0)
        self.assertTrue(connected)
        self.assertEqual(sleeps, [])
        self.assertIsNone(live_client._load_resume_handle())
        self.assertEqual(live.offered[1][1], None)
        self.assertTrue(any("stale handle" in line
                            for line in self.log_lines))
        self.assertFalse(any("already connected" in line
                             for line in self.log_lines))

    def test_already_connected_backoff_then_cold_never_stale(self):
        live_client._save_resume_handle(HANDLE)
        live = _FakeLive([
            ("raise", Exception(FIELD_ALREADY)),
            ("ok", SimpleNamespace()),
        ])
        session = self._session_with(live)
        (connected, _timed, _rej), sleeps = self._run_candidates(
            session, live, ["gemini-3.8-live"], _resume_config(),
            deadline=time.monotonic() + 60.0)
        self.assertTrue(connected)
        self.assertEqual(sleeps, [live_client._ALREADY_CONNECTED_BACKOFF_S])
        self.assertIsNone(live_client._load_resume_handle())
        self.assertEqual(live.offered[1][1], None)
        self.assertTrue(any("already connected" in line
                            for line in self.log_lines))
        self.assertFalse(any("stale handle" in line
                             for line in self.log_lines))

    def test_backoff_capped_by_deadline(self):
        live_client._save_resume_handle(HANDLE)
        live = _FakeLive([
            ("raise", Exception(FIELD_ALREADY)),
            ("ok", SimpleNamespace()),
        ])
        session = self._session_with(live)
        (_connected, _timed, _rej), sleeps = self._run_candidates(
            session, live, ["gemini-3.8-live"], _resume_config(),
            deadline=time.monotonic() + 2.0)
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0)
        self.assertLessEqual(sleeps[0], 2.05)

    def test_second_candidate_goes_cold_after_clear(self):
        live_client._save_resume_handle(HANDLE)
        live = _FakeLive([
            ("raise", Exception("1008 policy violation")),
            ("raise", _ApiErr("not found", code=404)),
            ("raise", _ApiErr("not found", code=404)),
        ])
        session = self._session_with(live)
        (connected, _timed, _rej), _sleeps = self._run_candidates(
            session, live, ["m1", "m2"], _resume_config(),
            deadline=time.monotonic() + 60.0)
        self.assertFalse(connected)
        # m1 resume (handle) -> m1 cold (no handle) -> m2 first
        # attempt (no handle): a cleared handle never poisons the
        # next candidate in the same pass.
        self.assertEqual(live.offered[0][1], HANDLE)
        self.assertEqual(live.offered[1][1], None)
        self.assertEqual(live.offered[2][1], None)

    def test_second_pass_with_stale_config_goes_cold(self):
        # The proxy retry shape: a second pass reusing a config
        # whose handle the store no longer holds must go cold.
        # (No file save here on purpose — the handle is stale.)
        live = _FakeLive([("ok", SimpleNamespace())])
        session = self._session_with(live)
        (connected, _timed, _rej), _sleeps = self._run_candidates(
            session, live, ["gemini-3.8-live"], _resume_config(),
            deadline=time.monotonic() + 60.0)
        self.assertTrue(connected)
        self.assertEqual(live.offered[0][1], None)

    def test_strip_helper(self):
        live_client._save_resume_handle(HANDLE)
        cfg = _resume_config()
        self.assertEqual(
            live_client._config_resume_handle(
                live_client._strip_cleared_handle(cfg)), HANDLE)
        live_client._clear_resume_handle()
        self.assertIsNone(
            live_client._config_resume_handle(
                live_client._strip_cleared_handle(cfg)))
        cold = _cold_config()
        self.assertIs(
            live_client._strip_cleared_handle(cold), cold)

    def test_cold_already_connected_raises_honestly(self):
        live_client._save_resume_handle(HANDLE)
        live = _FakeLive([
            ("raise", Exception("1008 policy violation")),
            ("raise", Exception(FIELD_ALREADY)),
        ])
        session = self._session_with(live)
        with mock.patch.object(asyncio, "sleep",
                               new=_never_sleep):
            with self.assertRaises(Exception) as caught:
                asyncio.run(session._try_candidates(
                    ["gemini-3.8-live"], _resume_config(),
                    SimpleNamespace(), deadline=time.monotonic() + 60.0))
        self.assertIn("already connected", str(caught.exception).lower())
        self.assertTrue(any("cold connect refused" in line
                            and "already connected" in line
                            for line in self.log_lines))

    def test_exhausted_recovery_builds_cold(self):
        live_client._save_resume_handle(HANDLE)
        self.assertEqual(live_client._load_resume_handle(), HANDLE)
        live_client._clear_resume_handle()
        # A fresh start() builds its config from the file, so after
        # the clear there is no stale in-memory handle to present.
        cfg = live_client._resume_config_for_mode(
            live_client._load_resume_handle(), False)
        self.assertIsNone(live_client._config_resume_handle(cfg))


async def _never_sleep(delay):
    raise AssertionError("no sleep expected on this path")


class TestGuiSingleFlightPin(_LiveFixBase):
    def test_start_connection_hard_guard_pinned(self):
        # Static pin, not a Qt drive: every connect path funnels
        # through _start_connection, which must refuse while a
        # worker is alive, and the toggle must cancel in-flight
        # attempts instead of stacking a second worker.
        # GUI split #11 batch 2 INTENTIONAL: _start_connection +
        # _toggle_connection moved verbatim to window_session_connection.py.
        source = (ROOT / "gui" / "window_session_connection.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        by_name = {node.name: node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)}
        start_src = ast.get_source_segment(
            source, by_name["_start_connection"])
        self.assertIn("self.worker and self.worker.is_alive()",
                      start_src)
        self.assertIn("return False", start_src)
        toggle_src = ast.get_source_segment(
            source, by_name["_toggle_connection"])
        self.assertIn("_connection_in_flight()", toggle_src)
        self.assertIn("_cancel_reconnect()", toggle_src)


@unittest.skipUnless(
    hasattr(live_client.types, "Blob"),
    "SDK types absent (no google-genai on this interpreter)")
class TestMicRebinding(_LiveFixBase):
    def test_send_loop_rebinds_to_new_session(self):
        async def _main():
            session = live_client.GeminiLiveSession()
            session._running = True
            # Voice-control batch (2026-11-16, B3): the send
            # loop stops when the session is gone or
            # disconnected, so a directly-driven loop needs the
            # production precondition — _run_session sets
            # is_connected True before launching it.
            session.is_connected = True
            sent_a: list = []
            sent_b: list = []

            async def _send_a(audio):
                sent_a.append(audio)

            async def _send_b(audio):
                sent_b.append(audio)

            fake_a = SimpleNamespace(send_realtime_input=_send_a)
            fake_b = SimpleNamespace(send_realtime_input=_send_b)
            audio_manager = SimpleNamespace(mic_queue=queue.Queue())
            session._session = fake_a
            task = asyncio.create_task(
                session._send_audio_loop(audio_manager))
            audio_manager.mic_queue.put(bytes(320))
            await asyncio.sleep(0.05)
            self.assertEqual(len(sent_a), 1)
            # _run_session's FIRST_COMPLETED cancels the old loop
            # when the session ends; a new session gets a new loop
            # bound to the new session object.
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            session._session = fake_b
            task2 = asyncio.create_task(
                session._send_audio_loop(audio_manager))
            audio_manager.mic_queue.put(bytes(320))
            await asyncio.sleep(0.05)
            self.assertEqual(len(sent_b), 1)
            self.assertEqual(len(sent_a), 1)
            task2.cancel()
            try:
                await task2
            except asyncio.CancelledError:
                pass
            session._running = False

        asyncio.run(_main())


if __name__ == "__main__":
    unittest.main()
