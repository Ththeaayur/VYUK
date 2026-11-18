"""R3F1: the scifi_activity Qt flake — root-caused and fixed.

Two failure shapes, one root cause: the 1.5 s startup singleShot
fired _refresh_activity_tabs() asynchronously, rebuilding every
Activity row in the middle of whatever the user — or a test —
was doing with those rows. The rebuild hid the old rows and
scheduled their deletion (a held reference died on the next
pump: deleted-QWidget), and the new layout-built rows read the
default 9pt font until the next event-loop turn (the 9.0px
fifth size in test_headline_line_is_largest_and_no_fifth_size).

The fix (gui/window_memory_activity.py + gui/window_memory_routines.py):
1. _run_startup_recovery_check updates the recovery OFFER only
   (button + count) — the rows are already built from the same
   ledger at page construction and rebuilt on every Activity
   show, so the offer is the only state that check owns.
2. Layout-built rows (routines, plans) polish their labels
   synchronously when added, so a new row reads its styled
   sizes the moment it exists.
3. A refresh never deletes a routine row whose schedule editor
   is open — the user's in-progress edit survives the refresh.

Tests 1, 3, 4 and 5 pin the mechanism directly (the timer's
payload called synchronously, no timing involved). Test 2 pins
the real timer path (sleep until the 1.5 s singleShot is due on
MEASURED wall time, then one pump). Test 6 pins the behaviour
the narrowing must preserve: the recovery offer still updates.
"""

import json
import time
import unittest

from tests.test_scifi_activity import _px, _QT

try:
    from tests.test_chat_ui import WindowBase  # noqa: E402
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    WindowBase = object  # type: ignore[assignment]


