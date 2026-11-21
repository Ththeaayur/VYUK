"""Round-3 section E, E-F104: error-level disk/battery states are
word-identical to their warnings on the status strip.

Section-E finding E-F104: on the status strip an error-level disk
state read "Disk almost full" with a red dot, and the warning read
the same words with an amber dot — dot colour only. Same for
"Battery low" at <=10% vs <=20% unplugged. Deliberate shape of
``derive_strip_state`` (gui/status_strip.py), so a design gap, not
a code slip — and the same words come out of the Pulse page's own
``_metric_word`` (gui/pulse_page.py), which feeds the page's word
label: one vocabulary, two derivations, same dishonesty.

The fix (E-F104, two product files): the error level gets its own
words in both derivations — "Disk critically full" (disk >= 95%)
and "Battery critically low" (battery <= 10% unplugged) — while the
warning words stay "Disk almost full" (>= 90%) and "Battery low"
(<= 20% unplugged). Thresholds, worst-first order, the E-F102
Offline mapping and the other 15 strip states are all unchanged:
a user who cannot see (or does not notice) the dot colour still
knows the disk is in error, not merely warning.

The pins that asserted the old shape are re-baselined by the fix
itself (E-F104 comments in the tests): test_rethink_build3's
test_metric_errors + test_worst_first, test_rethink_build6's
test_word_names_the_worst_metric, and this queue's own
test_r3ef102_status_strip test_metric_error_beats_offline — same
rigour, exact tuples, not loosened.

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen); the derivations are pure but live in
Qt modules, so they are gated the same way as rethink Build 3's
TestStripDerivation (skipped on the sys interpreter, which has
no PySide6). One source pin runs on both interpreters: it reads
the two product files as text and pins the error branches to
the new words, so the vocabulary cannot drift back silently.
"""
import os
import pathlib
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]

CALM = {"cpu": 12, "mem": 45, "disk": 55, "net_bps": 1200.0,
        "battery_pct": 82, "battery_plugged": True,
        "uptime_s": 3600.0, "procs": 180}


def _blank_sample():
    return {"cpu": None, "mem": None, "disk": None,
            "net_bps": None, "battery_pct": None,
            "battery_plugged": None, "uptime_s": None,
            "procs": None}


def _pump(app, secs=0.4):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


class TestErrorWordsInSource(unittest.TestCase):
    """The error branches return the error words, never the
    warning words — pinned at source level so it runs on both
    interpreters (the derivations live in Qt modules)."""

    def test_error_branches_use_the_error_words(self):
        strip = (ROOT / "gui" / "status_strip.py").read_text()
        pulse = (ROOT / "gui" / "pulse_page.py").read_text()
        for text in (strip, pulse):
            self.assertIn("critically full", text)
            self.assertIn("critically low", text)
        self.assertIn('return ("error", "Disk critically full")', strip)
        self.assertIn('return ("error", "Battery critically low")',
                      strip)
        self.assertIn('return ("Disk critically full", "error")', pulse)
        self.assertIn('return ("Battery critically low", "error")',
                      pulse)
        # And the error branches never again return the warning
        # words (the warning branches still do — pinned below).
        self.assertNotIn('return ("error", "Disk almost full")', strip)
        self.assertNotIn('return ("error", "Battery low")', strip)
        self.assertNotIn('return ("Disk almost full", "error")', pulse)
        self.assertNotIn('return ("Battery low", "error")', pulse)
        self.assertIn('return ("warning", "Disk almost full")', strip)
        self.assertIn('return ("warning", "Battery low")', strip)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestStripDerivationF104(unittest.TestCase):
    """derive_strip_state: error and warning differ in words."""

    def setUp(self):
        from gui.status_strip import derive_strip_state
        self._derive = derive_strip_state

    def test_disk_levels_differ_in_words(self):
        warn = _blank_sample()
        warn["disk"] = 92.0
        self.assertEqual(self._derive(warn, "", False),
                         ("warning", "Disk almost full"))
        error = _blank_sample()
        error["disk"] = 97.0
        self.assertEqual(self._derive(error, "", False),
                         ("error", "Disk critically full"))
        # The boundary itself: 95% is the error threshold.
        at = _blank_sample()
        at["disk"] = 95.0
        self.assertEqual(self._derive(at, "", False),
                         ("error", "Disk critically full"))
        below = _blank_sample()
        below["disk"] = 94.9
        self.assertEqual(self._derive(below, "", False),
                         ("warning", "Disk almost full"))

    def test_battery_levels_differ_in_words(self):
        warn = _blank_sample()
        warn["battery_pct"] = 15.0
        warn["battery_plugged"] = False
        self.assertEqual(self._derive(warn, "", False),
                         ("warning", "Battery low"))
        error = _blank_sample()
        error["battery_pct"] = 5.0
        error["battery_plugged"] = False
        self.assertEqual(self._derive(error, "", False),
                         ("error", "Battery critically low"))
        # The boundary itself: 10% is the error threshold.
        at = _blank_sample()
        at["battery_pct"] = 10.0
        at["battery_plugged"] = False
        self.assertEqual(self._derive(at, "", False),
                         ("error", "Battery critically low"))
        below = _blank_sample()
        below["battery_pct"] = 10.5
        below["battery_plugged"] = False
        self.assertEqual(self._derive(below, "", False),
                         ("warning", "Battery low"))

    def test_plugged_and_unknown_never_warn(self):
        charging = _blank_sample()
        charging["battery_pct"] = 5.0
        charging["battery_plugged"] = True
        self.assertEqual(self._derive(charging, "", False),
                         ("ok", "Ready"))
        self.assertEqual(self._derive(_blank_sample(), "", False),
                         ("ok", "Ready"))

    def test_worst_first_and_offline_unchanged(self):
        # E-F102 still holds: Offline is a warning, and a metric
        # error still beats it (worst-first preserved).
        self.assertEqual(self._derive(_blank_sample(), "Offline", False),
                         ("warning", "Offline"))
        error = _blank_sample()
        error["disk"] = 97.0
        self.assertEqual(self._derive(error, "Offline", False),
                         ("error", "Disk critically full"))
        error = _blank_sample()
        error["battery_pct"] = 5.0
        error["battery_plugged"] = False
        self.assertEqual(self._derive(error, "Offline", False),
                         ("error", "Battery critically low"))


