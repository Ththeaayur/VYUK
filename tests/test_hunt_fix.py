"""Hunt fix (c) — regression tests for the 15 ranked fixes.

One test per fix where testable; each is written to fail on the
unfixed tree for the defect itself (the N2 fail-before is proved
against a reverted tree copy in hunt_fix_work/).

Qt only for the GUI tests: without PySide6 they skip (the
test_chat_ui / test_ui_p1 shape).
"""
import datetime
import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    from PySide6.QtWidgets import QApplication
    from tests.test_chat_ui import WindowBase
    from tests.test_ui_p2 import P2WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]
    P2WindowBase = unittest.TestCase  # type: ignore[assignment]


def _pump(seconds=0.1):
    import time
    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _ensure_aliases():
    """Re-create the voice_assistant package aliases if a
    WindowBase tearDown popped them (test_chat_ui's
    _teardown_shim removes voice_assistant / .core / .gui from
    sys.modules after every WindowBase test, so a later test
    that imports from voice_assistant.* fails with
    ModuleNotFoundError unless the aliases are re-asserted).
    Idempotent: only fills in what is missing."""
    import types as _types
    for _name, _path in (
            ("voice_assistant", str(ROOT)),
            ("voice_assistant.core", str(ROOT / "core")),
            ("voice_assistant.gui", str(ROOT / "gui"))):
        if _name not in sys.modules:
            _pkg = _types.ModuleType(_name)
            _pkg.__path__ = [_path]
            sys.modules[_name] = _pkg


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixN2(WindowBase):
    """Item 1 — calendar month drift while not connected."""

    def test_month_stays_put_across_refreshes_and_tab_switches(self):
        # Real google_services, not connected (no credentials in
        # the test data) — the empty path is the drift path.
        window = self.window
        window._show_page(8)  # Calendar
        _pump(0.5)
        today = datetime.date.today()
        window._views_calendar_page_date = today.replace(day=1)
        window._views_calendar_selected_date = today
        window._refresh_calendar_page()
        _pump(0.3)
        self.assertEqual(window._views_calendar_page_date,
                         today.replace(day=1))
        for _ in range(3):
            window._refresh_calendar_page()
            _pump(0.2)
        self.assertEqual(window._views_calendar_page_date,
                         today.replace(day=1),
                         "refresh walked the month back (N2)")
        # Tab switches Month -> Week -> Agenda -> Month.
        tabs = window._views_calendar_tabs
        for index in (1, 2, 0, 1, 2, 0):
            tabs.setCurrentIndex(index)
            _pump(0.2)
            window._refresh_calendar_page()
            _pump(0.2)
        self.assertEqual(window._views_calendar_page_date,
                         today.replace(day=1),
                         "tab switches walked the month back (N2)")

    def test_month_cell_changed_ignores_negative_cells(self):
        window = self.window
        window._show_page(8)
        _pump(0.5)
        today = datetime.date.today()
        window._views_calendar_page_date = today.replace(day=1)
        window._on_views_month_cell_changed(-1, -1)
        self.assertEqual(window._views_calendar_page_date,
                         today.replace(day=1))
        window._on_views_month_cell_changed(-1, 3)
        self.assertEqual(window._views_calendar_page_date,
                         today.replace(day=1))


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixBriefingGeometry(WindowBase):
    """Item 2 (§7) — the expanded briefing details get room.

    Runs at 1280x1000, not 1280x800: WindowBase's 800-tall
    layout gives the right column 103 px less than a real boot
    at the same size, so the card is environment-squeezed there
    and the geometry assertions cannot discriminate the fix.
    At 1000 the column matches a real boot and the card gets
    its full room; the real 1280x800 proof is the renders."""

    def _expand(self, window):
        try:
            window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        window.resize(1280, 1000)
        window.show()
        _pump(0.5)
        window._show_page(0)
        _pump(0.5)
        if not getattr(window, "_briefing_expanded", False):
            window._toggle_briefing_card()
            _pump(0.5)

    def test_expanded_details_show_button_kicker_schedule_whole(self):
        """Pristine (not connected, no reminder): the connect
        button is at least its sizeHint tall, the kicker at least
        its height-for-width at the laid-out width (2 lines), and
        the schedule at least its height-for-width (3 lines)."""
        window = self.window
        window._set_briefing_schedule_text(
            "Connect your calendar to see your day")
        self._expand(window)
        btn = window.btn_connect_calendar
        kicker = window.briefing_kicker
        schedule = window.briefing_schedule
        details = window._briefing_details
        self.assertGreaterEqual(
            btn.height(), btn.sizeHint().height(),
            "connect button squeezed below its sizeHint (§7d)")
        self.assertGreaterEqual(
            kicker.height(), kicker.heightForWidth(kicker.width()),
            "kicker squeezed below its height-for-width (§7a)")
        self.assertGreaterEqual(
            schedule.height(),
            schedule.heightForWidth(schedule.width()),
            "schedule squeezed below its height-for-width (§7b)")
        self.assertGreaterEqual(
            details.height(), details.sizeHint().height(),
            "details squeezed below their sizeHint (§7)")

    def test_five_line_schedule_with_reminder_shows_all_lines(self):
        """Not connected WITH an unfired reminder: the schedule
        is 5 lines (heightForWidth(96) = 85) and the details hold
        it whole; the collapsed summary is the state line alone,
        shown in full (never a bare \"Connect…\"). The text is set
        directly (the geometry is the fix under test); the live
        reminder path is exercised by the renders."""
        window = self.window
        try:
            window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        window.resize(1280, 1000)
        window.show()
        _pump(0.5)
        # Expand FIRST: the schedule must be laid out at the 96 px
        # interior when the text lands, or it measures at its
        # preferred width (151 px, 3 lines) instead of 5 lines.
        self._expand(window)
        window._set_briefing_schedule_text(
            "Connect your calendar to see your day · Reminders: "
            "Daily standup")
        _pump(0.5)
        summary = window.briefing_summary
        self.assertEqual(summary.text(),
                         "Connect your calendar to see your day")
        self.assertTrue(summary.wordWrap())
        schedule = window.briefing_schedule
        details = window._briefing_details
        self.assertGreaterEqual(
            schedule.height(),
            schedule.heightForWidth(schedule.width()),
            "5-line schedule squeezed (§7b)")
        self.assertGreaterEqual(
            schedule.height(), 85,
            "5-line schedule must be 85 px at the 96 px interior")
        self.assertGreaterEqual(
            details.height(), details.sizeHint().height(),
            "details squeezed below their sizeHint (§7)")


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixPill(WindowBase):
    """Item 3 — the Home degradation notice reads whole."""

    def test_banner_wraps_whole_at_1280_and_640(self):
        """At 1280 the banner shows the whole notice (wrapped,
        never elided); at 640 it shows the shortened form
        (first sentence + fix route, also never elided). The old
        one-line elision ended in "…" at both sizes."""
        window = self.window
        try:
            window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        window._show_page(0)
        _pump(0.5)
        banner = window.home_degradation_banner
        self.assertTrue(banner.isVisible(),
                        "no degradation notice in this environment")
        self.assertTrue(banner.wordWrap())
        window.resize(1280, 800)
        window.show()
        _pump(0.5)
        window._refresh_home_banner()
        _pump(0.3)
        self.assertNotIn("…", banner.text(),
                         "banner elided at 1280 (item 3)")
        self.assertIn("Settings → Voice", banner.text())
        self.assertIn("She can still hear you through the cloud",
                      banner.text())
        window.resize(640, 800)
        window.show()
        _pump(0.5)
        window._refresh_home_banner()
        _pump(0.3)
        self.assertNotIn("…", banner.text(),
                         "banner elided at 640 (item 3)")
        self.assertIn("Voice is degraded", banner.text())
        self.assertIn("Settings → Voice", banner.text())
        self.assertGreater(banner.height(), 30,
                           "banner is one line at 640 (item 3)")


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixSkillsRows(unittest.TestCase):
    """Item 4 — Skills rows elide at word boundaries, never
    mid-word (same for the plugin rows: they share the delegate)."""

    def _paint_row(self, friendly, summary, width):
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import (QListWidget, QListWidgetItem,
                                       QStyleOptionViewItem)
        from gui.skills_catalog_data import (
            ROLE_CHIPS, ROLE_FRIENDLY, ROLE_KIND, ROLE_SUMMARY)
        from gui.skills_catalog_widget import _CatalogueDelegate

        class _RecordingPainter(QPainter):
            def __init__(self, device):
                super().__init__(device)
                self.drawn = []

            def drawText(self, *args):
                text = args[-1] if args else ""
                if isinstance(text, str):
                    self.drawn.append(text)
                return super().drawText(*args)

        app = QApplication.instance() or QApplication([])
        lst = QListWidget()
        delegate = _CatalogueDelegate(lst)
        lst.setItemDelegate(delegate)
        item = QListWidgetItem()
        item.setData(ROLE_KIND, "tool")
        item.setData(ROLE_FRIENDLY, friendly)
        item.setData(ROLE_SUMMARY, summary)
        item.setData(ROLE_CHIPS, [])
        lst.addItem(item)
        lst.resize(width, 100)
        lst.show()
        app.processEvents()
        index = lst.model().index(0, 0)
        pix = QPixmap(width, 38)
        pix.fill(QColor(4, 9, 14))
        painter = _RecordingPainter(pix)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, width, 38)
        opt.font = lst.font()
        delegate.paint(painter, opt, index)
        painter.end()
        return painter.drawn

    def _assert_word_boundary(self, drawn, original):
        if drawn == original:
            return
        self.assertTrue(drawn.endswith("…"),
                        (drawn, original))
        visible = drawn[:-1]
        self.assertTrue(original.startswith(visible),
                        (drawn, original))
        self.assertTrue(
            len(visible) == len(original)
            or original[len(visible)] == " ",
            "mid-word cut: %r of %r" % (drawn, original))

    def test_summarise_caps_at_word_boundary(self):
        """The 95-char summary cap (shared by the rows, the
        detail drawer and the Galaxy card) must not cut mid-word
        — the old character cap left "creat…" / "te…" in the
        rows before the delegate ever saw them."""
        from gui.skills_catalog_data import _summarise
        text = ("Real file management: list a folder, find files "
                "by glob pattern, copy/move/rename files, create "
                "directories and archives.")
        out = _summarise(text)
        self.assertTrue(out.endswith("…"), out)
        visible = out[:-1]
        self.assertTrue(text.startswith(visible), (out, text))
        self.assertTrue(
            len(visible) == len(text) or text[len(visible)] == " ",
            "mid-word cap: %r of %r" % (out, text))

    def test_rows_elide_at_word_boundaries(self):
        friendly = "Desktop control panel settings manager"
        summary = ("Control the desktop with precision and care "
                   "for every window")
        for width in (200, 350, 500):
            drawn = self._paint_row(friendly, summary, width)
            texts = [t for t in drawn if t and t != " — "]
            self.assertTrue(texts, (width, drawn))
            for text in texts:
                if friendly.startswith(text.rstrip("…")):
                    self._assert_word_boundary(text, friendly)
                elif summary.startswith(text.rstrip("…")):
                    self._assert_word_boundary(text, summary)
                else:
                    self.fail("unrecognised drawn text %r (width %d)"
                              % (text, width))


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixConnectLines(WindowBase):
    """Item 5 — one connect line per page, in the header sync
    slot; the panes show their own real empty states."""

    def test_calendar_panes_show_empty_states_not_connect_line(self):
        """Not connected: the header sync slot carries the
        connect line (once); the Month "This day" list, the
        Agenda list and the Week all-day cell do not repeat it.
        The old fills put it in all three (and clipped it in
        the Week all-day cell)."""
        window = self.window
        try:
            window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        window._show_page(8)
        _pump(0.5)
        window._refresh_calendar_page()
        _pump(0.5)
        connect = "Connect Google in Settings"
        self.assertIn(connect, window._views_calendar_sync.text())
        month_items = [window._views_month_agenda.item(i).text()
                       for i in range(window._views_month_agenda.count())]
        self.assertNotIn(connect, " ".join(month_items))
        self.assertIn("No events this day.", month_items)
        agenda_items = [window._views_agenda_list.item(i).text()
                        for i in range(window._views_agenda_list.count())]
        self.assertNotIn(connect, " ".join(agenda_items))
        self.assertIn("No events in this window.", agenda_items)
        self.assertEqual(window._views_agenda_summary.text(),
                         "No events loaded.")
        cell = window._views_week_table.item(0, 1)
        self.assertTrue(cell is None or not cell.text(),
                        "Week all-day cell carries the connect line")

    def test_inbox_panes_show_empty_states_not_connect_line(self):
        """Not connected: the header sync slot carries the
        connect line (once); the list pane shows its EmptyState
        and the reading pane its default line — the old code
        repeated the line in both (3× total, the list item
        clipped)."""
        window = self.window
        try:
            window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the rule works either way
            pass
        window._show_page(9)
        _pump(0.5)
        window._refresh_inbox_page()
        _pump(0.5)
        connect = "Connect Google in Settings"
        self.assertIn(connect, window._views_inbox_sync.text())
        list_items = [window._views_inbox_list.item(i).text()
                      for i in range(window._views_inbox_list.count())]
        self.assertNotIn(connect, " ".join(list_items))
        self.assertFalse(window._views_inbox_empty.isHidden())
        self.assertIn("No mail to show.",
                      window._views_inbox_empty.sentence_text)
        self.assertNotIn(connect,
                         window._views_inbox_body.toPlainText())
        self.assertIn("Pick a message from the list to read it.",
                      window._views_inbox_body.toPlainText())


