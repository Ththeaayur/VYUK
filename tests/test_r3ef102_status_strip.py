"""Round-3 section E, E-F102: the status strip says "Ready" while
the session pill says "Offline" after an intentional disconnect.

Section-E finding E-F102: after an intentional disconnect the
header pill read `Offline` and the always-visible status strip
read green-dot `Ready`. `derive_strip_state` (gui/status_strip.py)
fell through to `("ok", "Ready")` for any session word not in
`_ACTIVE_WORDS`, and `Offline` is not in it — while the strip
module's own docstring says the strip and the pill "can never
disagree about the session". `Ready` reads as ready-to-talk
while the session is disconnected.

The fix (E-F102, one product file): the fall-through no longer
swallows a real session word into "Ready". "Offline" derives to
`("warning", "Offline")` — a degraded session (the user cannot
talk), not a fault (nothing broke) and not "all good". Any other
non-empty session word shows as-is ("Local core" — the on-device
brain is live — and any future word), and "Ready" is honest only
when there is no session word at all. Worst-first is unchanged:
metric errors/warnings, "Problem", "Setup needed" and the muted-
mic rule all still run before the session-word branches, so the
strip's other 15 states are exactly as section E verified them.

The rethink Build 3 pin that asserted the old shape
(`test_ready_when_idle_and_unknown`: Offline/Local core -> "Ready")
is re-baselined by the fix itself (E-F102 supersession comment
in the test) to pin the new honest mapping — same rigour, exact
tuples, not loosened.

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen); the derivation is pure but lives in
a Qt module, so it is gated the same way as rethink Build 3's
TestStripDerivation (skipped on the sys interpreter, which has
no PySide6).
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

# Old pill label -> (pill word, strip word, dot) the strip must show.
WORDS = [
    ("Listening", "Listening", "ok"),
    ("Speaking", "Responding", "ok"),
    ("Thinking", "Thinking", "ok"),
    ("Connecting", "Connecting", "ok"),
    ("Reconnecting", "Reconnecting", "ok"),
    ("Setting up", "Setting up", "ok"),
    ("Problem", "Problem", "error"),
    ("Setup needed", "Setup needed", "warning"),
    ("On-device brain", "Local core", "ok"),
    ("Offline", "Offline", "warning"),
]


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


@unittest.skipUnless(_QT, "Qt unavailable")
class TestStripDerivationF102(unittest.TestCase):
    """derive_strip_state: the strip word is the session word."""

    def setUp(self):
        from gui.status_strip import derive_strip_state
        self._derive = derive_strip_state

    def test_offline_is_warning_not_ready(self):
        self.assertEqual(self._derive(_blank_sample(), "Offline", False),
                         ("warning", "Offline"))

    def test_local_core_shows_as_is(self):
        self.assertEqual(
            self._derive(_blank_sample(), "Local core", False),
            ("ok", "Local core"))

    def test_every_display_word_agrees(self):
        for _label, word, dot in WORDS:
            self.assertEqual(
                self._derive(_blank_sample(), word, False), (dot, word),
                f"{word} must derive to itself, not Ready")

    def test_idle_is_ready(self):
        self.assertEqual(self._derive(_blank_sample(), "", False),
                         ("ok", "Ready"))

    def test_offline_muted_stays_offline(self):
        # The mic branch only fires for live words — a muted mic
        # on a dead session must not relabel it "Mic off".
        self.assertEqual(self._derive(_blank_sample(), "Offline", True),
                         ("warning", "Offline"))

    def test_metric_error_beats_offline(self):
        # Worst-first is unchanged: a metric error still wins
        # over the session word. E-F104: the winning word is now
        # "Disk critically full", not the warning word.
        sample = _blank_sample()
        sample["disk"] = 97.0
        self.assertEqual(self._derive(sample, "Offline", False),
                         ("error", "Disk critically full"))

    def test_unknown_word_shows_as_is_not_ready(self):
        # A future session word must never again be swallowed
        # into "Ready" by the fall-through.
        self.assertEqual(self._derive(_blank_sample(), "Paused", False),
                         ("ok", "Paused"))


@unittest.skipUnless(_QT, "Qt unavailable")
class TestStripWindowF102(WindowBase):
    """The REAL MainWindow offscreen: strip and pill agree."""

    def test_idle_strip_ready(self):
        strip = getattr(self.window, "vitals_strip", None)
        self.assertIsNotNone(strip, "the docked strip was not built")
        strip.set_sampler(lambda: dict(CALM))
        strip._tick()
        self.assertEqual(strip.state_word(), "Ready")
        self.assertEqual(strip._dot_state, "ok")

    def test_disconnect_path_offline(self):
        from voice_assistant.core.gui_facade import audio_manager
        audio_manager.is_muted = False
        strip = getattr(self.window, "vitals_strip", None)
        strip.set_sampler(lambda: dict(CALM))
        strip._tick()
        # The exact Connect-toggle path (slice-1 repro (1)).
        self.window._intentional_disconnect = True
        self.window._on_status("DISCONNECTED")
        _pump(self.app, 0.6)
        strip._tick()
        self.assertEqual(self.window.status_pill.text(), "Offline")
        self.assertEqual(strip.state_word(), "Offline")
        self.assertEqual(strip._dot_state, "warning")

    def test_all_words_via_pill(self):
        from gui.style import AMBER, DANGER, OK
        from voice_assistant.core.gui_facade import audio_manager
        audio_manager.is_muted = False
        strip = getattr(self.window, "vitals_strip", None)
        strip.set_sampler(lambda: dict(CALM))
        strip._tick()
        want_hex = {"ok": OK, "warning": AMBER, "error": DANGER}
        for label, word, dot in WORDS:
            self.window._set_pill(label)
            _pump(self.app, 0.4)
            strip._tick()
            self.assertEqual(self.window.status_pill.text(), word,
                             f"pill for {label!r}")
            self.assertEqual(strip.state_word(), word,
                             f"strip for {label!r} must match the pill")
            self.assertEqual(strip._dot_state, dot,
                             f"dot for {label!r}")
            self.assertIn(want_hex[dot],
                          strip.dot_button.styleSheet(),
                          f"visible dot for {label!r}")


if __name__ == "__main__":
    unittest.main()
