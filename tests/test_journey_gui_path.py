"""Journey 2 — GUI path (whole-path phase).

Open -> settled Home renders -> palette -> a tool action ->
status/ledger/receipt shows it honestly. The REAL MainWindow
constructs offscreen (render_nova pattern: only hardware/service
singletons stubbed — mic, hotkeys, scheduler, live session, vision,
wake word; every line of gui/ code that runs is shipped code),
shows the settled Home, opens the command palette (the Ctrl+K
action, invoked directly), and then a real funnel action leaves
records in the exact places the in-app surfaces read: activity.log
(the readable activity log) and the receipt store.

Stub artifacts, stated not hidden: the stubbed audio manager feeds
the visualizer telemetry a stand-in it cannot measure (a contained
telemetry exception, non-fatal — the real manager returns real
bands); the stubbed memory module lacks as_user_note, so the skills
catalogue logs itself unavailable and the palette opens without
catalogue entries. Neither affects the journey's assertions (window
visible, palette opens, action recorded). The full Home with live
services (real mic, real catalogue, real Live session) is
field-owed on a real desktop.

Qt only: skips without PySide6 / without a creatable QApplication.
Runs under QT_QPA_PLATFORM=offscreen with the syslibs EGL env.
QSettings is redirected to a temp dir (XDG_CONFIG_HOME); working
data/ is guarded and restored.

Stdlib unittest only (plus PySide6).
"""

from __future__ import annotations

import importlib
import os
import pathlib
import shutil
import sys
import tempfile
import types
import unittest

try:
    from PySide6.QtWidgets import QApplication as _QApp
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt optional
    _QT_AVAILABLE = False

try:
    from tests._journey_base import DataGuard
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"journey base unavailable: {exc}")

_TREE = pathlib.Path(__file__).resolve().parent.parent


class _Fake:
    """Permissive stand-in for hardware/service singletons (render_nova)."""

    def __init__(self, *a, **k):
        self.is_playing = False
        self.speaker_level = 0.0
        self.mic_level = 0.0
        self.is_muted = True
        self.spectrum_bands = [0.0] * 16
        self.is_connected = False
        self.client = None
        self.enabled = False

    def __call__(self, *a, **k):
        return _Fake()

    def __getattr__(self, _name):
        return _Fake()

    def start(self, *a, **k):
        pass

    def stop(self, *a, **k):
        pass

    def get_devices(self, *a, **k):
        return {"inputs": [], "outputs": []}

    def get_all_facts(self, *a, **k):
        return []

    def search_facts(self, *a, **k):
        return []

    def get_all_reminders(self, *a, **k):
        return []


class _AudioFake(_Fake):
    """Audio stand-in with a REAL telemetry shape.

    The services telemetry tick calls audio_manager.display_bands()
    every tick and measures len(bands); the permissive _Fake
    returns a stand-in for it, which raises on every tick and
    floods stderr. This subclass returns the real shape (16 quiet
    bands) so the tick stays silent.
    """

    def display_bands(self, _playing):
        return [0.0] * 16


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for key, val in attrs.items():
        setattr(mod, key, val)
    sys.modules[name] = mod


