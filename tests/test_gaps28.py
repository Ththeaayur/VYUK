"""GAPS-28 regression suite: review section 6.9, G-series (GUI).

Pins: the memory view/model split (G-V1), the state bus living
in core with the GUI shim (G-V2), the SettingsChanged change bus
(G-V3 — chat-set appearance applies live), the window subscribing
to the bus (G-V4), the public session/journal seams (G-V5), the
deleted module-class swap (G-V6), the tool labels in core + the
shell-frame boot split (G-V7), the service host (G-V8), and the
briefing button driving the digest pipeline (G-V9). No new tool:
census pins (755/382/373/164, params 2077, grouped 752, closed
754).

All behavioural checks run against tempfile stores or fakes —
they never touch the tree's data/. Module hygiene: importing
core modules may construct module-level singletons that migrate
the LIVE assistant.db; data/ is snapshotted at import (before
any core import) and restored in tearDownModule, so the suite
leaves the tree exactly as it found it.

Run from the tree root with PYTHONPATH=. :
    python -m unittest tests.test_gaps28
"""

import asyncio
import datetime
import json
import re
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent

# The gui modules import voice_assistant.core.* (the shipped
# package name); plant the alias before importing them.
_pkg = types.ModuleType("voice_assistant")
_pkg.__path__ = [str(TREE)]
sys.modules.setdefault("voice_assistant", _pkg)

# Snapshot the tree's data/ BEFORE any core import (see the
# module docstring).
_DATA = TREE / "data"
_SAVED = {}
for _entry in _DATA.iterdir():
    if _entry.is_file():
        _SAVED[_entry.name] = _entry.read_bytes()


def setUpModule():
    pass


def tearDownModule():
    for _entry in list(_DATA.iterdir()):
        if _entry.is_file() and _entry.name not in _SAVED:
            _entry.unlink()
    for _name, _blob in _SAVED.items():
        (_DATA / _name).write_bytes(_blob)


try:
    from PySide6.QtCore import QSettings
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt optional, skip Qt-only tests
    QSettings = None
    _QT_AVAILABLE = False


# ------------------------------------------------------------------ G-V1
class TestMemoryView(unittest.TestCase):
    def setUp(self):
        from gui.memory_view import memory_facts_view, reminders_view
        self.view_facts = memory_facts_view
        self.view_reminders = reminders_view
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from core.memory.memory import MemoryStore
        from core.scheduler import ProactiveScheduler
        self.store = MemoryStore(
            db_path=str(Path(self._tmp.name) / "mem.db"))
        self.store.save_fact("general", "k1", "v1")
        self.store.save_fact("prefs", "k2", "v2")
        self.sched = ProactiveScheduler(
            store_path=str(Path(self._tmp.name) / "sched.json"))

    def test_facts_lists_all_with_categories(self):
        view = self.view_facts(query="", store=self.store)
        self.assertEqual(view["shown"], 2)
        self.assertEqual(view["total"], 2)
        self.assertEqual(view["categories"], ["general", "prefs"])
        self.assertEqual([f["key"] for f in view["facts"]],
                         ["k1", "k2"])

    def test_facts_query_and_category_filter(self):
        view = self.view_facts(query="k1", store=self.store)
        self.assertEqual([f["key"] for f in view["facts"]], ["k1"])
        view = self.view_facts(query="", category="prefs",
                               store=self.store)
        self.assertEqual([f["key"] for f in view["facts"]], ["k2"])
        # The category list stays over ALL facts, not the shown ones.
        self.assertEqual(view["categories"], ["general", "prefs"])

    def test_reminders_next_and_summary(self):
        self.sched.add_reminder(
            "test", datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1), "msg")
        view = self.view_reminders(scheduler=self.sched)
        self.assertEqual(len(view["reminders"]), 1)
        self.assertEqual(view["next"]["message"], "msg")
        self.assertIn("1 upcoming", view["summary"])
        self.assertIn("0 already spoken", view["summary"])

    def test_reminders_empty_summary(self):
        view = self.view_reminders(scheduler=self.sched)
        self.assertEqual(view["reminders"], [])
        self.assertIsNone(view["next"])
        self.assertEqual(view["summary"],
                         "Nothing scheduled this session.")

    def test_view_module_imports_no_qt(self):
        text = (TREE / "gui" / "memory_view.py").read_text(
            encoding="utf-8")
        self.assertNotIn("PySide6", text)


