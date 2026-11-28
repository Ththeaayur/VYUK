"""Screen 1 Home — the sci-fi room (2026-11-16).

Covers the build per the room model (gui/room_home.py):

Headless (both interpreters):
- the light table: every state maps to its wash, night scales it,
  speaking follows the voice energy, error holds the last wash;
- the state mapping from the live state the app already knows;
- the 220 ms OutCubic easing settles;
- the shell-strength mapping keeps the brightest the room ever
  gets at the pre-build loud-speaking strength;
- the briefing summary derives from the schedule line;
- the type scale sits on the 1.2 ladder from body 13;
- the readability floor (4.5:1 body, 3:1 large) on the pairs the
  room actually shows, and the greyscale hierarchy (the order
  survives desaturation);
- every demotion names its way back.

Qt (offscreen, the REAL MainWindow via the Chat UI harness):
- the greeting is display type (28 Orbitron), the state line is
  quiet body text, the vitals are a mono readout;
- the orb is first in the room (tallest stage child, above the
  card);
- idle breathing: two grabs 300 ms apart differ;
- night dims the wash and the particle field;
- the light follows the real state (speaking/listening/muted/
  offline) through _sync_room_light;
- the demotions hold on the live window (chip hidden, pace box
  and mic meter hidden at idle, card collapsed, privacy card
  gone, home strip gone);
- the transcript did not regress (composer placeholder, chat
  panel);
- the render grabs land in scifi_home_renders/ for the human
  look (the five room-test verdicts live in the build report,
  judged from those grabs, not from these asserts).
"""

from __future__ import annotations

import math
import os
import pathlib
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.room_home import (  # noqa: E402
    DEMOTIONS,
    LIGHT_TABLE,
    ROOM_COPY,
    TYPE_SCALE,
    RoomLight,
    briefing_summary_for,
    contrast_ratio,
    is_night,
    luminance,
    night_factor,
    out_cubic,
    shell_strength,
    state_from,
    wash_for,
)
from gui.style_tokens import (  # noqa: E402
    ACCENT,
    BG,
    SURFACE_1,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    TYPE_BODY,
    TYPE_TITLE,
)

RENDER_DIR = ROOT.parents[2] / "scifi_home_renders"


# ------------------------------------------------------------------
# Headless: the light table
# ------------------------------------------------------------------

