"""UI Phase 4 (Pulse, Senses, Doctor, Settings) — 2026-11-26.

Discriminated tests per decision, not a re-run of the probes
(ui_p4_work/probe_*.py, ui_p4_logs/probe_*.log carry the full
42/32/40/62-check probes). Pure tests run on both interpreters;
Qt tests skip without PySide6 (the standing optional-dependency
skip). One suite per unittest process; never pipe through tail.

Covered:
  * D-P4-03 Pulse hide-reset (stats hidden until two new samples
    after hide/show; ceiling back to floor).
  * D-P4-07 Pulse word link: the word->finding table is EMPTY
    (no Doctor check verdicts on machine CPU/memory/disk/battery)
    and the chip stays hidden while a warning word shows.
  * D-P4-11 Senses matrix agrees with the Permissions model
    (OS/App/Capture words) and D-P4-12 a failed probe leg reads
    "—" + "Couldn't check", never a state word.
  * D-P4-19/27 Doctor repairs: pip bounded to declared
    dependencies, partial pack gets no second downloader,
    receipt/chmod/integrity/stored-brain rows.
  * R-P4-B async download repairs show PENDING, never a
    mid-download verdict.
  * D-P4-23 verification re-runs the single check after repair.
  * R-P4-G every Doctor check declares a category (37 checks).
  * D-P4-31 effect lines for all 35 cards (33 lines; Presence
    + Your settings file owed, line hidden, never guessed).
    UI Phase 5 (2026-11-27): 36 cards, 34 lines — the "Home
    hub" card joins the effect table as instant.
  * R-P4-F/D-P4-32 HUD preview writes nothing until Apply
    (ConfigSpec + QSettings + .env all three).
  * D-P4-33 stored-vs-effective key/service lines, including
    the save-didn't-land wording.
  * D-P4-08/R-P4-E Pulse cadence mirror on the Appearance card.
  * D-P4-26 undo coverage stated on the About card.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast
import re
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / ".pkgroot") not in sys.path:
    sys.path.insert(0, str(ROOT / ".pkgroot"))

try:
    import PySide6  # noqa: F401 — availability probe only
    _QT = True
except ImportError:
    _QT = False

# The voice_assistant.* alias (test_doctorui pattern): register
# only keys that are absent; tearDownModule removes exactly the
# keys this module added.
_ADDED_VA_MODULES = []


def setUpModule():
    for key, path in (
        ("voice_assistant", str(ROOT)),
        ("voice_assistant.core", str(ROOT / "core")),
        ("voice_assistant.gui", str(ROOT / "gui")),
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


def _ensure_va_alias():
    """Re-register the voice_assistant.* alias if it is gone.

    WindowBase (tests/test_chat_ui.py) installs its own shim
    over the alias and pops it in _teardown_shim, so after any
    full-window test the module-level alias is gone and a
    dotted `import voice_assistant.core...` fails with
    ModuleNotFoundError. Standalone-page tests call this before
    importing through the alias; full-window tests must NOT
    (their setUp installs the shim, which is the regime they
    run under).
    """
    for key, path in (
        ("voice_assistant", str(ROOT)),
        ("voice_assistant.core", str(ROOT / "core")),
        ("voice_assistant.gui", str(ROOT / "gui")),
    ):
        if key not in sys.modules:
            pkg = types.ModuleType(key)
            pkg.__path__ = [path]
            sys.modules[key] = pkg


def _qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------- pure: Doctor
class TestDoctorCategories(unittest.TestCase):
    def test_every_check_declares_a_category(self):
        import doctor
        self.assertEqual(len(doctor._CHECKS), 37)
        self.assertEqual(len(doctor._CHECK_CATEGORIES), 37)
        names = {fn.__name__ for fn in doctor._CHECKS}
        self.assertEqual(set(doctor._CHECK_CATEGORIES), names)
        for name, cat in doctor._CHECK_CATEGORIES.items():
            self.assertIn(cat, doctor._CATEGORIES, name)

    def test_check_subprocess_carries_category_marker(self):
        # Pure text parse (gui.doctor_worker imports Qt, so the
        # suite does not use it here): --check runs exactly one
        # check; --only runtime findings all carry the runtime
        # marker (PASS lines carry no marker — only WARN/FAIL
        # findings do).
        out1 = subprocess.run(
            [sys.executable, "doctor.py", "--check", "check_data"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        self.assertIn(out1.returncode, (0, 1))
        m = re.search(r"SUMMARY: (\d+) PASS", out1.stdout)
        self.assertIsNotNone(m, out1.stdout[-500:])
        self.assertEqual(int(m.group(1)), 1)
        out = subprocess.run(
            [sys.executable, "doctor.py", "--only", "runtime"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        self.assertIn(out.returncode, (0, 1))
        markers = re.findall(r"category: (\S+)", out.stdout)
        self.assertGreater(len(markers), 0)
        self.assertEqual(set(markers), {"runtime"})
        self.assertIn("SUMMARY:", out.stdout)

    def test_check_summary_counts_checks_not_verdicts(self):
        """Fix round (F-P4-02, parent ruling 2026-11-26): doctor.py
        prints a CHECK_SUMMARY line next to the verdict SUMMARY —
        one outcome per check, its worst verdict. A single check
        sums to 1; a full run sums to the 37 checks, however the
        verdict lines split (the old hero summed the verdicts,
        68 lines, and called them checks)."""
        out1 = subprocess.run(
            [sys.executable, "doctor.py", "--check", "check_data"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        self.assertIn("CHECK_SUMMARY: 1 PASS · 0 WARN · 0 FAIL",
                      out1.stdout)
        out = subprocess.run(
            [sys.executable, "doctor.py"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        line = next((ln for ln in out.stdout.splitlines()
                     if ln.strip().startswith("CHECK_SUMMARY:")), None)
        self.assertIsNotNone(line, "no CHECK_SUMMARY in a full run")
        m = re.search(r"(\d+)\s+PASS.*?(\d+)\s+WARN.*?(\d+)\s+FAIL",
                      line)
        self.assertIsNotNone(m, line)
        total = sum(int(m.group(i)) for i in (1, 2, 3))
        self.assertEqual(total, 37,
                         f"CHECK_SUMMARY sums to {total}, not 37: {line}")


class TestPipRepairsBounded(unittest.TestCase):
    def test_pip_specs_are_declared_dependencies(self):
        import doctor
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        declared = set()
        for line in req.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!~\s;\[]", line, 1)[0].strip().lower()
            if name:
                declared.add(name.replace("_", "-"))
        self.assertEqual(len(doctor._PIP_REPAIR_SPECS), 13)
        for label, spec in doctor._PIP_REPAIR_SPECS.items():
            name = re.split(r"[<>=!~\s]", spec, 1)[0].strip().lower()
            self.assertIn(name.replace("_", "-"), declared,
                          f"{label}: {spec} is not a declared dependency")


# ---------------------------------------------------------- pure: Settings
class TestEffectLines(unittest.TestCase):
    def test_effect_table_shape(self):
        from voice_assistant.core.gui_facade import settings_search as ss
        # UI Phase 5 (2026-11-27): the "Home hub" card joins the
        # effect table as instant (34 entries, 31 instant).
        self.assertEqual(len(ss.CARD_EFFECT), 34)
        nxt = {t for t, v in ss.CARD_EFFECT.items()
               if v == "Takes effect the next time you connect."}
        self.assertEqual(
            nxt, {"How VYUK thinks", "Voice", "Local brain"})
        inst = {t for t, v in ss.CARD_EFFECT.items()
                if v == "Applies instantly."}
        self.assertEqual(len(inst), 31)
        # The two owed timings are absent, never guessed.
        self.assertEqual(ss.card_effect_line("Presence"), "")
        self.assertEqual(ss.card_effect_line("Your settings file"), "")
        self.assertEqual(ss.card_effect_line("Your world"),
                         "Applies instantly.")

    def test_effect_titles_are_real_cards(self):
        from voice_assistant.core.gui_facade import settings_search as ss
        titles = set()
        for path in (ROOT / "gui").glob("window_*.py"):
            src = path.read_text(encoding="utf-8")
            for m in re.finditer(
                    r"_settings_card\(\s*\n?\s*[^,]+,\s*\"([^\"]+)\"",
                    src):
                titles.add(m.group(1))
        # The dynamic first-open card passes its title through a
        # variable, so the literal grep misses it — it is a real
        # card (probe_card_count proved it on the built window).
        titles.add("A choice about watching what you do")
        for title in ss.CARD_EFFECT:
            if title == "Permissions":
                continue  # the PermissionsWidget, not _settings_card
            self.assertIn(title, titles, title)

    def test_your_world_is_searchable(self):
        from voice_assistant.core.gui_facade import settings_search as ss
        self.assertIn("Your world", ss.match_cards(
            "your world", list(ss.CARD_EFFECT)))
        self.assertTrue(ss.card_voice_line("Your world"))


class TestWordToDoctorEmpty(unittest.TestCase):
    def test_word_to_doctor_table_is_empty(self):
        # R-P4-C: no Doctor check verdicts on machine CPU,
        # machine memory, disk space or battery level, so the
        # Pulse word->finding table is honestly empty.
        tree = ast.parse(
            (ROOT / "gui" / "pulse_page.py").read_text(encoding="utf-8"))
        found = [n for n in ast.walk(tree)
                 if isinstance(n, ast.AnnAssign)
                 and isinstance(n.target, ast.Name)
                 and n.target.id == "_WORD_TO_DOCTOR"]
        found += [n for n in ast.walk(tree)
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name)
                          and t.id == "_WORD_TO_DOCTOR" for t in n.targets)]
        self.assertEqual(len(found), 1)
        self.assertIsInstance(found[0].value, ast.Dict)
        self.assertEqual(len(found[0].value.keys), 0)


# ---------------------------------------------------------- Qt: Pulse
@unittest.skipUnless(_QT, "Qt unavailable")
class TestPulseHideReset(unittest.TestCase):
    def setUp(self):
        _qt_app()
        from gui.pulse_page import PulsePage

        def sample(cpu=10.0, mem=20.0, disk=30.0, net=500_000.0,
                   batt=80.0, plugged=True, uptime=3600.0, procs=120):
            return {"cpu": cpu, "mem": mem, "disk": disk,
                    "net_bps": net, "battery_pct": batt,
                    "battery_plugged": plugged, "uptime_s": uptime,
                    "procs": procs}

        class FakeSampler:
            def __init__(self, s):
                self.s = s

            def __call__(self):
                return dict(self.s)

            def top_processes(self, n=5):
                return []

        self._sample = sample
        self.fake = FakeSampler(sample())
        self.page = PulsePage(sampler=self.fake)
        self.page.show()
        _qt_app().processEvents()
        self.page._timer.stop()  # the 1 s tick must not add samples
        self.page.sample_now()

    def tearDown(self):
        self.page._timer.stop()
        self.page.deleteLater()
        _qt_app().processEvents()

    def test_hide_clears_history_and_ceiling(self):
        self.assertGreater(len(self.page.dials["cpu"].raw_history()), 0)
        self.page.hide()
        _qt_app().processEvents()
        self.assertEqual(len(self.page.dials["cpu"].raw_history()), 0)
        self.assertEqual(self.page._net_ceiling, 1_000_000.0)

    def test_stats_hidden_until_two_new_samples(self):
        self.page.hide()
        _qt_app().processEvents()
        self.page.show()
        _qt_app().processEvents()
        self.page._timer.stop()
        # showEvent's own sample is the first of the two.
        self.assertFalse(
            self.page._row_by_key["cpu"].stats_label.isVisible())
        self.page.sample_now()
        self.assertTrue(
            self.page._row_by_key["cpu"].stats_label.isVisible())

    def test_word_link_hidden_while_word_shows(self):
        self.fake.s = self._sample(disk=96.0)
        self.page.sample_now()
        self.assertEqual(self.page.word_label.text(),
                         "Disk critically full")
        self.assertFalse(self.page.word_link_button.isVisible())

    def test_paused_header_shows_one_status_line(self):
        """Fix round (F-06, parent ruling 2026-11-26 19:19 IST):
        while paused the header shows ONE status line — the stamp
        alone ("Paused — last sample …, N s ago"). The bare
        "Paused" live label and the word label are hidden, so the
        header cannot stutter. Resuming (the real Pause button,
        clicked) brings the live state back."""
        self.page.pause_button.click()
        _qt_app().processEvents()
        self.assertFalse(self.page.live_label.isVisible())
        self.assertFalse(self.page.word_label.isVisible())
        self.assertTrue(self.page.stamp_label.isVisible())
        self.assertTrue(self.page.stamp_label.text().startswith(
            "Paused — last sample"))
        self.page.pause_button.click()
        _qt_app().processEvents()
        self.assertTrue(self.page.live_label.isVisible())
        self.assertTrue(self.page.word_label.isVisible())


# ---------------------------------------------------------- Qt: Senses
@unittest.skipUnless(_QT, "Qt unavailable")
class TestSensesMatrixAndFlags(unittest.TestCase):
    def setUp(self):
        _qt_app()
        _ensure_va_alias()
        from gui import pf_permissions as pf
        self._pf = pf
        self._orig_model = pf.permissions_model

        class FakeEntry:
            def __init__(self, pid, state, os_state):
                self.permission_id = pid
                self.state = state
                self.os_state = os_state

        pf.permissions_model = lambda *a, **k: [
            FakeEntry("microphone", pf.PERMISSION_GRANTED, "allowed"),
            FakeEntry("camera", pf.PERMISSION_UNKNOWN, "unknown"),
            FakeEntry("screen", pf.PERMISSION_DENIED, None),
        ]
        import voice_assistant.core.gui_facade as facade
        self._facade = facade
        self._orig_grants = getattr(facade, "permission_grants", None)

        class FakeGrants:
            def has_session_grant(self, pid):
                return pid == "camera"  # session grant on camera only

        facade.permission_grants = FakeGrants()
        from gui.senses_page import SensesPage
        self._SensesPage = SensesPage
        self.state = {"ssid": None, "connection": "ethernet",
                      "battery_pct": 55.0, "battery_plugged": False,
                      "camera_user": None, "mic_user": None,
                      "connection_ok": True, "battery_ok": True,
                      "privacy_ok": True}
        self.events = []
        self.page = SensesPage(providers={
            "state": lambda: dict(self.state),
            "events": lambda: list(self.events),
            "events_total": lambda: 500,
        })
        self.page.show()
        _qt_app().processEvents()

    def tearDown(self):
        self._pf.permissions_model = self._orig_model
        if self._orig_grants is None:
            self._facade.__dict__.pop("permission_grants", None)
        else:
            self._facade.permission_grants = self._orig_grants
        self.page.deleteLater()
        _qt_app().processEvents()

    def test_matrix_agrees_with_permissions_model(self):
        w = self.page._matrix_words
        self.assertEqual(w[("microphone", "os")].text(), "Allowed")
        self.assertEqual(w[("microphone", "app")].text(), "On")
        self.assertEqual(w[("camera", "os")].text(), "—")
        self.assertEqual(w[("camera", "app")].text(), "This session only")
        self.assertEqual(w[("screen", "os")].text(), "—")
        self.assertEqual(w[("screen", "app")].text(), "Off")

    def test_failed_leg_reads_couldnt_check(self):
        self.state.update(connection_ok=False, battery_ok=False,
                          privacy_ok=False)
        self.page.refresh_state()
        _qt_app().processEvents()
        self.assertEqual(self.page.state_labels["connection"].text(), "—")
        self.assertEqual(self.page._state_subs["connection"].text(),
                         "Couldn't check")
        self.assertEqual(self.page.state_labels["battery"].text(), "—")
        self.assertEqual(self.page._state_subs["battery"].text(),
                         "Couldn't check")
        self.assertEqual(self.page.state_labels["privacy"].text(), "—")
        self.assertEqual(self.page._state_subs["privacy"].text(),
                         "Couldn't check")

    def test_battery_less_desktop_is_no_battery_not_failed(self):
        self.state.update(battery_ok=True, battery_pct=None)
        self.page.refresh_state()
        _qt_app().processEvents()
        self.assertEqual(self.page.state_labels["battery"].text(), "—")
        self.assertEqual(self.page._state_subs["battery"].text(),
                         "No battery")


# ---------------------------------------------------------- Qt: Doctor
@unittest.skipUnless(_QT, "Qt unavailable")
class TestDoctorPage(unittest.TestCase):
    def setUp(self):
        _qt_app()
        _ensure_va_alias()
        from PySide6.QtWidgets import QLabel
        from gui.doctor_page import DoctorPage

        class FakeSettings:
            def __init__(self):
                self._store = {}

            def value(self, key, default=""):
                return self._store.get(key, default)

            def setValue(self, key, value):
                self._store[key] = value

        class StubWindow:
            def __init__(self):
                self.settings = FakeSettings()
                self._w10_doctor_status = QLabel("")

            def _setup_local_brain(self, *a):
                pass

            def show_settings_card(self, *a):
                pass

            def _w10_doctor_verify(self, *a):
                self._w10_doctor_status.setText(
                    "Install verified: 5/5 files match.")

            def _w10_doctor_repair(self, *a):
                self._w10_doctor_status.setText(
                    "Repaired 2 file(s); 0 unrepairable.")

        self.win = StubWindow()
        self.page = DoctorPage(self.win)
        self.page.show()
        _qt_app().processEvents()

    def tearDown(self):
        try:
            self.page.stop_checks()
        except Exception:
            pass
        self.page.deleteLater()
        _qt_app().processEvents()

    def _finding(self, **kw):
        base = {"label": "", "status": "WARN", "detail": "",
                "fix": "", "category": ""}
        base.update(kw)
        return base

    def test_findings_scroll_has_usable_minimum_height(self):
        # Leg-found (render gate): without a floor the nested
        # findings list was crushed to 56 px — the 24 finding
        # cards showed only as a 2-line slit — on the live tree
        # AND the pre-Phase-4 tree (pre-existing, not a Phase 4
        # regression). The floor shows 3+ cards at 1280.
        self.assertGreaterEqual(
            self.page.findings_scroll.minimumHeight(), 400)

    def test_category_tiles_show_glyph_and_counts(self):
        """Fix round (F-P4-03, parent ruling 2026-11-26): each
        category tile shows a visible check glyph when ticked
        and its check count — the six counts sum to the 37
        checks. Unticking drops the glyph and keeps the count,
        so checked and unchecked are distinguishable at a
        glance."""
        total = 0
        for cat, box in self.page._category_boxes.items():
            text = box.text()
            self.assertTrue(text.startswith("✓ "),
                            f"{cat} tile has no check glyph: {text!r}")
            m = re.search(r"· (\d+)$", text)
            self.assertIsNotNone(m, f"{cat} tile has no count: {text!r}")
            total += int(m.group(1))
        self.assertEqual(total, 37,
                         "tile counts do not sum to the 37 checks")
        first = next(iter(self.page._category_boxes.values()))
        first.setChecked(False)
        _qt_app().processEvents()
        self.assertFalse(first.text().startswith("✓ "),
                         "unticked tile still shows the glyph")
        self.assertRegex(first.text(), r"· \d+$",
                         "unticked tile lost its count")

    def test_category_tiles_relayout_to_two_columns_when_narrow(self):
        """Fix round (F-P4-03): the glyph + count labels are wider
        than the Phase 4 labels — three columns overflow a 640 px
        window (the hero card grew a horizontal scrollbar). The
        grid drops to two columns below 700 px page width and
        returns to three above it. Positions are the assertion,
        not columnCount(): QGridLayout never shrinks its reported
        column count after items are taken out, so the count
        stays 3 even in the two-column layout."""
        grid = self.page._category_grid
        self.page.resize(500, 800)
        _qt_app().processEvents()
        self.assertIsNone(grid.itemAtPosition(0, 2),
                          "third column still occupied when narrow")
        self.assertIsNotNone(grid.itemAtPosition(2, 1),
                             "sixth tile not in the second column")
        self.page.resize(1000, 800)
        _qt_app().processEvents()
        self.assertIsNotNone(grid.itemAtPosition(0, 2),
                             "third column not restored when wide")
        self.assertIsNone(grid.itemAtPosition(2, 1),
                          "sixth tile still in the second column")

    def test_resilience_summary_speaks_in_plain_words(self):
        """Fix round (F-04, parent wording 2026-11-26): the
        summary card says "Safety guards on", "Libraries
        protected", "Licence checks waiting on you" and the
        plain-words kill-drill line — no lab jargon, and the
        numbers stay live."""
        from gui.doctor_page import DoctorPage
        status = {"configured": True, "tripped": False,
                  "safe_mode": False,
                  "fences": ["a", "b"], "libraries": ["x"],
                  "owed_reads": {"APSW": {}, "Litestream": {}},
                  "field_proof": "Still owed on your machine: "
                                 "a kill drill — your run, your verdict"}
        text = DoctorPage._resilience_summary(status, [])
        self.assertIn("Resilience wiring: healthy", text)
        self.assertIn("Safe mode: not engaged", text)
        self.assertIn("Safety guards on: 2", text)
        self.assertIn("Libraries protected: 1", text)
        self.assertIn("Licence checks waiting on you: 2", text)
        self.assertIn("Still owed on your machine: a kill drill — "
                      "your run, your verdict", text)
        for old in ("Fences active", "Libraries wired",
                    "Pilot-only licence reads", "NOT claimed"):
            self.assertNotIn(old, text)

    def test_pip_repair_uses_allowlisted_spec(self):
        r = self.page._repair_for(self._finding(
            label="Dependency: psutil", status="FAIL",
            detail="not installed",
            fix="pip install 'psutil>=5.9.0'"))
        self.assertIsNotNone(r)
        self.assertEqual(r["kind"], "pip")
        self.assertEqual(r["spec"], "psutil>=5.9.0")
        self.assertEqual(r["verify_check"], "check_deps")

    def test_undeclared_package_gets_no_repair(self):
        r = self.page._repair_for(self._finding(
            label="Dependency: evilpkg", status="FAIL",
            detail="not installed",
            fix="pip install 'evilpkg>=1.0'"))
        self.assertIsNone(r)

    def test_dotenv_gets_no_repair(self):
        # R-P4-A: python-dotenv is not a declared dependency
        # (the product never imports it — own .env loader —
        # and the dependency tests assert its absence), so the
        # dotenv FAIL must never offer a pip install of it.
        r = self.page._repair_for(self._finding(
            label="Dependency: dotenv", status="FAIL",
            detail="not installed",
            fix="pip install 'python-dotenv>=1.0.0'"))
        self.assertIsNone(r)
        reason = self.page._no_repair_reason(self._finding(
            label="Dependency: dotenv", status="FAIL",
            detail="not installed"))
        self.assertIn("outside VYUK's declared dependency set", reason)

    def test_partial_pack_gets_no_second_downloader(self):
        r = self.page._repair_for(self._finding(
            label="Offline pack", status="WARN",
            detail="Offline pack on disk: 1 GB — the brain model "
                   "isn't finished yet; downloads resume where "
                   "they left off"))
        self.assertIsNone(r)

    def test_not_downloaded_pack_gets_download_repair(self):
        r = self.page._repair_for(self._finding(
            label="Offline pack", status="WARN",
            detail="Offline pack not downloaded yet — VYUK still answers"))
        self.assertIsNotNone(r)
        self.assertEqual(r["kind"], "download")

    def test_chmod_refuses_a_foreign_path(self):
        res = self.page._run_sync_repair(
            {"kind": "chmod", "path": "/tmp/evil"})
        self.assertIs(res["ok"], False)

    def test_integrity_finding_gains_verify_install(self):
        import time
        findings = [self._finding(
            label="Install-tree integrity", status="WARN",
            detail="2 problem(s)", fix="re-extract",
            category="installation")]
        self.page._run_started = time.monotonic()
        self.page._on_done({"ok": True, "text": "fake\nSUMMARY: 0 PASS",
                            "counts": {"PASS": 0, "WARN": 1, "FAIL": 0},
                            "findings": findings})
        _qt_app().processEvents()
        card = self.page._finding_cards["Install-tree integrity"]
        from PySide6.QtWidgets import QPushButton
        buttons = [b.text() for b in card.findChildren(QPushButton)]
        self.assertIn("Verify install", buttons)

    def test_download_repair_shows_pending_not_a_verdict(self):
        _ensure_va_alias()
        from PySide6.QtWidgets import QLabel, QPushButton
        import voice_assistant.core.gui_facade as facade

        class FakeDownloads:
            def __init__(self, started):
                self._started = started

            def start(self, progress, done):
                return self._started

        finding = self._finding(
            label="Offline pack", status="WARN",
            detail="Offline pack not downloaded yet")
        spec = {"kind": "download", "verb": "Download the pack",
                "verify_check": "check_brain"}
        label = QLabel("")
        button = QPushButton(spec["verb"])
        with mock.patch.object(facade, "downloads",
                               FakeDownloads(True)):
            self.page._start_download_repair(
                finding, spec, label, button)
        self.assertIn("verification PENDING", label.text())
        self.assertNotIn("Re-checked:", label.text())
        # A download already running stays PENDING too.
        label2 = QLabel("")
        with mock.patch.object(facade, "downloads",
                               FakeDownloads(False)):
            self.page._start_download_repair(
                finding, spec, label2, button)
        self.assertIn("verification PENDING", label2.text())
        self.assertNotIn("Re-checked:", label2.text())

    def test_verify_repair_reruns_the_single_check(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QLabel
        self.page._repair_running = True
        finding = self._finding(
            label="Data directory", status="FAIL", detail="",
            category="runtime")
        spec = {"kind": "chmod", "verb": "Fix folder permissions",
                "verify_check": "check_data",
                "verify_label": "Data directory"}
        label = QLabel("")
        self.page._verify_repair(finding, spec, label)
        loop = QEventLoop()
        QTimer.singleShot(60000, loop.quit)

        def poll():
            if "Re-checked:" in label.text() \
                    or "couldn't run" in label.text():
                loop.quit()
            else:
                QTimer.singleShot(200, poll)

        QTimer.singleShot(200, poll)
        loop.exec()
        self.assertIn(
            "Re-checked: PASS — Data directory (check_data,",
            label.text(), label.text())
        self.assertIs(self.page._repair_running, False)


# ---------------------------------------------------------- Qt: Settings
@unittest.skipUnless(_QT, "Qt unavailable")
class _WindowBaseCase(unittest.TestCase):
    """Full-window Settings tests (WindowBase; E-F209 pre-import)."""
    @classmethod
    def setUpClass(cls):
        import gui.w9_wiring_proof  # noqa: F401 — E-F209 pre-import
        from tests.test_chat_ui import WindowBase

        class Probe(WindowBase):
            def runTest(self):
                pass

        cls._Probe = Probe

    def setUp(self):
        self._probe = self._Probe("runTest")
        self._probe.setUp()
        self.w = self._probe.window
        self.w.show()
        _qt_app().processEvents()
        self.w._show_page(4)
        _qt_app().processEvents()
        if not getattr(self.w, "_more_expanded", False):
            self.w._on_more_toggled()
            _qt_app().processEvents()
        from voice_assistant.core.gui_facade import (
            config as cfg, qsettings_get,
            _stored_env_file_value as stored)
        self._cfg = cfg
        self._qget = qsettings_get
        self._stored = stored
        self._orig_profile = str(
            qsettings_get("appearance/hud_profile",
                          settings=self.w.settings) or "")
        self._orig_cadence = qsettings_get(
            "pulse/refresh_ms", settings=self.w.settings)
        self._env_existed = (ROOT / ".env").exists()

    def tearDown(self):
        # Leave no trace: restore the cadence + profile keys and
        # remove a .env this test created.
        try:
            from voice_assistant.core.gui_facade import qsettings_set
            if self._orig_cadence is not None:
                qsettings_set("pulse/refresh_ms", self._orig_cadence,
                              settings=self.w.settings)
            if self._orig_profile:
                qsettings_set("appearance/hud_profile",
                              self._orig_profile,
                              settings=self.w.settings)
        except Exception:
            pass
        try:
            self._probe.tearDown()
        except Exception:
            pass
        env_path = ROOT / ".env"
        if not self._env_existed and env_path.exists():
            env_path.unlink()
        _qt_app().processEvents()


class TestSettingsEffectLinesQt(_WindowBaseCase):
    def test_36_cards_with_effect_lines(self):
        eff = getattr(self.w, "_settings_card_effect_labels", {})
        # UI Phase 5 (2026-11-27): the "Home hub" card joins the
        # effect table as instant (35 labels; 33 visible).
        self.assertEqual(len(eff), 35)
        self.w._refresh_settings_card_values()
        _qt_app().processEvents()
        visible = {t: lbl.text() for t, lbl in eff.items()
                   if lbl.isVisible()}
        hidden = {t for t, lbl in eff.items() if not lbl.isVisible()}
        self.assertEqual(len(visible), 33)
        self.assertEqual(hidden, {"Presence", "Your settings file"})
        nxt = {t for t, v in visible.items()
               if v == "Takes effect the next time you connect."}
        self.assertEqual(nxt, {"How VYUK thinks", "Voice", "Local brain"})
        from gui import pf_permissions
        pw = [c for c in self.w.findChildren(object)
              if type(c).__name__ == "PermissionsWidget"]
        self.assertEqual(len(pw), 1)
        self.assertEqual(pw[0].effect_label.text(), "Applies instantly.")
        self.assertTrue(pw[0].effect_label.isVisible())


class TestSettingsPreviewWritesNothing(_WindowBaseCase):
    def test_preview_cancel_apply(self):
        w = self.w
        orig_cfg = str(getattr(self._cfg, "hud_profile", "") or "")
        orig_qs = self._orig_profile
        orig_env = self._stored("VYUK_HUD_PROFILE")
        self.assertFalse(w.hud_apply_btn.isVisible())
        w.hud_profile_buttons["Minimal"].click()
        _qt_app().processEvents()
        self.assertTrue(w.hud_apply_btn.isVisible())
        self.assertEqual(w._hud_preview_current, "Minimal")
        # Preview writes nothing, on any of the three stores.
        self.assertEqual(
            str(getattr(self._cfg, "hud_profile", "") or ""), orig_cfg)
        self.assertEqual(str(self._qget(
            "appearance/hud_profile", settings=w.settings) or ""), orig_qs)
        self.assertEqual(self._stored("VYUK_HUD_PROFILE"), orig_env)
        # Cancel restores the checked button, stores untouched.
        w.hud_cancel_btn.click()
        _qt_app().processEvents()
        self.assertFalse(w.hud_apply_btn.isVisible())
        self.assertEqual(self._stored("VYUK_HUD_PROFILE"), orig_env)
        want = orig_qs or orig_cfg or "Cinematic"
        self.assertTrue(w.hud_profile_buttons[want].isChecked())
        # Apply writes all three stores.
        w.hud_profile_buttons["Professional"].click()
        _qt_app().processEvents()
        w.hud_apply_btn.click()
        _qt_app().processEvents()
        self.assertEqual(
            str(getattr(self._cfg, "hud_profile", "") or ""),
            "Professional")
        self.assertEqual(str(self._qget(
            "appearance/hud_profile", settings=w.settings) or ""),
            "Professional")
        self.assertEqual(self._stored("VYUK_HUD_PROFILE"), "Professional")
        # Restore the original profile through Apply.
        w.hud_profile_buttons[want].click()
        _qt_app().processEvents()
        w.hud_apply_btn.click()
        _qt_app().processEvents()


class TestSettingsKeyTruth(_WindowBaseCase):
    def test_truth_lines_present_and_honest_without_env(self):
        w = self.w
        w._refresh_brain_truth()
        _qt_app().processEvents()
        self.assertTrue(
            w.api_key_truth_lbl.text().startswith("Saved key:"))
        self.assertTrue(
            w.groq_key_truth_lbl.text().startswith("Saved Groq key:"))
        self.assertTrue(
            w.cloud_provider_truth_lbl.text().startswith(
                "Saved service:"))
        self.assertEqual(w.api_key_truth_lbl.text(),
                         "Saved key: not saved yet")

    def test_refresh_key_truth_cases(self):
        from PySide6.QtWidgets import QLabel
        w = self.w
        lbl = QLabel("")

        class FakeInput:
            def __init__(self, t):
                self._t = t

            def text(self):
                return self._t

        w._refresh_key_truth(lbl, "GEMINI_API_KEY", "Saved key", None,
                             None, lambda k: "k-abc")
        self.assertEqual(lbl.text(), "Saved key: set")
        w._refresh_key_truth(lbl, "GEMINI_API_KEY", "Saved key", None,
                             None, lambda k: None)
        self.assertEqual(lbl.text(), "Saved key: not saved yet")
        w._refresh_key_truth(lbl, "GEMINI_API_KEY", "Saved key",
                             FakeInput("k-try"), "k-try", lambda k: None)
        self.assertEqual(
            lbl.text(),
            "Saved key: the save didn't land — try saving again.")
        w._refresh_key_truth(lbl, "GEMINI_API_KEY", "Saved key",
                             FakeInput("k-new"), None, lambda k: "k-old")
        self.assertEqual(
            lbl.text(),
            "Saved key: set — the key in the box isn't saved yet.")


class TestCadenceMirror(_WindowBaseCase):
    def test_mirror_writes_and_resyncs(self):
        w = self.w
        combo = getattr(w, "pulse_cadence_combo", None)
        self.assertIsNotNone(combo)
        combo.setCurrentIndex(combo.findData(2000))
        _qt_app().processEvents()
        self.assertEqual(int(self._qget(
            "pulse/refresh_ms", settings=w.settings)), 2000)
        from voice_assistant.core.gui_facade import qsettings_set
        qsettings_set("pulse/refresh_ms", 5000, settings=w.settings)
        w._sync_appearance_controls()
        _qt_app().processEvents()
        self.assertEqual(combo.currentData(), 5000)


class TestAboutUndo(_WindowBaseCase):
    def test_undo_coverage_stated(self):
        from PySide6.QtWidgets import QLabel
        texts = [lbl.text() for lbl in self.w.findChildren(QLabel)]
        self.assertTrue(any(
            "Undo covers settings changes, file writes, and "
            "extension state" in t for t in texts))
        self.assertTrue(any(
            "can't undo a message sent or a payment completed" in t
            for t in texts))


if __name__ == "__main__":
    unittest.main()
