"""B-R1-4w tests: the boot-sequence call-site wiring.

The wiring is gui/window_shell_boot.py (ShellBootMixin, moved
verbatim out of gui/window_shell_frame.py by GAPS-28, G-V7) with
the one call site in gui/window_shell_frame.py (ShellFrameMixin
``__init__``): one call site that creates ONE BootSequence at
startup, ticks it from a timer (one stage per tick, never a
blocking wait), and skips it on Escape. The AST checks run on
BOTH interpreters (no Qt needed — they parse the file). The
behavioural checks are Qt-only (they run the real mixin methods
on a fake shell and, once, on the real MainWindow offscreen) and
skip without PySide6, the test_journey_gui_path pattern. No real
mic, no real audio: the sequence's own checks are status reads,
and the senses-timeline writer is patched to record instead of
writing.
"""

import ast
import importlib
import os
import pathlib
import sys
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication as _QApp
    from PySide6.QtWidgets import QWidget as _QWidget
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt optional, skip behavioural
    _QT_AVAILABLE = False

_TREE = pathlib.Path(__file__).resolve().parent.parent
_SHELL_PATH = _TREE / "gui" / "window_shell_frame.py"
_BOOT_PATH = _TREE / "gui" / "window_shell_boot.py"

_WIRING_METHODS = (
    "_start_boot_sequence",
    "_tick_boot_sequence",
    "_skip_boot_sequence",
    "_finish_boot_sequence",
    "_stop_boot_timer",
)


def _shell_source():
    return _SHELL_PATH.read_text(encoding="utf-8")


def _boot_source():
    """The boot methods' file (GAPS-28: moved out of the frame)."""
    return _BOOT_PATH.read_text(encoding="utf-8")


def _method_segment(source, name):
    """The source text of one method (by AST segment)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _wiring_source(source):
    return "\n".join(_method_segment(source, name)
                     for name in _WIRING_METHODS)


class TestBootWiringAst(unittest.TestCase):
    """The wiring's shape, from the file itself — no Qt needed."""

    def test_start_is_scheduled_after_first_paint(self):
        source = _shell_source()
        init = _method_segment(source, "__init__")
        self.assertIn(
            "QTimer.singleShot(100, self._start_boot_sequence)", init,
            "the boot must start on a singleShot after first paint, "
            "not during construction")

    def test_start_is_idempotent(self):
        source = _boot_source()
        start = _method_segment(source, "_start_boot_sequence")
        self.assertIn("if existing is not None:", start)
        self.assertIn("return existing", start)

    def test_tick_runs_exactly_one_stage(self):
        source = _boot_source()
        tick = _method_segment(source, "_tick_boot_sequence")
        tree = ast.parse(tick)
        advances = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "advance"]
        self.assertEqual(len(advances), 1,
                         "one tick runs exactly one stage")

    def test_no_blocking_wait_in_the_wiring(self):
        source = _boot_source()
        wiring = _wiring_source(source)
        self.assertNotIn("run_all", wiring,
                         "the UI driver ticks advance(), never run_all()")
        self.assertNotIn(".wait(", wiring)
        self.assertNotIn("time.sleep", wiring)

    def test_onboarding_runs_exactly_as_before(self):
        source = _shell_source()
        init = _method_segment(source, "__init__")
        self.assertIn(
            "QTimer.singleShot(700, self._run_onboarding)", init)
        self.assertIn('"onboarded"', init,
                      "the onboarding timer stays gated on the "
                      "onboarded setting")
        wiring = _wiring_source(_boot_source())
        self.assertNotIn("_run_onboarding", wiring,
                         "the boot never calls the onboarding")

    def test_boot_never_renders_through_on_status(self):
        # A boot status would falsely clear the session-liveness
        # probe (window_session_status.py: "any status proves the
        # worker loop is alive" — the boot is not the worker).
        wiring = _wiring_source(_boot_source())
        self.assertNotIn("_on_status", wiring)

    def test_skip_calls_the_sequence_skip(self):
        source = _boot_source()
        skip = _method_segment(source, "_skip_boot_sequence")
        self.assertIn(".skip()", skip)


class _Fake:
    """Permissive stand-in for hardware/service singletons
    (the test_journey_gui_path pattern)."""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _install_voice_assistant_shim():
    """The test_journey_gui_path shim, minimal for this file:
    ``voice_assistant`` as a namespace over the tree root, the
    real config, and a stubbed audio_manager (hardware).
    Returns the module names it added, for teardown."""
    tree = str(_TREE)
    added = []

    def _pkg(name, path):
        if name in sys.modules:
            return
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod
        added.append(name)

    _pkg("voice_assistant", tree)
    _pkg("voice_assistant.core", os.path.join(tree, "core"))
    _pkg("voice_assistant.core.voice", os.path.join(tree, "core", "voice"))
    _pkg("voice_assistant.gui", os.path.join(tree, "gui"))
    if "voice_assistant.core.config" not in sys.modules:
        importlib.import_module("voice_assistant.core.config")
        added.append("voice_assistant.core.config")
    if "voice_assistant.core.voice.audio" not in sys.modules:
        mod = types.ModuleType("voice_assistant.core.voice.audio")
        mod.audio_manager = _Fake()
        sys.modules["voice_assistant.core.voice.audio"] = mod
        added.append("voice_assistant.core.voice.audio")
    return added