# ------------------------------------------------------------------ G-V2
class TestStateBusInCore(unittest.TestCase):
    def test_shim_reexports_the_core_objects(self):
        from core import state_bus as core_bus
        from gui import state_bus as gui_bus
        self.assertIs(gui_bus.StateBus, core_bus.StateBus)
        self.assertIs(gui_bus.get_state_bus, core_bus.get_state_bus)
        self.assertIs(gui_bus.AppState, core_bus.AppState)
        self.assertIs(gui_bus.SettingsChanged, core_bus.SettingsChanged)

    def test_producers_emit_through_the_core_bus(self):
        from core import state_bus as core_bus
        from core import state_bus_producers as producers
        core_bus.reset_state_bus()
        self.addCleanup(core_bus.reset_state_bus)
        bus = core_bus.get_state_bus()
        seen = []
        bus.subscribe(core_bus.ToolStarted,
                      lambda ev: seen.append(ev))
        producers.emit_tool_started(kind="test")
        bus.drain()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].kind, "test")

    def test_no_core_module_imports_gui(self):
        import ast
        bad = []
        for path in sorted((TREE / "core").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "gui" or alias.name.startswith(
                                "gui."):
                            bad.append(str(path))
                elif isinstance(node, ast.ImportFrom):
                    if node.module and (node.module == "gui"
                                        or node.module.startswith("gui.")):
                        bad.append(str(path))
        self.assertEqual(bad, [])


# ------------------------------------------------------------------ G-V3
@unittest.skipUnless(_QT_AVAILABLE, "Qt only: PySide6 unavailable")
class TestSettingsChangedBus(unittest.TestCase):
    def setUp(self):
        import core.app_settings as app_settings
        from core.state_bus import get_state_bus, reset_state_bus
        self.app_settings = app_settings
        self.get_state_bus = get_state_bus
        self.reset_state_bus = reset_state_bus
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = QSettings(
            str(Path(self._tmp.name) / "test.ini"), QSettings.IniFormat)
        self._orig = app_settings._qsettings
        app_settings._qsettings = lambda: self.store
        self.addCleanup(setattr, app_settings, "_qsettings", self._orig)
        reset_state_bus()
        self.addCleanup(reset_state_bus)

    def _emit_for(self, key, value):
        bus = self.get_state_bus()
        seen = []
        from core.state_bus import SettingsChanged
        bus.subscribe(SettingsChanged, lambda ev: seen.append(ev))
        self.app_settings._qs_set(key, value)
        bus.drain()
        return seen

    def test_theme_set_emits(self):
        from core.state_bus import SettingsChanged
        seen = self._emit_for("theme", "Contrast")
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], SettingsChanged)
        self.assertEqual(seen[0].key, "theme")
        self.assertEqual(seen[0].value, "Contrast")

    def test_appearance_key_emits(self):
        seen = self._emit_for("appearance/density", "compact")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].key, "appearance/density")

    def test_non_appearance_key_emits_nothing(self):
        seen = self._emit_for("orb_style", "Aura")
        self.assertEqual(seen, [])


@unittest.skipUnless(_QT_AVAILABLE, "Qt only: PySide6 unavailable")
class TestSettingsChangedHandler(unittest.TestCase):
    """G-V4: the window's handler re-applies the appearance."""

    def test_handler_calls_apply_appearance(self):
        from gui.window_services_trust import ServicesTrustMixin
        calls = []

        class FakeWindow(ServicesTrustMixin):
            def _apply_appearance(self):
                calls.append(True)

        from core.state_bus import SettingsChanged
        FakeWindow()._on_settings_changed(
            SettingsChanged(key="theme", value="Contrast"))
        self.assertEqual(calls, [True])


