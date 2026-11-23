"""Stage 0 features batch 1 (Group D, items 15–18) — the views:
Calendar, Inbox, Contacts list, Call history.

The pure tests run on both interpreters: the new core reads degrade
to empty when Google isn't connected, the contact list groups by
person, and the call ledger carries the agent's name. The AST tests
pin the wiring (mixin, sidebar map, palette rows) without Qt. The
window tests need Qt (the real MainWindow offscreen, via the chat-ui
WindowBase harness); they prove the four pages build, show, and
refresh — the connect line when Google is off, grouped contacts and
a recorded call when the stores have rows.
"""

import ast
import datetime
import importlib
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TREE = Path(__file__).resolve().parent.parent
if str(TREE) not in sys.path:
    sys.path.insert(0, str(TREE))


def _install_voice_assistant_shim():
    """The test_journey_gui_path shim (test_boot_sequence_wiring):
    ``voice_assistant`` as a namespace over the tree root, so the
    pure tests can import voice_assistant.core.* on the sys
    interpreter (no .pkgroot exists in this tree)."""
    def _pkg(name, path):
        if name in sys.modules:
            return
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod

    _pkg("voice_assistant", str(TREE))
    _pkg("voice_assistant.core", str(TREE / "core"))
    _pkg("voice_assistant.core.voice", str(TREE / "core" / "voice"))
    _pkg("voice_assistant.gui", str(TREE / "gui"))
    if "voice_assistant.core.config" not in sys.modules:
        importlib.import_module("voice_assistant.core.config")


_install_voice_assistant_shim()

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    WindowBase = object  # type: ignore[assignment]
    _QT = False


def _gui_source(name):
    return (TREE / "gui" / name).read_text(encoding="utf-8")


def _core(name):
    import importlib
    return importlib.import_module(f"voice_assistant.core.{name}")


class TestViewsCoreReads(unittest.TestCase):
    def test_events_in_range_empty_when_not_connected(self):
        # No Google sign-in in the lab — the view's month fetch
        # degrades to an empty list, never a raise.
        google_services = _core("google_services")
        self.assertTrue(callable(google_services.events_in_range))
        today = datetime.date.today()
        self.assertEqual(
            google_services.events_in_range(
                today, today + datetime.timedelta(days=30)),
            [])

    def test_recent_messages_empty_when_not_connected(self):
        google_services = _core("google_services")
        self.assertTrue(callable(google_services.recent_messages))
        self.assertEqual(google_services.recent_messages(20), [])

    def test_message_body_empty_when_not_connected(self):
        google_services = _core("google_services")
        self.assertTrue(callable(google_services.message_body))
        self.assertEqual(google_services.message_body(""), "")
        self.assertEqual(google_services.message_body("no-such-id"), "")

    def test_facade_resolves_view_sources(self):
        # gui never imports core directly — the views read through
        # these facade entries.
        facade = _core("gui_facade")
        self.assertTrue(callable(
            facade.skills_admin_schedules.list_contacts))
        self.assertTrue(callable(facade.voice_agent.VoiceAgentStore))
        for name in ("events_in_range", "recent_messages", "message_body"):
            self.assertTrue(callable(getattr(facade.google_services, name)),
                            f"google_services.{name} missing from facade")

    def test_list_contacts_groups_by_person(self):
        schedules = _core("skills_admin_schedules")
        person = "Stage0f1 Test Person"
        other = "Stage0f1 Other Person"
        try:
            schedules.contact_card("save", person, "phone: +44 7000 000001")
            schedules.contact_card("save", person, "email: f1@example.test")
            schedules.contact_card("save", other, "phone: +44 7000 000002")
            grouped = schedules.list_contacts()
            by_name = {p["person"]: p["details"] for p in grouped}
            self.assertEqual(by_name[person],
                             ["email: f1@example.test",
                              "phone: +44 7000 000001"])
            self.assertEqual(by_name[other], ["phone: +44 7000 000002"])
            names = [p["person"] for p in grouped]
            self.assertEqual(names, sorted(names, key=str.lower))
        finally:
            # Remove only the rows this test wrote.
            try:
                from voice_assistant.core.memory import memory_files
                entries, _broken = schedules._load_contacts()
                for entry in entries:
                    if entry.get("person") in (person, other):
                        memory_files.delete_fact("contact", entry["_key"])
            except Exception:  # noqa: BLE001 — runner restores data anyway
                pass

    def test_list_calls_carries_agent_name(self):
        # A temp store — this leg touches no live data at all.
        voice_agent = _core("voice_agent")
        with tempfile.TemporaryDirectory(prefix="stage0f1_calls_") as tmp:
            store = voice_agent.VoiceAgentStore(
                path=Path(tmp) / "voice_agent.db")
            agent = store.add_agent(
                "Stage0f1 Agent", "Hello", None, None, None)
            self.assertIsNotNone(agent)
            started = time.time() - 65
            store.record_call("in", "+44 7000 000001", agent["id"],
                              started, started + 65, "answered", [])
            calls = store.list_calls(20)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["agent"], "Stage0f1 Agent")
            self.assertEqual(calls[0]["direction"], "in")
            self.assertEqual(calls[0]["number"], "+44 7000 000001")


