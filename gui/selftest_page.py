"""The Self-test page — auto-runs when opened, plus a manual re-run.

Owner decision 2026-11-01 (#12): the Self-test AUTO-RUNS when
opened, and a manual re-run button re-runs the same set. The set
itself lives in one place (gui/selftest_checks.py); this module
is only the runner + the page.

Behaviour contract:
- Auto-run on open: showEvent triggers a run, so navigating to
  the page (Back/Continue in the wizard, or any re-show) always
  runs fresh checks — never the stale build-time result the old
  page showed (it ran once at construction and never again until
  Run again was pressed).
- One runner: the auto-run and the Run-again button share
  run_selftest() — no duplicated logic.
- Worker thread: checks run on a QThread (the DoctorPage worker
  pattern); the GUI thread only paints results. A run in flight
  ignores further triggers (open or button) — it never starts a
  second concurrent run, and the ignored trigger is a no-op, not
  a queued run.
- Honest results: rows paint ✓ Pass / FAULT from the fresh run;
  the detail line is "All checks pass." or "Needs attention — "
  with each failure's reason. A re-run actually re-runs (nothing
  is cached green); a run that cannot complete reports its own
  FAULT row, never a pass.
- No voice_assistant import at top level: the check function is
  injected (production passes run_first_run_self_tests), so this
  module stays importable wherever Qt is present.
"""
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .selftest_checks import (
    SELFTEST_CHECK_NAMES,
    run_first_run_self_tests as _default_checks,
)
from .style import DANGER, OK


class _SelfTestWorker(QThread):
    """Run the check function off the GUI thread and emit its
    result rows. The worker never touches a widget."""

    done = Signal(object)

    def __init__(self, check_fn, parent=None):
        super().__init__(parent)
        self._check_fn = check_fn

    def run(self):
        try:
            results = self._check_fn()
        except Exception as exc:  # noqa: BLE001 — a broken runner
            # is a FAULT row, never a crash and never a pass.
            results = [("Self-test runner", False,
                        f"could not run: {exc}")]
        self.done.emit(results)


class _SelfTestPage(QWidget):
    """The Self-test page widget. The dialog owns it; it only
    runs the injected check function and paints its rows."""

    def __init__(self, header: QWidget, check_fn=None, parent=None,
                 on_done=None):
        super().__init__(parent)
        self._check_fn = check_fn or _default_checks
        self._on_done_cb = on_done
        self._worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(header)
        # Rows are built from the set's declared names so the
        # page is never empty while a run is in flight; _paint
        # also creates a row on the fly for any result name
        # without one, so a new check can never go invisible.
        self._rows: dict[str, QLabel] = {}
        for name in SELFTEST_CHECK_NAMES:
            row = QHBoxLayout()
            name_lbl = QLabel(name)
            state_lbl = QLabel("…")
            state_lbl.setObjectName("muted")
            state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(name_lbl)
            row.addStretch()
            row.addWidget(state_lbl)
            layout.addLayout(row)
            self._rows[name] = state_lbl
        self._detail = QLabel("Checks haven't run yet.")
        self._detail.setObjectName("muted")
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)
        # Round-3 wizard expansion (parent ruling: a self-test
        # failure never blocks and is never silent): the
        # continue-anyway line shows only when a run has faults
        # — the self-test never gates Continue, and the failing
        # check is pinned to Home as a fix-it card until it
        # passes (see _on_selftest_done in onboarding_pages).
        self._continue_note = QLabel(
            "You can continue anyway — the failing check is pinned "
            "to Home until it passes.")
        self._continue_note.setObjectName("muted")
        self._continue_note.setWordWrap(True)
        self._continue_note.setVisible(False)
        layout.addWidget(self._continue_note)
        self._run_btn = QPushButton("Run again")
        self._run_btn.setObjectName("chipBtn")
        self._run_btn.clicked.connect(self.run_selftest)
        layout.addWidget(self._run_btn)
        layout.addStretch()
        self._rows_layout = layout

    def showEvent(self, event):
        """Auto-run on open: every time the page becomes visible
        (wizard navigation to it, or any re-show), run fresh —
        never the stale result from construction time."""
        self.run_selftest()
        super().showEvent(event)

    def run_selftest(self):
        """One runner for auto-run and Run again. A run already
        in flight ignores further triggers (open or button) —
        no second concurrent run, no queued run."""
        if self._worker is not None:
            try:
                if self._worker.isRunning():
                    return
            except RuntimeError:  # noqa: BLE001 — stale worker
                # (same pattern as DoctorPage.run: the finished
                # signal is wired to deleteLater, so a stored
                # wrapper's C++ object may already be gone)
                self._worker = None
        self._run_btn.setEnabled(False)
        self._run_btn.setText("Running…")
        self._detail.setText("Running checks…")
        for lbl in self._rows.values():
            lbl.setText("…")
            lbl.setObjectName("muted")
            lbl.setStyleSheet("")
        worker = _SelfTestWorker(self._check_fn, self)
        worker.done.connect(self._on_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_done(self, results):
        # Clear the worker reference the moment the run is done
        # (finished→deleteLater will delete the C++ object, and
        # run_selftest must never touch the stale wrapper again).
        self._worker = None
        self._run_btn.setEnabled(True)
        self._run_btn.setText("Run again")
        self._paint(results)

    def _paint(self, results):
        """Paint rows from a fresh run. A result name without a
        row gets one created, so no result is ever invisible."""
        failed: list[str] = []
        for name, ok, detail in results:
            lbl = self._rows.get(name)
            if lbl is None:
                row = QHBoxLayout()
                name_lbl = QLabel(name)
                lbl = QLabel("")
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
                row.addWidget(name_lbl)
                row.addStretch()
                row.addWidget(lbl)
                # Insert above the detail label: the detail is
                # the layout item four from the end (detail,
                # continue-anyway note, Run again, stretch).
                self._rows_layout.insertLayout(
                    self._rows_layout.count() - 4, row)
                self._rows[name] = lbl
            lbl.setText("✓ Pass" if ok else "FAULT")
            lbl.setStyleSheet(f"color: {OK if ok else DANGER};")
            if not ok:
                failed.append(f"{name}: {detail}")
        self._detail.setText(
            "All checks pass." if not failed else
            "Needs attention — " + "; ".join(failed))
        # Round-3 wizard expansion: the continue-anyway line
        # shows only while a run has faults, and the run's full
        # results go to the on_done hook (the wizard records the
        # failing check names for the Home fix-it card; an
        # all-pass run clears them).
        self._continue_note.setVisible(bool(failed))
        if self._on_done_cb is not None:
            try:
                self._on_done_cb(list(results))
            except Exception:  # noqa: BLE001 — a hook never breaks paint
                pass