# ------------------------------------------------------------------ G-V5
class TestSessionPublicSeams(unittest.TestCase):
    def test_engine_name(self):
        from core.brain.brain import BrainSession
        from core.provider_identity import Engine
        session = BrainSession()
        self.assertEqual(session.engine_name, "")
        session._engine = Engine.GEMINI
        self.assertEqual(session.engine_name, "gemini")
        session._engine = Engine.GROQ
        self.assertEqual(session.engine_name, "groq")

    def test_send_watch_frame_routes_groq_by_question(self):
        from core.brain.brain import BrainSession

        class FakeInner:
            is_connected = True
            engine = "groq"
            calls = []

            async def send_image_frame(self, image_bytes, mime_type,
                                       question=""):
                self.calls.append((image_bytes, mime_type, question))
                return "delivered"

        session = BrainSession()
        inner = FakeInner()
        session._inner = inner
        result = asyncio.run(
            session.send_watch_frame(b"bytes", "image/jpeg",
                                     framing="what is this?"))
        self.assertEqual(result, "delivered")
        self.assertEqual(inner.calls,
                         [(b"bytes", "image/jpeg", "what is this?")])

    def test_send_watch_frame_proxy_for_others(self):
        from core.brain.brain import BrainSession

        class FakeInner:
            is_connected = True
            engine = "gemini"
            calls = []

            async def send_image_frame(self, image_bytes, mime_type,
                                       question=""):
                self.calls.append((image_bytes, mime_type, question))
                return "delivered"

        session = BrainSession()
        inner = FakeInner()
        session._inner = inner
        result = asyncio.run(
            session.send_watch_frame(b"bytes", "image/jpeg",
                                     framing="what is this?"))
        self.assertEqual(result, "delivered")
        self.assertEqual(inner.calls, [(b"bytes", "image/jpeg", "")])

    def test_send_watch_frame_no_session(self):
        from core.brain.brain import BrainSession
        session = BrainSession()
        session._inner = None
        result = asyncio.run(session.send_watch_frame(b"bytes"))
        self.assertEqual(result, "no-session")

    def test_interrupt_in_flight(self):
        # Parent fix (2026-11-14): the panic-hotkey follow-up is
        # the third G-V5 site — it used to read live_session._inner
        # (and its _inner) from the GUI. The seam interrupts both
        # levels, clears the speaking flag where an interrupt ran,
        # and cancels the pending turn tasks.
        from core.brain.brain import BrainSession

        class FakeTask:
            def __init__(self):
                self.cancelled = False

            def get_loop(self):
                return None

            def cancel(self):
                self.cancelled = True

        class FakeInner:
            def __init__(self):
                self.interrupted = False
                self._speaking = True
                self.task = FakeTask()
                self._turn_tasks = {self.task}
                self._inner = None

            def _interrupt_playback(self):
                self.interrupted = True

        session = BrainSession()
        outer, nested = FakeInner(), FakeInner()
        outer._inner = nested
        session._inner = outer
        session.interrupt_in_flight()
        for inner in (outer, nested):
            self.assertTrue(inner.interrupted)
            self.assertFalse(inner._speaking)
            self.assertTrue(inner.task.cancelled)

    def test_interrupt_in_flight_no_session(self):
        from core.brain.brain import BrainSession
        session = BrainSession()
        session._inner = None
        session.interrupt_in_flight()  # never raises, no-op


class TestJournalPublicSeam(unittest.TestCase):
    def test_journal_action_lists_entries(self):
        # The public seam returns the listing (the private handler
        # refuses direct calls — see the test below — so there is
        # no direct-call comparison to make).
        from core.funnel.tools_registry import tools
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "journal.jsonl")
            result = tools.journal_action("list", limit=5,
                                          store_path=path)
        self.assertIn("Journal entries", result)
        self.assertIn("Counts: retryable 0, done 0, unknown 0.", result)

    def test_journal_action_compensate_needs_confirmation(self):
        from core.funnel.tools_registry import tools
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "journal.jsonl")
            result = tools.journal_action(
                "compensate", receipt_id="r1", store_path=path,
                confirmed=False, confirmed_by="user")
        self.assertIn("needs confirmation", result)

    def test_private_handler_still_refuses_direct_calls(self):
        # The GAPS-23 F-V7 boundary holds for the private method:
        # only the public seam enters the dispatch scope.
        from core.funnel.tools_registry import tools
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "journal.jsonl")
            with self.assertRaises(RuntimeError):
                tools._tool_journal_action("list", limit=5,
                                           store_path=path)


# ------------------------------------------------------------------ G-V6
@unittest.skipUnless(_QT_AVAILABLE, "Qt only: PySide6 unavailable")
class TestNoForwardingModule(unittest.TestCase):
    def test_module_class_is_plain(self):
        import gui.main_window as main_window
        self.assertIs(type(main_window), types.ModuleType)
        self.assertNotIn("_P43ForwardingModule", dir(main_window))

    def test_cache_served_live_from_its_owner(self):
        import gui.main_window as main_window
        from gui import window_widgets_chatbits as chatbits
        orig = chatbits._aura_chip_pixmap_cache
        sentinel = object()
        try:
            chatbits._aura_chip_pixmap_cache = sentinel
            self.assertIs(main_window._aura_chip_pixmap_cache,
                          sentinel)
        finally:
            chatbits._aura_chip_pixmap_cache = orig

    def test_unknown_attribute_still_raises(self):
        import gui.main_window as main_window
        with self.assertRaises(AttributeError):
            _ = main_window._no_such_name_gaps28