class TestViewsWiringAST(unittest.TestCase):
    """The wiring, pinned without Qt — the prescratch tree fails
    every one of these (no mixin file, no facade entries, no
    sidebar defs, no palette rows)."""

    def test_mixin_defines_four_pages(self):
        tree = ast.parse(_gui_source("window_pages_views.py"))
        mixin = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "PagesViewsMixin")
        methods = {node.name for node in mixin.body
                   if isinstance(node, ast.FunctionDef)}
        for name in ("_build_calendar_page", "_build_inbox_page",
                     "_build_contacts_page", "_build_calls_page",
                     "_refresh_calendar_page", "_refresh_inbox_page",
                     "_refresh_contacts_page", "_refresh_calls_page"):
            self.assertIn(name, methods, f"PagesViewsMixin lacks {name}")

    def test_pages_mixin_composes_views(self):
        tree = ast.parse(_gui_source("window_pages.py"))
        composed = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PagesMixin")
        bases = [b.attr if isinstance(b, ast.Attribute) else b.id
                 for b in composed.bases]
        self.assertIn("PagesViewsMixin", bases)

    def test_sidebar_maps_views_to_stack_8_to_11(self):
        tree = ast.parse(_gui_source("window_shell_sidebar.py"))
        func = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_build_sidebar")
        assigns = {}
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigns[target.id] = node.value
                    elif isinstance(target, ast.Attribute):
                        # self._nav_page_indices / self._nav_icon_names
                        assigns[target.attr] = node.value
        defs = ast.literal_eval(assigns["_nav_defs"])
        labels = [label for label, _, _ in defs]
        for label in ("Calendar", "Inbox", "Contacts", "Calls"):
            self.assertIn(label, labels)
        pages = {label: page for label, _, page in defs}
        self.assertEqual(
            (pages["Calendar"], pages["Inbox"],
             pages["Contacts"], pages["Calls"]),
            (8, 9, 10, 11))
        indices = [e.value for e in assigns["_nav_page_indices"].elts
                   if isinstance(e, ast.Constant)]
        self.assertEqual(indices, [0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 4])

    def test_show_page_refreshes_each_view(self):
        src = _gui_source("window_chat_nav.py")
        for index, refresher in ((8, "_refresh_calendar_page"),
                                 (9, "_refresh_inbox_page"),
                                 (10, "_refresh_contacts_page"),
                                 (11, "_refresh_calls_page")):
            self.assertIn(f"if index == {index}:", src)
            self.assertIn(refresher, src)

    def test_palette_rows_open_each_view(self):
        tree = ast.parse(_gui_source("window_widgets_palette.py"))
        func = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_build_commands")
        rows = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name) \
                    and node.func.id == "go" \
                    and len(node.args) == 2 \
                    and all(isinstance(a, ast.Constant)
                            for a in node.args):
                rows.add((node.args[0].value, node.args[1].value))
        for expected in ((8, "Go to Calendar"), (9, "Go to Inbox"),
                         (10, "Go to Contacts"), (11, "Go to Calls")):
            self.assertIn(expected, rows, f"palette row {expected} missing")