class RoomLightTableTests(unittest.TestCase):
    def test_every_state_has_its_wash(self):
        self.assertAlmostEqual(wash_for("idle", hour=12), 0.04)
        self.assertAlmostEqual(wash_for("listening", hour=12), 0.08)
        self.assertAlmostEqual(wash_for("thinking", hour=12), 0.06)
        self.assertAlmostEqual(wash_for("busy", hour=12), 0.08)
        self.assertAlmostEqual(wash_for("muted", hour=12), 0.02)
        self.assertAlmostEqual(wash_for("offline", hour=12), 0.02)

    def test_speaking_follows_the_voice(self):
        self.assertAlmostEqual(wash_for("speaking", 0.0, hour=12), 0.04)
        self.assertAlmostEqual(wash_for("speaking", 0.5, hour=12), 0.09)
        self.assertAlmostEqual(wash_for("speaking", 1.0, hour=12), 0.14)
        # Energy clamps — a loud spike never blows the room out.
        self.assertAlmostEqual(wash_for("speaking", 2.0, hour=12), 0.14)
        self.assertAlmostEqual(wash_for("speaking", -1.0, hour=12), 0.04)

    def test_error_has_no_target(self):
        self.assertIsNone(wash_for("error", hour=12))
        self.assertIsNone(LIGHT_TABLE["error"])

    def test_night_dims_the_wash(self):
        self.assertTrue(is_night(23))
        self.assertTrue(is_night(3))
        self.assertFalse(is_night(12))
        self.assertFalse(is_night(8))
        self.assertAlmostEqual(night_factor(23), 0.7)
        self.assertAlmostEqual(night_factor(12), 1.0)
        self.assertAlmostEqual(wash_for("idle", hour=23), 0.04 * 0.7)
        self.assertAlmostEqual(wash_for("listening", hour=23), 0.08 * 0.7)
        self.assertAlmostEqual(wash_for("speaking", 1.0, hour=23),
                               0.14 * 0.7)

    def test_state_mapping_priority(self):
        base = dict(connected=True, in_flight=False, muted=False,
                    busy=False, fatal=False)
        self.assertEqual(state_from("LISTENING", **base), "listening")
        self.assertEqual(state_from("SPEAKING", **base), "speaking")
        self.assertEqual(state_from("THINKING", **base), "thinking")
        self.assertEqual(state_from("CONNECTING", **base), "thinking")
        self.assertEqual(state_from("SETUP", **base), "thinking")
        self.assertEqual(state_from("", **base), "idle")
        # A muted mic outranks speaking (the orb is already dimmed).
        self.assertEqual(
            state_from("SPEAKING", **{**base, "muted": True}), "muted")
        # A fatal drop holds the room whatever the words say.
        self.assertEqual(
            state_from("LISTENING", **{**base, "fatal": True}), "error")
        self.assertEqual(
            state_from("ERROR", **base), "error")
        # A running goal is busy even with no session.
        self.assertEqual(
            state_from("", **{**base, "connected": False, "busy": True}),
            "busy")
        # Offline is the disconnected rest state.
        self.assertEqual(
            state_from("", **{**base, "connected": False}), "offline")
        # An in-flight connection reads as thinking, not offline.
        self.assertEqual(
            state_from("", **{**base, "connected": False,
                              "in_flight": True}), "thinking")

    def test_shell_strength_keeps_the_brightest_unchanged(self):
        # Full speaking wash maps to the pre-build loud-speaking
        # shell strength (0.85); the retune dims the lower states,
        # it never brightens the top.
        self.assertAlmostEqual(shell_strength(0.14), 0.85)
        self.assertAlmostEqual(shell_strength(0.04), 0.04 / 0.14 * 0.85)
        self.assertAlmostEqual(shell_strength(0.02), 0.02 / 0.14 * 0.85)
        self.assertLess(shell_strength(0.04), 0.37)  # was idle ~0.37
        self.assertIsNone(shell_strength(None))

    def test_easing_settles_in_220ms(self):
        self.assertAlmostEqual(out_cubic(0.0), 0.0)
        self.assertAlmostEqual(out_cubic(1.0), 1.0)
        self.assertGreater(out_cubic(0.5), 0.5)  # OutCubic leads
        light = RoomLight()
        self.assertAlmostEqual(light.current, 0.04)
        light.update("listening", hour=12, dt_ms=25.0)
        self.assertGreater(light.current, 0.04)
        self.assertLess(light.current, 0.08)
        for _ in range(9):
            light.update("listening", hour=12, dt_ms=25.0)
        self.assertAlmostEqual(light.current, 0.08, places=6)

    def test_error_holds_the_last_wash(self):
        light = RoomLight()
        for _ in range(10):
            light.update("listening", hour=12, dt_ms=25.0)
        self.assertAlmostEqual(light.current, 0.08, places=6)
        held = light.update("error", hour=12, dt_ms=25.0)
        self.assertAlmostEqual(held, 0.08, places=6)
        for _ in range(5):
            self.assertAlmostEqual(
                light.update("error", hour=12, dt_ms=25.0), 0.08,
                places=6)


# ------------------------------------------------------------------
# Headless: briefing summary, type scale, copy, demotions
# ------------------------------------------------------------------

class BriefingSummaryTests(unittest.TestCase):
    def test_two_clauses_pass_through(self):
        self.assertEqual(
            briefing_summary_for("3 things coming up · Standup 09:00"),
            "3 things coming up · Standup 09:00")

    def test_three_clauses_keep_two_plus_ellipsis(self):
        self.assertEqual(
            briefing_summary_for(
                "3 things coming up · Standup 09:00 · Reminders: Call bank"),
            "3 things coming up · Standup 09:00 …")

    def test_empty_loading_and_failure_lines_pass_through(self):
        self.assertEqual(
            briefing_summary_for(
                "Nothing scheduled today — ask me to add something."),
            "Nothing scheduled today — ask me to add something.")
        self.assertEqual(
            briefing_summary_for("Checking today's calendar…"),
            "Checking today's calendar…")
        self.assertEqual(
            briefing_summary_for(
                "Today's calendar couldn't be loaded — press Play "
                "briefing and ask her."),
            "Today's calendar couldn't be loaded — press Play "
            "briefing and ask her.")
        self.assertEqual(briefing_summary_for(""),
                         ROOM_COPY["empty_schedule"])

    def test_not_connected_copy_passes_through(self):
        # Briefing fix (2026-11-26): the not-connected line is a
        # failure line — it must reach the summary verbatim, never
        # cut to "Calendar not…".
        self.assertEqual(
            briefing_summary_for("Connect your calendar to see your day"),
            "Connect your calendar to see your day")


