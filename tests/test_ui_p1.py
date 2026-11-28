"""UI Phase 1 — suite (rebuild, 2026-11-24).

15 tests pinning the Phase 1 seams: the core stores (brain source,
wake-word state, suggestions private-mode, scheduler epoch,
user-pinned facts, SegmentBar, HudPanel Light wash, home feeds,
orb/mission mapping, home-city store, degradation notice) and the
three pages (Home no-scroll + additions + greeting + mute pill,
Memory six tabs + fact cards, Activity rail + approval card,
weather city line + composer), plus one look-fix round 2 pin
(Light input well, SegmentBar, HudSwitch knob, backdrop tile).
The 71-leg repro driver
(ui_p1_work/repro_ui_p1.py) is the exhaustive gate; this suite is
the fast pin set that runs in the neighbour tally.

Qt only for the GUI class: without PySide6 every GUI test skips
(the test_chat_ui / test_rethink_build6 shape).
"""
import dataclasses
import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The `voice_assistant` package is synthesised, not on disk (the
# repro's shape): create the alias packages here so the pure core
# tests can import the real modules. WindowBase.setUp() re-creates
# these packages and stubs four modules; the GUI tests below work
# around the stubs (facade patching) where they need real data.
import types as _types

for _name, _path in (
        ("voice_assistant", str(ROOT)),
        ("voice_assistant.core", str(ROOT / "core")),
        ("voice_assistant.gui", str(ROOT / "gui"))):
    if _name not in sys.modules:
        _pkg = _types.ModuleType(_name)
        _pkg.__path__ = [_path]
        sys.modules[_name] = _pkg

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]


def _show(window, index):
    window._show_page(index)
    # MainWindow carries no `app` attribute — pump the application
    # instance directly (the repro driver uses the test's own app).
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance()
    if _app is not None:
        _app.processEvents()


