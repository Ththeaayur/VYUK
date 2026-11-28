"""UI Phase 2 — Discoverability suite (2026-11-25).

Pins the Phase 2 seams: the shared goal-search token index, the
ONE detail builder, the pin store shared by Home/Centre/Galaxy,
the sidebar badges (badge == page), the palette goal lane +
Browse row + recents, the catalogue widget (goal search, Pinned
section, private mode), the Galaxy card (the pre-Phase-2 card
never filled — its Ask button was built inside set_font_scale,
which raised NameError on every call), and the Skills page
itself (drawer, filter sync, stacking, refresh).

Qt only for the GUI tests: without PySide6 every GUI test skips
(the test_chat_ui / test_ui_p1 shape). The pure tests
(skill_search, command_palette, pending_approvals_count) run on
the system interpreter too.
"""
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
# test_ui_p1 shape): create the alias packages here so the pure
# core tests can import the real modules.
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
    if _QT:
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance()
        if _app is not None:
            _app.processEvents()


class P2WindowBase(WindowBase):
    """WindowBase with the stubs enriched so the Skills page
    builds. The base harness stubs core.memory.memory with only
    memory_store (the tools handlers import more names),
    core.scheduler with only scheduler (the wave3 pack imports
    _default_tz / _resolve_wall_time and its guarded registration
    fails, leaving 26 batch tools unregistered), and
    plugin_loader to discover nothing (the two plugin tools
    never register) — so the registry's import-time agreement
    check raises and _build_skills_page ships without a list.
    This override copies the missing names from the real modules
    (bare core.* path, as test_ui_p1 does) and lets the real
    plugin loader run. Product code is untouched; only this
    harness differs. Registry reads 772 under it."""

    def _install_shim(self):
        super()._install_shim()
        from core.memory import memory as _real_mem
        mod = sys.modules["voice_assistant.core.memory.memory"]
        for _name in dir(_real_mem):
            if _name.startswith("_") or _name == "memory_store":
                continue
            setattr(mod, _name, getattr(_real_mem, _name))
        from core import scheduler as _real_sched
        smod = sys.modules["voice_assistant.core.scheduler"]
        for _name in dir(_real_sched):
            if _name == "scheduler":
                continue
            setattr(smod, _name, getattr(_real_sched, _name))
        sys.modules.pop("voice_assistant.core.plugin_loader", None)

    def setUp(self):
        super().setUp()
        # Every test starts in Catalogue view with clean pin /
        # recent stores (QSettings is cached per process, so the
        # temp XDG dir does not isolate it — clear explicitly,
        # the AGENTS.md lesson).
        try:
            self.window._set_skills_view("list")
            self.window.settings.remove("skills/pinned")
            self.window.settings.remove("palette/recent")
            self.window.settings.sync()
        except Exception:  # noqa: BLE001 — isolation is best-effort
            pass

    def tearDown(self):
        try:
            self.window.settings.remove("skills/pinned")
            self.window.settings.remove("palette/recent")
            self.window.settings.sync()
        except Exception:  # noqa: BLE001
            pass
        super().tearDown()


@unittest.skipUnless(_QT, "Qt unavailable")
class _QtTempStore(unittest.TestCase):
    """A temp QSettings file for the store-level tests."""

    def setUp(self):
        from PySide6.QtCore import QSettings
        super().setUp()
        self._tmp = tempfile.NamedTemporaryFile(
            prefix="p2_qs_", suffix=".ini", delete=False)
        self._tmp.close()
        self.settings = QSettings(
            self._tmp.name, QSettings.Format.IniFormat)

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass
        super().tearDown()


class TestSkillSearchGoal(unittest.TestCase):
    """D-P2-04: ONE shared token index — a goal phrase ranks the
    tool it names first, without its snake_case name."""

    def test_send_an_email_finds_send_email_first(self):
        from voice_assistant.core.skill_search import get_index, search
        names = search(get_index(), "send an email")
        self.assertTrue(names)
        self.assertEqual(names[0], "send_email")

    def test_goal_phrases_rank_first(self):
        from voice_assistant.core.skill_search import get_index, search
        for query, expected in (
                ("what time is it", "get_current_time_date"),
                ("take a screenshot", "take_screenshot"),
                ("turn the volume up", "adjust_volume"),
                ("will it rain", "get_weather"),
                ("open chrome", "launch_application")):
            with self.subTest(query=query):
                names = search(get_index(), query)
                self.assertTrue(names, query)
                self.assertEqual(names[0], expected, query)

    def test_nonsense_matches_nothing(self):
        from voice_assistant.core.skill_search import get_index, search
        self.assertEqual(search(get_index(), "xyzzy plugh"), [])

    def test_prefix_matches(self):
        from voice_assistant.core.skill_search import get_index, search
        names = search(get_index(), "emai")
        self.assertTrue(names)
        self.assertEqual(names[0], "download_email_attachment")


class TestCommandPaletteGoal(unittest.TestCase):
    """D-P2-04, palette side: rank_entries takes the shared
    index's goal ranks (55–65 band) for TOOL rows."""

    _TOOL = {"label": "Send email", "kind": "TOOL",
             "description": "Send an email to someone.",
             "action_id": "tool_send_email", "tool_name": "send_email",
             "consequential": False}
    _PAGE = {"label": "Go to Home", "kind": "PAGE", "description": "",
             "action_id": "page_0", "consequential": False}

    def test_goal_rank_surfaces_tool_without_its_name(self):
        from voice_assistant.core.command_palette import rank_entries
        ranked = rank_entries(
            [self._PAGE, self._TOOL], "send an email",
            goal_ranks={"send_email": 65.0})
        self.assertEqual(ranked[0]["tool_name"], "send_email")

    def test_no_goal_ranks_is_exactly_the_old_ranking(self):
        from voice_assistant.core.command_palette import rank_entries
        ranked = rank_entries([self._PAGE, self._TOOL], "home")
        self.assertEqual([r["label"] for r in ranked], ["Go to Home"])

    def test_goal_band_straddles_description_lane(self):
        # A strong goal hit (65) beats a description hit (60);
        # a weak one (55) does not.
        from voice_assistant.core.command_palette import rank_entries
        desc_hit = {"label": "Mailbox", "kind": "TOOL",
                    "description": "send an email digest",
                    "action_id": "tool_mailbox", "tool_name": "mailbox",
                    "consequential": False}
        strong = rank_entries(
            [desc_hit, self._TOOL], "send an email",
            goal_ranks={"send_email": 65.0})
        self.assertEqual(strong[0]["tool_name"], "send_email")
        weak = rank_entries(
            [desc_hit, self._TOOL], "send an email",
            goal_ranks={"send_email": 55.0})
        self.assertEqual(weak[0]["tool_name"], "mailbox")

    def test_filter_entries_passes_goal_ranks_through(self):
        from voice_assistant.core.command_palette import filter_entries
        shown, _hidden = filter_entries(
            [self._PAGE, self._TOOL], "send an email",
            goal_ranks={"send_email": 65.0})
        self.assertEqual(shown[0]["tool_name"], "send_email")