@unittest.skipUnless(_QT, "Qt unavailable")
class R3F1FlakeTests(WindowBase):
    """The REAL MainWindow offscreen — the flake's two shapes.

    NOTE: this class does NOT subclass ActivityWindowTests — that
    would make the loader collect its 18 tests into this suite.
    The seed/show helpers live on a bound ActivityWindowTests
    instance (self._act) that shares this window/app; it is never
    run by the loader, so only these 6 tests are collected.
    ActivityWindowTests is imported INSIDE setUp on purpose: a
    top-level import puts the name in this module's namespace
    and unittest's loadTestsFromModule collects its 18 tests
    from here too (the 24-test run that caught this).
    """

    def setUp(self):
        self._t0 = time.monotonic()
        super().setUp()
        self._t1 = time.monotonic()
        from tests.test_scifi_activity import ActivityWindowTests
        self._act = ActivityWindowTests("test_headline_lines")
        self._act.app = self.app
        self._act.window = self.window
        self._act._window = self.window
        self._act._facade_scheduler = None
        self._act._tmpdir = None

    def tearDown(self):
        act = getattr(self, "_act", None)
        if act is not None and act._facade_scheduler is not None:
            import voice_assistant.core.gui_facade as facade
            facade.scheduler = act._facade_scheduler
            act._facade_scheduler = None
        super().tearDown()

    # -- helpers ----------------------------------------------------
    def _routine_rows(self):
        layout = self.window.routines_layout
        return [layout.itemAt(i).widget()
                for i in range(layout.count())]

    def _routine_editor(self, row):
        from PySide6.QtWidgets import QWidget as _QWidget
        children = [c for c in row.children()
                    if isinstance(c, _QWidget)]
        self.assertEqual(len(children), 2)
        return children[1]

    def _label_sizes(self):
        sizes = {}
        for label in self._act._activity_labels():
            sizes.setdefault(_px(label), []).append(label.objectName())
        return sizes

    # -- shape 1: the 9.0px fifth size ------------------------------
    def test_headline_sizes_after_recovery_check(self):
        """The recovery check must not leave unpolished rows: read
        the label sizes right after it runs, with NO pump."""
        self._act._seed_dense()
        self._act._show_activity()
        self.window._run_startup_recovery_check()
        sizes = self._label_sizes()
        bad = [s for s in sizes if s not in (20, 16, 13, 12)]
        self.assertEqual(
            bad, [],
            f"fifth weight on the page: {bad} on "
            f"{[sizes[s] for s in bad]}")

    def test_headline_sizes_when_timer_fires_in_pump(self):
        """The real timer path: sleep until the 1.5 s singleShot
        is due (measured wall time from window construction),
        pump once, read the sizes immediately — exactly what the
        flaking test does. Pre-fix this read the 9.0px fifth
        size 6/6 (probe_race2)."""
        self._act._seed_dense()
        self._act._show_activity()
        while time.monotonic() - self._t1 < 1.7:
            time.sleep(0.05)
        self.app.processEvents()
        sizes = self._label_sizes()
        bad = [s for s in sizes if s not in (20, 16, 13, 12)]
        self.assertEqual(
            bad, [],
            f"fifth weight on the page: {bad} on "
            f"{[sizes[s] for s in bad]}")

    # -- shape 2: the deleted-QWidget editor ------------------------
    def test_recovery_check_does_not_rebuild_rows(self):
        """The recovery check must not rebuild the routine rows:
        the same widget objects stay in the layout afterwards,
        alive after two pumps (pre-fix they were deleteLater'd
        by the check's full refresh and died on the second
        pump)."""
        import shiboken6
        self._act._seed_dense()
        rows = self._routine_rows()
        self.assertEqual(len(rows), 2)
        self.window._run_startup_recovery_check()
        self.app.processEvents()
        self.app.processEvents()
        for row in rows:
            self.assertTrue(shiboken6.isValid(row),
                            "routine row deleted by recovery check")
        now = self._routine_rows()
        self.assertEqual(now, rows)

    def test_routine_editor_survives_recovery_check(self):
        """A routine editor held across the recovery check stays
        usable: pre-fix the check's refresh condemned its row
        and the next pump deleted it (RuntimeError on
        isHidden, the test_plan_and_routine_rows failure)."""
        self._act._seed_dense()
        editor = self._routine_editor(self._routine_rows()[0])
        self.assertTrue(editor.isHidden())
        self.window._run_startup_recovery_check()
        self.app.processEvents()
        self.app.processEvents()
        # Touching the editor must not raise: pre-fix the check's
        # refresh condemned its row and the second pump deleted
        # it (RuntimeError on isHidden).
        self.assertTrue(editor.isHidden(),
                        "editor should still be hidden (never opened)")

    # -- layer 3: an open editor survives a refresh ------------------
    def test_open_editor_survives_routines_refresh(self):
        """Open the schedule editor, type a schedule, refresh the
        routines: the SAME editor widget stays alive, stays
        open, and keeps the typed text. Pre-fix the refresh
        deleted it with its row."""
        import shiboken6
        from PySide6.QtWidgets import QLineEdit, QPushButton
        self._act._seed_dense()
        row = self._routine_rows()[0]
        editor = self._routine_editor(row)
        schedule = [b for b in row.findChildren(QPushButton)
                    if b.text() == "Schedule"]
        self.assertEqual(len(schedule), 1)
        schedule[0].click()
        self.assertFalse(editor.isHidden())
        edit = editor.findChild(QLineEdit)
        self.assertIsNotNone(edit)
        edit.setText("daily 07:30")
        self.window._refresh_routines()
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(shiboken6.isValid(editor),
                        "open editor deleted by routines refresh")
        self.assertFalse(editor.isHidden(),
                         "open editor closed by routines refresh")
        self.assertEqual(edit.text(), "daily 07:30")
        self.assertIn(row, self._routine_rows())

    # -- behaviour preserved: the offer still updates ----------------
    def test_recovery_check_still_updates_offer(self):
        """Narrowing the check must not lose the B16 contract:
        with two interrupted records in the ledger, the check
        shows the recovery offer with the count."""
        import pathlib
        data_dir = pathlib.Path(__file__).resolve().parent.parent \
            / "data"
        (data_dir / "task_ledger.json").write_text(json.dumps({
            "tasks": [
                {"task_id": "x1", "kind": "task",
                 "state": "running", "title": "Interrupted one"},
                {"task_id": "x2", "kind": "task",
                 "state": "paused", "title": "Interrupted two"},
            ]}), encoding="utf-8")
        self.assertEqual(
            self.window._run_startup_recovery_check(), 2)
        btn = self.window._activity_recovery_btn
        self.assertFalse(btn.isHidden())
        self.assertEqual(btn.text(), "Resume interrupted tasks (2)")