# ------------------------------------------------------------------ G-V7
class TestToolLabels(unittest.TestCase):
    def test_shim_reexports_the_core_table(self):
        from core import tool_labels
        from gui import window_widgets_toolnames as shim
        self.assertIs(shim._TOOL_FRIENDLY_NAMES,
                      tool_labels.TOOL_FRIENDLY_NAMES)
        self.assertIs(shim._tool_friendly_name,
                      tool_labels.tool_friendly_name)
        self.assertIs(shim._tool_icon_name,
                      tool_labels.tool_icon_name)

    def test_table_covers_694_entries(self):  # INTENTIONAL — field-check fix #32 (2026-11-18): 680 -> 681 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 681 -> 682 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 682 -> 683 — one new tool get_morning_briefing; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        from core.tool_labels import TOOL_FRIENDLY_NAMES
        self.assertEqual(len(TOOL_FRIENDLY_NAMES), 694)  # INTENTIONAL — field-check fix #32 (2026-11-18): 680 -> 681 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 681 -> 682 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 682 -> 683 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_every_registry_tool_resolves(self):
        from core.funnel.tools_registry import tools
        from core.tool_labels import tool_friendly_name
        names = list(tools._tools)
        self.assertEqual(len(names), 769)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        for name in names:
            label = tool_friendly_name(name)
            self.assertTrue(label, name)
            self.assertNotIn("_", label, name)

    def test_boot_methods_live_in_the_boot_mixin(self):
        import ast
        text = (TREE / "gui" / "window_shell_boot.py").read_text(
            encoding="utf-8")
        tree = ast.parse(text)
        methods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) \
                    and node.name == "ShellBootMixin":
                methods = {n.name for n in node.body
                           if isinstance(n, ast.FunctionDef)}
        for name in ("_start_boot_sequence", "_boot_screen_widget",
                     "_show_boot_screen", "_update_boot_screen",
                     "_hide_boot_screen", "_tick_boot_sequence",
                     "_skip_boot_sequence", "_finish_boot_sequence",
                     "_stop_boot_timer"):
            self.assertIn(name, methods)
        frame = (TREE / "gui" / "window_shell_frame.py").read_text(
            encoding="utf-8")
        self.assertLess(frame.count("\n"), 1018)
        self.assertIn("class ShellFrameMixin(ShellBootMixin):", frame)


# ------------------------------------------------------------------ G-V8
class TestServiceHost(unittest.TestCase):
    class _Fake:
        def __init__(self):
            self.started = False
            self.stopped = False

        def start(self, *a, **kw):
            self.started = True
            return {"ok": True}

        def ensure_engine_running(self):
            self.started = True
            return {"ok": True}

        def stop(self):
            self.stopped = True
            return {"ok": True}

    def _host(self):
        from core.service_host import ServiceHost
        fakes = {k: self._Fake() for k in
                 ("scheduler", "engine", "hotkey", "clipboard",
                  "failsafe", "wake")}
        host = ServiceHost(scheduler=fakes["scheduler"],
                           engine=fakes["engine"],
                           hotkey=fakes["hotkey"],
                           clipboard=fakes["clipboard"],
                           failsafe=fakes["failsafe"],
                           wake=fakes["wake"],
                           audio_manager=object(),
                           job_scheduler=fakes["engine"])
        return host, fakes

    def test_start_stop_headless(self):
        host, fakes = self._host()
        results = host.start()
        self.assertTrue(all(r["ok"] for r in results.values()))
        self.assertTrue(all(f.started for f in fakes.values()))
        self.assertTrue(all(host.status().values()))
        results = host.stop()
        self.assertTrue(all(r["ok"] for r in results.values()))
        self.assertTrue(all(f.stopped for f in fakes.values()))
        self.assertFalse(any(host.status().values()))

    def test_start_is_idempotent(self):
        host, fakes = self._host()
        host.start()
        second = host.start()
        self.assertTrue(all(r.get("already_started")
                            for r in second.values()))
        host.stop()

    def test_one_failing_service_does_not_break_the_rest(self):
        host, fakes = self._host()

        def _boom(*a, **kw):
            raise RuntimeError("no hotkeys here")

        fakes["hotkey"].start = _boom
        results = host.start()
        self.assertFalse(results["hotkey"]["ok"])
        self.assertTrue(results["scheduler"]["ok"])
        self.assertTrue(results["clipboard"]["ok"])
        host.stop()

    def test_unstarted_host_reports_all_stopped(self):
        from core.service_host import ServiceHost
        host = ServiceHost()
        self.assertFalse(any(host.status().values()))
        self.assertEqual(host.stop(), {})