class TestPendingApprovalsCount(unittest.TestCase):
    """D-P2-02: ONE shared count — the badge, the page and the
    Review queue all read pending_approvals_count()."""

    def _ledger(self, summaries):
        class _FakeLedger:
            def list_interrupted(self):
                return list(summaries)
        return _FakeLedger()

    def test_counts_pending_approvals_with_a_tool(self):
        from gui.pending_approval import pending_approvals_count
        summaries = [
            {"task_id": "t1", "pending_approval": {
                "tool": "menu_action", "label": "Close tab"}},
            {"task_id": "t2", "pending_approval": {
                "tool": "send_email", "label": "Send"}},
        ]
        self.assertEqual(
            pending_approvals_count(ledger=self._ledger(summaries)), 2)

    def test_drops_dicts_without_a_tool_and_without_an_id(self):
        from gui.pending_approval import pending_approvals_count
        summaries = [
            {"task_id": "t1", "pending_approval": {"label": "stale"}},
            {"pending_approval": {"tool": "menu_action"}},
            {"task_id": "t3"},
            {"task_id": "t4", "pending_approval": "not a dict"},
        ]
        self.assertEqual(
            pending_approvals_count(ledger=self._ledger(summaries)), 0)

    def test_broken_ledger_reads_as_zero(self):
        from gui.pending_approval import pending_approvals_count

        class _Broken:
            def list_interrupted(self):
                raise RuntimeError("boom")

        self.assertEqual(
            pending_approvals_count(ledger=_Broken()), 0)

    def test_verifying_state_is_counted_by_the_ledger_shape(self):
        # The count itself is state-blind (it reads the Review
        # queue's formula); the ledger is what admits verifying
        # records into list_interrupted. A verifying summary with
        # a pending approval counts — the pre-Phase-2 page, which
        # counted paused live records from all_records(), missed
        # exactly this case.
        from gui.pending_approval import pending_approvals_count
        summaries = [
            {"task_id": "t1", "state": "verifying",
             "pending_approval": {"tool": "menu_action"}},
        ]
        self.assertEqual(
            pending_approvals_count(ledger=self._ledger(summaries)), 1)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestSkillPins(_QtTempStore):
    """D-P2-15: one QSettings list, shared by the Home dock, the
    Centre Pinned section and the Galaxy card."""

    def test_pin_unpin_round_trip(self):
        from gui.skill_pins import is_pinned, pinned_tools, set_pinned
        self.assertEqual(pinned_tools(settings=self.settings), [])
        set_pinned("send_email", True, settings=self.settings)
        self.assertTrue(is_pinned("send_email", settings=self.settings))
        self.assertEqual(pinned_tools(settings=self.settings),
                         ["send_email"])
        # Most recently pinned first (the module's contract):
        # pinning take_screenshot moves it to the front.
        set_pinned("take_screenshot", True, settings=self.settings)
        self.assertEqual(pinned_tools(settings=self.settings),
                         ["take_screenshot", "send_email"])
        set_pinned("send_email", False, settings=self.settings)
        self.assertFalse(is_pinned("send_email", settings=self.settings))
        self.assertEqual(pinned_tools(settings=self.settings),
                         ["take_screenshot"])

    def test_pin_is_idempotent_and_keeps_order(self):
        from gui.skill_pins import pinned_tools, set_pinned
        set_pinned("send_email", True, settings=self.settings)
        set_pinned("send_email", True, settings=self.settings)
        self.assertEqual(pinned_tools(settings=self.settings),
                         ["send_email"])

    def test_corrupt_store_reads_as_empty(self):
        from gui.skill_pins import pinned_tools
        from voice_assistant.core.gui_facade import qsettings_set
        qsettings_set("skills/pinned", 42, settings=self.settings)
        self.assertEqual(pinned_tools(settings=self.settings), [])
        qsettings_set("skills/pinned", {"a": 1},
                      settings=self.settings)
        self.assertEqual(pinned_tools(settings=self.settings), [])

    def test_lone_string_reads_as_one_pin(self):
        # QSettings hands a one-item list back as a bare string;
        # the normaliser accepts that form (it is the stored
        # shape of a single pin, not corruption).
        from gui.skill_pins import pinned_tools
        from voice_assistant.core.gui_facade import qsettings_set
        qsettings_set("skills/pinned", "send_email",
                      settings=self.settings)
        self.assertEqual(pinned_tools(settings=self.settings),
                         ["send_email"])


@unittest.skipUnless(_QT, "Qt unavailable")
class TestPaletteRecents(_QtTempStore):
    """D-P2-08: action_id + label only, capped at 8, most recent
    first; a failed read/write degrades to no recents."""

    def test_record_and_cap(self):
        from gui.palette_recents import load_recents, record_recent
        for i in range(10):
            record_recent(f"page_{i}", f"Row {i}",
                          settings=self.settings)
        recents = load_recents(settings=self.settings)
        self.assertEqual(len(recents), 8)
        self.assertEqual(recents[0],
                         {"action_id": "page_9", "label": "Row 9"})
        self.assertEqual(recents[-1],
                         {"action_id": "page_2", "label": "Row 2"})

    def test_rerun_moves_to_front_without_duplicating(self):
        from gui.palette_recents import load_recents, record_recent
        record_recent("page_0", "Go to Home", settings=self.settings)
        record_recent("page_3", "Go to Skills", settings=self.settings)
        record_recent("page_0", "Go to Home", settings=self.settings)
        self.assertEqual(load_recents(settings=self.settings), [
            {"action_id": "page_0", "label": "Go to Home"},
            {"action_id": "page_3", "label": "Go to Skills"},
        ])

    def test_empty_and_blank_entries_are_dropped(self):
        from gui.palette_recents import load_recents, record_recent
        record_recent("", "No id", settings=self.settings)
        record_recent("page_0", "", settings=self.settings)
        self.assertEqual(load_recents(settings=self.settings), [])

    def test_only_action_id_and_label_are_stored(self):
        from gui.palette_recents import load_recents, record_recent
        record_recent("tool_send_email", "Send email",
                      settings=self.settings)
        stored = load_recents(settings=self.settings)
        self.assertEqual(stored, [
            {"action_id": "tool_send_email", "label": "Send email"}])
        self.assertEqual(set(stored[0].keys()),
                         {"action_id", "label"})


@unittest.skipUnless(_QT, "Qt unavailable")
class TestSkillDetail(unittest.TestCase):
    """D-P2-06: ONE shared detail builder — the drawer and the
    Galaxy card render the same contents."""

    def test_send_email_is_confirm_gated_with_connection(self):
        from gui.skill_detail import build_skill_detail
        detail = build_skill_detail("send_email",
                                    is_connected=lambda: False)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["friendly"], "Send email")
        self.assertEqual(detail["group"], "Your world")
        self.assertEqual(detail["example"], "Use Send email to …")
        self.assertIn("approval", detail["requirements"])
        self.assertEqual(detail["connection"], "not_connected")
        self.assertFalse(detail["pinned"])

    def test_synonym_phrase_becomes_the_example(self):
        from gui.skill_detail import build_skill_detail
        detail = build_skill_detail("get_unread_email")
        self.assertEqual(detail["example"], "Try: new mail")
        self.assertIn("Read-only", detail["requirements"])
        self.assertTrue(detail["owner_only"])

    def test_free_tool_has_no_chips_and_no_connection(self):
        from gui.skill_detail import build_skill_detail
        detail = build_skill_detail("take_screenshot")
        self.assertEqual(detail["requirements"],
                         "Runs without asking")
        self.assertIsNone(detail["connection"])
        self.assertEqual(detail["group"], "Desktop control")

    def test_unknown_tool_returns_none(self):
        from gui.skill_detail import build_skill_detail
        self.assertIsNone(build_skill_detail("no_such_tool_xyz"))

    def test_state_chips_match_the_detail(self):
        from gui.skill_detail import build_skill_detail, state_chips
        detail = build_skill_detail("send_email", is_connected=lambda: False)
        chips = state_chips(detail)
        self.assertEqual(detail["connection"], "not_connected")
        self.assertIn("Needs permission", chips)
        self.assertIn("Needs connection", chips)
        connected = build_skill_detail(
            "send_email", is_connected=lambda: True)
        self.assertNotIn("Needs connection",
                         state_chips(connected))
        free = build_skill_detail("take_screenshot")
        self.assertEqual(state_chips(free), [])


