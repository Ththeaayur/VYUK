"""Doctor report worker — extracted from gui/doctor_page.py (GUI split #1).

Runs doctor.py's checks off the GUI thread and captures their printed
report verbatim, for the DoctorPage widget. Moved verbatim from
doctor_page.py; DoctorPage imports _DoctorWorker from here.
"""

import contextlib
import io
import re

from PySide6.QtCore import QThread, Signal


def _parse_report(text: str):
    """Parse doctor.py's printed report into (counts, findings).

    A finding line is `[PASS|WARN|FAIL] label` plus an optional
    ` — detail`; a following `fix: ...` line (indented in the
    printout) belongs to the finding above it, as does a
    `category: ...` marker line (UI Phase 4, D-P4-17 — the
    check's category, printed by doctor.report()). The SUMMARY
    line carries the counts; if it is missing, counts fall back
    to the parsed finding lines.
    """
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    findings = []
    current = None
    line_re = re.compile(r"^\[(PASS|WARN|FAIL)\]\s+(.*?)\s*$")
    for raw in text.splitlines():
        line = raw.strip()
        m = line_re.match(line)
        if m:
            status, rest = m.group(1), m.group(2)
            counts[status] += 1
            if " — " in rest:
                label, detail = rest.split(" — ", 1)
            else:
                label, detail = rest, ""
            current = {"status": status, "label": label,
                       "detail": detail, "fix": "", "category": ""}
            if status != "PASS":
                findings.append(current)
            else:
                current = None  # PASS lines carry no card
            continue
        if line.startswith("fix:") and current is not None:
            current["fix"] = line[len("fix:"):].strip()
            continue
        if line.startswith("category:") and current is not None:
            current["category"] = line[len("category:"):].strip()
            continue
        if line.startswith("SUMMARY:"):
            sm = re.search(r"(\d+)\s+PASS.*?(\d+)\s+WARN.*?(\d+)\s+FAIL",
                           line)
            if sm:
                counts = {"PASS": int(sm.group(1)),
                          "WARN": int(sm.group(2)),
                          "FAIL": int(sm.group(3))}
    return counts, findings


