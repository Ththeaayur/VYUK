"""Chatfix H1 diagnostic tests: a failing typed send must surface.

Pre-fix, ``_send_typed_message`` dropped the future returned by
``asyncio.run_coroutine_threadsafe``, so any exception inside
``send_text`` vanished with zero UI trace — the v5.93 typed-chat
silence (user types, nothing comes back, voice works). These tests
prove the fix:

(a) the future is captured and its done callback runs;
(b) a failing send posts a system message (via the bridge signal),
    logs the exception with traceback, and advances the real w9
    error ladder — instead of silence;
(c) a succeeding send behaves exactly as before (input cleared,
    history recorded, ack called, sent sound played, no message,
    no ladder, no log).

Chatfix2 extension: one diag line is logged for every completed
typed send (success as well as failure), recording the send-time
snapshot — proxy connected, inner on_transcript None or not, and
whether the echo call ran (inner flag). The line settles M1
(session dropped before execution: connected True, callback
present, echo_ran False, no exception) vs M2 (callback None at
echo time: inner_on_transcript_none True, echo_ran False). The
success test therefore expects exactly the diag line (and nothing
else); the failure test expects diag line + failure line.

Harness: no Qt, no display. PySide6 and the heavy leaf modules
``window_session`` imports are stubbed; the only real product code
under test is ``gui/window_session.py`` itself plus the real
``w9_wiring`` diagnostic sink. Where the window module cannot
import, the suite skips (test_auditfix1 precedent) instead of
erroring, so the full run stays green on interpreters without Qt.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


def _register_packages():
    """Mirror doctor._register_package / test_auditfix1: make
    voice_assistant.* importable from this folder whatever it is
    called, with core as a real namespace so the lazy w9_wiring
    import inside the callback resolves to the real module.
    Returns the package names this call created."""
    created = []
    if "voice_assistant" not in sys.modules:
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = pkg
        created.append("voice_assistant")
    if "voice_assistant.core" not in sys.modules:
        core = types.ModuleType("voice_assistant.core")
        core.__path__ = [str(ROOT / "core")]
        sys.modules["voice_assistant.core"] = core
        created.append("voice_assistant.core")
    if "gui" not in sys.modules:
        gui = types.ModuleType("gui")
        gui.__path__ = [str(ROOT / "gui")]
        sys.modules["gui"] = gui
        created.append("gui")
    return created


def _install_stubs():
    """Stub Qt + the heavy leaf modules window_session imports, so
    the suite runs headless on any interpreter. The real code under
    test is window_session.py itself; w9_wiring stays real."""
    for sub in ("QtCore", "QtGui", "QtWidgets"):
        sys.modules["PySide6." + sub] = mock.MagicMock(name="PySide6." + sub)
    sys.modules["PySide6"] = mock.MagicMock(name="PySide6")
    _stub_module("voice_assistant.core.voice.audio",
                 audio_manager=mock.MagicMock(name="audio_manager"))  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
    _stub_module("voice_assistant.core.brain.brain",  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move
                 live_session=mock.MagicMock(name="live_session"))
    _stub_module("voice_assistant.core.desktop.vision",  # Phase 3 D4c INTENTIONAL: stub follows vision hub move
                 vision_manager=mock.MagicMock(name="vision_manager"))
    _stub_module("voice_assistant.core.voice.wake_word",
                 wake_word_detector=mock.MagicMock(name="wake_word_detector"))  # Phase 3 V1a-2 INTENTIONAL: stub follows wake_word move
    _stub_module("gui.icons", make_icon=mock.MagicMock(name="make_icon"))
    _stub_module(
        "gui.window_widgets",
        BackendWorker=mock.MagicMock(name="BackendWorker"),
        _tool_friendly_name=mock.MagicMock(name="_tool_friendly_name"),
        _MISSING_KEY_NOTICE="",
        _RECONNECT_DELAYS=[],
        _is_fatal_error=mock.MagicMock(name="_is_fatal_error"),
        _friendly_error=mock.MagicMock(name="_friendly_error"),
        _friendly_fallback=mock.MagicMock(name="_friendly_fallback"),
        _friendly_setup=mock.MagicMock(name="_friendly_setup"),
    )


# Names the harness stubs (fakes). The pre-harness state is
# snapshotted so the stubs can be restored immediately after the
# import: a stub left in sys.modules would leak into later suites
# (a MagicMock PySide6 defeats the Qt-optional guards in
# test_wiring5_pin_briefing and breaks its bridge-dialog tests —
# seen in the first full run of this suite). Discovery imports
# every test module before any test runs, so the restore must
# happen here at import time, not in setUp/tearDown.
_STUB_NAMES = (
    "PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "voice_assistant.core.voice.audio", "voice_assistant.core.brain.brain",  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move; Phase 3 V1a-1 INTENTIONAL: audio stub follows audio move
    "voice_assistant.core.desktop.vision", "voice_assistant.core.voice.wake_word",  # Phase 3 V1a-2 INTENTIONAL: stub follows wake_word move; Phase 3 D4c INTENTIONAL: vision stub follows vision hub move
    "gui.icons", "gui.window_widgets",
)
_PRE_EXISTING = {name: sys.modules[name] for name in _STUB_NAMES
                 if name in sys.modules}

_CREATED = _register_packages()
_install_stubs()
try:
    from gui import window_session as _ws
    # GUI split #11 batch 2 INTENTIONAL: the typed methods read
    # live_session from the typed module's globals, so the live-session
    # patch must target that module (patch where bound), not the facade.
    from gui import window_session_typed as _wst
    # Pre-import the real diagnostic sink while the stubs are in
    # place (chat_render needs the Qt stubs): at call time the
    # lazy import inside the callback then hits this cache instead
    # of re-importing.
    from voice_assistant.core import w9_wiring as _w9_pre  # noqa: F401
    _AVAILABLE = True
    _IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001 — headless interpreters
    _ws = None
    _AVAILABLE = False
    _IMPORT_ERROR = exc
finally:
    # Restore every stub to its pre-harness state. Kept: the
    # voice_assistant namespaces and the real w9 chain (the
    # call-time lazy import resolves from that cache). Dropped:
    # the stub-dependent window_session module object is held
    # directly in _ws, so later suites importing gui.* / core.*
    # get a fresh import exactly as before this suite.
    for _name in _STUB_NAMES:
        if _name in _PRE_EXISTING:
            sys.modules[_name] = _PRE_EXISTING[_name]
        else:
            sys.modules.pop(_name, None)
    for _name in _CREATED:
        if _name not in ("voice_assistant", "voice_assistant.core"):
            sys.modules.pop(_name, None)
    for _name in ("gui.window_session", "gui.window_session_status",
                  "gui.window_session_learning",
                  "gui.window_session_suggestions",
                  "gui.window_session_connection",
                  "gui.window_session_typed",
                  "gui.window_session_watch",
                  "voice_assistant.core.config",
                  "voice_assistant.core.failsafe"):
        # GUI split #11 batch 1 INTENTIONAL: the facade import loads the
        # three new sibling modules under the stubs; popping only the
        # facade would leave stub-bound siblings in sys.modules for
        # later suites, so they are popped too.
        # GUI split #11 batch 2 INTENTIONAL: the three batch-2 siblings
        # are popped for the same reason.
        sys.modules.pop(_name, None)


class _FakeInput:
    def __init__(self, text):
        self._text = text
        self.cleared = False

    def text(self):
        return self._text

    def clear(self):
        self.cleared = True

    def setText(self, text):
        self._text = text


class _FakeEarcons:
    def __init__(self):
        self.sent_calls = 0

    def sent(self):
        self.sent_calls += 1


class _FakeSignal:
    """Stand-in for a BridgeSignals Signal(str): records emissions
    and wakes a waiter. Thread-safe: the callback runs on the loop
    thread, the test waits on the main thread."""

    def __init__(self):
        self.emitted = []
        self.event = threading.Event()
        self._lock = threading.Lock()

    def emit(self, text, kind="typed"):
        with self._lock:
            self.emitted.append(text)
        self.event.set()


class _FakeSignals:
    def __init__(self):
        self.typed_send_failed = _FakeSignal()


class _FakeLiveSession:
    """Stand-in for the brain proxy: is_connected + _inner with an
    on_transcript callback, mirroring the real echo semantics
    (echo runs only when the callback is present; a "drop"
    behaviour no-ops silently like a session dropped between the
    GUI check and worker execution)."""

    def __init__(self, is_connected=True, behaviour="ok",
                 inner_on_transcript=True):
        self.is_connected = is_connected
        self.behaviour = behaviour
        self.sent_texts = []
        self.done_event = threading.Event()
        self._inner = types.SimpleNamespace(
            on_transcript=(mock.Mock()
                           if inner_on_transcript else None),
            _diag_typed_echo_ran=False)

    async def send_text(self, text):
        self.sent_texts.append(text)
        try:
            if self.behaviour == "fail":
                raise ValueError("boom test failure")
            if self.behaviour == "drop":
                return None  # M1: silent no-op, no echo, no error
            # Mimic the real inner: entry reset + echo semantics.
            self._inner._diag_typed_echo_ran = False
            if self._inner.on_transcript is not None:
                self._inner.on_transcript("user", text)
                self._inner._diag_typed_echo_ran = True
        finally:
            self.done_event.set()
        return None


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@unittest.skipUnless(_AVAILABLE,
                     "window_session import unavailable: %s" % _IMPORT_ERROR)
class TestChatfix(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_loop)
        self._old_live = _wst.live_session
        self.addCleanup(setattr, _wst, "live_session", self._old_live)
        self.handler = _ListHandler()
        self.logger = logging.getLogger("nova.gui.window_session")
        self.logger.addHandler(self.handler)
        self.addCleanup(self.logger.removeHandler, self.handler)

    def _stop_loop(self):
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except RuntimeError:
            pass
        self.thread.join(timeout=5)

    def _make_window(self, input_text="  hello  ", live=None,
                     signals=None):
        win = _ws.SessionMixin.__new__(_ws.SessionMixin)
        win.input_cmd = _FakeInput(input_text)
        win._recall_idx = "stale"
        win.earcons = _FakeEarcons()
        win.worker = types.SimpleNamespace(loop=self.loop)
        win._ack_receipt = mock.Mock()
        win._add_system = mock.Mock()
        if signals is not None:
            win.signals = signals
        return win

    def _run_send(self, behaviour, input_text="  hello  ",
                  inner_on_transcript=True):
        live = _FakeLiveSession(behaviour=behaviour,
                                inner_on_transcript=inner_on_transcript)
        _wst.live_session = live
        signals = _FakeSignals()
        win = self._make_window(input_text=input_text, live=live,
                                signals=signals)
        win._send_typed_message()
        return win, signals, live

    def _diag_messages(self):
        return [r.getMessage() for r in self.handler.records
                if "typed send diag" in r.getMessage()]

    def test_failure_surfaces_system_message(self):
        # (a) + (b): the future is captured, the callback runs, and
        # a failing send becomes a visible system message, a log
        # line with traceback, and a ladder step — not silence.
        win, signals, live = self._run_send("fail")
        self.assertTrue(
            signals.typed_send_failed.event.wait(timeout=5),
            "done callback never fired")
        self.assertTrue(live.done_event.wait(timeout=5))
        self.assertEqual(len(signals.typed_send_failed.emitted), 1)
        msg = signals.typed_send_failed.emitted[0]
        self.assertTrue(
            msg.startswith("Your message didn't go through"),
            msg)
        self.assertIn("ValueError", msg)
        self.assertIn("boom test failure", msg)
        # Submit path intact even on failure (all pre-schedule).
        self.assertTrue(win.input_cmd.cleared)
        self.assertEqual(win._sent_history, ["hello"])
        self.assertEqual(win.earcons.sent_calls, 1)
        win._ack_receipt.assert_called_once_with(
            "hello", typed_submit=True)
        # Real w9 ladder advanced by the callback.
        ladder = getattr(win, "_w9_error_ladder", None)
        self.assertIsNotNone(ladder)
        self.assertEqual(ladder.consecutive_errors, 1)
        # Log lines: the chatfix2 diag line first (fail case:
        # echo never ran, since the send raised before it), then
        # the exception with traceback.
        self.assertEqual(len(self.handler.records), 2)
        diag = self.handler.records[0].getMessage()
        self.assertIn("typed send diag", diag)
        self.assertIn("connected=True", diag)
        self.assertIn("inner_on_transcript_none=False", diag)
        self.assertIn("echo_ran=False", diag)
        record = self.handler.records[1]
        self.assertIn("typed send failed", record.getMessage())
        self.assertIn("ValueError", record.getMessage())
        self.assertIsNotNone(record.exc_info)

    def test_success_unchanged(self):
        # (c): a succeeding send behaves exactly as before — no
        # message, no ladder, submit path intact — plus exactly
        # the chatfix2 diag line (echo ran, callback present).
        win, signals, live = self._run_send("ok")
        self.assertTrue(live.done_event.wait(timeout=5))
        # Settle delay so the done callback has run before
        # the negative assertions below.
        threading.Event().wait(0.3)
        self.assertTrue(win.input_cmd.cleared)
        self.assertEqual(win._sent_history, ["hello"])
        self.assertEqual(win.earcons.sent_calls, 1)
        win._ack_receipt.assert_called_once_with(
            "hello", typed_submit=True)
        self.assertEqual(signals.typed_send_failed.emitted, [])
        self.assertIsNone(getattr(win, "_w9_error_ladder", None))
        self.assertEqual(len(self.handler.records), 1)
        diag = self.handler.records[0].getMessage()
        self.assertIn("typed send diag", diag)
        self.assertIn("connected=True", diag)
        self.assertIn("inner_on_transcript_none=False", diag)
        self.assertIn("echo_ran=True", diag)

    def test_diag_line_m1_signature(self):
        # M1: session dropped between the GUI check and worker
        # execution — the send no-ops silently: no exception, no
        # echo, no message, no ladder — but the diag line shows
        # connected True, callback present, echo_ran False.
        win, signals, live = self._run_send("drop")
        self.assertTrue(live.done_event.wait(timeout=5))
        threading.Event().wait(0.3)
        self.assertEqual(signals.typed_send_failed.emitted, [])
        self.assertIsNone(getattr(win, "_w9_error_ladder", None))
        diags = self._diag_messages()
        self.assertEqual(len(diags), 1)
        self.assertIn("connected=True", diags[0])
        self.assertIn("inner_on_transcript_none=False", diags[0])
        self.assertIn("echo_ran=False", diags[0])

    def test_diag_line_m2_signature(self):
        # M2: inner callback None at echo time — the send
        # succeeds but the echo is skipped: no message, no
        # ladder, diag line shows callback None + echo_ran False.
        win, signals, live = self._run_send(
            "ok", inner_on_transcript=False)
        self.assertTrue(live.done_event.wait(timeout=5))
        threading.Event().wait(0.3)
        self.assertEqual(signals.typed_send_failed.emitted, [])
        self.assertIsNone(getattr(win, "_w9_error_ladder", None))
        diags = self._diag_messages()
        self.assertEqual(len(diags), 1)
        self.assertIn("connected=True", diags[0])
        self.assertIn("inner_on_transcript_none=True", diags[0])
        self.assertIn("echo_ran=False", diags[0])

    def test_callback_unit_with_diag(self):
        # Direct callback proof: diag line on success when a
        # snapshot is given, diag + failure on failure, and no
        # diag line without a snapshot (old behaviour).
        win = self._make_window(signals=_FakeSignals())
        inner = types.SimpleNamespace(_diag_typed_echo_ran=True)
        ok = concurrent.futures.Future()
        ok.set_result(None)
        win._on_typed_send_done(ok, diag=(True, False, inner))
        self.assertEqual(len(self.handler.records), 1)
        msg = self.handler.records[0].getMessage()
        self.assertIn("typed send diag", msg)
        self.assertIn("echo_ran=True", msg)
        self.assertEqual(win.signals.typed_send_failed.emitted, [])

        win2 = self._make_window(signals=_FakeSignals())
        inner2 = types.SimpleNamespace(_diag_typed_echo_ran=False)
        failing = concurrent.futures.Future()
        failing.set_exception(RuntimeError("kaboom2"))
        win2._on_typed_send_done(failing, diag=(True, True, inner2))
        self.assertEqual(len(self.handler.records), 3)
        diag_msg = self.handler.records[1].getMessage()
        self.assertIn("typed send diag", diag_msg)
        self.assertIn("inner_on_transcript_none=True", diag_msg)
        self.assertIn("echo_ran=False", diag_msg)
        self.assertEqual(len(win2.signals.typed_send_failed.emitted), 1)

        win3 = self._make_window(signals=_FakeSignals())
        ok3 = concurrent.futures.Future()
        ok3.set_result(None)
        win3._on_typed_send_done(ok3)
        self.assertEqual(len(self.handler.records), 3)

    def test_callback_unit(self):
        # Direct callback proof, no loop needed.
        win = self._make_window(signals=_FakeSignals())
        failing = concurrent.futures.Future()
        failing.set_exception(RuntimeError("kaboom"))
        win._on_typed_send_done(failing)
        emitted = win.signals.typed_send_failed.emitted
        self.assertEqual(len(emitted), 1)
        self.assertIn("RuntimeError", emitted[0])
        self.assertIn("kaboom", emitted[0])
        self.assertEqual(win._w9_error_ladder.consecutive_errors, 1)

        # Succeeding future: callback runs, does nothing.
        win2 = self._make_window(signals=_FakeSignals())
        ok = concurrent.futures.Future()
        ok.set_result(None)
        win2._on_typed_send_done(ok)
        self.assertEqual(win2.signals.typed_send_failed.emitted, [])
        self.assertIsNone(getattr(win2, "_w9_error_ladder", None))

        # Cancelled future (session teardown): silent by design.
        win3 = self._make_window(signals=_FakeSignals())
        cancelled = concurrent.futures.Future()
        cancelled.cancel()
        win3._on_typed_send_done(cancelled)
        self.assertEqual(win3.signals.typed_send_failed.emitted, [])
        self.assertIsNone(getattr(win3, "_w9_error_ladder", None))

        # No bridge at all: falls back to a direct system line
        # rather than staying silent.
        win4 = self._make_window(signals=None)
        failing2 = concurrent.futures.Future()
        failing2.set_exception(ValueError("no bridge"))
        win4._on_typed_send_done(failing2)
        win4._add_system.assert_called_once()
        fallback_msg = win4._add_system.call_args[0][0]
        self.assertIn("ValueError", fallback_msg)
        self.assertIn("no bridge", fallback_msg)

    def test_not_connected_branch_restores_draft(self):
        # INTENTIONAL (S6 behavioral slice): the else branch is no
        # longer "unchanged" — the S6 offline-draft fix restores the
        # cleared composer text here so an offline send keeps its
        # draft editable. Every old assertion is kept (system
        # message once, nothing sent, no failure signal); the
        # restore assertion is added, not substituted.
        live = _FakeLiveSession(is_connected=False)
        _wst.live_session = live
        win = self._make_window(signals=_FakeSignals())
        win._send_typed_message()
        win._add_system.assert_called_once_with(
            "You're not connected yet — press Connect first.")
        self.assertEqual(win.signals.typed_send_failed.emitted, [])
        self.assertEqual(live.sent_texts, [])
        self.assertEqual(win.input_cmd.text(), "hello")


if __name__ == "__main__":
    unittest.main()