class TypeScaleTests(unittest.TestCase):
    def test_tokens_match_the_scale(self):
        # Build 2: the 28/23/19 tokens are deleted (the greeting
        # and briefing were reassigned in Build 1, Activity in
        # Build 2). The title and body rungs remain.
        self.assertEqual(TYPE_TITLE, 20)
        self.assertEqual(TYPE_BODY, 13)

    def test_every_used_size_sits_on_the_1_2_ladder(self):
        # The scale is a 1.2 geometric ladder from body 13: each
        # used size is within 1.5 px of 13 x 1.2^n for some rung n.
        # (Display 20 is 1.28 px above the exact 1.2^2 rung,
        # 18.72, per the Build 1 reassignment.)
        for _name, (size, _family, _role) in TYPE_SCALE.items():
            best = min(
                abs(size - 13 * 1.2 ** n) for n in range(-2, 6))
            self.assertLessEqual(best, 1.5, f"{_name} {size} off-ladder")

    def test_display_is_orbitron_only_and_data_is_mono(self):
        self.assertEqual(TYPE_SCALE["display"][1], "Orbitron")
        self.assertEqual(TYPE_SCALE["data"][1], "Share Tech Mono")
        self.assertEqual(TYPE_SCALE["body"][1], "Sora")


class ContrastTests(unittest.TestCase):
    def test_readability_floor_on_the_pairs_the_room_shows(self):
        # Body floor 4.5:1, large (display/lead) floor 3:1.
        pairs = [
            (TEXT, BG, 4.5, "greeting on the canvas"),
            (TEXT_MUTED, BG, 4.5, "state line and caption on the canvas"),
            (TEXT, SURFACE_1, 4.5, "summary on the card"),
            (TEXT_MUTED, SURFACE_1, 4.5, "card text on the card"),
            (ACCENT, BG, 3.0, "accent wordmark glow on the canvas"),
        ]
        for fg, bg, floor, label in pairs:
            ratio = contrast_ratio(fg, bg)
            self.assertGreaterEqual(ratio, floor, f"{label}: {ratio:.2f}")

    def test_contrast_math_agrees_with_core_tokens(self):
        from core.tokens import contrast_ratio as _core_ratio
        for fg, bg in ((TEXT, BG), (TEXT_MUTED, BG), (ACCENT, BG),
                       (TEXT_FAINT, SURFACE_1)):
            self.assertAlmostEqual(contrast_ratio(fg, bg),
                                   _core_ratio(fg, bg), places=6)

    def test_greyscale_hierarchy_survives_desaturation(self):
        # Room test 3: strip the colour and the TEXT order must
        # hold — the greeting outranks body text, body outranks
        # muted, muted outranks the faint hint. (The accent's
        # flat luminance is darker than the text — it reads as
        # the light through saturation and the orb's bright
        # painted core, which the grey grab judges, not this
        # token computation.)
        order = [TEXT, TEXT_MUTED, TEXT_FAINT]
        luminances = [luminance(c) for c in order]
        self.assertEqual(luminances, sorted(luminances, reverse=True),
                         f"luminances out of order: {list(zip(order, luminances))}")
        # And the canvas itself is far darker than any of them.
        self.assertLess(luminance(BG), min(luminances) / 4)


class DemotionsTests(unittest.TestCase):
    def test_every_demotion_names_its_way_back(self):
        self.assertTrue(DEMOTIONS)
        for element, was, now, way_back in DEMOTIONS:
            self.assertTrue(element and was and now and way_back,
                            f"demotion without a way back: {element}")
            # The way back is a real sentence, not a placeholder.
            self.assertGreater(len(way_back), 20,
                               f"way back too short: {element}")


# ------------------------------------------------------------------
# Qt: the real window
# ------------------------------------------------------------------

try:
    from tests.test_chat_ui import WindowBase  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    WindowBase = object  # type: ignore[assignment]
    _QT = False


