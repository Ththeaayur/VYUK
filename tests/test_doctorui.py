"""Doctor UI garble regression (field evidence 2026-10-30).

The owner's screenshot showed the Doctor page's cards squashed
to 1-6px strips on a window shorter than the ~1570px content.
Lab repro (doctor_repro.py, outside the tree) proved the cause:
the doctor stack page had NO page-level scroll area — only the
findings list scrolls — so the VBox compressed every card widget
below its minimum (hero title 6px vs 22, resilience label 1px
vs 162, buttons 1-2px vs 36) at a 1260x800 window, and wrapping
the page in a widgetResizable QScrollArea removed every flag
at 800 and at 650.

This suite pins the fix two ways: (1) AST — _build_doctor_page
returns a QScrollArea wrapper (widgetResizable, frameless,
autoFill off) around the unchanged page; (2) offscreen geometry
— the fixed hosting gives every visible label its full text
height and every button at least 20px, with no widget below its
minimumSizeHint and no sibling overlaps, at a 650px viewport.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast
import sys
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent


# T-3 (debug pass): gui modules import the frozen voice_assistant.*
# package names (gui/doctor_page.py does `from voice_assistant.core.
# gui_facade import ...`) — the alias is registered at runtime by
# doctor._register_package(), so tests register it themselves.
# Snapshot/restore discipline (L3): register only keys that are
# absent, and tearDownModule removes exactly the keys this module
# added — never delete-all.
_ADDED_VA_MODULES = []


def setUpModule():
    for key, path in (
        ("voice_assistant", str(TREE)),
        ("voice_assistant.core", str(TREE / "core")),
        ("voice_assistant.gui", str(TREE / "gui")),
    ):
        if key not in sys.modules:
            pkg = types.ModuleType(key)
            pkg.__path__ = [path]
            sys.modules[key] = pkg
            _ADDED_VA_MODULES.append(key)


def tearDownModule():
    for key in _ADDED_VA_MODULES:
        sys.modules.pop(key, None)
    _ADDED_VA_MODULES.clear()


def _fn_src(tree_text, name):
    mod = ast.parse(tree_text)
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(tree_text, node)
    # Methods live inside classes; walk those too.
    for node in ast.walk(mod):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if (isinstance(item, ast.FunctionDef)
                        and item.name == name):
                    return ast.get_source_segment(tree_text, item)
    return ""


class TestDoctorScrollWrapper(unittest.TestCase):
    def test_build_doctor_page_returns_scroll_wrapper(self):
        # GUI split #8 INTENTIONAL: _build_doctor_page moved verbatim to
        # window_pages_system.py. Assertion unchanged.
        text = (TREE / "gui" / "window_pages_system.py").read_text()
        src = _fn_src(text, "_build_doctor_page")
        self.assertTrue(src, "_build_doctor_page not found")
        # The wrapper itself (reminders-page pattern).
        self.assertIn("QScrollArea()", src)
        self.assertIn("setWidgetResizable(True)", src)
        self.assertIn("setFrameShape(QFrame.Shape.NoFrame)", src)
        self.assertIn("scroll.setWidget(page)", src)
        # setWidget() flips autoFillBackground on; the fix turns
        # it back off so no opaque fill washes out the cards.
        self.assertIn("page.setAutoFillBackground(False)", src)
        self.assertIn("return scroll", src)
        # Everything the page was before is unchanged.
        self.assertIn("self.doctor_page = DoctorPage(self)", src)
        self.assertIn("layout.addWidget(self.doctor_page, stretch=1)",
                      src)
        self.assertIn("Distribution & repair", src)


class _FakeSettings:
    def __init__(self):
        self._store = {}

    def value(self, key, default=""):
        return self._store.get(key, default)

    def setValue(self, key, value):
        self._store[key] = value


class _StubWindow:
    def __init__(self):
        self.settings = _FakeSettings()

    def _show_page(self, *args):
        pass

    def show_settings_card(self, *args):
        pass

    def _setup_local_brain(self):
        pass


class _FakeGate:
    def render_status_lines(self):
        return [
            "Resilience wiring: healthy",
            "Safe mode: not engaged",
            # INTENTIONAL (Phase 2 B3 rename): fixture mimics the real "Libraries wired" line, which lists current module names (was "rl_state_v575, rl_outbox_v575").
            "Libraries wired: resilience_state, resilience_outbox",
            "Fence: tripped failsafe = zero dispatches",
            "Fence: no auto-resume without explicit human re-arm",
            "Fence: durability outside the executor",
            "Pilot-only (owed licence reads): APSW, Litestream",
            # Fix round (F-04, 2026-11-26): the field-proof line
            # reads in the parent's plain wording now.
            "Still owed on your machine: a kill drill — your run, "
            "your verdict",
            "Last trip: none recorded in this session",
        ]


class _FakeHarness:
    entries = {}


def _geometry_ok():
    """Build the fixed hosting offscreen and check geometry.

    Returns a list of violation strings (empty == pass). The
    gate/harness are stubbed so the suite never touches global
    gate state or the real data dir; the layout under test is
    real (DoctorPage + shell + scroll wrapper, app stylesheet
    when importable).
    """
    try:
        import PySide6  # noqa: F401 — availability probe only
    except ImportError:
        # System python3 in this lab has no PySide6 (the venv-qt
        # interpreter does); the AST pin above still runs there,
        # and the geometry proof runs where Qt exists.
        return ["SKIP: PySide6 not installed on this interpreter"]
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )
    from gui.doctor_page import DoctorPage

    if QApplication.instance() is None:
        try:
            QApplication([])
        except Exception as exc:  # noqa: BLE001
            return [f"SKIP: QApplication failed: {exc}"]
    host = QWidget()
    try:
        from gui.style import PREMIUM_STYLE
        host.setStyleSheet(PREMIUM_STYLE)
    except Exception:  # noqa: BLE001 — geometry is style-independent
        pass
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 20, 28, 20)
    layout.setSpacing(10)
    layout.addWidget(QLabel("DOCTOR"))
    layout.addWidget(QLabel("Her health, at a glance."))
    layout.addSpacing(6)
    orig_gate = DoctorPage._resilience_gate
    orig_harness = DoctorPage._shared_harness
    DoctorPage._resilience_gate = lambda self: _FakeGate()  # noqa: E731
    DoctorPage._shared_harness = lambda self: _FakeHarness()  # noqa: E731
    try:
        doctor = DoctorPage(_StubWindow())
        layout.addWidget(doctor, stretch=1)
        dcard = QFrame()
        dcard.setObjectName("card")
        dl = QVBoxLayout(dcard)
        dl.setContentsMargins(16, 12, 16, 12)
        dl.addWidget(QLabel("Distribution & repair"))
        for label in ("Verify install", "Repair from pristine...",
                      "Machine health", "Reset settings..."):
            dl.addWidget(QPushButton(label))
        layout.addWidget(dcard)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        page.setAutoFillBackground(False)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        host.setFixedSize(1260, 650)
        host.show()
        app = QApplication.instance()
        app.processEvents()
        doctor._on_done({
            "ok": True, "text": "report",
            "counts": {"PASS": 41, "WARN": 20, "FAIL": 3},
            "findings": [
                {"status": "FAIL",
                 "label": "Doctor check: check_watchers",
                 "detail": "watchers unhealthy",
                 "fix": "restart the watchers"},
                {"status": "WARN", "label": "Sample warning",
                 "detail": "detail here", "fix": ""},
            ],
        })
        app.processEvents()
        page.layout().activate()
        doctor.layout().activate()
        app.processEvents()
        problems = []
        # Wrapper properties (the fix itself, live).
        if not scroll.widgetResizable():
            problems.append("scroll wrapper is not widgetResizable")
        if scroll.frameShape() != QFrame.Shape.NoFrame:
            problems.append("scroll wrapper is not frameless")
        if scroll.widget() is not page:
            problems.append("scroll wrapper does not hold the page")
        if page.autoFillBackground():
            problems.append("page autoFillBackground still on")
        base = page.mapToGlobal(__import__(
            "PySide6.QtCore", fromlist=["QPoint"]).QPoint(0, 0))
        seen = []
        for w in page.findChildren(QWidget):
            if not w.isVisible():
                continue
            try:
                if w.visibleRegion().isEmpty():
                    continue
            except Exception:  # noqa: BLE001
                pass
            cls = type(w).__name__
            if cls not in ("QLabel", "QPushButton"):
                continue
            g = w.geometry()
            glob = w.mapToGlobal(__import__(
                "PySide6.QtCore", fromlist=["QPoint"]).QPoint(0, 0)) - base
            text = w.text() if hasattr(w, "text") else ""
            if g.height() < w.minimumSizeHint().height():
                problems.append(
                    f"below minimum: {cls} '{text[:40]}' "
                    f"{g.height()} < min "
                    f"{w.minimumSizeHint().height()}")
            if cls == "QLabel":
                fm = w.fontMetrics()
                rect = fm.boundingRect(
                    0, 0, max(1, g.width()), 10000,
                    Qt.TextFlag.TextWordWrap if w.wordWrap()
                    else Qt.TextFlag(0), text)
                if g.height() < rect.height():
                    problems.append(
                        f"clipped label: '{text[:40]}' "
                        f"{g.height()} < text {rect.height()}")
                if g.height() < fm.height():
                    problems.append(
                        f"collapsed label: '{text[:40]}' "
                        f"{g.height()} < one line {fm.height()}")
            if cls == "QPushButton" and g.height() < 20:
                problems.append(
                    f"collapsed button: '{text[:40]}' "
                    f"{g.height()} < 20")
            seen.append((w.parent(), int(glob.x()), int(glob.y()),
                         int(g.width()), int(g.height()),
                         cls, text[:40]))
        by_parent = {}
        for item in seen:
            by_parent.setdefault(item[0], []).append(item)
        for parent, items in by_parent.items():
            for i, a in enumerate(items):
                for b in items[i + 1:]:
                    if (a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
                            and a[2] < b[2] + b[4]
                            and b[2] < a[2] + a[4]):
                        problems.append(
                            f"overlap under {type(parent).__name__}: "
                            f"{a[5]} '{a[6]}' vs {b[5]} '{b[6]}'")
        return problems
    finally:
        DoctorPage._resilience_gate = orig_gate
        DoctorPage._shared_harness = orig_harness
        host.deleteLater()


class TestDoctorFixedHostingGeometry(unittest.TestCase):
    def test_scroll_wrapped_page_keeps_natural_heights(self):
        problems = _geometry_ok()
        if problems and problems[0].startswith("SKIP:"):
            self.skipTest(problems[0])
        self.assertEqual(problems, [], "\n".join(problems))


# ----------------------------------------------------------------------
# Field-check fix #3 (U7): closing the window while the Doctor
# checks worker runs used to abort the process (SIGABRT — Qt
# destroys a QThread still running). The worker now stops
# (interruption + child-process kill) and the window's closeEvent
# stops it first. These tests pin the stop seam three ways: the
# real worker ends promptly when stopped, the page's stop_checks
# drives it and never raises, and the wiring is pinned by AST so
# a later split cannot silently drop it.
# ----------------------------------------------------------------------

def _ensure_qapp():
    """A QApplication for the Qt-only tests below (same probe
    pattern as _geometry_ok)."""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return None
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        QApplication([])
    return QApplication.instance()


class _FakeDoctorModule:
    """Stand-in for voice_assistant.doctor in the worker tests."""

    def __init__(self, path="", checks=()):
        self.__file__ = path
        self._CHECKS = list(checks)
        self._counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

    def report(self, status, label, detail="", fix=""):
        self._counts[status] += 1


class TestDoctorWorkerStop(unittest.TestCase):
    """The real _DoctorWorker ends promptly when stopped."""

    def setUp(self):
        self.app = _ensure_qapp()
        if self.app is None:
            self.skipTest("PySide6 not installed on this interpreter")
        self._saved_doctor = sys.modules.get("voice_assistant.doctor",
                                             None)
        self._had_doctor = "voice_assistant.doctor" in sys.modules
        # Fix round (F-P4-03, 2026-11-26): the Doctor page now
        # imports the real doctor module at construction (tile
        # counts), which ALSO binds it as the `doctor` attribute
        # on the voice_assistant package — and `from
        # voice_assistant import doctor` answers from that
        # attribute first, never consulting sys.modules. A fake
        # installed only in sys.modules is therefore invisible
        # to the worker whenever a real page was built earlier in
        # the process (the full suite failed exactly this way
        # while the single test passed). Save the attribute too,
        # and install the fake on both.
        _pkg = sys.modules.get("voice_assistant")
        self._had_pkg_doctor = (
            _pkg is not None and hasattr(_pkg, "doctor"))
        self._saved_pkg_doctor = (
            getattr(_pkg, "doctor", None) if _pkg is not None else None)
        self._tmp = None

    def tearDown(self):
        if self._had_doctor:
            sys.modules["voice_assistant.doctor"] = self._saved_doctor
        else:
            sys.modules.pop("voice_assistant.doctor", None)
        _pkg = sys.modules.get("voice_assistant")
        if _pkg is not None:
            if self._had_pkg_doctor:
                _pkg.doctor = self._saved_pkg_doctor
            elif hasattr(_pkg, "doctor"):
                delattr(_pkg, "doctor")
        if self._tmp is not None:
            self._tmp.cleanup()

    def _install_doctor(self, module):
        sys.modules["voice_assistant.doctor"] = module
        _pkg = sys.modules.get("voice_assistant")
        if _pkg is not None:
            _pkg.doctor = module

    def test_stop_kills_the_child_process_and_ends_the_thread(self):
        import tempfile
        import time

        from gui.doctor_worker import _DoctorWorker

        self._tmp = tempfile.TemporaryDirectory()
        script = os.path.join(self._tmp.name, "slow_doctor.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("import time\ntime.sleep(30)\n")
        self._install_doctor(_FakeDoctorModule(path=script))
        emitted = []
        worker = _DoctorWorker()
        worker.done.connect(lambda payload: emitted.append(payload))
        worker.start()
        # Wait until the child process is actually running.
        deadline = time.monotonic() + 10
        while (getattr(worker, "_proc", None) is None
               and time.monotonic() < deadline):
            time.sleep(0.02)
        self.assertIsNotNone(worker._proc, "child never started")
        self.assertTrue(worker.isRunning())
        stopped = time.monotonic()
        worker.stop()
        self.assertTrue(worker.wait(5000), "worker did not end")
        self.assertLess(time.monotonic() - stopped, 10,
                        "stop took nearly as long as the 30 s check")
        self.assertFalse(worker.isRunning())
        # A stopped worker reports nothing — its caller is closing
        # the page, and a late result would re-enable buttons (and
        # could pop a confirm dialog) on a window going away.
        self.assertEqual(emitted, [])

    def test_stop_is_a_noop_on_a_worker_that_never_started(self):
        from gui.doctor_worker import _DoctorWorker

        worker = _DoctorWorker()
        worker.stop()  # must never raise
        self.assertFalse(worker.isRunning())

    def test_in_process_fallback_honours_the_stop_between_checks(self):
        import threading
        import time

        from gui.doctor_worker import _DoctorWorker

        ran = []
        entered = threading.Event()
        release = threading.Event()

        def slow_check():
            entered.set()
            release.wait(timeout=10)

        def third_check():
            ran.append("third")

        self._install_doctor(_FakeDoctorModule(
            path="", checks=[slow_check, third_check]))
        emitted = []
        worker = _DoctorWorker()
        worker.done.connect(lambda payload: emitted.append(payload))
        worker.start()
        self.assertTrue(entered.wait(timeout=10),
                        "fallback never started the first check")
        worker.stop()
        release.set()
        self.assertTrue(worker.wait(5000), "worker did not end")
        self.assertEqual(ran, [], "a check ran after the stop")
        self.assertEqual(emitted, [])


class _RecordingWorker:
    """Fake _DoctorWorker for the page-level stop tests."""

    def __init__(self, running=True, wait_ok=True,
                 raise_stale=False):
        self._running = running
        self._wait_ok = wait_ok
        self._raise_stale = raise_stale
        self.stopped = False
        self.waits = []
        self.terminated = False

    def isRunning(self):  # noqa: N802 (Qt naming)
        if self._raise_stale:
            raise RuntimeError("Internal C++ object already deleted")
        return self._running

    def stop(self):
        # A real QThread stays running after stop() until its run()
        # returns — only a successful wait() ends it.
        self.stopped = True

    def wait(self, msecs):  # noqa: N802 (Qt naming)
        self.waits.append(msecs)
        if self._wait_ok:
            self._running = False
        return self._wait_ok

    def terminate(self):
        self.terminated = True
        self._running = False


class TestDoctorPageStopChecks(unittest.TestCase):
    """DoctorPage.stop_checks drives the worker and never raises."""

    def setUp(self):
        self.app = _ensure_qapp()
        if self.app is None:
            self.skipTest("PySide6 not installed on this interpreter")

    def _page(self):
        from gui.doctor_page import DoctorPage

        return DoctorPage(_StubWindow())

    def test_stop_checks_stops_a_running_worker_and_clears_it(self):
        page = self._page()
        fake = _RecordingWorker(running=True, wait_ok=True)
        page._worker = fake
        page.stop_checks()
        self.assertTrue(fake.stopped)
        self.assertEqual(fake.waits, [5000])
        self.assertFalse(fake.terminated)
        self.assertIsNone(page._worker)

    def test_stop_checks_is_a_noop_without_a_worker(self):
        page = self._page()
        page._worker = None
        page.stop_checks()  # must never raise

    def test_stop_checks_clears_a_stale_worker_without_raising(self):
        page = self._page()
        page._worker = _RecordingWorker(raise_stale=True)
        page.stop_checks()  # must never raise
        self.assertIsNone(page._worker)

    def test_stop_checks_terminates_a_worker_that_will_not_end(self):
        page = self._page()
        fake = _RecordingWorker(running=True, wait_ok=False)
        page._worker = fake
        page.stop_checks()
        self.assertTrue(fake.stopped)
        self.assertTrue(fake.terminated)
        self.assertIsNone(page._worker)


class TestDoctorCloseWiring(unittest.TestCase):
    """AST pins: the close path actually reaches the worker stop."""

    def test_close_event_stops_the_doctor_worker_first(self):
        src = (TREE / "gui" / "window_memory_watchers.py").read_text()
        fn = _fn_src(src, "closeEvent")
        self.assertTrue(fn, "closeEvent not found")
        self.assertIn("doctor_page", fn)
        self.assertIn("stop_checks()", fn)

    def test_worker_stop_kills_the_child_process(self):
        src = (TREE / "gui" / "doctor_worker.py").read_text()
        self.assertIn("def stop(self):", src)
        self.assertIn("subprocess.Popen(", src)
        self.assertIn("proc.kill()", src)
        # A blocking run() cannot be interrupted — the Popen form
        # is what makes stop() work at all.
        self.assertNotIn("subprocess.run(", src)

    def test_page_defines_stop_checks(self):
        src = (TREE / "gui" / "doctor_page.py").read_text()
        self.assertIn("def stop_checks(self):", src)


if __name__ == "__main__":
    unittest.main()