@unittest.skipUnless(_QT_AVAILABLE, "Qt only: PySide6 unavailable")
class TestBootWiringBehaviour(unittest.TestCase):
    """The real mixin methods, on a fake shell (no MainWindow)
    and once on the real window offscreen."""

    @classmethod
    def setUpClass(cls):
        cls._app = _QApp.instance() or _QApp([])
        cls._shimmed = _install_voice_assistant_shim()
        cls._frame = importlib.import_module(
            "voice_assistant.gui.window_shell_frame")
        cls._mixin = cls._frame.ShellFrameMixin

    @classmethod
    def tearDownClass(cls):
        for name in reversed(cls._shimmed):
            sys.modules.pop(name, None)

    def _fake_shell(self):
        """A bare QWidget carrying the real wiring methods —
        no window, no settings, no UI. The methods only touch
        _boot_sequence / _boot_timer / _boot_skip_shortcut /
        boot_report, so a bare widget suffices (a QWidget,
        not a QObject — QShortcut needs a widget parent)."""
        obj = _QWidget()
        obj._BOOT_TICK_MS = self._mixin._BOOT_TICK_MS
        for name in _WIRING_METHODS:
            func = getattr(self._mixin, name)
            setattr(obj, name, types.MethodType(func, obj))
        return obj

    def _patch_senses(self):
        """Record senses-timeline writes instead of writing."""
        import voice_assistant.core.senses_log as senses
        calls = []
        real = senses.append_event

        def _fake(kind, message, *a, **k):
            calls.append((kind, message))
        senses.append_event = _fake
        self.addCleanup(setattr, senses, "append_event", real)
        return calls

    def test_second_start_returns_the_existing_sequence(self):
        shell = self._fake_shell()
        first = shell._start_boot_sequence()
        self.assertIsNotNone(first)
        timer = shell._boot_timer
        self.assertIsNotNone(timer)
        second = shell._start_boot_sequence()
        self.assertIs(second, first,
                      "a second start returns the existing sequence, "
                      "never a second boot")
        self.assertIs(shell._boot_timer, timer,
                      "the timer is not recreated either")
        shell._skip_boot_sequence()

    def test_start_returns_without_running_any_stage(self):
        shell = self._fake_shell()
        sequence = shell._start_boot_sequence()
        self.assertEqual(sequence.pending, 10,
                         "start must not run any stage — the driver "
                         "never waits for the sequence")
        self.assertTrue(all(s["verdict"] == "unchecked"
                            for s in shell.boot_report["stages"]))
        shell._skip_boot_sequence()

    def test_one_tick_advances_exactly_one_stage(self):
        shell = self._fake_shell()
        self._patch_senses()
        sequence = shell._start_boot_sequence()
        shell._tick_boot_sequence()
        self.assertEqual(sequence.pending, 9)
        checked = [s for s in shell.boot_report["stages"]
                   if s["checked"]]
        self.assertEqual(len(checked), 1)
        shell._tick_boot_sequence()
        self.assertEqual(sequence.pending, 8)
        shell._skip_boot_sequence()

    def test_skip_ends_cleanly_and_records_it(self):
        shell = self._fake_shell()
        calls = self._patch_senses()
        sequence = shell._start_boot_sequence()
        shell._tick_boot_sequence()
        shell._tick_boot_sequence()
        report = shell._skip_boot_sequence()
        self.assertTrue(sequence.complete)
        self.assertTrue(sequence.skipped)
        self.assertTrue(report["skipped"])
        self.assertIn("skipped", report["greeting"])
        remaining = [s for s in report["stages"]
                     if s["verdict"] == "skipped"]
        self.assertEqual(len(remaining), 8,
                         "the 8 unrun stages are marked skipped")
        self.assertTrue(any(k == "boot" and "skipped" in m
                            for k, m in calls))

    def test_complete_sequence_stops_timer_and_shortcut(self):
        shell = self._fake_shell()
        self._patch_senses()
        sequence = shell._start_boot_sequence()
        while not sequence.complete:
            shell._tick_boot_sequence()
        self.assertFalse(shell._boot_timer.isActive())
        self.assertFalse(shell._boot_skip_shortcut.isEnabled())
        self.assertTrue(shell.boot_report["complete"])
        self.assertIn("greeting", shell.boot_report)

    def test_verdict_lines_reach_the_senses_timeline(self):
        shell = self._fake_shell()
        calls = self._patch_senses()
        sequence = shell._start_boot_sequence()
        while not sequence.complete:
            shell._tick_boot_sequence()
        boot_lines = [m for k, m in calls if k == "boot"]
        self.assertEqual(len(boot_lines), 11,
                         "10 stage lines + the greeting")
        self.assertTrue(boot_lines[0].startswith("[identity]"))
        self.assertTrue(boot_lines[-1].startswith("Good "))

    def test_real_window_start_is_idempotent(self):
        import importlib
        try:
            mw = importlib.import_module(
                "voice_assistant.gui.main_window")
            window = mw.MainWindow()
        except Exception as exc:  # noqa: BLE001 — construction is heavy
            raise unittest.SkipTest(
                f"MainWindow unavailable offscreen: {exc}")
        try:
            self._patch_senses()
            first = window._start_boot_sequence()
            self.assertIsNotNone(first)
            second = window._start_boot_sequence()
            self.assertIs(second, first)
            window._skip_boot_sequence()
        finally:
            try:
                window.close()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass


if __name__ == "__main__":
    unittest.main()