@unittest.skipUnless(_QT, "Qt unavailable")
class TestViewsWindow(WindowBase):
    def test_stack_has_twelve_pages_and_eleven_nav_buttons(self):
        # Stage 0 features batch 5: the labels are a comfort-width
        # property — offscreen the window opens at 760 (compact),
        # where the sidebar is the 64 px icon rail and the labels
        # hide by design — so establish 1280 first.
        self.window.resize(1280, 800)
        self.app.processEvents()
        self.window._fix_chat_width()
        self.app.processEvents()
        self.assertEqual(self.window.pages.count(), 12)
        labels = [btn.text() for btn in self.window.nav_buttons]
        self.assertEqual(labels, ["Home", "Activity", "Memory",
                                  "System Pulse", "Senses", "Doctor",
                                  "Calendar", "Inbox", "Contacts",
                                  "Calls", "Settings"])

    def test_show_each_view_checks_its_button(self):
        # Stage 0 features batch 5: the checked button's label
        # reads only at comfort width (hidden on the compact
        # rail) — establish 1280 first, as above.
        self.window.resize(1280, 800)
        self.app.processEvents()
        self.window._fix_chat_width()
        self.app.processEvents()
        for index in (8, 9, 10, 11):
            self.window._show_page(index)
            self.app.processEvents()
            self.assertEqual(self.window.pages.currentIndex(), index)
            checked = [btn.text() for btn in self.window.nav_buttons
                       if btn.isChecked()]
            self.assertEqual(len(checked), 1)
            self.assertEqual(
                checked[0],
                ["Calendar", "Inbox", "Contacts", "Calls"][index - 8])

    def test_calendar_weekday_header_is_dark(self):
        # The weekday-name header is painted from the weekday text
        # formats, not the palette or the stylesheet — a bare
        # calendar renders Mon–Fri invisible (light text on a
        # white header). Pin the explicit dark format.
        from PySide6.QtCore import Qt
        from voice_assistant.gui.style import SURFACE_2, TEXT
        self.window._show_page(8)
        self.app.processEvents()
        fmt = self.window._views_calendar.weekdayTextFormat(Qt.Monday)
        self.assertEqual(
            fmt.foreground().color().name(), TEXT.lower())
        self.assertEqual(
            fmt.background().color().name(), SURFACE_2.lower())

    def test_calendar_page_shows_connect_line_when_offline(self):
        self.window._show_page(8)
        self.app.processEvents()
        upcoming = self.window._views_calendar_upcoming
        self.assertGreaterEqual(upcoming.count(), 1)
        self.assertIn("Connect Google", upcoming.item(0).text())
        # The month grid itself builds (QCalendarWidget, first use).
        self.assertIsNotNone(self.window._views_calendar)
        self.assertEqual(
            self.window._views_calendar.selectedDate().toPython(),
            datetime.date.today())

    def test_inbox_page_shows_connect_line_when_offline(self):
        self.window._show_page(9)
        self.app.processEvents()
        listing = self.window._views_inbox_list
        self.assertGreaterEqual(listing.count(), 1)
        self.assertIn("Connect Google", listing.item(0).text())

    def test_contacts_page_groups_and_shows_details(self):
        # WindowBase stubs core.memory.memory, so the helpers'
        # _store() seam is pointed at the window's real temp
        # store for this test (the seam's documented purpose:
        # "Tests substitute a temp MemoryStore by replacing this
        # function").
        from voice_assistant.core import (
            skills_admin_helpers, skills_admin_schedules)
        person = "Stage0f1 Window Person"
        orig_store = skills_admin_helpers._store
        skills_admin_helpers._store = lambda: self._store
        try:
            skills_admin_schedules.contact_card(
                "save", person, "phone: +44 7000 000003")
            skills_admin_schedules.contact_card(
                "save", person, "email: win@example.test")
            self.window._show_page(10)
            self.app.processEvents()
            listing = self.window._views_contacts_list
            names = [listing.item(i).text()
                     for i in range(listing.count())]
            self.assertIn(person, names)
            item = next(listing.item(i) for i in range(listing.count())
                        if listing.item(i).text() == person)
            self.window._on_views_contact_picked(item)
            body = self.window._views_contacts_body.toPlainText()
            self.assertIn("phone: +44 7000 000003", body)
            self.assertIn("email: win@example.test", body)
        finally:
            skills_admin_helpers._store = orig_store

    def test_calls_page_shows_recorded_call(self):
        from voice_assistant.core import voice_agent
        store = voice_agent.VoiceAgentStore()
        # The ledger may already hold rows (a render tree seeds
        # them); the new call lands first (newest first) and the
        # row count grows by exactly one.
        before = len(store.list_calls(20))
        agent = store.add_agent(
            "Stage0f1 Window Agent", "Hello", None, None, None)
        self.assertIsNotNone(agent)
        started = time.time() - 65
        store.record_call("out", "+44 7000 000004", agent["id"],
                          started, started + 65, "answered", [])
        try:
            self.window._show_page(11)
            self.app.processEvents()
            table = self.window._views_calls_table
            self.assertEqual(table.rowCount(), before + 1)
            cells = [table.item(0, col).text() for col in range(6)]
            self.assertEqual(cells[1], "Outgoing")
            self.assertEqual(cells[2], "+44 7000 000004")
            self.assertEqual(cells[3], "Stage0f1 Window Agent")
            self.assertEqual(cells[4], "1m 05s")
            self.assertEqual(cells[5], "answered")
        finally:
            store.conn.execute(
                "DELETE FROM calls WHERE number = '+44 7000 000004'")
            store.conn.execute(
                "DELETE FROM agents WHERE name = 'Stage0f1 Window Agent'")
            store.conn.commit()

    def test_palette_rows_open_each_view(self):
        from voice_assistant.gui.window_widgets_palette import CommandPalette
        palette = CommandPalette(self.window)
        try:
            rows = {row["label"]: row
                    for row in palette._build_commands()}
            for label, index in (("Go to Calendar", 8),
                                 ("Go to Inbox", 9),
                                 ("Go to Contacts", 10),
                                 ("Go to Calls", 11)):
                self.assertIn(label, rows)
                rows[label]["action"]()
                self.app.processEvents()
                self.assertEqual(self.window.pages.currentIndex(), index)
        finally:
            palette.close()


if __name__ == "__main__":
    unittest.main()