@unittest.skipUnless(_QT, "Qt unavailable")
class RoomWindowTests(WindowBase):
    """The REAL MainWindow offscreen — the room as built."""

    def _day(self):
        """Pin the room to day (hour 12) so the light asserts are
        not hour-dependent."""
        self.window._room_hour = 12
        self.window._room_night = False
        field = getattr(self.window, "particle_field", None)
        if field is not None:
            field.set_night(False)

    def _audio_fake(self):
        """The audio stand-in the window actually reads.

        UI upgrade Phase 1 (2026-11-24): _sync_audio_telemetry
        resolves audio_manager fresh via the facade on every tick
        (the leg-35 staleness fix), so the window reads the
        CURRENT stub — not the module-top binding this used to
        return. The facade re-resolves per access, so this
        returns the live object the telemetry reads.
        """
        import voice_assistant.core.gui_facade as _facade
        return _facade.audio_manager

    def _settle_light(self, target=None, timeout=10.0):
        # Freeze the ~1 Hz vitals block for the settle: it would
        # otherwise overwrite the forced test hour with the real
        # clock on its first tick.
        import time as _time
        self.window._vitals_last = _time.monotonic()
        # Look-fix 2 (2026-11-24): time-anchored, not count-anchored.
        # The old fixed 10 processEvents() starved under VM load —
        # one pump can take ~0.5 s of boot work, and a state flip
        # mid-settle restarts the 220 ms ease, so 10 ticks never
        # guaranteed convergence (the night leg read 0.0397 against
        # 0.028 in the parent's slow run). With a target, pump
        # until the MEASURED value is within tolerance or a
        # wall-clock timeout expires; without one, pump for a
        # measured 1.0 s. The count is never the anchor.
        start = _time.monotonic()
        while True:
            # D-01 (UI Phase 6): re-freeze every iteration. The
            # freeze above expires 1.0 s after it is set (the
            # vitals guard in _sync_audio_telemetry), but a settle
            # under VM load runs far longer — one pump can take
            # ~0.5 s, and nine ease ticks need ~4.5 s. Without this
            # the vitals block fires mid-settle and overwrites the
            # forced _room_hour with the real clock, so the light
            # eases back to the day wash: the pre-fix leg read 0.04
            # against the 0.028 night target.
            self.window._vitals_last = _time.monotonic()
            self.window._sync_audio_telemetry()
            self.app.processEvents()
            elapsed = _time.monotonic() - start
            if target is not None:
                try:
                    current = self.window._room_light.current
                except Exception:  # noqa: BLE001 — no light, no settle
                    current = None
                if (current is not None
                        and abs(current - target) <= 0.0005):
                    return
                if elapsed >= timeout:
                    return
            elif elapsed >= 1.0:
                return

    def test_greeting_is_display_type(self):
        # UI rethink Build 1: the greeting steps 28 -> 20 Orbitron
        # (the title rung) — the face stays, the size steps down.
        from PySide6.QtWidgets import QLabel
        greeting = self.window.findChild(QLabel, "homeGreeting")
        self.assertIsNotNone(greeting, "no homeGreeting label")
        font = greeting.font()
        self.assertEqual(font.pixelSize(), 20)
        self.assertEqual(font.family(), "Orbitron")

    def test_state_line_is_quiet_body_text(self):
        from PySide6.QtWidgets import QLabel
        # UI upgrade Phase 1: the state label lives in the HUD
        # as homeStateLine (the old stateLabel is gone).
        label = self.window.findChild(QLabel, "homeStateLine")
        self.assertIsNotNone(label)
        self.assertEqual(label.font().pixelSize(), 13)
        self.assertNotEqual(label.font().family(), "Orbitron")

    def test_vitals_are_a_mono_readout(self):
        from PySide6.QtWidgets import QLabel
        line = self.window.findChild(QLabel, "briefingLine")
        self.assertIsNotNone(line)
        self.assertEqual(line.font().pixelSize(), 13)
        self.assertEqual(line.font().family(), "Share Tech Mono")

    def test_briefing_summary_is_lead_type(self):
        # UI rethink Build 1: the briefing summary steps 19 -> 13
        # (body) — it is the day's headline at body size, not a
        # lead-size shout.
        from PySide6.QtWidgets import QLabel
        summary = self.window.findChild(QLabel, "briefingSummary")
        self.assertIsNotNone(summary)
        self.assertEqual(summary.font().pixelSize(), 13)

    def _set_long_briefing_summary(self):
        """Set the field check's 130-char summary AFTER the
        window has settled, and read it back at once: the async
        calendar fill can overwrite the schedule line later,
        and the narrow rule itself applies synchronously on
        the set."""
        from PySide6.QtTest import QTest
        self.window._set_briefing_schedule_text(
            "Calendar is packed: standup at 09:00, lab at 11:00, "
            "review at 15:30, and dinner with the team at 19:00 — "
            "four things before night.")
        self.app.processEvents()
        QTest.qWait(150)
        self.app.processEvents()

    def test_briefing_play_button_collapses_to_icon_when_narrow(self):
        # Field-check fix #8 (U5 #34): below the narrow width
        # the Play briefing button collapses to icon-only so the
        # one-line summary keeps a readable width.
        # UI upgrade Phase 1: the briefing card lives in the
        # right accordion column (150 px at 1280), so the card
        # is ALWAYS below the 420 threshold and the button is
        # icon-only at 1280 — the narrow rule is verified at
        # 1280 now, not 760 (where the right column is hidden
        # and the card keeps a stale 612 px geometry).
        from PySide6.QtTest import QTest
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        self.window.resize(1280, 800)
        self.window.show()
        QTest.qWait(300)
        self.app.processEvents()
        self.assertLess(self.window.briefing_card.width(), 420)
        self._set_long_briefing_summary()
        self.assertEqual(self.window.btn_briefing.text(), "")
        self.assertEqual(
            self.window.btn_briefing.accessibleName(), "Play briefing")
        # UI upgrade Phase 1: the card is 150 px in the right
        # column, so the one-line summary elides to what fits
        # (~13 chars in the vertical header). It must stay a
        # meaningful elision — starting with "Calendar", longer
        # than the old 2-char "C…" squeeze — and the FULL text
        # (which carries "packed") is preserved in
        # _briefing_summary_full and the expanded details.
        summary = self.window.briefing_summary.text()
        self.assertTrue(summary.startswith("Calendar"))
        self.assertGreater(len(summary), 5)
        self.assertIn("packed", self.window._briefing_summary_full)

    def test_briefing_play_button_text_returns_when_wide(self):
        # UI upgrade Phase 1: the briefing card lives in the
        # right accordion column (150 px at 1280, 225 px at
        # 1920 — both below the 420 threshold), so the button
        # stays icon-only at every tier; the label never returns
        # because the card is never wide. The action is unchanged
        # — the tooltip and accessible name still carry it.
        from PySide6.QtTest import QTest
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        self.window.resize(1280, 800)
        self.window.show()
        QTest.qWait(300)
        self.app.processEvents()
        self._set_long_briefing_summary()
        self.assertEqual(self.window.btn_briefing.text(), "")
        self.window.resize(1920, 1080)
        QTest.qWait(300)
        self.app.processEvents()
        self._set_long_briefing_summary()
        self.assertEqual(self.window.btn_briefing.text(), "")
        self.assertEqual(
            self.window.btn_briefing.toolTip(), "Play briefing")
        self.assertEqual(
            self.window.btn_briefing.accessibleName(), "Play briefing")

    # -- the Briefing fix (2026-11-26) -------------------
    def test_briefing_summary_not_connected_shows_full_line(self):
        """Finding 1: with the calendar not connected, the
        collapsed summary shows the complete line — "Connect
        your calendar to see your day" — wrapped, never elided
        to "Calendar not…"."""
        from PySide6.QtTest import QTest
        import voice_assistant.core.google_services as _gs
        _orig = _gs.is_connected
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        _gs.is_connected = lambda: False
        try:
            self.window.resize(1280, 800)
            self.window.show()
            QTest.qWait(300)
            self.app.processEvents()
            self.window._refresh_briefing_card()
            QTest.qWait(300)
            self.app.processEvents()
            summary = self.window.briefing_summary
            self.assertEqual(
                summary.text(), "Connect your calendar to see your day")
            self.assertNotIn("…", summary.text())
            self.assertTrue(summary.wordWrap())
            self.assertGreater(summary.height(), 17)
            self.assertEqual(
                self.window.briefing_schedule.text(),
                "Connect your calendar to see your day")
        finally:
            _gs.is_connected = _orig

    def test_briefing_summary_empty_schedule_shows_full_copy(self):
        """Finding 1: connected with nothing scheduled, the
        summary shows the empty-schedule copy in full — never
        elided."""
        from PySide6.QtTest import QTest
        import time as _time
        import voice_assistant.core.google_services as _gs
        _orig_conn = _gs.is_connected
        _orig_next = _gs.next_events
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        _gs.is_connected = lambda: True
        _gs.next_events = lambda within_hours=24, limit=20: []
        try:
            self.window.resize(1280, 800)
            self.window.show()
            QTest.qWait(300)
            self.app.processEvents()
            self.window._refresh_briefing_card()
            # The calendar worker answers off-thread: pump until
            # the schedule line lands or a wall-clock timeout
            # expires (time-anchored, never count-anchored).
            _deadline = _time.monotonic() + 10.0
            while (_time.monotonic() < _deadline
                   and "Nothing scheduled today" not in
                   self.window.briefing_schedule.text()):
                self.app.processEvents()
                _time.sleep(0.05)
            self.app.processEvents()
            summary = self.window.briefing_summary
            self.assertEqual(
                summary.text(),
                "Nothing scheduled today — ask me to add something.")
            self.assertNotIn("…", summary.text())
            self.assertTrue(summary.wordWrap())
        finally:
            _gs.is_connected = _orig_conn
            _gs.next_events = _orig_next

    def test_briefing_summary_multi_clause_still_elides(self):
        """Finding 1, the guard: a real multi-clause schedule
        still elides to its first two clauses + "…" — the
        no-elide rule is for failure/empty lines only."""
        from PySide6.QtTest import QTest
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        self.window.resize(1280, 800)
        self.window.show()
        QTest.qWait(300)
        self.app.processEvents()
        self.window._set_briefing_schedule_text(
            "2 things coming up · Standup 09:00 · Q3 review 14:00")
        QTest.qWait(150)
        self.app.processEvents()
        summary = self.window.briefing_summary
        self.assertFalse(summary.wordWrap())
        self.assertTrue(summary.text().endswith("…"))
        self.assertIn("2 things", summary.text())

    def test_briefing_news_rows_one_headline_plus_source(self):
        """Finding 2: the top two headlines each get their own row
        — one headline plus a small source tag — no " · "-joined
        paragraph wall. Two rows, not three: the column budget at
        1280 cannot hold three full rows under the card (the
        layout clipped the third row), and the old block showed
        two headlines too — the fake returns three to prove the
        cap holds."""
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLabel as _QLabel
        import voice_assistant.core.home_feeds as _hf
        _orig = _hf.fetch_news_headlines
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        _hf.fetch_news_headlines = lambda limit=3: [
            {"title": "IIT Bombay tops the national research rankings",
             "source": "The Hindu"},
            {"title": "Monsoon session of Parliament begins today",
             "source": "Indian Express"},
            {"title": "India launches its heaviest communication satellite",
             "source": "BBC News"},
        ][:limit]
        try:
            self.window.resize(1280, 800)
            self.window.show()
            QTest.qWait(300)
            self.app.processEvents()
            self.window._home_news_done = False
            self.window._refresh_home_news()
            QTest.qWait(300)
            self.app.processEvents()
            block = self.window._home_news_block
            rows = [block.layout().itemAt(i).widget()
                    for i in range(block.layout().count())]
            self.assertEqual(len(rows), 2)
            _want = [
                ("IIT Bombay tops the national research rankings",
                 "The Hindu"),
                ("Monsoon session of Parliament begins today",
                 "Indian Express"),
            ]
            for _row, (_title, _source) in zip(rows, _want):
                _labels = _row.findChildren(_QLabel)
                self.assertEqual(len(_labels), 2)
                _heads = [l for l in _labels
                          if l.objectName() != "smallMuted"]
                _tags = [l for l in _labels
                         if l.objectName() == "smallMuted"]
                self.assertEqual(len(_heads), 1)
                self.assertEqual(len(_tags), 1)
                self.assertEqual(_heads[0].text(), _title)
                self.assertEqual(_tags[0].text(), _source)
                # The tag takes the app's own small-muted rule
                # (11 px, the ladder rung below body 13); the
                # headline is body size. font() reflects the
                # stylesheet-resolved size (a QFont pixel size
                # does not — the stylesheet overrides setFont()).
                self.assertEqual(_tags[0].font().pixelSize(), 11)
                self.assertEqual(_heads[0].font().pixelSize(), 13)
                # The column budget must not squeeze the row
                # below the height its wrapped headline needs
                # (the offline render clipped the first line at
                # 38 px against 51 px of text): the row's
                # Minimum vertical policy floors it at its
                # sizeHint, so the headline keeps its full
                # heightForWidth (within rounding).
                self.assertGreaterEqual(
                    _heads[0].height(),
                    _heads[0].heightForWidth(_heads[0].width()) - 2)
            _all = " ".join(
                lbl.text() for _row in rows
                for lbl in _row.findChildren(_QLabel))
            self.assertNotIn(" · ", _all)
        finally:
            _hf.fetch_news_headlines = _orig

    def test_home_docs_card_lists_seeded_documents(self):
        """Step 2 F1: _refresh_home_docs sliced list_documents()
        like a list, but it returns a dict — the slice raised
        TypeError, the except swallowed it, and the card always
        read "Documents unavailable.". Seeded documents list by
        title now (the file basename is the stored title)."""
        import pathlib
        import tempfile
        import voice_assistant.core.document_memory as _dm
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="s2_docs_"))
        (tmp / "Field notes.txt").write_text(
            "Alpha content for the docs card.\n", encoding="utf-8")
        (tmp / "Shopping list.txt").write_text(
            "Beta content for the docs card.\n", encoding="utf-8")
        for path in sorted(tmp.iterdir()):
            res = _dm.sync_file(str(path))
            self.assertTrue(res["ok"], f"seed failed: {res}")
        self.window._refresh_home_docs()
        text = self.window._home_docs_body.text()
        self.assertIn("Field notes.txt", text)
        self.assertIn("Shopping list.txt", text)
        self.assertNotIn("unavailable", text)

    def test_home_docs_card_empty_state(self):
        """Step 2 F1: with no documents the card reads "No
        documents in memory yet." — never "Documents
        unavailable."."""
        self.window._refresh_home_docs()
        self.assertEqual(
            self.window._home_docs_body.text(),
            "No documents in memory yet.")

    def test_orb_is_first_in_the_room(self):
        # UI upgrade Phase 1: the hero height is tier-driven
        # (§5), so size the window to 1280 first — at the
        # default 760 the hero is only ~128 px tall.
        self.window.resize(1280, 800)
        self.window.show()
        self.app.processEvents()
        hero = self.window.hero
        card = self.window.briefing_card
        self.assertGreaterEqual(hero.height(), 300)
        # UI upgrade Phase 1: the hero (centre column) and the
        # briefing card (right column) are side by side, not
        # stacked — their .top() values live in different parents
        # and are not comparable. The intent survives within the
        # centre column: the hero is the tallest child there, so
        # nothing else in the room competes with the orb for the
        # eye. (The stage's own children are the column hosts,
        # as tall as the stage itself — they are not the
        # comparison.)
        centre = hero.parentWidget()
        self.assertIsNotNone(centre)
        # Exclude the column hairlines (homeColumnRule-*) — they
        # are layout chrome as tall as the column, not content
        # competing with the orb.
        children = [c for c in centre.children()
                    if hasattr(c, "height") and c.isVisible()
                    and c is not hero
                    and not (c.objectName() or "").startswith(
                        "homeColumnRule")]
        heights = [c.height() for c in children if c.height() > 0]
        self.assertTrue(heights)
        self.assertGreater(hero.height(), max(heights))

    def test_idle_breathing(self):
        # The orb breathes at idle: two grabs 300 ms apart differ.
        self._day()
        self.app.processEvents()
        first = self.window.visualizer.grab().toImage()
        time.sleep(0.35)
        self.app.processEvents()
        second = self.window.visualizer.grab().toImage()
        self.assertEqual(first.size(), second.size())
        self.assertNotEqual(
            first.bits().tobytes(), second.bits().tobytes(),
            "the orb is frozen at idle — no breathing")

    def test_night_dims_the_wash_and_the_field(self):
        fake = self._audio_fake()
        fake.is_muted = False
        fake.is_playing = False
        self._day()
        # Night idle: 4% x 0.7.
        self.window._room_hour = 23
        self.window._status_raw = ""
        self.window._connected = True
        self._settle_light(target=0.04 * 0.7)
        self.assertAlmostEqual(self.window._room_light.current,
                               0.04 * 0.7, places=3)
        self.assertTrue(self.window._room_night)
        field = self.window.particle_field
        self.assertIsNotNone(field)
        self.assertTrue(field.is_night())
        # Day offline: 2% (and the night flag clears too).
        self.window._room_hour = 12
        self.window._connected = False
        self._settle_light(target=0.02)
        self.assertAlmostEqual(self.window._room_light.current,
                               0.02, places=3)
        self.assertFalse(self.window._room_night)
        self.assertFalse(field.is_night())

    def test_light_follows_the_real_state(self):
        fake = self._audio_fake()
        fake.is_muted = False
        fake.is_playing = False
        self._day()
        # Listening: 8%.
        self.window._status_raw = "LISTENING"
        self.window._connected = True
        self._settle_light(target=0.08)
        self.assertAlmostEqual(self.window._room_light.current,
                               0.08, places=3)
        # Speaking at 0.8 energy: 4% + 10% x 0.8 = 12%.
        fake.is_playing = True
        fake.speaker_level = 0.8
        self.window._status_raw = "SPEAKING"
        self._settle_light(target=0.12)
        self.assertAlmostEqual(self.window._room_light.current,
                               0.12, places=3)
        # Muted outranks speaking: 2%.
        fake.is_muted = True
        self._settle_light(target=0.02)
        self.assertAlmostEqual(self.window._room_light.current,
                               0.02, places=3)
        fake.is_muted = False
        # Offline: 2%.
        fake.is_playing = False
        self.window._status_raw = ""
        self.window._connected = False
        self._settle_light(target=0.02)
        self.assertAlmostEqual(self.window._room_light.current,
                               0.02, places=3)
        # The shell glow tracks the wash. Force one glow update
        # on the settled wash (the ~10 Hz cadence fires when the
        # tick crosses a multiple of 4).
        self.window._tick = 3
        self.window._sync_audio_telemetry()
        self.app.processEvents()
        strength = self.window.shell._glow_strength
        self.assertAlmostEqual(
            strength, shell_strength(0.02), places=3)

    def test_demotions_hold_on_the_live_window(self):
        from PySide6.QtWidgets import QFrame, QLabel
        # UI upgrade Phase 1: size to 1280 first — the right
        # column (briefing summary) is hidden below 770 px.
        self.window.resize(1280, 800)
        self.window.show()
        self.app.processEvents()
        # The voice chip still exists and is still wired, but it
        # is hidden and out of the stage layout.
        chip = self.window.voice_chip
        self.assertFalse(chip.isVisible())
        layout = self.window._stage_widget.layout()
        in_layout = [layout.itemAt(i).widget()
                     for i in range(layout.count())]
        self.assertNotIn(chip, in_layout)
        # Pace box and mic meter are hidden at idle.
        self.assertFalse(self.window.pace_box.isVisible())
        self.assertFalse(self.window.mic_meter.isVisible())
        # The briefing card is collapsed: summary shown, the five
        # rows behind the tap.
        self.assertTrue(self.window.briefing_summary.isVisible())
        self.assertFalse(self.window._briefing_details.isVisible())
        self.assertFalse(self.window._briefing_expanded)
        # The home strip is gone; Play briefing lives in the card.
        self.assertIsNone(getattr(self.window, "home_next", None))
        self.assertIsNone(getattr(self.window, "home_date", None))
        self.assertIsNone(
            self.window.findChild(QFrame, "homeCard"))
        self.assertIsNotNone(self.window.btn_briefing)
        # UI upgrade Phase 1 §2: the sidebar privacy card
        # is ONE muted line now ("Private by design"); the full
        # promise lives on hover (tooltip), not in a body label.
        card = self.window.findChild(QFrame, "privacyCard")
        self.assertIsNotNone(card)
        texts = {lbl.text() for lbl in card.findChildren(QLabel)}
        self.assertIn("Private by design", texts)
        # Loophole batch 8 (L-31): the full sentence from
        # core/privacy_copy.py (PRIVACY_PROMISE) is the title's
        # tooltip in Phase 1.
        import core.privacy_copy as _pc  # noqa: E402
        title = self.window.findChild(QLabel, "privacyTitle")
        self.assertIsNotNone(title)
        self.assertEqual(title.toolTip(), _pc.PRIVACY_PROMISE)
        self.assertIsNone(getattr(self.window, "privacy_lock", None))

    def test_briefing_card_expands_and_collapses(self):
        # UI upgrade Phase 1: size to 1280 first — the briefing
        # card lives in the right column, hidden below 770 px.
        self.window.resize(1280, 800)
        self.window.show()
        self.app.processEvents()
        self.window._set_briefing_schedule_text(
            "3 things coming up · Standup 09:00 · Reminders: Call bank")
        self.assertEqual(self.window._briefing_summary_full,
                         "3 things coming up · Standup 09:00 …")
        self.assertTrue(self.window.briefing_summary.text())
        self.window.btn_briefing_expand.click()
        self.app.processEvents()
        self.assertTrue(self.window._briefing_expanded)
        self.assertTrue(self.window._briefing_details.isVisible())
        self.assertEqual(self.window.btn_briefing_expand.text(), "▴")
        self.window.btn_briefing_expand.click()
        self.app.processEvents()
        self.assertFalse(self.window._briefing_expanded)
        self.assertFalse(self.window._briefing_details.isVisible())

    def test_transcript_did_not_regress(self):
        self.assertIsNotNone(self.window.chat_panel)
        # UI upgrade Phase 1, leg 57: the composer placeholder
        # is state-aware — at idle it reads "Ask anything"
        # (the old "Type, or hold mic" copy is gone).
        self.assertEqual(self.window.input_cmd.placeholderText(),
                         "Ask anything")

    def test_render_grabs(self):
        """Save the grabs the build report judges from."""
        from PySide6.QtGui import QImage
        fake = self._audio_fake()
        fake.is_muted = False
        fake.is_playing = False
        RENDER_DIR.mkdir(parents=True, exist_ok=True)
        self._day()
        # The boot is a ritual, never a gate — skip it so the grab
        # shows the room, not the boot overlay; then size the
        # window to the real 1280x800 room before grabbing.
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the grab works either way
            pass
        self.window.resize(1280, 800)
        self.window.show()
        self.app.processEvents()
        # One short exchange, so the idle grab shows the greeting
        # against a real reply (room test 2) instead of an empty
        # chat.
        self.window._add_bubble("You", "What's on today?", True)
        self.window._add_bubble(
            "VYUK", "Three things — standup at 09:00 is first.", False)
        self.app.processEvents()
        self.window._status_raw = ""
        self.window._connected = False
        self._settle_light()
        idle = self.window.grab()
        idle.save(str(RENDER_DIR / "home_idle_day.png"))
        grey = idle.toImage().convertToFormat(
            QImage.Format.Format_Grayscale8)
        grey.save(str(RENDER_DIR / "home_idle_grey.png"))
        fake.is_playing = True
        fake.speaker_level = 0.8
        # The real presentation path, so the state label is honest
        # too (it reads "VYUK is speaking", not the idle line).
        self.window._on_status("SPEAKING")
        self._settle_light()
        speaking = self.window.grab()
        speaking.save(str(RENDER_DIR / "home_speaking.png"))
        self.window._room_hour = 23
        fake.is_playing = False
        self.window._on_status("DISCONNECTED")
        self._settle_light()
        night = self.window.grab()
        night.save(str(RENDER_DIR / "home_night.png"))
        for name in ("home_idle_day.png", "home_idle_grey.png",
                     "home_speaking.png", "home_night.png"):
            path = RENDER_DIR / name
            self.assertTrue(path.exists(), f"missing grab {name}")
            self.assertGreater(path.stat().st_size, 0, name)


if __name__ == "__main__":
    unittest.main()