@unittest.skipUnless(_QT, "Qt unavailable")
class TestSidebarBadges(unittest.TestCase):
    """D-P2-12: exactly two badges; exact to 99, "99+" above,
    zero hides."""

    def test_badge_text(self):
        from gui.sidebar_badges import badge_text
        self.assertEqual(badge_text(None), "")
        self.assertEqual(badge_text(0), "")
        self.assertEqual(badge_text(1), "1")
        self.assertEqual(badge_text(99), "99")
        self.assertEqual(badge_text(100), "99+")
        self.assertEqual(badge_text(150), "99+")

    def test_set_badge_shows_and_hides(self):
        from PySide6.QtWidgets import QApplication, QPushButton
        from gui.sidebar_badges import make_badge, set_badge
        app = QApplication.instance() or QApplication([])
        btn = QPushButton("x")
        btn.show()
        badge = make_badge(btn, "#35D6FF", "#04090E")
        self.assertTrue(badge.isHidden())
        set_badge(badge, btn, 3, False)
        self.assertEqual(badge.text(), "3")
        self.assertFalse(badge.isHidden())
        set_badge(badge, btn, 0, False)
        self.assertTrue(badge.isHidden())
        set_badge(badge, btn, 150, False)
        self.assertEqual(badge.text(), "99+")