class TestHuntFixMemoryCard(unittest.TestCase):
    """Item 6 — Memory "Nothing learned yet" contradiction.

    The card used to read get_learned_facts() (the "learned"
    category only) while the page header counts every fact, so
    with 5 facts (general + preferences + inferred, none
    "learned") the card said "Nothing learned yet". The widened
    card reads get_all_user_facts(); the store is a temp
    MemoryStore, so no test touches the live data/ (the
    test_memory_cap.py pattern)."""

    def setUp(self):
        import tempfile
        import shutil
        from core.memory import learning as learn
        from core.memory.memory import MemoryStore
        self._tmp = tempfile.mkdtemp(prefix="hunt_fix_mem_")
        self._learn = learn
        self._orig_store = learn.memory_store
        self._orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = os.path.join(
            self._tmp, "feed")
        learn.memory_store = MemoryStore(
            os.path.join(self._tmp, "assistant.db"))
        learn.take_dropped_at_cap()
        # The re-check's 5 facts: general x2, preferences x2,
        # inferred x1 — none of them "learned".
        store = learn.memory_store
        for category, key, value in (
                ("general", "repro_home_city", "Mumbai"),
                ("preferences", "repro_theme", "dark"),
                ("general", "repro_quokka_alpha", "quokka_alpha"),
                ("preferences", "repro_quokka_beta", "quokka_beta"),
                ("inferred", "repro_inferred", "likes morning briefings")):
            self.assertTrue(store.save_fact(category, key, value))

    def tearDown(self):
        import shutil
        learn = self._learn
        learn.memory_store = self._orig_store
        if self._orig_feed is None:
            os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
        else:
            os.environ["VYUK_LEARNING_FEED_DIR"] = self._orig_feed
        learn.take_dropped_at_cap()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_card_shows_general_preferences_and_inferred_facts(self):
        learn = self._learn
        # The old narrow read sees none of the 5 facts.
        self.assertEqual(learn.get_learned_facts(), [])
        # The widened read sees all 5, in every user-fact category.
        got = learn.get_all_user_facts()
        self.assertEqual(len(got), 5)
        self.assertEqual(
            {f["key"] for f in got},
            {"repro_home_city", "repro_theme", "repro_quokka_alpha",
             "repro_quokka_beta", "repro_inferred"})

    def test_forget_user_fact_works_for_general_facts(self):
        learn = self._learn
        general = [f for f in learn.get_all_user_facts()
                   if f["key"] == "repro_home_city"][0]
        # The "learned"-only forget refuses a general fact.
        self.assertFalse(learn.forget_learned_fact(general["id"]))
        # The card's forget works for every row it shows.
        self.assertTrue(learn.forget_user_fact(general["id"]))
        self.assertEqual(len(learn.get_all_user_facts()), 4)
        self.assertNotIn(
            "repro_home_city",
            {f["key"] for f in learn.get_all_user_facts()})


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixMemoryCardQt(P2WindowBase):
    """Item 6 — the card itself: 5 non-"learned" facts must render
    as 5 rows, not the "Nothing learned yet" empty state.
    P2WindowBase (not plain WindowBase): the base harness stubs
    core.memory.memory with only memory_store, so the facade's
    learning module cannot import MAX_USER_FACTS from it."""

    def setUp(self):
        import tempfile
        # super().setUp() FIRST: it installs the WindowBase shim
        # (which re-creates the voice_assistant aliases a previous
        # test's tearDown popped). The card reads the learning
        # module through the facade
        # (voice_assistant.core.memory.learning) — NOT the
        # top-level core.memory.learning, which is a different
        # module object. Redirect the facade's identity or the
        # card reads the live store.
        super().setUp()
        from voice_assistant.core.gui_facade import learning as learn
        from core.memory.memory import MemoryStore
        self._tmp = tempfile.mkdtemp(prefix="hunt_fix_memqt_")
        self._learn = learn
        self._orig_store = learn.memory_store
        self._orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = os.path.join(
            self._tmp, "feed")
        learn.memory_store = MemoryStore(
            os.path.join(self._tmp, "assistant.db"))
        learn.take_dropped_at_cap()
        store = learn.memory_store
        for category, key, value in (
                ("general", "repro_home_city", "Mumbai"),
                ("preferences", "repro_theme", "dark"),
                ("general", "repro_quokka_alpha", "quokka_alpha"),
                ("preferences", "repro_quokka_beta", "quokka_beta"),
                ("inferred", "repro_inferred", "likes morning briefings")):
            store.save_fact(category, key, value)

    def tearDown(self):
        import shutil
        learn = self._learn
        learn.memory_store = self._orig_store
        if self._orig_feed is None:
            os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
        else:
            os.environ["VYUK_LEARNING_FEED_DIR"] = self._orig_feed
        learn.take_dropped_at_cap()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def test_card_renders_five_rows_not_nothing_learned(self):
        window = self.window
        window._refresh_learned_facts()
        _pump(0.2)
        layout = window.learned_layout
        # Fact rows are wrap widgets with the display QLabel
        # nested inside; walk the tree for every label's text.
        from PySide6.QtWidgets import QLabel
        texts = []
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is None:
                continue
            for label in widget.findChildren(QLabel):
                texts.append(label.text())
            if isinstance(widget, QLabel):
                texts.append(widget.text())
        # No "Nothing learned yet" empty state.
        self.assertFalse(any("Nothing learned yet" in t for t in texts))
        # The header count matches the card's rows.
        self.assertEqual(window._learned_fact_count(), 5)
        # Each fact's display sentence is on the card.
        joined = " ".join(texts)
        for fragment in ("Mumbai", "dark", "quokka_alpha",
                         "quokka_beta", "morning briefings"):
            self.assertIn(fragment, joined)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixHeaderUniqueCount(WindowBase):
    """Header double-count fix round — the Memory header counts
    UNIQUE facts: 5 stored facts read "5 facts remembered", not
    10, even though the widened card renders all 5 a second time.
    (Parent verdict, hunt fix (c): the E-F204 list+card rule
    counted every fact twice. The seed is the item-6 5-fact
    seed; the bridge is the r3ef204 seam — header and card share
    the one harness temp store.)"""

    def setUp(self):
        super().setUp()
        self._orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = str(self._tmp / "feed")
        import voice_assistant.core.gui_facade as facade
        self._facade = facade
        facade.memory_store = self._store
        from core.memory import learning as module
        self._learn = module
        self._orig_learn_store = module.memory_store
        module.memory_store = self._store
        self.window._learning_module = lambda: module
        import voice_assistant.gui.window_memory_web as _wmw
        self._wmw = _wmw
        self._orig_wmw_store = _wmw.memory_store
        _wmw.memory_store = self._store
        for category, key, value in (
                ("general", "repro_home_city", "Mumbai"),
                ("preferences", "repro_theme", "dark"),
                ("general", "repro_quokka_alpha", "quokka_alpha"),
                ("preferences", "repro_quokka_beta", "quokka_beta"),
                ("inferred", "repro_inferred", "likes morning briefings")):
            self.assertTrue(
                self._store.save_fact(category, key, value), key)

    def tearDown(self):
        try:
            self._wmw.memory_store = self._orig_wmw_store
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        try:
            self._learn.memory_store = self._orig_learn_store
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        try:
            delattr(self._facade, "memory_store")
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        if self._orig_feed is None:
            os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
        else:
            os.environ["VYUK_LEARNING_FEED_DIR"] = self._orig_feed
        super().tearDown()

    def test_header_reads_five_for_five_stored_facts(self):
        self.window._show_page(2)
        self.window._set_memory_view("list")
        _pump(0.5)
        self.assertEqual(self.window.mem_count_label.text(),
                         "5 facts remembered")
        # The card still renders all 5 a second time (item 6
        # stays) — the header just does not count the second
        # rendering.
        self.assertEqual(self.window._learned_fact_count(), 5)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixKgLabel(unittest.TestCase):
    """Item 7 — KG node label clip ("repro_quokka_alpha" reads as
    merged with the General hub's sphere).

    The label's rect sat ~3 px from the hub's disc, so the first
    glyph read as merged with the sphere. The planner now clears
    every non-own disc by >= 6 px, sliding the label along its
    row instead of dropping it (a fact label has no alt and is
    not sticky, so without the slide it would vanish)."""

    def test_fact_label_slides_clear_of_neighbour_hub_disc(self):
        _ensure_aliases()
        from PySide6.QtCore import QPointF, QRectF
        from gui.memory_web_graph import _plan_label_rects
        # The quokka geometry: a hub disc centred at (794, 497)
        # with radius 11 (right edge 805), and the fact label
        # rect [808, 918] x [487, 503] — 3 px from the disc.
        hub_centre = QPointF(794.0, 497.0)
        hub_r = 11.0
        fact_rect = QRectF(808.0, 487.0, 110.0, 16.0)
        fact_centre = QPointF(863.0, 471.0)
        entries = [
            {"id": "hub_general", "priority": 1, "degree": 5,
             "rect": QRectF(750.0, 511.0, 88.0, 16.0),
             "alt_rect": QRectF(750.0, 470.0, 88.0, 16.0),
             "force": False, "sticky": True},
            {"id": "fact_quokka", "priority": 4, "degree": 0,
             "rect": fact_rect, "alt_rect": None,
             "force": False, "sticky": False},
        ]
        obstacles = [
            (hub_centre, hub_r, "hub_general"),
            (fact_centre, 6.0, "fact_quokka"),
        ]
        planned = _plan_label_rects(entries, obstacles=obstacles)
        # The fact label is still placed (not dropped).
        self.assertIn("fact_quokka", planned)
        rect = planned["fact_quokka"]
        # It stays on its row (a horizontal slide, not a flip).
        self.assertEqual(rect.y(), fact_rect.y())
        # It clears the hub disc by >= 6 px.
        cx = min(max(hub_centre.x(), rect.left()), rect.right())
        cy = min(max(hub_centre.y(), rect.top()), rect.bottom())
        dist = ((hub_centre.x() - cx) ** 2
                + (hub_centre.y() - cy) ** 2) ** 0.5
        self.assertGreaterEqual(dist, hub_r + 6.0)
        # And it moved right (away from the disc on its left).
        self.assertGreater(rect.x(), fact_rect.x())

    def test_label_without_obstacles_behaves_as_before(self):
        _ensure_aliases()
        # Without obstacles the planner is exactly the old one,
        # plus the row slide: a non-sticky label whose rect
        # overlaps a placed label by a few px slides clear
        # instead of vanishing.
        from PySide6.QtCore import QRectF
        from gui.memory_web_graph import _plan_label_rects
        entries = [
            {"id": "hub_a", "priority": 1, "degree": 5,
             "rect": QRectF(100.0, 100.0, 80.0, 16.0),
             "alt_rect": QRectF(100.0, 70.0, 80.0, 16.0),
             "force": False, "sticky": True},
            {"id": "fact_b", "priority": 4, "degree": 0,
             "rect": QRectF(175.0, 100.0, 80.0, 16.0),
             "alt_rect": None, "force": False, "sticky": False},
        ]
        planned = _plan_label_rects(entries)
        self.assertIn("hub_a", planned)
        # fact_b's rect overlaps hub_a's by 5 px; the slide
        # moves it right by 8 px so it clears hub_a's rect.
        self.assertIn("fact_b", planned)
        self.assertEqual(planned["fact_b"].x(), 183.0)
        self.assertFalse(
            planned["fact_b"].intersects(planned["hub_a"]))


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixActivityEmpties(WindowBase):
    """Item 8 — Activity History / Mission Log doubled "nothing".

    With an empty ledger the headline already says the empty
    message ("Nothing finished yet" / "Nothing needs
    attention"), so the list's EmptyState is a second "nothing"
    line. The fix: the EmptyState is added only when tasks exist
    but the filter / time window excludes them all. History's
    filter chips drop "(0)" at zero (fix (a)'s pattern)."""

    def _show_activity(self):
        window = self.window
        window._show_page(1)  # Activity
        window._refresh_activity_tabs()
        _pump(0.3)

    def test_history_one_empty_line_and_bare_filter_names(self):
        self._show_activity()
        window = self.window
        self.assertEqual(
            window._activity_headlines["history"]["line"].text(),
            "Nothing finished yet")
        # No EmptyState item in the list — the headline is the
        # one empty line.
        self.assertEqual(window._activity_history_list.count(), 0)
        # The five filter chips carry no "(0)" while all are zero.
        from PySide6.QtWidgets import QPushButton
        for state in ("All", "Done", "Failed", "Partial", "Cancelled"):
            btn = window.findChild(
                QPushButton, f"historyFilter-{state}")
            self.assertIsNotNone(btn, state)
            self.assertEqual(btn.text(), state)

    def test_mission_log_one_empty_line_and_trace_box_stays(self):
        self._show_activity()
        window = self.window
        self.assertEqual(
            window._activity_headlines["mission_log"]["line"].text(),
            "Nothing needs attention")
        # No EmptyState item in the list — the headline is the
        # one empty line.
        self.assertEqual(window._mission_log_list.count(), 0)
        # The evidence-trace box stays (it is the tab's purpose,
        # not a second empty state).
        detail = window._mission_log_detail
        self.assertEqual(
            detail.placeholderText(),
            "Select a mission to see its evidence trace.")


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixGalaxyLabel(unittest.TestCase):
    """Item 9 — Galaxy "Desktop control" collision.

    The label's final "l" hid behind the Intelligence cluster's
    left-edge stars. The 2D planner now takes every node disc as
    an obstacle (owner-filtered by group, so a label ignores its
    own cluster) and a sticky hub label slides along its row
    instead of landing on a neighbour's stars."""

    def test_desktop_label_slides_clear_of_intelligence_stars(self):
        _ensure_aliases()
        from PySide6.QtCore import QPointF, QRectF
        from gui.skills_galaxy_data import _plan_label_rects
        # Desktop control hub at (700, 240); its label rect
        # [645, 755] x [256, 272] (centred under the hub). The
        # Intelligence cluster's leftmost stars sit at x ~752-758,
        # y ~258-268 — inside the label's right edge, where the
        # final "l" is.
        desktop_rect = QRectF(645.0, 256.0, 110.0, 16.0)
        entries = [
            {"id": "hub:desktop_control", "priority": 1, "degree": 40,
             "rect": desktop_rect,
             "alt_rect": QRectF(645.0, 221.0, 110.0, 16.0),
             "force": False, "sticky": True, "group": "desktop_control"},
            {"id": "hub:intelligence", "priority": 1, "degree": 60,
             "rect": QRectF(760.0, 256.0, 80.0, 16.0),
             "alt_rect": QRectF(760.0, 221.0, 80.0, 16.0),
             "force": False, "sticky": True, "group": "intelligence"},
        ]
        obstacles = [
            (QPointF(700.0, 240.0), 11.0, "desktop_control"),
            (QPointF(800.0, 240.0), 11.0, "intelligence"),
            (QPointF(752.0, 260.0), 2.5, "intelligence"),
            (QPointF(758.0, 266.0), 2.5, "intelligence"),
            (QPointF(755.0, 252.0), 2.5, "intelligence"),
        ]
        planned = _plan_label_rects(entries, obstacles=obstacles)
        # Both hub labels are still placed (sticky — they slide
        # rather than vanish).
        self.assertIn("hub:desktop_control", planned)
        self.assertIn("hub:intelligence", planned)
        rect = planned["hub:desktop_control"]
        # The Desktop label's plate (rect inflated 11 px, the
        # planner's probe) clears every Intelligence star.
        probe = rect.adjusted(-11.0, -5.0, 11.0, 5.0)
        for centre, radius, owner in obstacles:
            if owner != "intelligence":
                continue
            cx = min(max(centre.x(), probe.left()), probe.right())
            cy = min(max(centre.y(), probe.top()), probe.bottom())
            dist = ((centre.x() - cx) ** 2
                    + (centre.y() - cy) ** 2) ** 0.5
            self.assertGreaterEqual(dist, radius + 2.0)
        # It moved — flipped above its hub or slid along its row —
        # rather than landing on the Intelligence stars.
        self.assertNotEqual(
            (rect.x(), rect.y()),
            (desktop_rect.x(), desktop_rect.y()))

    def test_label_ignores_its_own_cluster_stars(self):
        _ensure_aliases()
        # A hub label below its hub overlaps its OWN cluster's
        # lower stars by construction — the owner filter means
        # those never push it away.
        from PySide6.QtCore import QPointF, QRectF
        from gui.skills_galaxy_data import _plan_label_rects
        rect = QRectF(645.0, 256.0, 110.0, 16.0)
        entries = [
            {"id": "hub:desktop_control", "priority": 1, "degree": 40,
             "rect": rect,
             "alt_rect": QRectF(645.0, 221.0, 110.0, 16.0),
             "force": False, "sticky": True, "group": "desktop_control"},
        ]
        obstacles = [
            (QPointF(700.0, 240.0), 11.0, "desktop_control"),
            (QPointF(680.0, 262.0), 2.5, "desktop_control"),
            (QPointF(720.0, 264.0), 2.5, "desktop_control"),
        ]
        planned = _plan_label_rects(entries, obstacles=obstacles)
        # Own-cluster stars do not move the label.
        self.assertEqual(planned["hub:desktop_control"].x(), rect.x())


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixSkillsCountOnce(P2WindowBase):
    """Item 10 — Skills count twice.

    The catalogue card-foot ("772 capabilities") duplicated the
    page header ("772 capabilities — 11 groups"). The card-foot
    label is deleted; the header is the one count line.
    P2WindowBase (not plain WindowBase): the base harness stubs
    the registry so the Skills page ships without a list."""

    def test_catalogue_has_no_count_label_and_header_counts_once(self):
        window = self.window
        window._show_page(3)  # Skills
        window._refresh_skills_page()
        window._update_skills_count_line()
        _pump(0.3)
        catalog = window.skills_catalog
        self.assertFalse(hasattr(catalog, "count_label"))
        # The header is the one count line.
        header = window.skills_count_label.text()
        self.assertIn("772 capabilities", header)
        self.assertIn("11 groups", header)
        # It appears exactly once on the Skills page (no second
        # "capabilities" line from the card foot).
        from PySide6.QtWidgets import QLabel
        page = window.pages.widget(3)
        texts = [w.text() for w in page.findChildren(QLabel)
                 if "capabilities" in w.text()]
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0], header)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixFooterOnce(WindowBase):
    """Item 11 — Home footer "local local".

    _refresh_home_vitals set both the strip's status_label and a
    separate brain label to the same brain source, so the footer
    read it twice. The brain label is deleted; the strip's
    status_label is the one brain-source line."""

    def test_brain_source_appears_once_in_vitals(self):
        window = self.window
        window._show_page(0)  # Home
        window._refresh_home_vitals()
        _pump(0.3)
        self.assertFalse(hasattr(window, "_home_vitals_brain"))
        strip = window.home_vitals_line
        self.assertIsNotNone(strip)
        # The strip's own status label carries the brain source.
        status = strip.status_label.text()
        self.assertTrue(status)
        # No second brain-source label in the strip: the vitals
        # text contains the brain source exactly once. (The strip
        # is not visible in the harness, so visibility is not
        # part of the assertion — the count is.)
        from PySide6.QtWidgets import QLabel
        texts = [w.text() for w in strip.findChildren(QLabel)
                 if w.text()]
        joined = " ".join(texts)
        self.assertEqual(joined.count(status), 1)
        self.assertNotIn("local local", joined)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixUndatedChip(WindowBase):
    """Item 12 — "No date recorded: 0" chip.

    The undated-facts chip hides at 0 (a bucket of nothing is
    not a filter); it shows when there are undated facts."""

    def test_undated_chip_hidden_at_zero_shown_when_undated(self):
        window = self.window
        window._show_page(2)  # Memory
        # Empty store: no facts at all, so no undated facts.
        # isHidden is the chip's own flag (isVisible also needs
        # every ancestor visible, which the harness does not give).
        window._refresh_memory_learned_strip([], private=False)
        _pump(0.2)
        self.assertTrue(window._mem_learned_undated.isHidden())
        # One undated fact (no epoch): the chip shows with its count.
        window._refresh_memory_learned_strip(
            [{"id": 1, "key": "k", "value": "v",
              "category": "general", "updated_at": None}],
            private=False)
        _pump(0.2)
        self.assertFalse(window._mem_learned_undated.isHidden())
        self.assertEqual(window._mem_learned_undated.text(),
                         "No date recorded: 1")