@unittest.skipUnless(_QT, "Qt unavailable")
class TestMetricWordF104(unittest.TestCase):
    """_metric_word: the Pulse page's derivation, same vocabulary."""

    def setUp(self):
        from gui.pulse_page import _metric_word
        self._word = _metric_word

    def test_disk_levels_differ_in_words(self):
        self.assertEqual(
            self._word(dict(_blank_sample(), disk=92.0)),
            ("Disk almost full", "warning"))
        self.assertEqual(
            self._word(dict(_blank_sample(), disk=96.0)),
            ("Disk critically full", "error"))

    def test_battery_levels_differ_in_words(self):
        self.assertEqual(
            self._word(dict(_blank_sample(), battery_pct=15.0,
                            battery_plugged=False)),
            ("Battery low", "warning"))
        self.assertEqual(
            self._word(dict(_blank_sample(), battery_pct=8.0,
                            battery_plugged=False)),
            ("Battery critically low", "error"))

    def test_charging_battery_is_not_low(self):
        self.assertEqual(
            self._word(dict(_blank_sample(), battery_pct=8.0,
                            battery_plugged=True)),
            ("Ready", "ok"))


@unittest.skipUnless(_QT, "Qt unavailable")
class TestStripWindowF104(WindowBase):
    """The REAL MainWindow offscreen: the strip reads the error
    in words, on the live dot and the live word."""

    def _drive(self, sample_over, want_word, want_dot):
        from gui.style import AMBER, DANGER, OK
        strip = getattr(self.window, "vitals_strip", None)
        self.assertIsNotNone(strip, "the docked strip was not built")
        strip.set_sampler(lambda: dict(CALM, **sample_over))
        strip._tick()
        _pump(self.app, 0.3)
        self.assertEqual(strip.state_word(), want_word)
        self.assertEqual(strip._dot_state, want_dot)
        want_hex = {"ok": OK, "warning": AMBER, "error": DANGER}[want_dot]
        self.assertIn(want_hex, strip.dot_button.styleSheet(),
                      "the visible dot must carry the level colour")

    def test_disk_warn_then_error_live(self):
        self._drive({"disk": 92.0}, "Disk almost full", "warning")
        self._drive({"disk": 97.0}, "Disk critically full", "error")

    def test_battery_warn_then_error_live(self):
        self._drive({"battery_pct": 15.0, "battery_plugged": False},
                    "Battery low", "warning")
        self._drive({"battery_pct": 5.0, "battery_plugged": False},
                    "Battery critically low", "error")

    def test_offline_still_holds_and_metric_wins_live(self):
        strip = getattr(self.window, "vitals_strip", None)
        strip.set_sampler(lambda: dict(CALM))
        strip._tick()
        self.window._set_pill("Offline")
        _pump(self.app, 0.4)
        strip._tick()
        self.assertEqual(strip.state_word(), "Offline")
        self.assertEqual(strip._dot_state, "warning")
        # Worst-first: Offline + disk error -> the metric wins.
        strip.set_sampler(lambda: dict(CALM, disk=97.0))
        strip._tick()
        _pump(self.app, 0.3)
        self.assertEqual(strip.state_word(), "Disk critically full")
        self.assertEqual(strip._dot_state, "error")

    def test_pulse_page_word_label(self):
        from gui.pulse_page import PulsePage
        page = PulsePage(
            sampler=lambda: dict(CALM, disk=96.0))
        self.addCleanup(page.deleteLater)
        page.sample_now()
        self.assertEqual(page.word_label.text(),
                         "Disk critically full")
        page2 = PulsePage(
            sampler=lambda: dict(CALM, battery_pct=8.0,
                                 battery_plugged=False))
        self.addCleanup(page2.deleteLater)
        page2.sample_now()
        self.assertEqual(page2.word_label.text(),
                         "Battery critically low")


if __name__ == "__main__":
    unittest.main()
