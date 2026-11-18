"""Self-test auto-run (owner decision 2026-11-01 #12): the Self-test
AUTO-RUNS when opened, plus a manual re-run button; the auto-run
set is safe, fast tests only.

System-python tests cover the set itself (gui/selftest_checks.py
is stdlib-only at top level, so it imports anywhere). Qt tests
cover the page + worker (gui/selftest_page.py) and skip where
PySide6 is unavailable — the established pattern for Qt suites
in this tree.
"""
import hashlib
import socket
import time
import unittest
from pathlib import Path

from gui import selftest_checks as sc

TREE = Path(__file__).resolve().parent.parent
DATA = TREE / "data"

EXPECTED_NAMES = [
    "Python",
    "Package layout",
    "Settings",
    "Microphone",
    "On-device voice",
    "Install integrity",
]


def _snapshot_data():
    snap = {}
    if not DATA.is_dir():
        return snap
    for path in sorted(DATA.rglob("*")):
        if path.is_file():
            snap[str(path.relative_to(DATA))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return snap


class TestSelfTestSet(unittest.TestCase):
    def test_set_membership_and_shape(self):
        rows = sc.run_first_run_self_tests()
        self.assertEqual([r[0] for r in rows], EXPECTED_NAMES)
        for name, ok, detail in rows:
            self.assertIsInstance(name, str)
            self.assertIsInstance(ok, bool)
            self.assertIsInstance(detail, str)
            self.assertTrue(detail, f"{name} has an empty detail")

    def test_names_constant_matches_function(self):
        self.assertEqual(list(sc.SELFTEST_CHECK_NAMES),
                         [r[0] for r in sc.run_first_run_self_tests()])

    def test_data_untouched_by_full_run(self):
        before = _snapshot_data()
        self.assertTrue(before, "lab data/ snapshot is empty")
        sc.run_first_run_self_tests()
        self.assertEqual(_snapshot_data(), before)

    def test_no_network_dependency(self):
        real_socket = socket.socket
        real_create = socket.create_connection

        def _boom(*args, **kwargs):
            raise AssertionError("network use in the auto-run set")

        socket.socket = _boom
        socket.create_connection = _boom
        try:
            rows = sc.run_first_run_self_tests()
        finally:
            socket.socket = real_socket
            socket.create_connection = real_create
        self.assertEqual(len(rows), len(EXPECTED_NAMES))


try:
    from PySide6.QtWidgets import QApplication, QLabel

    from gui.selftest_page import _SelfTestPage

    _QT = True
except Exception:  # noqa: BLE001 — system python has no PySide6
    _QT = False


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _pump_until(pred, timeout=10.0):
    app = _app()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    app.processEvents()
    return pred()


@unittest.skipUnless(_QT, "Qt unavailable on this interpreter")
class TestSelfTestPage(unittest.TestCase):
    def setUp(self):
        self._app = _app()
        self._pages = []

    def tearDown(self):
        for page in self._pages:
            try:
                page.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self._pages = []
        self._app.processEvents()

    def _make_page(self, stub):
        page = _SelfTestPage(QLabel("Self-test"), stub)
        self._pages.append(page)
        return page

    def _wait_done(self, page, calls, want):
        return _pump_until(
            lambda: page._run_btn.isEnabled()
            and page._run_btn.text() == "Run again"
            and calls[0] >= want)

    def test_no_run_at_construction(self):
        calls = [0]

        def stub():
            calls[0] += 1
            return [("Python", True, "ok")]

        page = self._make_page(stub)
        self.assertEqual(calls[0], 0)
        self.assertTrue(page._run_btn.isEnabled())
        self.assertEqual(page._run_btn.text(), "Run again")
        self.assertTrue(all(lbl.text() == "…"
                            for lbl in page._rows.values()))

    def test_open_triggers_run_and_paints(self):
        calls = [0]

        def stub():
            calls[0] += 1
            return [("Python", True, "Running 3.12.3"),
                    ("Package layout", False, "Missing folder(s): data")]

        page = self._make_page(stub)
        page.show()
        self.assertTrue(self._wait_done(page, calls, 1))
        self.assertTrue(_pump_until(
            lambda: page._rows["Python"].text() == "✓ Pass"))
        self.assertEqual(page._rows["Package layout"].text(), "FAULT")
        self.assertIn("Package layout: Missing folder(s): data",
                      page._detail.text())
        self.assertTrue(page._run_btn.isEnabled())

    def test_rerun_button_reruns_and_no_cached_green(self):
        calls = [0]
        failing = [False]

        def stub():
            calls[0] += 1
            if failing[0]:
                return [("Python", False, "Could not read the version")]
            return [("Python", True, "ok")]

        page = self._make_page(stub)
        page.show()
        self.assertTrue(self._wait_done(page, calls, 1))
        self.assertEqual(page._rows["Python"].text(), "✓ Pass")
        failing[0] = True
        page._run_btn.click()
        self.assertTrue(self._wait_done(page, calls, 2))
        self.assertEqual(page._rows["Python"].text(), "FAULT")
        self.assertIn("Could not read the version",
                      page._detail.text())

    def test_concurrent_triggers_ignored(self):
        calls = [0]

        def stub():
            calls[0] += 1
            time.sleep(0.6)
            return [("Python", True, "ok")]

        page = self._make_page(stub)
        page.show()
        self.assertTrue(_pump_until(
            lambda: not page._run_btn.isEnabled()))
        page.run_selftest()
        page._run_btn.click()
        self.assertTrue(self._wait_done(page, calls, 1))
        self.assertEqual(calls[0], 1)

    def test_reshow_reruns(self):
        calls = [0]

        def stub():
            calls[0] += 1
            return [("Python", True, "ok")]

        page = self._make_page(stub)
        page.show()
        self.assertTrue(self._wait_done(page, calls, 1))
        page.hide()
        self._app.processEvents()
        page.show()
        self.assertTrue(self._wait_done(page, calls, 2))
        self.assertEqual(calls[0], 2)

    def test_unknown_result_name_gets_row(self):
        calls = [0]

        def stub():
            calls[0] += 1
            return [("Python", True, "ok"),
                    ("Brand new check", False, "nope")]

        page = self._make_page(stub)
        page.show()
        self.assertTrue(_pump_until(
            lambda: "Brand new check" in page._rows))
        self.assertEqual(page._rows["Brand new check"].text(), "FAULT")
        self.assertTrue(self._wait_done(page, calls, 1))

    def test_runner_exception_becomes_fault_row(self):
        def stub():
            raise RuntimeError("boom")

        page = self._make_page(stub)
        page.show()
        self.assertTrue(_pump_until(
            lambda: "Self-test runner" in page._rows))
        self.assertEqual(page._rows["Self-test runner"].text(), "FAULT")
        self.assertTrue(page._run_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