def _parse_check_counts(text: str):
    """Parse doctor.py's CHECK_SUMMARY line into per-check counts.

    Fix round (F-P4-02): one outcome per check, so the three
    numbers sum to the number of checks run. Returns None when
    the line is absent (an old cached report) — the caller falls
    back to the verdict counts.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("CHECK_SUMMARY:"):
            sm = re.search(r"(\d+)\s+PASS.*?(\d+)\s+WARN.*?(\d+)\s+FAIL",
                           line)
            if sm:
                return {"PASS": int(sm.group(1)),
                        "WARN": int(sm.group(2)),
                        "FAIL": int(sm.group(3))}
    return None


class _DoctorWorker(QThread):
    """Run doctor.py's checks off the GUI thread and capture their
    printed report verbatim.

    Preferred path: a subprocess (`sys.executable doctor.py`) whose
    stdout is piped — clean isolation both ways: the app's own
    concurrent prints cannot leak into the report (a global
    redirect_stdout would catch them), and a crashing check cannot
    take the app down. doctor main() returns 0 (all pass) or 1
    (failures found); both are successful runs. Any other exit
    code, a timeout, or a launch failure falls back to the
    in-process run below (same per-check catching as main()).

    Stopping (field-check fix #3, U7): closing the window while
    this thread runs used to abort the process — Qt destroys a
    QThread still running with SIGABRT. `stop()` asks the thread
    to end (interruption flag + kill the child process, if any)
    and `wait()` then returns promptly; a stopped worker never
    emits `done`, because its caller is closing the page and no
    result is needed. The in-process fallback checks the flag
    between checks, so it ends too."""

    done = Signal(dict)

    def __init__(self, parent=None, categories=None, checks=None):
        """`categories` (UI Phase 4, D-P4-18): run only the checks
        in these categories — None means every check. `checks`:
        run only these check function names (the repair
        verification re-runs one check) — takes precedence over
        categories when both are given."""
        super().__init__(parent)
        self._proc = None
        self._categories = (list(categories) if categories else None)
        self._checks = (list(checks) if checks else None)

    def stop(self):
        """Ask the worker to stop: flag the interruption and kill
        the child check process, if one is running. Never raises —
        closing must never break on a stop. The thread ends on its
        own once the kill lands (communicate() returns); callers
        still wait() for it before destroying anything."""
        try:
            self.requestInterruption()
        except Exception:  # noqa: BLE001 — stop must never raise
            pass
        try:
            proc = getattr(self, "_proc", None)
            if proc is not None and proc.poll() is None:
                proc.kill()
        except Exception:  # noqa: BLE001 — stop must never raise
            pass

    def run(self):
        try:
            try:
                from voice_assistant import doctor as _doctor
            except ImportError:  # running un-packaged, next to doctor.py
                import doctor as _doctor
        except Exception as exc:  # noqa: BLE001 — the page reports it
            if not self.isInterruptionRequested():
                self.done.emit({"ok": False, "error": str(exc)})
            return
        payload = self._run_subprocess(_doctor)
        if payload is None:
            payload = self._run_in_process(_doctor)
        # A stopped worker reports nothing: its caller is closing
        # the page, and emitting here would re-enable buttons (and
        # could pop a confirm dialog) on a window going away.
        if self.isInterruptionRequested():
            return
        self.done.emit(payload)

    def _subprocess_args(self):
        """CLI args for the doctor subprocess (D-P4-18): one
        --check for a single-check verification, --only for a
        category selection, nothing for a full run."""
        if self._checks:
            return ["--check", self._checks[0]]
        if self._categories:
            return ["--only", ",".join(self._categories)]
        return []

    def _run_subprocess(self, doctor_mod):
        """Returns a payload dict, or None to mean 'fall back to
        in-process'. A timeout or a crash exit code is a real
        failure (not a fallback): the page must say so honestly.

        Runs through Popen (not run()) so stop() can kill the
        child mid-flight — a blocking run() cannot be interrupted,
        and quit() on a thread that never enters its event loop
        does nothing."""
        import subprocess
        import sys
        from pathlib import Path
        doctor_py = Path(getattr(doctor_mod, "__file__", "") or "")
        if not doctor_py.is_file() or not sys.executable:
            return None
        if self.isInterruptionRequested():
            return {"ok": False,
                    "error": "the checks were stopped before they "
                             "finished"}
        try:
            self._proc = subprocess.Popen(
                [sys.executable, str(doctor_py)]
                + self._subprocess_args(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=str(doctor_py.parent))
            try:
                out, err = self._proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001 — already dead
                    pass
                try:
                    out, err = self._proc.communicate()
                except Exception:  # noqa: BLE001 — nothing to read
                    out, err = "", ""
                return {"ok": False,
                        "error": "the checks took too long and were "
                                 "stopped"}
            text = out or ""
            if self._proc.returncode in (0, 1):
                # v5.57.1 (item 4): a crashing check prints its
                # traceback to stderr (see doctor main()); keep it
                # attached to the full text report — it is the
                # diagnostic surface the user can send back, and the
                # report parser only recognises [PASS|WARN|FAIL] and
                # SUMMARY lines, so traceback lines cannot move the
                # counts.
                if err:
                    text += ("\n--- doctor diagnostics (stderr) ---\n"
                             + err)
                counts, findings = _parse_report(text)
                return {"ok": True, "text": text, "counts": counts,
                        "check_counts": _parse_check_counts(text),
                        "findings": findings}
            err_lines = (err or "").strip().splitlines()
            tail = (err_lines[-1] if err_lines
                    else f"exit code {self._proc.returncode}")
            return {"ok": False,
                    "error": f"the check runner stopped early: {tail}",
                    "text": text + ("\n" + err if err else "")}
        except Exception:  # noqa: BLE001 — launch failed: fall back
            return None
        finally:
            self._proc = None

    def _run_in_process(self, doctor_mod):
        """Fallback: run _CHECKS in this thread, exactly as main()
        does — one check at a time, a broken check reported as FAIL,
        never a crash. stdout is captured globally (concurrent app
        prints can leak in); the subprocess path avoids this."""
        try:
            checks = list(getattr(doctor_mod, "_CHECKS", []))
            # UI Phase 4 (D-P4-18): the in-process fallback honours
            # the same selection the subprocess path takes — one
            # check for a verification, categories for a run.
            if self._checks:
                checks = [c for c in checks
                          if c.__name__ in self._checks]
            elif self._categories:
                _cats = getattr(doctor_mod, "_CHECK_CATEGORIES", {})
                checks = [c for c in checks
                          if _cats.get(c.__name__) in self._categories]
            # doctor._counts accumulates across runs in one process;
            # reset it so this run's SUMMARY line is its own.
            try:
                doctor_mod._counts.update({"PASS": 0, "WARN": 0,
                                           "FAIL": 0})
            except Exception:  # noqa: BLE001 — counts stay approximate
                pass
            # Fix round (F-P4-02): the per-check outcomes
            # accumulate the same way — reset them too, so this
            # run's hero counts only this run's checks.
            try:
                doctor_mod._check_outcomes.clear()
            except Exception:  # noqa: BLE001 — hero falls back
                pass
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                for check in checks:
                    # Field-check fix #3 (U7): a stop request ends
                    # the fallback too — a hanging check cannot be
                    # cut off mid-call, but nothing starts after it.
                    if self.isInterruptionRequested():
                        return {"ok": False,
                                "error": "the checks were stopped "
                                         "before they finished"}
                    # UI Phase 4 (D-P4-17): the current-check name
                    # is what report() prints the category marker
                    # from — main() sets it the same way.
                    try:
                        doctor_mod._CURRENT_CHECK_NAME = check.__name__
                    except Exception:  # noqa: BLE001 — marker stays blank
                        pass
                    try:
                        check()
                    except Exception as exc:  # noqa: BLE001
                        # v5.57.1 (item 4): same rule as doctor
                        # main() — a crashing check's verdict
                        # names its cause, and the traceback
                        # goes to stderr (the app's log), never
                        # swallowed into a bare verdict.
                        import traceback

                        traceback.print_exc()
                        doctor_mod.report(
                            "FAIL", f"Doctor check: {check.__name__}",
                            "the check itself ran into a problem "
                            f"and couldn't finish ({type(exc).__name__}: "
                            f"{exc})",
                            "this is a problem in doctor.py itself — "
                            "please report it with this line")
            text = buf.getvalue()
            counts, findings = _parse_report(text)
            # Fix round (F-P4-02): the fallback runs the checks
            # in-process, so there is no CHECK_SUMMARY line to
            # parse — the per-check counts come straight from the
            # module's outcome table.
            try:
                _cc = doctor_mod._check_summary_counts()
            except Exception:  # noqa: BLE001 — hero falls back
                _cc = None
            return {"ok": True, "text": text, "counts": counts,
                    "check_counts": _cc, "findings": findings}
        except Exception as exc:  # noqa: BLE001 — the page reports it
            return {"ok": False, "error": str(exc)}