# ------------------------------------------------------------------ G-V9
class TestPlayBriefing(unittest.TestCase):
    """The briefing button drives run_digest, not a literal prompt."""

    def setUp(self):
        from gui.window_session_watch import SessionWatchMixin
        self._mixin = SessionWatchMixin
        import voice_assistant.core.scheduled_agents as agents
        self._agents = agents
        self._real_run = agents.run_digest
        self._real_last = agents.load_last_digest
        import gui.window_session_watch as watch_mod
        self._watch_mod = watch_mod
        self._real_session = watch_mod.live_session
        self.sent = []
        self.noted = []
        self.system = []
        self.notified = []
        self.earcon_calls = []

        class FakeSession:
            is_connected = True

            def set_speech_speed_override(self, pace):
                pass

            async def send_text(self, text):
                self.sent_text = text
                return {"status": "accepted"}

        self.fake_session = FakeSession()
        self.fake_session.sent_text = None
        watch_mod.live_session = self.fake_session
        # A running loop for run_coroutine_threadsafe.
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever,
                                        daemon=True)
        self._thread.start()

        class FakeSettings:
            def value(self, key, default=None):
                return None

        class FakeWindow(SessionWatchMixin):
            pass

        self.window = FakeWindow()
        self.window.worker = types.SimpleNamespace(loop=self.loop)
        self.window.settings = FakeSettings()
        self.window.earcons = types.SimpleNamespace(
            sent=lambda: self.earcon_calls.append("sent"))
        self.window._note_self_sent = lambda t: self.noted.append(t)
        self.window._add_system = lambda t: self.system.append(t)
        self.window._notify = lambda t: self.notified.append(t)

    def tearDown(self):
        self._agents.run_digest = self._real_run
        self._agents.load_last_digest = self._real_last
        self._watch_mod.live_session = self._real_session
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)

    def _wait_send(self, timeout=5.0):
        import time
        deadline = time.time() + timeout
        while self.fake_session.sent_text is None \
                and time.time() < deadline:
            time.sleep(0.05)

    def test_fresh_digest_is_spoken(self):
        self._agents.run_digest = lambda speak=False: {
            "ok": True,
            "digest": {"id": "d1", "text": "Your day: one meeting.",
                       "kind": "extractive"}}
        self.window._play_briefing()
        self._wait_send()
        self.assertEqual(self.fake_session.sent_text,
                         "Your day: one meeting.")
        self.assertEqual(self.noted, ["Your day: one meeting."])
        self.assertEqual(self.system, ["Playing your morning briefing…"])
        self.assertEqual(self.earcon_calls, ["sent"])
        self.assertEqual(self.notified, [])

    def test_failed_build_falls_back_to_last_digest(self):
        self._agents.run_digest = lambda speak=False: {
            "ok": False, "reason": "store unwritable"}
        self._agents.load_last_digest = lambda: {
            "id": "d0", "text": "Yesterday's digest.", "kind": "extractive"}
        self.window._play_briefing()
        self._wait_send()
        self.assertEqual(self.fake_session.sent_text,
                         "Yesterday's digest.")
        self.assertEqual(self.notified, [])

    def test_no_digest_says_so_honestly(self):
        self._agents.run_digest = lambda speak=False: {
            "ok": False, "reason": "store unwritable"}
        self._agents.load_last_digest = lambda: None
        self.window._play_briefing()
        self.assertIsNone(self.fake_session.sent_text)
        self.assertEqual(len(self.notified), 1)
        self.assertIn("couldn't build your briefing", self.notified[0])


# ------------------------------------------------------------------ census
class TestCensusUnchanged(unittest.TestCase):
    def test_registry_and_audit_totals_pinned(self):
        from core.funnel.tools_registry import tools
        from core.funnel.tools_gate import _CONFIRM_GATE_TOOLS
        import core.catalogue_groups_data as cat
        grouped = set()
        for members in cat.GROUP_MEMBERS.values():
            grouped.update(members)
        params = sum(
            len((s.get("parameters") or {}).get("properties") or {})
            for s in tools._tools.values())
        self.assertEqual(len(tools._tools), 769)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(params, 2121)  #Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(
            len([n for n in tools._tools if n in _CONFIRM_GATE_TOOLS]),
            169)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan (confirm-gated); fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(len(grouped), 766)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 755 -> 759 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        totals = re.search(
            r"AUDIT-TOTALS: (\{[^}]*\})",
            (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
                encoding="utf-8"))
        self.assertIsNotNone(totals)
        data = json.loads(totals.group(1))
        self.assertEqual(
            (data["total"], data["read"], data["effectful"],
             data["confirm_gated"]),
            (769, 384, 385, 169))  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756, 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757, 374 -> 375, 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169


if __name__ == "__main__":
    unittest.main()