class TestUiP1Core(unittest.TestCase):
    """Pure/core Phase 1 seams — no window needed."""

    def test_brain_source_and_wake_word_state(self):
        from voice_assistant.core import brain_state
        from voice_assistant.core.config import config
        self.assertIn(brain_state.brain_source(), ("cloud", "local"))
        old = config.brain_engine
        try:
            config.brain_engine = "local"
            self.assertEqual(brain_state.brain_source(), "local")
            config.brain_engine = "gemini"
            self.assertEqual(brain_state.brain_source(), "cloud")
        finally:
            config.brain_engine = old
        self.assertIn(brain_state.wake_word_state(),
                      ("on", "off", "unavailable"))
        self.assertEqual(brain_state.wake_word_state(), "off")

    def test_suggestions_private_mode(self):
        from voice_assistant.core.brain import suggestions
        from voice_assistant.core import private_mode
        from voice_assistant.core.config import config
        registry = suggestions.default_registry()
        old = config.private_mode
        try:
            config.private_mode = True
            self.assertTrue(private_mode.private_mode_on())
            rec = registry.raise_suggestion(
                "repro", "repro", "This must never be raised.",
                {"tool": "search_documents", "args": {}},
                "repro-private")
            self.assertIsNone(rec, "suggestion raised in private mode")
            config.private_mode = False
            rec = registry.raise_suggestion(
                "repro", "repro", "Raised once private mode is off.",
                {"tool": "search_documents", "args": {}},
                "repro-private-off")
            self.assertIsNotNone(rec, "suggestion not raised when off")
            registry.dismiss(rec["id"])
        finally:
            config.private_mode = old

    def test_scheduler_epoch(self):
        from voice_assistant.core.scheduler import ProactiveScheduler
        sched = ProactiveScheduler()
        rows = sched.get_all_reminders_with_epoch()
        self.assertIsInstance(rows, list)
        for row in rows:
            self.assertIn("fire_at_epoch", row)
            self.assertIsInstance(row["fire_at_epoch"], (int, float))

    def test_memory_user_pinned(self):
        from voice_assistant.core.memory.memory import MemoryStore
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(db_path=pathlib.Path(tmp) / "m.db")
            store.save_fact("general", "repro_a", "alpha")
            store.save_fact("general", "repro_b", "beta")
            rows = store.get_all_facts()
            ids = {r["key"]: r["id"] for r in rows}
            self.assertIn("user_pinned", rows[0])
            store.set_user_pinned(ids["repro_b"], True)
            ordered = store.get_all_facts()
            self.assertEqual(ordered[0]["key"], "repro_b")
            self.assertEqual(store.count_facts(), 2)
            store.set_user_pinned(ids["repro_b"], False)
            self.assertEqual(store.get_all_facts()[0]["key"], "repro_a")

    @unittest.skipUnless(_QT, "PySide6 unavailable")
    def test_segmentbar_steps(self):
        from PySide6.QtWidgets import QApplication
        from voice_assistant.gui.design_system.progress import SegmentBar
        QApplication.instance() or QApplication([])
        bar = SegmentBar()
        bar.set_label_mode("steps")
        bar.set_progress(2, 5)
        self.assertEqual(bar.label_text, "Step 2 of 5")
        self.assertNotIn("%", bar.label_text)
        bar.set_label_mode("compact")
        bar.set_progress(2, 5)
        self.assertEqual(bar.label_text, "2/5")
        bar.set_label_mode("percent")
        bar.set_progress(2, 5)
        self.assertEqual(bar.label_text, "40%")

    @unittest.skipUnless(_QT, "PySide6 unavailable")
    def test_lookfix2_light_theme_values(self):
        # UI Phase 1 look-fix round 2 (2026-11-24): the Light
        # theme values the appearance pass drives — the input
        # well, the SegmentBar, the HudSwitch knob, and the
        # backdrop grid tile. Each pin is the exact regression
        # the hunt found: a dark house value leaking onto Light.
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        from voice_assistant.gui.design_system.backdrop import (
            glass_tile)
        from voice_assistant.gui.design_system.progress import (
            SegmentBar)
        from voice_assistant.gui.design_system.switches import (
            HudSwitch)
        from voice_assistant.gui.style import THEMES as STYLE_THEMES
        from voice_assistant.gui.style_theme import _resolve_theme
        QApplication.instance() or QApplication([])
        light = _resolve_theme(STYLE_THEMES["Light"])
        cyan = _resolve_theme(STYLE_THEMES["Cyan"])
        # 1. The input well is the Light surface, not the house
        # black slab (finding 2).
        self.assertEqual(light["input_bg"], light["surface_2"])
        self.assertNotEqual(light["input_bg"], cyan["input_bg"])
        self.assertEqual(cyan["input_bg"], "rgba(0, 0, 0, 96)")
        # 2. The SegmentBar label + resting track follow the
        # resolved theme (finding 1 companion); Cyan is the
        # house values exactly.
        bar = SegmentBar()
        bar.set_theme(light)
        self.assertIn(light["text"], bar._label.styleSheet())
        self.assertEqual(bar._bar._rest, light["surface_2"])
        bar.set_theme(cyan)
        self.assertIn(cyan["text"], bar._label.styleSheet())
        self.assertEqual(bar._bar._rest, "#122636")
        # 3. The checked HudSwitch knob is white on Light (it
        # sits on the accent fill) and the theme text on Cyan
        # (finding 11).
        switch = HudSwitch()
        switch.set_theme(light)
        self.assertEqual(switch._knob_on, "#FFFFFF")
        switch.set_theme(cyan)
        self.assertEqual(switch._knob_on, cyan["text"])
        # 4. The backdrop grid tile parses the CSS rgba line
        # token — QColor() could not, so the lines painted
        # opaque black (the Light Activity checkerboard,
        # finding 1). Painted over white, a line pixel is
        # light; pre-fix it was (0, 0, 0).
        tile = glass_tile(light)
        canvas = QPixmap(96, 96)
        canvas.fill(Qt.white)
        from PySide6.QtGui import QPainter
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, tile)
        painter.end()
        pixel = canvas.toImage().pixelColor(2, 24)
        self.assertGreater(pixel.red(), 180)
        self.assertGreater(pixel.green(), 180)
        self.assertGreater(pixel.blue(), 180)

    def test_home_feeds_parsers(self):
        from voice_assistant.core import home_feeds
        self.assertEqual(home_feeds.condition_word("clearsky_day"),
                         "Clear")
        self.assertEqual(home_feeds.condition_word("rain"), "Rain")
        self.assertEqual(home_feeds.condition_word("heavyrain"), "Rain")
        self.assertEqual(home_feeds.condition_word("snow"), "Snow")
        self.assertEqual(home_feeds.condition_word("fog"), "Fog")
        self.assertEqual(home_feeds.condition_word("thunderstorm"),
                         "Thunderstorm")
        self.assertEqual(home_feeds.condition_word("cloudy"), "Cloudy")
        self.assertEqual(home_feeds.condition_word("partlycloudy_day"),
                         "Partly cloudy")
        self.assertEqual(home_feeds.condition_word(""), "Unknown")
        fixture = {"properties": {"timeseries": [{
            "data": {
                "instant": {"details": {
                    "air_temperature": 29.4, "wind_speed": 3.1,
                    "cloud_area_fraction": 12.0}},
                "next_1_hours": {"summary": {"symbol_code":
                                             "partlycloudy_day"}}}}]}}
        parsed = home_feeds.parse_forecast(fixture)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["temp_c"], 29.4)
        self.assertEqual(parsed["condition"], "Partly cloudy")
        self.assertEqual(parsed["wind_ms"], 3.1)
        rss = ('<rss><channel>'
               '<item><title>First headline</title>'
               '<source>Example News</source></item>'
               '<item><title>Second headline</title>'
               '<source>Other News</source></item>'
               '</channel></rss>')
        heads = home_feeds.parse_news_rss(rss, limit=5)
        self.assertEqual(len(heads), 2)
        self.assertEqual(heads[0]["title"], "First headline")
        self.assertEqual(heads[0]["source"], "Example News")

    def test_orb_state_and_mission_state(self):
        from voice_assistant.gui.room_home import orb_state_for
        from voice_assistant.gui.task_state import mission_state_for_record
        self.assertEqual(orb_state_for("RESPONDING"), "responding")
        self.assertEqual(orb_state_for("LISTENING"), "listening")
        self.assertEqual(orb_state_for("PROCESSING"), "understanding")
        self.assertEqual(
            orb_state_for("PROCESSING", plan_active=True), "planning")
        self.assertEqual(
            orb_state_for("PROCESSING", tool_executing=True), "executing")
        self.assertEqual(
            orb_state_for("PROCESSING", pending=True), "waiting")
        self.assertEqual(orb_state_for("IDLE", pending=True), "waiting")
        self.assertEqual(
            orb_state_for("RESPONDING", pending=True), "responding")
        self.assertEqual(orb_state_for("FAULT"), "error")
        self.assertEqual(orb_state_for("LINK OFFLINE"), "offline")
        self.assertEqual(orb_state_for("IDLE"), "idle")
        self.assertEqual(orb_state_for("SETUP REQUIRED"), "idle")
        running = {"task_id": "t1", "title": "Repro mission",
                   "state": "running",
                   "steps": [{"label": "a"}, {"label": "b"},
                             {"label": "c"}],
                   "outcomes": [{"status": "success"},
                                {"status": "success"}]}
        mapped = mission_state_for_record(running, live_count=1)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["goal"], "Repro mission")
        self.assertEqual(mapped["step"], 2)
        self.assertEqual(mapped["steps_total"], 3)
        paused = dict(running, state="paused",
                      pending_approval={"prompt": "Allow this?"})
        mapped = mission_state_for_record(paused, live_count=1)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["phase"], "paused")
        self.assertTrue(mapped["permission"])
        done = dict(running, state="completed")
        self.assertIsNone(mission_state_for_record(done, live_count=0))

    def test_home_city_store(self):
        from voice_assistant.core.config_spec import (
            ConfigSpec, QSETTINGS_FIELDS)
        fields = {f.name for f in dataclasses.fields(ConfigSpec)}
        self.assertIn("home_city", fields)
        self.assertIn("hud_profile", fields)
        keys = {entry[1] for entry in QSETTINGS_FIELDS}
        self.assertIn("home/city", keys)
        self.assertIn("appearance/hud_profile", keys)

    def test_degradation_notice(self):
        from voice_assistant.core import degradation
        old_w = degradation.whisper_ready
        old_k = degradation.kokoro_voice_ready
        try:
            degradation.whisper_ready = lambda: True
            degradation.kokoro_voice_ready = lambda: True
            self.assertIsNone(degradation.degradation_notice())
            degradation.whisper_ready = lambda: False
            notice = degradation.degradation_notice()
            self.assertIsNotNone(notice)
            self.assertIn("ears", notice["text"].lower())
            self.assertTrue(notice["fix_route"])
        finally:
            degradation.whisper_ready = old_w
            degradation.kokoro_voice_ready = old_k


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestUiP1Gui(WindowBase):
    """Window Phase 1 seams — the real MainWindow, offscreen."""

    def test_home_no_scroll_1280_and_additions(self):
        from PySide6.QtWidgets import QScrollArea, QWidget
        _show(self.window, 0)
        self.window.show()
        self.window.resize(1280, 800)
        self.app.processEvents()
        self.assertIsNone(self.window._stage_scroll,
                          "stage scroll still exists")
        stage = self.window._home_stage
        scrolls = [s for s in stage.findChildren(QScrollArea)]
        self.assertEqual(scrolls, [], "a scroll area lives in the stage")
        page = self.window.pages.widget(0)
        self.assertLessEqual(
            page.minimumSizeHint().height(),
            self.window.pages.height() + 2,
            "Home page minimum height exceeds the viewport at 1280")
        self.assertLessEqual(
            stage.minimumSizeHint().height(), stage.height() + 2,
            "stage content exceeds its height at 1280")
        for name in (
                "homeStatusBlock", "homeServices", "homeAlertsFeed",
                "homeAttentionRail", "homeCapabilityTiles",
                "homeDocsGlance", "homeCommsGlance", "homeSuggestions",
                "homeMutePill", "homePermissionsPill",
                "homeQuietPill", "homePrivatePill", "homeTelemetry",
                "homeDegradationBanner", "homeNowDoing",
                "homeScreenMini", "homeGreeting"):
            found = page.findChild(QWidget, name)
            self.assertIsNotNone(found, f"Home addition missing: {name}")

    def test_home_greeting_and_mute_pill(self):
        from PySide6.QtWidgets import QLabel, QPushButton
        _show(self.window, 0)
        self.window.resize(1280, 800)
        self.app.processEvents()
        greeting = self.window.findChild(QLabel, "homeGreeting")
        text = greeting.text() if greeting is not None else ""
        self.assertIn("Nothing scheduled today", text,
                      f"greeting headline wrong: {text!r}")
        pill = self.window.findChild(QPushButton, "homeMutePill")
        self.assertIsNotNone(pill, "no homeMutePill")
        from voice_assistant.core.voice import audio as _audio
        self.window.btn_mute.setChecked(False)
        self.window._toggle_mute()
        self.app.processEvents()
        self.assertFalse(bool(_audio.audio_manager.is_muted))
        self.assertEqual(pill.text(), "Mute")
        pill.click()
        self.app.processEvents()
        self.assertTrue(bool(_audio.audio_manager.is_muted))
        self.assertEqual(pill.text(), "Muted")
        pill.click()
        self.app.processEvents()
        self.assertFalse(bool(_audio.audio_manager.is_muted))
        self.assertEqual(pill.text(), "Mute")

    def test_memory_six_tabs_and_fact_cards(self):
        _show(self.window, 2)
        self.app.processEvents()
        tabs = self.window._memory_tabs
        names = [tabs.tabText(i) for i in range(tabs.count())]
        # "&&" is Qt's escape for a literal "&" in tab text — a
        # single "&" renders as an underscore ("Projects_Context").
        # tabText() returns the stored "&&"; the DISPLAY reads
        # "Projects & Context" (the plan's name).
        self.assertEqual(
            names, ["Overview", "Profile", "Projects && Context",
                    "Episodes", "Knowledge Graph", "Review Inbox"])
        # A fact card exists for a hand-saved fact, with Pin.
        # BARE import + facade patch (the repro's seam): the
        # harness stubs voice_assistant.core.memory.memory, so
        # the facade's memory_store is a _Fake that stores
        # nothing. The bare MemoryStore reads the real DB file.
        from core.memory.memory import MemoryStore
        _store = MemoryStore()
        _store.save_fact("general", "repro_fact", "repro value")
        from voice_assistant.core import gui_facade as _facade
        _old = _facade.memory_store
        _facade.memory_store = _store
        try:
            self.window._refresh_memories()
            self.app.processEvents()
        finally:
            _facade.memory_store = _old
        from voice_assistant.gui.window_pages_memory import (
            MemoryFactCard)
        cards = self.window.findChildren(MemoryFactCard)
        self.assertTrue(any(
            getattr(c, "fact_key", None) == "repro_fact" for c in cards),
            "no fact card for the saved fact")

    def test_activity_rail_and_approval(self):
        from PySide6.QtWidgets import QWidget
        _show(self.window, 1)
        self.window._refresh_activity_tabs()
        self.app.processEvents()
        rail = getattr(self.window, "_mission_rail", None)
        self.assertIsNotNone(rail, "no MissionRail")
        empty = rail.findChild(QWidget, "missionRailEmpty")
        self.assertIsNotNone(empty, "no designed empty state on the rail")
        self.assertTrue(empty.isVisible(),
                        "empty state hidden on an idle rail")
        self.assertIn("Ask VYUK to start something.", empty.text())

    def test_home_weather_city_and_composer(self):
        from PySide6.QtWidgets import QLineEdit, QPushButton
        _show(self.window, 0)
        self.app.processEvents()
        row = self.window.findChild(QPushButton, "homeWeatherRow")
        if row is None:
            self.window._toggle_briefing_card()
            self.app.processEvents()
            row = self.window.findChild(QPushButton, "homeWeatherRow")
        self.assertIsNotNone(row, "no homeWeatherRow in the briefing")
        self.assertIn("set your city", row.text().lower(),
                      f"weather row wrong with no city: {row.text()!r}")
        row.click()
        self.app.processEvents()
        prompt = self.window.findChild(QLineEdit, "homeCityPrompt")
        self.assertIsNotNone(prompt, "city prompt did not open")
        self.assertTrue(prompt.isVisible(), "city prompt not visible")
        # Composer: the send button follows the text (leg 52).
        edit = self.window.input_cmd
        send = self.window.btn_send
        edit.setPlainText("")
        self.app.processEvents()
        self.assertFalse(send.isEnabled())
        edit.setPlainText("hello")
        self.app.processEvents()
        self.assertTrue(send.isEnabled())


if __name__ == "__main__":
    unittest.main()