@unittest.skipUnless(_QT, "Qt unavailable")
class TestCatalogueWidget(unittest.TestCase):
    """The Centre list: goal search, Pinned section, private
    mode, EmptyState. Built standalone on the real registry."""

    def _widget(self):
        from PySide6.QtWidgets import QApplication
        from voice_assistant.core.gui_facade import tools as _tools
        from gui.skills_catalog import build_skills_catalog
        from gui.window_widgets import _TOOL_FRIENDLY_NAMES
        app = QApplication.instance() or QApplication([])
        self._app = app
        widget = build_skills_catalog(
            list(_tools.get_declarations()), _TOOL_FRIENDLY_NAMES,
            settings=self._store())
        return widget

    def _store(self):
        from PySide6.QtCore import QSettings
        tmp = tempfile.NamedTemporaryFile(
            prefix="p2_cat_", suffix=".ini", delete=False)
        tmp.close()
        self._tmp_path = tmp.name
        return QSettings(tmp.name, QSettings.Format.IniFormat)

    def tearDown(self):
        try:
            os.unlink(self._tmp_path)
        except (AttributeError, OSError):
            pass
        super().tearDown()

    def test_total_is_772_and_groups_agree(self):
        widget = self._widget()
        self.assertEqual(widget.total_count, 772)
        self.assertEqual(widget.visible_count, 772)
        counts = widget.group_counts()
        self.assertEqual(counts["Desktop control"], 68)
        self.assertEqual(counts["Intelligence"], 110)
        self.assertEqual(counts["More skills"], 2)

    def test_goal_search_finds_send_email(self):
        from gui.skills_catalog_data import (
            ROLE_GROUP, ROLE_KIND, ROLE_NAME)
        widget = self._widget()
        widget.set_filter("send an email")
        names = widget.tool_names()
        self.assertIn("send_email", names)
        # Grouped display, ranked within each group (a Desktop
        # control match may precede it in the flat list): within
        # "Your world", send_email is first.
        your_world = []
        for i in range(widget.skills_list.count()):
            item = widget.skills_list.item(i)
            if (item.data(ROLE_KIND) == "tool"
                    and item.data(ROLE_GROUP) == "Your world"):
                your_world.append(item.data(ROLE_NAME))
        self.assertTrue(your_world)
        self.assertEqual(your_world[0], "send_email")

    def test_nonsense_query_shows_empty_state(self):
        widget = self._widget()
        widget.set_filter("xyzzy plugh")
        self.assertEqual(widget.visible_count, 0)
        self.assertFalse(widget.empty_state.isHidden())
        self.assertEqual(widget.empty_state.sentence_text,
                         "No skills match 'xyzzy plugh'.")

    def _pinned_section_names(self, widget):
        # Pinned rows carry their real group (ROLE_GROUP is the
        # tool's group, not "Pinned") — they are the tool rows
        # after the PINNED header, before the next header.
        from gui.skills_catalog_data import ROLE_KIND, ROLE_NAME
        out = []
        in_pinned = False
        for i in range(widget.skills_list.count()):
            item = widget.skills_list.item(i)
            kind = item.data(ROLE_KIND)
            if kind == "header":
                in_pinned = item.text().startswith("PINNED")
                continue
            if in_pinned and kind == "tool":
                out.append(item.data(ROLE_NAME))
        return out

    def test_pin_shows_pinned_section(self):
        from gui.skill_pins import set_pinned
        widget = self._widget()
        store = widget._settings
        set_pinned("send_email", True, settings=store)
        widget.refresh_pins()
        self.assertIn("send_email", widget._pins())
        self.assertEqual(self._pinned_section_names(widget),
                         ["send_email"])
        header_texts = [widget.skills_list.item(i).text()
                        for i in range(widget.skills_list.count())]
        self.assertTrue(any(t.startswith("PINNED")
                            for t in header_texts), header_texts)

    def test_narrow_row_chips_never_overlap_name(self):
        # UI Phase 2 (2026-11-25): at 640 the pinned Send email
        # row's chips were painted OVER the name — the summary
        # was elided to the chips' left edge, but the friendly
        # name was drawn whole. The delegate now elides the
        # name too. Proved by pixels: paint the row at a narrow
        # list width and assert no friendly-coloured pixel falls
        # inside the chips' band (while the chips themselves
        # are present, so the check is not vacuous).
        from PySide6.QtCore import QRect
        from PySide6.QtGui import (QColor, QFont, QFontMetrics,
                                   QPainter, QPixmap)
        from PySide6.QtWidgets import QStyleOptionViewItem
        from gui.skill_pins import set_pinned
        from gui.skills_catalog_data import (
            ROLE_CHIPS, ROLE_KIND, ROLE_NAME)
        from gui.skills_catalog_widget import parse_color
        widget = self._widget()
        set_pinned("send_email", True, settings=widget._settings)
        widget.refresh_pins()
        lst = widget.skills_list
        lst.resize(420, 400)
        widget.resize(420, 400)
        widget.show()
        self._app.processEvents()
        row = None
        for i in range(lst.count()):
            item = lst.item(i)
            if (item.data(ROLE_KIND) == "tool"
                    and item.data(ROLE_NAME) == "send_email"):
                row = i
                break
        self.assertIsNotNone(row, "pinned send_email row not found")
        index = lst.model().index(row, 0)
        chips = [str(c).upper()
                 for c in (index.data(ROLE_CHIPS) or []) if str(c)]
        self.assertEqual(len(chips), 3, chips)
        rect = lst.visualRect(index)
        self.assertTrue(rect.isValid())
        pix = QPixmap(rect.size())
        pix.fill(QColor(4, 9, 14))
        painter = QPainter(pix)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, rect.width(), rect.height())
        opt.font = lst.font()
        lst.itemDelegate().paint(painter, opt, index)
        painter.end()
        delegate = lst.itemDelegate()
        chip_font = QFont(lst.font())
        chip_font.setPointSizeF(10 * delegate._font_scale)
        chip_font.setWeight(QFont.Weight.DemiBold)
        chip_font.setLetterSpacing(
            QFont.SpacingType.PercentageSpacing, 106)
        chip_metrics = QFontMetrics(chip_font)
        chips_w = sum(chip_metrics.horizontalAdvance(t)
                      for t in chips) + 8 * len(chips)
        band_left = rect.width() - 12 - chips_w
        text_rgb = parse_color(delegate._text)[:3]
        muted_rgb = parse_color(delegate._text_muted)[:3]
        faint_rgb = parse_color(delegate._text_faint)[:3]
        img = pix.toImage()
        name_pixels = 0
        muted_pixels = 0
        chip_pixels = 0
        for x in range(max(0, band_left), rect.width()):
            for y in range(rect.height()):
                px = img.pixelColor(x, y)
                rgb = (px.red(), px.green(), px.blue())
                if rgb == text_rgb:
                    name_pixels += 1
                if rgb == muted_rgb:
                    muted_pixels += 1
                if rgb == faint_rgb:
                    chip_pixels += 1
        self.assertGreater(chip_pixels, 0,
                           "no chip pixels painted — vacuous check")
        self.assertEqual(name_pixels, 0,
                         f"{name_pixels} friendly-coloured pixels "
                         "inside the chip band")
        # The " — " separator is muted, like the summary: it must
        # not run into the chips either (the same 640 render
        # showed "—PINNED" after the name fix).
        self.assertEqual(muted_pixels, 0,
                         f"{muted_pixels} muted-coloured pixels "
                         "inside the chip band")

    def test_bottom_row_never_straddles_viewport_edge(self):
        # UI Phase 2 look-fix (2026-11-25, defect 1): at 1280
        # the catalogue's bottom row ("Typing") straddled the
        # viewport edge by 16 px, so its descenders were cut and
        # "Typing" painted as "Tvoina" — a legible-but-wrong word,
        # not an obviously cut row. The catalogue now snaps the
        # default view to a whole-row boundary: no row straddles
        # the viewport bottom edge, and the Typing row's
        # descenders are ink inside the viewport. Proved by
        # pixels: size the widget so the list viewport would land
        # at the 1280 height (234 px), then assert the snapped
        # geometry + the descender ink below the baseline.
        import time
        from gui.skill_pins import set_pinned
        from gui.skills_catalog_data import ROLE_NAME
        from gui.skills_catalog_widget import parse_color
        widget = self._widget()
        # The live 1280 view carries these 4 pins (the render
        # driver's seed) — they put "Typing" at row 6, y 212..250.
        for name in ("send_email", "take_screenshot", "get_weather",
                     "get_current_time_date"):
            set_pinned(name, True, settings=widget._settings)
        widget.refresh_pins()
        lst = widget.skills_list
        # Target viewport height: the measured 1280 value (234).
        # The widget height for it follows from the layout chrome
        # (margins + the one visible spacing + search + list
        # frame). Hunt fix (c) item 3 removed the card-foot
        # count label, so the chrome loses it and one spacing.
        # The Typing straddle zone is 38 px wide (212..250), so
        # a few px of chrome error is fine.
        layout = widget.layout()
        margins = layout.contentsMargins()
        chrome = (margins.top() + margins.bottom()
                  + layout.spacing()
                  + widget.search_input.sizeHint().height()
                  + 2 * lst.frameWidth())
        widget.resize(980, 234 + chrome)
        widget.show()
        for _ in range(25):
            self._app.processEvents()
            time.sleep(0.02)
        vp_h = lst.viewport().height()
        typing = None
        for row in range(lst.count()):
            if lst.item(row).data(ROLE_NAME) == "type_text":
                typing = lst.visualRect(lst.model().index(row, 0))
                break
        self.assertIsNotNone(typing, "type_text row missing")
        # The snap grows the list by the straddle shortfall, so
        # the settled viewport ends exactly at the Typing row's
        # bottom edge (250, not the pre-snap 234).
        self.assertEqual(vp_h, typing.bottom() + 1,
                         f"viewport {vp_h} does not end at the "
                         f"Typing row's bottom {typing.bottom()}")
        # No row straddles the viewport bottom edge.
        for row in range(lst.count()):
            vr = lst.visualRect(lst.model().index(row, 0))
            self.assertFalse(vr.top() < vp_h <= vr.bottom(),
                             f"row {row} straddles: {vr} vp_h={vp_h}")
        # The Typing row's descenders are ink below the baseline
        # (baseline = centre + ascent//2 - 1, the delegate's
        # rule; ascent 13 at pixelSize 13; descenders reach +4).
        # Pre-fix the band below the baseline fell outside the
        # viewport, so this count is 0.
        baseline = (typing.top() + typing.height() // 2
                    + 13 // 2 - 1)
        delegate = lst.itemDelegate()
        text_rgb = parse_color(delegate._text)[:3]
        muted_rgb = parse_color(delegate._text_muted)[:3]
        img = lst.viewport().grab().toImage()
        ink = 0
        for y in range(baseline + 1, baseline + 5):
            if y >= img.height():
                continue
            for x in range(typing.left() + 12, typing.right() - 12):
                px = img.pixelColor(x, y)
                if (px.red(), px.green(), px.blue()) in (
                        text_rgb, muted_rgb):
                    ink += 1
        self.assertGreater(ink, 0,
                           "no descender ink below the Typing baseline")

    def test_private_mode_blanks_pinned_names(self):
        from gui.skill_pins import set_pinned
        widget = self._widget()
        set_pinned("send_email", True, settings=widget._settings)
        widget.refresh_pins()
        widget.set_private_mode(True)
        rows = [widget.skills_list.item(i).text()
                for i in range(widget.skills_list.count())]
        self.assertTrue(any("Private mode is on" in t for t in rows),
                        rows)
        # The Pinned section shows the header + the note only —
        # no pinned tool row (the tool still appears in its
        # group below; the catalogue rows stay).
        self.assertEqual(self._pinned_section_names(widget), [])


@unittest.skipUnless(_QT, "Qt unavailable")
class TestGalaxyCard(unittest.TestCase):
    """The Galaxy card renders the shared detail — and, before
    Phase 2, it rendered nothing at all: its Ask button was
    created inside set_font_scale (which raised NameError on the
    undefined `layout`), so set_skill and clear() raised
    AttributeError and picking a star showed only the hint."""

    def _card(self):
        from PySide6.QtWidgets import QApplication
        from gui.skills_galaxy_widget import SkillDetailCard
        app = QApplication.instance() or QApplication([])
        self._app = app
        card = SkillDetailCard()
        card.show()
        return card

    def test_set_skill_fills_from_the_shared_builder(self):
        card = self._card()
        card.set_skill("send_email", "Send email", "Your world",
                       "Send an email.")
        self.assertEqual(card._title.text(), "Send email")
        self.assertEqual(card._example.text(), "Use Send email to …")
        self.assertIn("approval", card._requirements.text())
        self.assertIn("Not connected", card._connection.text())
        self.assertTrue(card._hint.isHidden())
        self.assertEqual(card._pin_btn.text(), "Pin to Home")

    def test_set_font_scale_no_longer_raises(self):
        card = self._card()
        card.set_font_scale(1.2)  # pre-Phase-2: NameError
        card.set_skill("take_screenshot", "Screenshot",
                       "Desktop control", "Capture a screenshot.")
        self.assertEqual(card._title.text(), "Screenshot")
        self.assertFalse(card._connection.isVisible())

    def test_clear_returns_to_the_hint(self):
        card = self._card()
        card.set_skill("send_email", "Send email", "Your world",
                       "Send an email.")
        card.clear()  # pre-Phase-2: AttributeError
        self.assertFalse(card._hint.isHidden())
        self.assertTrue(card._title.isHidden())

    def test_pin_toggle_and_private_mode(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        tmp = tempfile.NamedTemporaryFile(
            prefix="p2_card_", suffix=".ini", delete=False)
        tmp.close()
        self._tmp_path = tmp.name
        store = QSettings(tmp.name, QSettings.Format.IniFormat)
        from gui.skills_galaxy_widget import SkillDetailCard
        from gui.skill_pins import is_pinned
        app = QApplication.instance() or QApplication([])
        self._app = app
        card = SkillDetailCard(settings=store)
        card.show()
        card.set_skill("send_email", "Send email", "Your world",
                       "Send an email.")
        card._toggle_pin()
        self.assertTrue(is_pinned("send_email", settings=store))
        self.assertEqual(card._pin_btn.text(), "Unpin from Home")
        card._toggle_pin()
        self.assertFalse(is_pinned("send_email", settings=store))
        card.set_private_mode(True)
        card.set_skill("send_email", "Send email", "Your world",
                       "Send an email.")
        self.assertTrue(card._pin_btn.isHidden())
        os.unlink(tmp.name)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestSkillsPage(P2WindowBase):
    """The Centre as the user sees it: count line, drawer,
    pins, filter sync, Galaxy, stacking, refresh."""

    def test_count_line_reads_live(self):
        _show(self.window, 3)
        self.assertEqual(
            self.window.skills_count_label.text(),
            "772 capabilities — 11 groups")

    def test_catalogue_row_opens_the_drawer_on_shared_detail(self):
        w = self.window
        _show(w, 3)
        cat = w.skills_catalog
        self.assertIsNotNone(cat)
        cat.set_filter("send an email")
        self.app.processEvents()
        item = None
        for i in range(cat.skills_list.count()):
            candidate = cat.skills_list.item(i)
            from gui.skills_catalog_data import ROLE_NAME
            if candidate.data(ROLE_NAME) == "send_email":
                item = candidate
                break
        self.assertIsNotNone(item, "send_email row missing")
        cat.skills_list.setCurrentItem(item)
        cat._on_row_activated(item)
        self.app.processEvents()
        self.assertFalse(w.skills_detail_drawer.isHidden())
        self.assertEqual(w.skills_detail_title.text(), "Send email")
        self.assertEqual(w.skills_detail_example.text(),
                         "Use Send email to …")
        self.assertIn("approval", w.skills_detail_reqs.text())
        self.assertIn("Not connected", w.skills_detail_conn.text())
        self.assertEqual(w.skills_detail_raw.text(), "send_email")

    def test_drawer_pin_round_trip_updates_the_pinned_section(self):
        from gui.skill_pins import pinned_tools
        w = self.window
        _show(w, 3)
        w._on_catalogue_skill_chosen("send_email")
        self.app.processEvents()
        self.assertEqual(w.btn_skills_detail_pin.text(), "Pin to Home")
        w.btn_skills_detail_pin.click()
        self.app.processEvents()
        self.assertEqual(w.btn_skills_detail_pin.text(),
                         "Unpin from Home")
        self.assertIn("send_email",
                      pinned_tools(settings=w.settings))
        w.btn_skills_detail_pin.click()
        self.app.processEvents()
        self.assertNotIn("send_email",
                         pinned_tools(settings=w.settings))

    def test_drawer_close_returns_to_the_list_intact(self):
        w = self.window
        _show(w, 3)
        cat = w.skills_catalog
        cat.set_filter("screenshot")
        w._on_catalogue_skill_chosen("take_screenshot")
        self.app.processEvents()
        self.assertFalse(w.skills_detail_drawer.isHidden())
        w._on_skills_detail_close()
        self.assertTrue(w.skills_detail_drawer.isHidden())
        self.assertEqual(cat.search_input.text(), "screenshot")

    def test_light_theme_rethemes_page_backdrop_without_show_page(self):
        # UI Phase 2 look-fix (2026-11-25, defect 2): a theme
        # change left every already-built page's HudBackdrop on
        # the OLD theme — Light theme + detail drawer open showed
        # a dark page behind light headers — until the user
        # navigated away and back (only _show_page re-themed the
        # backdrops). The appearance pass now re-applies the
        # backdrops itself, so the backdrop follows the theme on
        # the change, with no page switch. The drawer is open
        # here, as in the render-driver detail sequence — but
        # the probe proved the drawer is incidental: the same
        # stale backdrop shows with it closed.
        from voice_assistant.core.config_spec import qsettings_set
        # NB: the window lives under the voice_assistant.gui
        # alias package, so the backdrop class must be imported
        # from there too — the bare gui.window_home_hud class is
        # a DIFFERENT class object and findChildren finds nothing
        # under it (a test-harness identity trap, not a product
        # difference).
        from voice_assistant.gui.window_home_hud import HudBackdrop
        w = self.window
        _show(w, 3)  # builds the Skills page + its backdrop (Cyan)
        self.app.processEvents()
        w._on_catalogue_skill_chosen("send_email")
        self.app.processEvents()
        self.assertFalse(w.skills_detail_drawer.isHidden())
        # Theme change with NO show_page after it.
        qsettings_set("theme", "Light", settings=w.settings)
        w._apply_appearance()
        self.app.processEvents()
        page = w.pages.widget(3)
        bds = page.findChildren(HudBackdrop)
        self.assertEqual(len(bds), 1,
                         f"expected one Skills backdrop, got {len(bds)}")
        self.assertEqual(bds[0]._bg, "#F7F9FC",
                         f"backdrop still on the old theme: {bds[0]._bg}")
        # And the pixel: the backdrop itself paints the Light bg
        # (its own grab, not the window grab — the test harness
        # window composites its translucent frame dark offscreen,
        # so a window-level pixel reads dark even when every
        # painted layer is light; the backdrop grab is the honest
        # surface). Sampled at the centre, where the vignette
        # gradient is transparent — the edge blends round by a
        # unit. Pre-fix this is the Cyan #04090E.
        img = bds[0].grab().toImage()
        c = img.pixelColor(img.width() // 2, img.height() // 2)
        self.assertEqual((c.red(), c.green(), c.blue()),
                         (247, 249, 252),
                         "backdrop pixel "
                         f"{(c.red(), c.green(), c.blue())}")

    def test_filter_is_shared_between_catalogue_and_galaxy(self):
        w = self.window
        _show(w, 3)
        w.skills_catalog.set_filter("screenshot")
        w._set_skills_view("galaxy")
        self.app.processEvents()
        self.assertEqual(w.skills_galaxy_search.text(), "screenshot")
        self.assertTrue(w.skills_galaxy_container.isVisible())
        w._set_skills_view("list")
        self.app.processEvents()
        self.assertEqual(w.skills_catalog.search_input.text(),
                         "screenshot")

    def test_galaxy_star_opens_the_shared_drawer(self):
        # Galaxy looks round (2026-11-25): the permanent detail
        # card beside the map is gone — picking a star opens the
        # SAME shared drawer the catalogue rows open (one detail
        # builder, one open/close behaviour). Clearing the
        # selection closes it again, unless the drawer is pinned
        # ("Keep open").
        w = self.window
        _show(w, 3)
        w._set_skills_view("galaxy")
        self.app.processEvents()
        self.assertIsNotNone(w.skills_galaxy)
        w._on_galaxy_skill_chosen("take_screenshot")
        self.app.processEvents()
        self.assertFalse(w.skills_detail_drawer.isHidden())
        self.assertEqual(w.skills_detail_title.text(), "Screenshot")
        self.assertIn("Runs without asking",
                      w.skills_detail_reqs.text())
        w._on_galaxy_selection_cleared()
        self.app.processEvents()
        self.assertTrue(w.skills_detail_drawer.isHidden())
        # Pinned ("Keep open") the drawer survives the clear.
        w._on_galaxy_skill_chosen("take_screenshot")
        self.app.processEvents()
        w.skills_detail_drawer.set_pinned(True)
        w._on_galaxy_selection_cleared()
        self.app.processEvents()
        self.assertFalse(w.skills_detail_drawer.isHidden())
        w.skills_detail_drawer.set_pinned(False)
        w._on_skills_detail_close()
        self.assertTrue(w.skills_detail_drawer.isHidden())

    def test_galaxy_canvas_fills_the_container(self):
        # Galaxy looks round (2026-11-25): the canvas sat at its
        # 320 px minimum while the card took the right half and
        # ~420 px of dead space sat between them — an AlignLeft
        # on the galaxy's layout item meant the stretch never
        # reached it. The canvas now fills the container at 1280
        # and at 640.
        w = self.window
        _show(w, 3)
        w._set_skills_view("galaxy")
        self.app.processEvents()
        for width, height in ((1280, 800), (640, 500)):
            w.resize(width, height)
            self.app.processEvents()
            w._layout_skills_galaxy()
            self.app.processEvents()
            container = w.skills_galaxy_container
            galaxy = w.skills_galaxy
            canvas = galaxy.canvas2d
            self.assertGreaterEqual(
                galaxy.width(), container.width() - 20,
                f"galaxy {galaxy.width()}px in a "
                f"{container.width()}px container "
                f"at {width}x{height}")
            self.assertEqual(canvas.width(), galaxy.width(),
                             "canvas does not fill its widget")
        w.resize(1280, 800)
        self.app.processEvents()

    def test_galaxy_labels_whole_and_clusters_separate(self):
        # Galaxy looks round (2026-11-25): hub labels were eaten
        # by their own cluster's dots (the semi-transparent halo
        # let bright dots show through thin glyphs — "Desktop
        # contro", "3uilt-in") and 7 cluster pairs overlapped.
        # The ring is spread so no two cluster boxes touch, the
        # label plates are opaque, and the dots breathe.
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QFont, QFontMetrics
        from voice_assistant.gui.skills_galaxy_data import (
            _LABEL_HUB_PX, _plan_label_rects)
        w = self.window
        w.resize(1280, 800)
        self.app.processEvents()
        _show(w, 3)
        w._set_skills_view("galaxy")
        self.app.processEvents()
        canvas = w.skills_galaxy.canvas2d
        data = w.skills_galaxy.galaxy
        pos = canvas.node_positions()
        # Hub label rects, built exactly as the canvas builds
        # them (same font, same below/above candidates, same
        # planner), clamped into the canvas like the paint path.
        entries = []
        for hub in data.hubs:
            centre = canvas.world_to_screen(*pos[hub.id])
            r = canvas._display_radius(hub)
            font = QFont()
            font.setPixelSize(max(1, round(
                _LABEL_HUB_PX * canvas._font_scale)))
            # Fix 2 (2026-11-25): ink width, not advance — the
            # canvas builds its rects this way now (E-F202), and
            # the test's window must be the canvas's rect.
            # +8 (4 each side): +4 restored the "I" but not the
            # "l" — the LCD fringe carries the ink ~2 px past
            # boundingRect on each side.
            ink_w = float(QFontMetrics(font).boundingRect(
                hub.label).width())
            text_w = ink_w + 8.0
            rect = QRectF(centre.x() - text_w / 2.0,
                          centre.y() + r + 3.0, text_w, 16.0)
            alt = QRectF(centre.x() - text_w / 2.0,
                         centre.y() - r - 3.0 - 16.0, text_w, 16.0)
            entries.append({"id": hub.id, "priority": 1,
                            "degree": hub.count, "rect": rect,
                            "alt_rect": alt, "force": False,
                            "sticky": True})
        planned = _plan_label_rects(
            entries, bounds=QRectF(0.0, 0.0,
                                   float(canvas.width()),
                                   float(canvas.height())))
        clamped = {}
        for node_id, rect in planned.items():
            x = min(max(rect.x(), 0.0),
                    canvas.width() - rect.width())
            y = min(max(rect.y(), 0.0),
                    canvas.height() - rect.height())
            clamped[node_id] = QRectF(x, y, rect.width(),
                                      rect.height())
        self.assertEqual(len(clamped), len(data.hubs),
                         "every hub keeps a label")
        rects = list(clamped.values())
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                self.assertFalse(rects[i].intersects(rects[j]),
                                 "two hub labels overlap")
            self.assertTrue(0 <= rects[i].x()
                            and rects[i].right() <= canvas.width()
                            and 0 <= rects[i].y()
                            and rects[i].bottom() <= canvas.height(),
                            "label outside the canvas")
        # Pixels: no star may show through a label. The plate
        # behind each hub label is fully opaque (see _LABEL_HALO),
        # so a dot under it is invisible — but the check reads
        # the pixels, not the code. A naive "saturated pixel
        # inside the rect" count does NOT work here: Qt draws
        # the glyphs with subpixel antialiasing and the LCD
        # filter spreads mid-tone saturated fringe pixels ~1-2 px
        # around every stroke (the naive count reads ~800 both
        # before and after the opaque plate). So a pixel counts
        # only if it is dot-like — saturated mid-tone (chroma >
        # 80, all channels in (20, 200), which fringe never is)
        # — AND no glyph pixel (any channel > 150) sits within
        # 2 px of it: text fringe hugs the glyphs, a star dot
        # showing through the plate does not. Proved on the
        # probe: the pre-fix translucent plate reads 47 such
        # pixels in total, the opaque plate reads 2 (fringe
        # bleed at one label). The threshold (10) sits between
        # the two with room.
        img = canvas.grab().toImage()
        total = 0
        for node_id, rect in clamped.items():
            x0, x1 = int(rect.x()), int(rect.right())
            y0, y1 = int(rect.y()), int(rect.bottom())
            glyph = set()
            for px in range(x0, x1):
                for py in range(y0, y1):
                    c = img.pixelColor(px, py)
                    if max(c.red(), c.green(), c.blue()) > 150:
                        glyph.add((px, py))
            for px in range(x0, x1):
                for py in range(y0, y1):
                    c = img.pixelColor(px, py)
                    r, g, b = c.red(), c.green(), c.blue()
                    chroma = max(r, g, b) - min(r, g, b)
                    if not (chroma > 80 and max(r, g, b) < 200
                            and min(r, g, b) > 20):
                        continue
                    near_glyph = any(
                        (px + dx, py + dy) in glyph
                        for dx in (-2, -1, 0, 1, 2)
                        for dy in (-2, -1, 0, 1, 2))
                    if not near_glyph:
                        total += 1
        self.assertLessEqual(total, 10,
                             f"{total} star-coloured pixels show "
                             "through hub labels")
        # Cluster boxes (screen, stars + display radius) never touch.
        bboxes = {}
        for hub in data.hubs:
            xs, ys = [], []
            for n in data.stars:
                if n.group != hub.group:
                    continue
                sc = canvas.world_to_screen(*pos[n.id])
                sr = canvas._display_radius(n)
                xs += [sc.x() - sr, sc.x() + sr]
                ys += [sc.y() - sr, sc.y() + sr]
            hc = canvas.world_to_screen(*pos[hub.id])
            hr = canvas._display_radius(hub)
            xs += [hc.x() - hr, hc.x() + hr]
            ys += [hc.y() - hr, hc.y() + hr]
            bboxes[hub.group] = QRectF(min(xs), min(ys),
                                       max(xs) - min(xs),
                                       max(ys) - min(ys))
        groups = list(bboxes)
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                self.assertFalse(
                    bboxes[groups[i]].intersects(bboxes[groups[j]]),
                    f"clusters {groups[i]!r} and {groups[j]!r} overlap")
        # Dots breathe: every star's nearest neighbour (same
        # cluster, screen space) sits further than one star diameter.
        for hub in data.hubs:
            pts = [canvas.world_to_screen(*pos[n.id])
                   for n in data.stars if n.group == hub.group]
            if len(pts) < 2:
                continue
            star = next(n for n in data.stars
                        if n.group == hub.group)
            diam = 2.0 * canvas._display_radius(star)
            for i, p in enumerate(pts):
                nn = min(((p.x() - q.x()) ** 2
                          + (p.y() - q.y()) ** 2) ** 0.5
                         for j, q in enumerate(pts) if j != i)
                self.assertGreater(
                    nn, diam,
                    f"stars touch in {hub.group!r}: "
                    f"NN {nn:.1f}px vs diameter {diam:.1f}px")

    def test_galaxy_hub_labels_first_and_last_glyphs_painted(self):
        # Galaxy looks round fix 2 (2026-11-25): the labels test
        # above checks rect geometry and dot intrusion, but a
        # MISSING glyph contributes nothing to either — the
        # parent verification found "Desktop contro" (final "l"
        # absent) and "ntelligence" (initial "I" absent) in the
        # render while that test passed. Root cause (E-F202's
        # shape, which the Galaxy canvas never adopted):
        # drawText clips to the rect, and the drawn ink overhangs
        # horizontalAdvance, so an advance-wide rect cuts the
        # edge glyphs — thin ones ("I", "l") vanish.
        # This test reads the pixels: it rebuilds each hub label
        # rect exactly as the canvas does (ink width + 8 px,
        # same planner) and asserts the painted ink reaches to
        # within 3 px of each end of the rect. A whole label's
        # ink does (measured gaps 0–3 px on the fixed tree);
        # a clipped edge glyph pulls its side's gap to 4–7 px.
        # The first draft checked the rect's own first/last 3
        # columns, but that reads padding once the rect carries
        # its 4 px/side margin, and it passed on unfixed code
        # because the neighbouring glyph's ink falls inside a
        # 3-column window — so the check is the ink-to-rect-edge
        # gap, not a column window. Glyph ink is min(R, G, B) >
        # 60: glyph cores read ~133, the plate reads ~10, so 60
        # sits between them with room. The check names the
        # label, so a failure says which label lost a glyph.
        # Proved failing on unfixed code (names "Desktop
        # control" and "Intelligence" among the labels whose
        # fringes the narrow rects clip, and the two slid labels
        # whose planner windows diverge from the canvas's —
        # see the fix-2 report for the full pre-fix table).
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QFont, QFontMetrics
        from voice_assistant.gui.skills_galaxy_data import (
            _LABEL_HUB_PX, _plan_label_rects)
        w = self.window
        w.resize(1280, 800)
        self.app.processEvents()
        _show(w, 3)
        w._set_skills_view("galaxy")
        self.app.processEvents()
        canvas = w.skills_galaxy.canvas2d
        data = w.skills_galaxy.galaxy
        pos = canvas.node_positions()
        # Hub label rects, built exactly as the canvas builds
        # them (same font, same below/above candidates, same
        # planner), clamped into the canvas like the paint path.
        entries = []
        labels = {}
        for hub in data.hubs:
            centre = canvas.world_to_screen(*pos[hub.id])
            r = canvas._display_radius(hub)
            font = QFont()
            font.setPixelSize(max(1, round(
                _LABEL_HUB_PX * canvas._font_scale)))
            # Fix 2 (2026-11-25): ink width, not advance — the
            # canvas builds its rects this way now (E-F202), and
            # the test's window must be the canvas's rect.
            # +8 (4 each side): +4 restored the "I" but not the
            # "l" — the LCD fringe carries the ink ~2 px past
            # boundingRect on each side.
            ink_w = float(QFontMetrics(font).boundingRect(
                hub.label).width())
            text_w = ink_w + 8.0
            rect = QRectF(centre.x() - text_w / 2.0,
                          centre.y() + r + 3.0, text_w, 16.0)
            alt = QRectF(centre.x() - text_w / 2.0,
                         centre.y() - r - 3.0 - 16.0, text_w, 16.0)
            entries.append({"id": hub.id, "priority": 1,
                            "degree": hub.count, "rect": rect,
                            "alt_rect": alt, "force": False,
                            "sticky": True, "group": hub.group})
            labels[hub.id] = hub.label
        # Hunt fix (c) item 5: the canvas passes every node disc
        # as an obstacle (owner-filtered by group) — the test's
        # window must be the canvas's plan, so it builds the same
        # obstacles (or the flipped "Desktop control" label's rect
        # diverges from the canvas's and the ink check reads the
        # wrong band).
        obstacles = [
            (canvas.world_to_screen(*pos[node.id]),
             canvas._display_radius(node), node.group)
            for node in data.stars + data.hubs]
        planned = _plan_label_rects(
            entries, obstacles=obstacles,
            bounds=QRectF(0.0, 0.0,
                          float(canvas.width()),
                          float(canvas.height())))
        img = canvas.grab().toImage()

        def _has_ink(px, y0, y1):
            for py in range(y0, y1 + 1):
                c = img.pixelColor(px, py)
                if min(c.red(), c.green(), c.blue()) > 60:
                    return True
            return False

        # The rect the canvas builds is ink-wide with padding
        # (E-F202 + the fix-2 LCD margin), so the glyphs do not
        # sit at the rect's own edge columns — the brief's
        # "first/last 3 columns of the rect" reads padding, not
        # text. What the defect actually moves is the painted
        # ink span: a missing edge glyph pulls the ink's edge
        # ~2 px in from where the font puts it, while a whole
        # label's ink reaches to within ~1 px of the rect's
        # padding edge. So assert the ink span against the
        # rect, allowing the 4 px of padding plus 1 px of
        # rounding — and no more.
        failures = []
        for node_id, rect in planned.items():
            x = min(max(rect.x(), 0.0),
                    canvas.width() - rect.width())
            y = min(max(rect.y(), 0.0),
                    canvas.height() - rect.height())
            rect = QRectF(x, y, rect.width(), rect.height())
            x0, x1 = int(rect.x()), int(rect.right())
            y0, y1 = int(rect.y()), int(rect.bottom())
            label = labels[node_id]
            _cols = [px for px in range(x0, x1 + 1)
                     if _has_ink(px, y0, y1)]
            _lo = min(_cols) if _cols else None
            _hi = max(_cols) if _cols else None
            if _lo is None or _lo - x0 > 3:
                failures.append(
                    f"{label!r}: first glyph not painted "
                    f"(ink starts {_lo}, rect starts {x0})")
            if _hi is None or x1 - _hi > 3:
                failures.append(
                    f"{label!r}: last glyph not painted "
                    f"(ink ends {_hi}, rect ends {x1})")
        self.assertTrue(not failures, "; ".join(failures))

    def test_galaxy_plugins_fit_without_scrolling_at_1280(self):
        # Galaxy looks round (2026-11-25): "Your plugins" sat
        # half-cut at the fold — the tall permanent card forced
        # the page past the viewport. The page now fits at 1280:
        # no vertical scroll is needed.
        w = self.window
        w.resize(1280, 800)
        self.app.processEvents()
        _show(w, 3)
        w._set_skills_view("galaxy")
        self.app.processEvents()
        scroll = w.pages.widget(3)
        self.assertEqual(scroll.verticalScrollBar().maximum(), 0,
                         "Skills page scrolls at 1280 in Galaxy view")

    def test_refresh_preserves_view_filter_and_count(self):
        w = self.window
        _show(w, 3)
        w.skills_catalog.set_filter("screenshot")
        self.app.processEvents()
        w._refresh_skills_page()
        self.app.processEvents()
        self.assertEqual(w.skills_catalog.search_input.text(),
                         "screenshot")
        self.assertIn("of 772 shown",
                      w.skills_count_label.text())

    def test_sidebar_has_fifteen_buttons_with_skills(self):
        # INTENTIONAL UPDATE (UI Phase 5, 2026-11-27): fifteen
        # buttons — Notes, Finance and Devices join as first-class
        # workspaces before Settings under the WORKSPACES section.
        w = self.window
        _show(w, 3)
        self.assertEqual(len(w.nav_buttons), 15)
        tooltips = [b.toolTip() for b in w.nav_buttons]
        self.assertEqual(
            tooltips,
            ["Home", "Activity", "Memory", "Skills", "System Pulse",
             "Senses", "Doctor", "Calendar", "Inbox", "Contacts",
             "Calls", "Notes", "Finance", "Devices", "Settings"])

    def test_badges_hide_at_zero_and_cap_at_99(self):
        from gui.sidebar_badges import badge_text
        w = self.window
        _show(w, 3)
        w._views_inbox_unread_count = 0
        w._refresh_sidebar_badges()
        self.assertTrue(w._badge_inbox.isHidden())
        w._views_inbox_unread_count = 3
        w._refresh_sidebar_badges()
        self.assertEqual(w._badge_inbox.text(), "3")
        self.assertFalse(w._badge_inbox.isHidden())
        w._views_inbox_unread_count = 150
        w._refresh_sidebar_badges()
        self.assertEqual(w._badge_inbox.text(), "99+")
        self.assertEqual(badge_text(150), "99+")

    def test_badge_equals_page_for_a_verifying_approval(self):
        # P2-02: a verifying record with a pending approval is
        # the case the old page count missed (it counted paused
        # live records from all_records()) and the old headline
        # over-counted (every record with any truthy
        # pending_approval). The badge and the page both read
        # pending_approvals_count() now, so they agree — and a
        # paused GOAL with a pending approval does not move
        # either (goals are not coordinator tasks).
        from voice_assistant.core.gui_facade import TaskLedger
        w = self.window
        ledger = TaskLedger()
        ledger._ensure_loaded()
        ledger._records["t-p2-badge"] = {
            "task_id": "t-p2-badge", "title": "P2 badge probe",
            "state": "verifying",
            "steps": [{"tool": "menu_action", "label": "Close tab"}],
            "outcomes": [],
            "pending_approval": {
                "tool": "menu_action", "label": "Close tab",
                "sentence": "needs approval",
                "confirm_arg": "confirmed"},
        }
        ledger._records["t-p2-goal"] = {
            "task_id": "t-p2-goal", "title": "P2 goal probe",
            "kind": "goal", "state": "paused",
            "steps": [{"tool": "menu_action", "label": "Close tab"}],
            "outcomes": [],
            "pending_approval": {
                "tool": "menu_action", "label": "Close tab",
                "sentence": "needs approval",
                "confirm_arg": "confirmed"},
        }
        ledger._save()
        try:
            _show(w, 1)
            self.assertIn("(1)", w._activity_pending_btn.text())
            w._refresh_sidebar_badges()
            self.assertEqual(w._badge_activity.text(), "1")
            self.assertFalse(w._badge_activity.isHidden())
        finally:
            ledger._ensure_loaded()
            ledger._records.pop("t-p2-badge", None)
            ledger._records.pop("t-p2-goal", None)
            ledger._save()


@unittest.skipUnless(_QT, "Qt unavailable")
class TestPaletteWindow(P2WindowBase):
    """The palette on the real window: count row, Browse row,
    goal lane, recents, private mode."""

    def _palette(self):
        from gui.window_widgets_palette import CommandPalette
        return CommandPalette(parent=self.window)

    def test_skills_rows_carry_the_live_count(self):
        pal = self._palette()
        labels = [e.get("label") for e in pal._commands]
        self.assertIn("Go to Skills — 772 capabilities", labels)
        browse = [e for e in pal._commands
                  if e.get("label") == "Browse all capabilities"]
        self.assertEqual(len(browse), 1)
        self.assertEqual(browse[0]["action_id"], "page_3_browse")
        self.assertEqual(browse[0]["kind"], "PAGE")

    def test_goal_search_surfaces_send_email_first(self):
        pal = self._palette()
        pal._apply_filter("send an email")
        self.app.processEvents()
        self.assertTrue(pal._visible_labels)
        self.assertEqual(pal._visible_labels[0], "Send email")

    def test_recents_round_trip_and_no_duplicate(self):
        from gui.palette_recents import load_recents
        w = self.window
        pal = self._palette()
        pal._apply_filter("")
        self.app.processEvents()
        row = pal._visible_labels.index("Go to Home")
        pal._run_row(row)
        self.app.processEvents()
        self.assertEqual(load_recents(settings=w.settings), [
            {"action_id": "page_0", "label": "Go to Home"}])
        pal2 = self._palette()
        pal2._apply_filter("")
        self.app.processEvents()
        self.assertFalse(pal2._recents_header.isHidden())
        self.assertEqual(pal2._visible_labels[0], "Go to Home")
        self.assertEqual(pal2._visible_labels.count("Go to Home"), 1)

    def test_recents_hidden_and_not_recorded_in_private_mode(self):
        from gui.palette_recents import load_recents
        w = self.window
        orig = w._mem_private_on
        w._mem_private_on = lambda: True
        try:
            pal = self._palette()
            pal._apply_filter("")
            self.app.processEvents()
            self.assertTrue(pal._recents_header.isHidden())
            row = pal._visible_labels.index("Go to Home")
            pal._run_row(row)
            self.app.processEvents()
            self.assertEqual(load_recents(settings=w.settings), [])
        finally:
            w._mem_private_on = orig

    def test_browse_row_opens_the_skills_page(self):
        pal = self._palette()
        pal._apply_filter("Browse all capabilities")
        self.app.processEvents()
        self.assertIn("Browse all capabilities",
                      pal._visible_labels)
        row = pal._visible_labels.index("Browse all capabilities")
        pal._run_row(row)
        self.app.processEvents()
        self.assertEqual(self.window.pages.currentIndex(), 3)
        self.assertTrue(self.window.skills_cat_card.isVisible())


if __name__ == "__main__":
    unittest.main()