@unittest.skipUnless(_QT, "Qt unavailable")
class TestHuntFixResidualEmpties(WindowBase):
    """Item 15 — residual double-empties.

    Contacts showed "0 of 0 contacts" + "No contacts saved
    yet."; Calls showed "Showing the latest 0 calls" + "No
    calls yet." The count/status line hides at zero, so the
    EmptyState is the one empty line."""

    def test_contacts_count_hidden_at_zero(self):
        window = self.window
        window._show_page(10)  # Contacts
        window._refresh_contacts_page()
        _pump(0.3)
        # No contacts in the pristine data: the count line hides
        # and the EmptyState is the one empty line.
        self.assertTrue(window._views_contacts_count.isHidden())
        self.assertFalse(window._views_contacts_empty.isHidden())
        self.assertEqual(window._views_contacts_empty.sentence_text,
                         "No contacts saved yet.")

    def test_calls_status_hidden_at_zero(self):
        window = self.window
        window._show_page(11)  # Calls
        window._refresh_calls_page()
        _pump(0.3)
        # No calls in the pristine data: the status line hides
        # and the EmptyState is the one empty line.
        self.assertTrue(window._views_calls_status.isHidden())
        self.assertFalse(window._views_calls_empty.isHidden())
        self.assertEqual(window._views_calls_empty.sentence_text,
                         "No calls yet.")


if __name__ == "__main__":
    unittest.main()