class TestGuiJourney(unittest.TestCase):
    def setUp(self):
        if not _QT_AVAILABLE:
            raise unittest.SkipTest("PySide6 unavailable")
        try:
            self.app = _QApp.instance() or _QApp([])
        except Exception as exc:  # noqa: BLE001 — no Qt platform
            raise unittest.SkipTest(f"QApplication unavailable: {exc}")
        self._guard = DataGuard()
        self._guard.__enter__()
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="journey_gui_"))
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self._tmp)
        self._shimmed = []
        try:
            self._install_shim()
        except Exception:
            self._teardown_shim()
            raise

    def _install_shim(self):
        tree = str(_TREE)
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [tree]
        sys.modules["voice_assistant"] = pkg
        core_pkg = types.ModuleType("voice_assistant.core")
        core_pkg.__path__ = [os.path.join(tree, "core")]
        sys.modules["voice_assistant.core"] = core_pkg
        real_config = importlib.import_module("voice_assistant.core.config")
        sys.modules["voice_assistant.core.config"] = real_config
        _stub("voice_assistant.core.voice.audio", audio_manager=_AudioFake())  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
        _stub("voice_assistant.core.desktop.clipboard_monitor",
              clipboard_monitor=_Fake())  # Phase 3 D3 INTENTIONAL: stub follows clipboard_monitor move
        _stub("voice_assistant.core.desktop.hotkeys", hotkey_daemon=_Fake())  # Phase 3 D3 INTENTIONAL: stub follows hotkeys move
        _stub("voice_assistant.core.live_client", live_session=_Fake())
        _stub("voice_assistant.core.memory.memory", memory_store=_Fake())  # Phase 3 M2h INTENTIONAL: import moved with module
        _stub("voice_assistant.core.plugin_loader",
              discover_plugins=lambda *a, **k: [])
        _stub("voice_assistant.core.scheduler", scheduler=_Fake())
        _stub("voice_assistant.core.memory.session_summary",
              generate_session_summary=lambda *a, **k: None,
              save_session_summary=lambda *a, **k: None)
        _stub("voice_assistant.core.desktop.vision", vision_manager=_Fake())  # Phase 3 D4c INTENTIONAL: stub follows vision hub move
        _stub("voice_assistant.core.voice.wake_word", wake_word_detector=_Fake())  # Phase 3 V1a-2 INTENTIONAL: stub follows wake_word move
        gui_pkg = types.ModuleType("voice_assistant.gui")
        gui_pkg.__path__ = [os.path.join(tree, "gui")]
        sys.modules["voice_assistant.gui"] = gui_pkg
        self._shimmed = [
            "voice_assistant", "voice_assistant.core",
            "voice_assistant.core.config", "voice_assistant.core.voice.audio",  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
            "voice_assistant.core.desktop.clipboard_monitor",  # Phase 3 D3 INTENTIONAL: stub follows clipboard_monitor move
            "voice_assistant.core.desktop.hotkeys",  # Phase 3 D3 INTENTIONAL: stub follows hotkeys move
            "voice_assistant.core.live_client",
            "voice_assistant.core.memory.memory",  # Phase 3 M2h INTENTIONAL: import moved with module
            "voice_assistant.core.plugin_loader",
            "voice_assistant.core.scheduler",
            "voice_assistant.core.memory.session_summary",
            "voice_assistant.core.desktop.vision",  # Phase 3 D4c INTENTIONAL: stub follows vision hub move
            "voice_assistant.core.voice.wake_word",  # Phase 3 V1a-2 INTENTIONAL: stub follows wake_word move
            "voice_assistant.gui",
        ]

    def _teardown_shim(self):
        for name in getattr(self, "_shimmed", []):
            sys.modules.pop(name, None)
        for name in [m for m in list(sys.modules)
                     if m.startswith("voice_assistant.gui.main_window")]:
            sys.modules.pop(name, None)

    def tearDown(self):
        window = getattr(self, "_window", None)
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
            self.app.processEvents()
        self._teardown_shim()
        if self._orig_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg
        self._guard.__exit__(None, None, None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_open_home_palette_action_ledger(self):
        # GAPS-30: the journey is the SETTLED Home, not first-run
        # onboarding. A fresh QSettings (temp XDG) reads
        # "onboarded" as False, so MainWindow schedules
        # _run_onboarding on a 700 ms single-shot — and its Terms
        # modal exec() has no user to accept it, so the test
        # blocks forever (under tracing the timer fires during
        # tearDown's processEvents; the onboarding flow itself
        # is covered by test_onboarding_mic.py, not here).
        from PySide6.QtCore import QSettings as _QSettings
        _QSettings("NovaAI", "Nova").setValue("onboarded", True)
        mw = importlib.import_module("voice_assistant.gui.main_window")
        window = mw.MainWindow()
        self._window = window
        # GAPS-30: stop the animation timers before pumping
        # events. The journey's assertions do not depend on the
        # orb animating, and under tracing a paint costs more
        # than the 25/33 ms timer intervals, so the paint queue
        # never drains and processEvents() never returns. (The
        # timers are a product feature; this test is not testing
        # the animation.)
        _audio_timer = getattr(window, "audio_timer", None)
        if _audio_timer is not None:
            _audio_timer.stop()
        _mini = getattr(window, "mini_orb", None)
        if _mini is not None:
            _mini_timer = getattr(_mini, "_timer", None)
            if _mini_timer is not None:
                _mini_timer.stop()
        window.show()
        self.app.processEvents()
        self.assertTrue(window.isVisible(), "settled Home did not show")
        self.assertTrue(hasattr(window, "btn_cmd_pill"),
                        "Home lost its command pill (Ctrl+K entry)")
        window._show_command_palette()
        self.app.processEvents()
        palette = getattr(window, "_palette", None)
        self.assertIsNotNone(palette, "palette did not open")
        self.assertEqual(type(palette).__name__, "CommandPalette")
        self.assertTrue(palette.isVisible(), "palette opened but not visible")

        from core import tool_receipts as receipts_mod
        from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
        from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
        from core import confirm_gate, injection
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        tools_mod.set_session_provenance("user")
        store = receipts_mod._get_store()
        before = {a["receipt_id"] for a in store.attempts()
                  if a.get("tool") == "get_current_time_date"}
        out = tools.execute("get_current_time_date", {})
        self.assertEqual(out.get("status"), "success", str(out))
        after = [a for a in store.attempts()
                 if a.get("tool") == "get_current_time_date"
                 and a["receipt_id"] not in before]
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0].get("status"), "succeeded")
        activity = (_TREE / "data" / "activity.log").read_text(
            encoding="utf-8", errors="replace")
        self.assertIn("get_current_time_date", activity,
                      "activity log (the in-app readable record) "
                      "does not show the action")


if __name__ == "__main__":
    unittest.main()
