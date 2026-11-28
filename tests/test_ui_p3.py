"""UI Phase 3 — Productivity suite (2026-11-25).

Pins the four workspaces (Calendar, Inbox, Contacts, Calls) as
usable workspaces, not read-only views: each advertised primary
job passes an end-to-end interaction test against faked Google
seams, a temp contacts store and a temp calls ledger (the
dependency gates proved the real seams first; the GUI tests
prove the wiring).

Qt only for the GUI tests: without PySide6 every GUI test skips
(the test_chat_ui / test_ui_p1 / test_ui_p2 shape). The pure
helper tests live in the GUI module, so they are Qt-gated too and
run under the Qt interpreter alongside the GUI tests.
"""
import datetime
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


def _pump():
    if _QT:
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance()
        if _app is not None:
            _app.processEvents()


class P3WindowBase(WindowBase):
    """WindowBase with the stubs enriched (the P2WindowBase shape)
    plus the Phase 3 fakes: a fake Google service on the real
    google_services module, a temp calls ledger behind the
    VoiceAgentStore name, and the temp memory store behind the
    contacts _store seam. The window is built first (real Google
    module, not connected), the fakes land before any workspace
    page is shown."""

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
        self._p3_restores = []
        self._install_fake_google()
        self._install_temp_calls()
        self._install_temp_contacts()
        self._install_recording_scheduler()
        try:
            self.window.settings.remove("calendar/view")
            self.window.settings.sync()
        except Exception:  # noqa: BLE001 — isolation is best-effort
            pass

    def tearDown(self):
        for restore in reversed(self._p3_restores):
            try:
                restore()
            except Exception:  # noqa: BLE001 — restore is best-effort
                pass
        self._p3_restores = []
        try:
            self.window.settings.remove("calendar/view")
            self.window.settings.sync()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._calls_store.close()
        except Exception:  # noqa: BLE001
            pass
        super().tearDown()

    # -- fake Google -------------------------------------------------
    def _install_fake_google(self):
        import voice_assistant.core.google_services as gs
        self._gs = gs
        self._gs_calls = []
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)

        def _dt(day, hour, minute=0):
            return datetime.datetime(day.year, day.month, day.day,
                                     hour, minute)

        self._fake_events = [
            {"id": "evt-1", "title": "Dentist",
             "start": _dt(today, 10), "location": "Clinic"},
            {"id": "evt-2", "title": "Standup",
             "start": _dt(today, 10)},
            {"id": "evt-3", "title": "All-day planning",
             "start": _dt(today, 0), "all_day": True},
            {"id": "evt-4", "title": "Tomorrow sync",
             "start": _dt(tomorrow, 9)},
        ]
        self._fake_messages = [
            {"id": "m-1", "sender": "Ada Lovelace",
             "subject": "Q3 numbers", "when": "09:12",
             "snippet": "The Q3 numbers are in."},
            {"id": "m-2", "sender": "bob@example.test",
             "subject": "Re: venue", "when": "08:40",
             "snippet": "Venue confirmed."},
        ]
        self._fake_snoozed = [
            {"message_id": "m-3", "sender": "Carol",
             "subject": "Snoozed mail",
             "snooze_until": (datetime.datetime.now()
                              + datetime.timedelta(days=1)).isoformat()},
        ]
        self._fake_body = (
            "First sentence here. Second sentence follows. "
            "Third sentence closes it.")
        calls = self._gs_calls
        events = self._fake_events
        messages = self._fake_messages
        snoozed = self._fake_snoozed
        probe = "__VYUK_ATTACHMENT_LISTING_PROBE__"
        self._attach_probe = probe

        def _is_connected():
            return True

        def _events_in_range(start, end):
            calls.append(("events_in_range", start, end))
            lo = start.date() if hasattr(start, "date") else start
            hi = end.date() if hasattr(end, "date") else end
            return [dict(e) for e in events
                    if lo <= e["start"].date() <= hi]

        def _create_event(title="", start="", end="", **kw):
            calls.append(("create_event", title, start, end, kw))
            new_id = f"evt-{100 + len(calls)}"
            try:
                parsed = datetime.datetime.fromisoformat(start)
            except Exception:  # noqa: BLE001 — keep the row anyway
                parsed = _dt(today, 10)
            events.append({"id": new_id, "title": title,
                           "start": parsed,
                           "location": kw.get("location", "")})
            return f"Event created — {title} (event id {new_id})"

        def _cancel_event(event="", date="", event_id="", **kw):
            from voice_assistant.core.tool_outcome import Outcome
            calls.append(("cancel_event", event_id))
            for idx, ev in enumerate(events):
                if str(ev.get("id")) == str(event_id):
                    del events[idx]
                    return "Event cancelled."
            return Outcome(False, "That event is already gone.",
                           kind="failed")

        def _reschedule_event(event="", date="", event_id="",
                              new_start="", new_end="", **kw):
            calls.append(("reschedule_event", event_id,
                          new_start, new_end))
            for ev in events:
                if str(ev.get("id")) == str(event_id):
                    try:
                        ev["start"] = datetime.datetime.fromisoformat(
                            new_start)
                    except Exception:  # noqa: BLE001
                        pass
                    return f"Event moved to {new_start}."
            from voice_assistant.core.tool_outcome import Outcome
            return Outcome(False, "That event is already gone.",
                           kind="failed")

        def _find_free_slots(date="", duration_minutes=60, **kw):
            calls.append(("find_free_slots", date, duration_minutes))
            return (f"Free on {date} for {duration_minutes} minutes: "
                    "08:00–20:00.")

        def _recent_messages(limit=20):
            calls.append(("recent_messages", limit))
            return [dict(m) for m in messages[:limit]]

        def _search_email(query, limit=6, **kw):
            calls.append(("search_email", query, limit))
            if query == "is:unread":
                return ("• Ada Lovelace — Q3 numbers (09:12) [id: m-1]")
            if "venue" in query.lower():
                return ("• bob@example.test — Re: venue (08:40) "
                        "[id: m-2]")
            return ""

        def _message_body(message_id):
            calls.append(("message_body", message_id))
            return self._fake_body if message_id == "m-1" else ""

        def _download_attachment(message_id="", filename="", **kw):
            calls.append(("download_attachment", message_id, filename))
            if filename == probe:
                return ("Attachments: 'notes.pdf' (12 KB), "
                        "'photo.png' (1.2 MB).")
            return f"Downloaded {filename}."

        def _send_email(to="", subject="", body="", attachments=None,
                        **kw):
            calls.append(("send_email", to, subject, body,
                          list(attachments or [])))
            return f"Email sent to {to} (Gmail message id msg-1)"

        def _snooze_email(message_id="", until="", **kw):
            from voice_assistant.core.tool_outcome import Outcome
            calls.append(("snooze_email", message_id, until))
            if not until:
                snoozed[:] = [r for r in snoozed
                              if r.get("message_id") != message_id]
                return "Brought back."
            row = next((r for r in snoozed
                        if r.get("message_id") == message_id), None)
            if row is None:
                row = {"message_id": message_id, "sender": "Ada Lovelace",
                       "subject": "Q3 numbers", "snooze_until": until}
                snoozed.append(row)
            else:
                row["snooze_until"] = until
            return f"Snoozed until {until}."

        def _load_snoozed(path=None):
            return [dict(r) for r in snoozed]

        def _active_snooze_ids(path=None):
            return {r.get("message_id") for r in snoozed}

        def _unread_count(limit=50):
            return 1

        def _load_credentials():
            return object()

        def _granted_scopes(creds):
            return {gs.CALENDAR_EVENTS_SCOPE, gs.GMAIL_SEND_SCOPE}

        fakes = {
            "is_connected": _is_connected,
            "events_in_range": _events_in_range,
            "create_event": _create_event,
            "cancel_event": _cancel_event,
            "reschedule_event": _reschedule_event,
            "find_free_slots": _find_free_slots,
            "recent_messages": _recent_messages,
            "search_email": _search_email,
            "message_body": _message_body,
            "download_attachment": _download_attachment,
            "send_email": _send_email,
            "snooze_email": _snooze_email,
            "_load_snoozed": _load_snoozed,
            "_active_snooze_ids": _active_snooze_ids,
            "unread_count": _unread_count,
            "_load_credentials": _load_credentials,
            "_granted_scopes": _granted_scopes,
        }
        self._gs_originals = {}
        for name, fn in fakes.items():
            self._gs_originals[name] = getattr(gs, name, None)
            setattr(gs, name, fn)

        def _restore_gs():
            for name, orig in self._gs_originals.items():
                if orig is None:
                    try:
                        delattr(gs, name)
                    except AttributeError:
                        pass
                else:
                    setattr(gs, name, orig)
        self._p3_restores.append(_restore_gs)

    def _set_google_connected(self, connected):
        self._gs.is_connected = (lambda: True) if connected \
            else (lambda: False)

    def _set_google_scopes(self, scopes):
        self._gs._granted_scopes = lambda creds: set(scopes)

    # -- temp calls ledger -------------------------------------------
    def _install_temp_calls(self):
        import voice_assistant.core.voice_agent as va
        self._calls_tmp = tempfile.mkdtemp(prefix="p3_calls_")
        self._calls_store = va.VoiceAgentStore(
            str(pathlib.Path(self._calls_tmp) / "calls.db"))
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        self._calls_store.record_call(
            "out", "+44 7000 000001", None,
            f"{today.isoformat()} 10:00", f"{today.isoformat()} 10:05",
            "completed",
            [{"step": 1, "kind": "say", "detail": "Hello"},
             {"step": 2, "kind": "gather",
              "detail": "Asked for the time"}])
        self._calls_store.record_call(
            "in", "+44 7000 000099", None,
            f"{today.isoformat()} 09:00", f"{today.isoformat()} 09:02",
            "answered", [])
        self._calls_store.record_call(
            "out", "+44 7000 000002", None,
            f"{yesterday.isoformat()} 15:00",
            f"{yesterday.isoformat()} 15:01",
            "completed", [])
        self._calls_rows = self._calls_store.list_calls(limit=20)
        store = self._calls_store
        orig = va.VoiceAgentStore
        va.VoiceAgentStore = lambda path=None: store

        def _restore_va():
            va.VoiceAgentStore = orig
        self._p3_restores.append(_restore_va)

    # -- temp contacts store -----------------------------------------
    def _install_temp_contacts(self):
        from voice_assistant.core import skills_admin_helpers
        from voice_assistant.core import skills_admin_schedules
        # skills_admin_schedules imported _store BY VALUE from the
        # helpers module, so substituting the helpers binding alone
        # never reaches delete_contact_detail (it reads the
        # schedules module's own _store). Substitute both.
        orig = skills_admin_helpers._store
        orig_sched = skills_admin_schedules._store
        skills_admin_helpers._store = lambda: self._store
        skills_admin_schedules._store = lambda: self._store
        skills_admin_schedules.contact_card(
            "save", "Ada Lovelace", "phone: +44 7000 000001")
        skills_admin_schedules.contact_card(
            "save", "Ada Lovelace", "email: ada@example.test")
        skills_admin_schedules.contact_card(
            "save", "Ada Lovelace", "address: 12 Analytical Way")
        skills_admin_schedules.contact_card(
            "save", "Bob", "phone: +44 7000 000002")

        def _restore_contacts():
            skills_admin_helpers._store = orig
            skills_admin_schedules._store = orig_sched
        self._p3_restores.append(_restore_contacts)

    # -- recording scheduler ------------------------------------------
    def _install_recording_scheduler(self):
        import voice_assistant.core.scheduler as sched_mod
        self._scheduler_calls = []
        calls = self._scheduler_calls

        class _RecordingScheduler:
            def add_reminder(self, label="", fire_at=None, message="",
                             repeat=False, **kw):
                calls.append(("add_reminder", label, fire_at, message))
                return True

        orig = sched_mod.scheduler
        sched_mod.scheduler = _RecordingScheduler()

        def _restore_sched():
            sched_mod.scheduler = orig
        self._p3_restores.append(_restore_sched)

    # -- helpers ------------------------------------------------------
    def _allow_writes(self, allow=True):
        self.window._views_confirm_write = (
            lambda *a, **k: bool(allow))

    def _drawer_button(self, drawer, text):
        from PySide6.QtWidgets import QPushButton
        for btn in drawer.findChildren(QPushButton):
            if btn.text() == text:
                return btn
        return None

    def _drawer_labels(self, drawer):
        from PySide6.QtWidgets import QLabel
        return [lbl.text() for lbl in drawer.findChildren(QLabel)]

    def _drawer_line_edits(self, drawer):
        from PySide6.QtWidgets import QLineEdit
        return drawer.findChildren(QLineEdit)

    def _list_texts(self, listing):
        return [listing.item(i).text()
                for i in range(listing.count())]

    def _set_private_mode(self, on):
        # The singleton, not the module (test_ui_p1's pattern):
        # private_mode_on() reads config.private_mode off the
        # singleton instance.
        from voice_assistant.core.config import config as _singleton
        from voice_assistant.core import private_mode
        old = _singleton.private_mode
        _singleton.private_mode = bool(on)
        self._p3_restores.append(
            lambda: setattr(_singleton, "private_mode", old))
        assert private_mode.private_mode_on() == bool(on)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestCalendarWorkspace(P3WindowBase):
    """Calendar: the primary jobs end to end."""

    def test_month_shows_events_conflict_and_all_day(self):
        _show(self.window, 8)
        _pump()
        rows = self._list_texts(self.window._views_month_agenda)
        joined = "\n".join(rows)
        self.assertIn("Dentist", joined)
        self.assertIn("Standup", joined)
        self.assertIn("All day", joined)
        self.assertIn("Conflict — also starts at", joined)
        self.assertIn("Tomorrow sync", "\n".join(
            self._list_texts(self.window._views_agenda_list)))

    def test_month_grid_marks_event_days_with_dots_and_titles(self):
        # Look-fix (2026-11-25): the 25th carries three events
        # but its cell looked identical to every empty day — the
        # dots were painted accent-on-accent on the selected
        # cell, so they were invisible — and the grid gave half
        # its height to a three-row day list. The cell now
        # carries its counts AND its titles, the dots read on
        # the selected day, and the grid takes the freed height.
        self.window.resize(1280, 800)
        _show(self.window, 8)
        _pump()
        from PySide6.QtCore import Qt
        table = self.window._views_month_table
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)
        empty_day = today + datetime.timedelta(days=3)
        first = today.replace(day=1)
        grid_start = first - datetime.timedelta(days=first.weekday())

        def _cell(day):
            off = (day - grid_start).days
            return off // 7, off % 7

        row, col = _cell(today)
        item = table.item(row, col)
        self.assertEqual(
            item.data(Qt.ItemDataRole.UserRole + 1), 3)
        titles = item.data(Qt.ItemDataRole.UserRole + 3)
        self.assertIn("Dentist", titles)
        self.assertIn("Standup", titles)
        self.assertIn("All-day planning", titles)
        row26, col26 = _cell(tomorrow)
        item26 = table.item(row26, col26)
        self.assertEqual(
            item26.data(Qt.ItemDataRole.UserRole + 1), 1)
        self.assertEqual(
            item26.data(Qt.ItemDataRole.UserRole + 3),
            ["Tomorrow sync"])
        row_e, col_e = _cell(empty_day)
        self.assertEqual(
            table.item(row_e, col_e).data(
                Qt.ItemDataRole.UserRole + 1), 0)
        self.assertEqual(
            table.item(row_e, col_e).data(
                Qt.ItemDataRole.UserRole + 3), [])
        # The day panel says how many events the day holds.
        self.assertIn(
            "· 3 events",
            self.window._views_calendar_day_label.text())
        # The grid takes the space the day list used to waste:
        # taller than the hugged list, rows taller than the old
        # 31 px, and the list capped at its content height.
        agenda = self.window._views_month_agenda
        self.assertLessEqual(agenda.maximumHeight(), 240)
        self.assertGreater(table.height(), agenda.height())
        self.assertGreater(table.rowHeight(row), 31)
        # The dots are VISIBLE on the selected day, and the titles
        # paint at 1280: paint the selected cell at its real size
        # and look for dark ink in the bottom strip (dots) and in
        # the title band (the first title line). The render caught
        # the titles missing while the data was right — the old
        # budget left 11 px for a 14 px title line, so no title
        # ever painted. (The painter is always ended, even if the
        # delegate raises: an active painter on a dying pixmap
        # segfaults Qt on teardown — hit while writing this test.)
        from PySide6.QtGui import QColor, QFont, QFontMetrics
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
        from PySide6.QtCore import QRect

        def _dark_ink_bands(r, c):
            delegate = self.window._views_month_delegate
            index = table.model().index(r, c)
            w = table.columnWidth(c)
            h = table.rowHeight(r)
            pix = QPixmap(w, h)
            pix.fill(QColor("#000000"))
            painter = QPainter(pix)
            try:
                opt = QStyleOptionViewItem()
                table.initViewItemOption(opt)
                opt.rect = QRect(0, 0, w, h)
                opt.state = (QStyle.StateFlag.State_Enabled
                             | QStyle.StateFlag.State_Selected)
                delegate.paint(painter, opt, index)
            finally:
                painter.end()
            img = pix.toImage()
            fm = QFontMetrics(table.font())
            small = QFont(table.font())
            if small.pointSize() > 0:
                small.setPointSize(max(8, small.pointSize() - 3))
            elif small.pixelSize() > 0:
                small.setPixelSize(max(9, small.pixelSize() - 3))
            sfm = QFontMetrics(small)
            title_y0 = 1 + fm.height() + 1
            title_y1 = title_y0 + sfm.height()

            def _dark(x0, x1, y0, y1):
                n = 0
                for x in range(x0, min(x1, w)):
                    for y in range(y0, min(y1, h)):
                        px = img.pixelColor(x, y)
                        if px.red() < 40 and px.green() < 60 \
                                and px.blue() < 70:
                            n += 1
                return n

            return (_dark(6, 40, h - 11, h - 1),
                    _dark(6, w - 4, title_y0, title_y1))

        dots, titles_ink = _dark_ink_bands(row, col)
        self.assertGreater(
            dots, 10, "selected event day shows no dark dot ink")
        self.assertGreater(
            titles_ink, 10,
            "event day shows no title ink at 1280")
        # Control: an empty day painted selected has neither.
        dots_e, titles_e = _dark_ink_bands(row_e, col_e)
        self.assertEqual(dots_e, 0)
        self.assertEqual(titles_e, 0)

    def test_agenda_rows_render_rich_text_not_tags(self):
        # Render catch (2026-11-25): the Agenda rows are built as
        # markup (bold time, muted conflict line), but a list item
        # shows its text as plain text — the render showed literal
        # "<b>10:00 AM…" tags on both the day list and the Agenda.
        # Both calendar lists paint through the shared rich-text
        # delegate now, so the markup draws and the tags never
        # reach the screen.
        self.window.resize(1280, 800)
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(2)
        _pump()
        from voice_assistant.gui.window_pages_views import (
            _ViewsRichTextDelegate)
        delegate = self.window._views_rich_delegate
        self.assertIsInstance(delegate, _ViewsRichTextDelegate)
        self.assertIs(
            self.window._views_agenda_list.itemDelegate(), delegate)
        self.assertIs(
            self.window._views_month_agenda.itemDelegate(), delegate)
        listing = self.window._views_agenda_list
        self.assertGreater(listing.count(), 0)
        first = listing.item(0)
        self.assertIn("<b>", first.text())
        # The markup is well-formed HTML that a document renders
        # as clean text — no tags survive the paint path.
        from PySide6.QtGui import QTextDocument
        doc = QTextDocument()
        doc.setHtml(first.text())
        plain = doc.toPlainText()
        self.assertNotIn("<", plain)
        self.assertIn("Dentist", plain)
        self.assertIn("Conflict", plain)
        # Pixel proof: the delegate paints the row — the first
        # line carries text ink, so the delegate is not a no-op.
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
        from PySide6.QtCore import QRect
        index = listing.model().index(0, 0)
        pix = QPixmap(400, 64)
        pix.fill(QColor("#000000"))
        painter = QPainter(pix)
        try:
            opt = QStyleOptionViewItem()
            listing.initViewItemOption(opt)
            opt.rect = QRect(0, 0, 400, 64)
            opt.state = QStyle.StateFlag.State_Enabled
            delegate.paint(painter, opt, index)
        finally:
            painter.end()
        img = pix.toImage()
        ink = 0
        for x in range(4, 396):
            for y in range(10, 54):
                px = img.pixelColor(x, y)
                if px.red() > 120 or px.green() > 120 \
                        or px.blue() > 120:
                    ink += 1
        self.assertGreater(ink, 50, "rich row paints no text ink")
        # Dimmed-line fix (2026-11-25): the event line rendered
        # faint grey while the conflict line read bright — the
        # hierarchy inverted. The cause was the document's default
        # stylesheet: setDefaultStyleSheet("body { color: ... }")
        # does NOT colour the text in this Qt build (variant probe:
        # body-sheet / p-selector / no-sheet all render nothing
        # bright; only an inline span renders at full brightness),
        # so the event line fell back to a faint default while the
        # conflict span (inline #B0C1CD) read bright. The row
        # builder now carries the ink inline (see _VIEWS_ROW_INK)
        # and the delegate substitutes the paint-time colour — so
        # the event line must read BRIGHTER than the muted
        # conflict line, at the themed text colour.
        def _bright_and_dark(image, y0, y1):
            bright = dark = 0
            for x in range(4, 396):
                for y in range(y0, y1):
                    px = image.pixelColor(x, y)
                    total = px.red() + px.green() + px.blue()
                    if total > 600:
                        bright += 1
                    elif total < 150:
                        dark += 1
            return bright, dark

        # The document is two lines centred in the 64 px row, so
        # the event line sits in the top half and the conflict
        # line in the bottom half (doc ~42 px, offset ~11).
        bright_top, _ = _bright_and_dark(img, 11, 32)
        bright_bottom, _ = _bright_and_dark(img, 32, 53)
        self.assertGreater(
            bright_top, 50,
            "event line has no full-brightness text pixels")
        self.assertGreater(
            bright_top, bright_bottom,
            "event line must read brighter than the muted "
            "conflict line")
        # And the selected state: the same row painted selected
        # keeps the event line BRIGHT. A list row's selection
        # background is only a faint accent tint (the selected
        # row measured #0b2c34 over black — every top-band pixel
        # dark), so the dark ink the table delegates use on
        # selection would be dark-on-dark here; the delegate
        # keeps the themed text in both states, and selection
        # must not dim the event line either.
        pix_sel = QPixmap(400, 64)
        pix_sel.fill(QColor("#000000"))
        painter = QPainter(pix_sel)
        try:
            opt = QStyleOptionViewItem()
            listing.initViewItemOption(opt)
            opt.rect = QRect(0, 0, 400, 64)
            opt.state = (QStyle.StateFlag.State_Enabled
                         | QStyle.StateFlag.State_Selected)
            delegate.paint(painter, opt, index)
        finally:
            painter.end()
        img_sel = pix_sel.toImage()
        bright_sel, _ = _bright_and_dark(img_sel, 11, 32)
        self.assertGreater(
            bright_sel, 50,
            "selected event line is dimmed — selection must not "
            "hide the event line")

    def test_week_chip_titles_fit_without_ellipsis(self):
        # Look-fix (2026-11-25): "9:00 AM Q3 review" read as
        # "9:00 AM Q3 revi…" in its chip — the chip face was the
        # grid's full face and the elide width was short, and the
        # delegate read the painter's font (the app default) instead
        # of the table's face. The chip face is three points smaller
        # now, taken from the table's face, and the elide uses the
        # full text width, so the whole title fits at 1280.
        self.window.resize(1280, 800)
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(1)
        _pump()
        from PySide6.QtGui import QFont, QFontMetrics
        from PySide6.QtCore import Qt
        table = self.window._views_week_table
        font = QFont(table.font())
        if font.pointSize() > 0:
            font.setPointSize(max(8, font.pointSize() - 3))
        elif font.pixelSize() > 0:
            font.setPixelSize(max(9, font.pixelSize() - 3))
        fm = QFontMetrics(font)
        width = table.columnWidth(4) - 4 - 8
        shown = fm.elidedText(
            "9:00 AM Tomorrow sync", Qt.TextElideMode.ElideRight,
            max(8, width))
        self.assertEqual(shown, "9:00 AM Tomorrow sync")

    def test_day_label_carries_no_baked_colour(self):
        # Render catch (2026-11-25): the day label's sheet baked
        # in the dark theme's text colour, so on Light it read
        # white on light and vanished. The palette carries the
        # colour in both themes now — no colour in the sheet.
        _show(self.window, 8)
        _pump()
        self.assertNotIn(
            "color:",
            self.window._views_calendar_day_label.styleSheet())

    def test_event_drawer_rows_keep_content_height(self):
        # Render catch (2026-11-25): the drawer content layout
        # shares its spare space across its Preferred rows, so
        # every label grew to ~100 px tall and the drawer read as
        # three giant boxes. A trailing stretch pins the rows to
        # their content height.
        _show(self.window, 8)
        agenda = self.window._views_month_agenda
        self.assertGreater(agenda.count(), 0)
        self.window._on_views_agenda_row_picked(agenda.item(0))
        _pump()
        drawer = self.window._views_calendar_drawer
        self.assertTrue(drawer.isVisible())
        from PySide6.QtWidgets import QLabel
        lay = drawer.content_layout()
        labels = [lay.itemAt(i).widget()
                  for i in range(lay.count())]
        labels = [w for w in labels
                  if isinstance(w, QLabel)]
        self.assertTrue(labels)
        for lbl in labels:
            self.assertLessEqual(
                lbl.height(), lbl.sizeHint().height() + 4,
                f"{lbl.text()[:40]!r} is {lbl.height()} px tall")

    def test_agenda_filters_to_one_day(self):
        # D-P3-03: Agenda scoped to one day IS the Day view — the
        # filter must actually filter, not just label the page.
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(2)
        _pump()
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)
        from PySide6.QtCore import QDate
        # The picker already shows today at build, so setting it
        # to today is a no-op (no dateChanged signal) — drive the
        # filter tomorrow-first, then back to today.
        self.window._views_agenda_date.setDate(
            QDate(tomorrow.year, tomorrow.month, tomorrow.day))
        _pump()
        rows = self._list_texts(self.window._views_agenda_list)
        self.assertEqual(len(rows), 1)
        self.assertIn("Tomorrow sync", rows[0])
        self.window._views_agenda_date.setDate(
            QDate(today.year, today.month, today.day))
        _pump()
        rows = self._list_texts(self.window._views_agenda_list)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all("Dentist" in r or "Standup" in r
                            or "All-day planning" in r for r in rows))
        self.window._on_views_agenda_show_all()
        _pump()
        rows = self._list_texts(self.window._views_agenda_list)
        self.assertEqual(len(rows), 4)

    def test_create_event_end_to_end(self):
        self._allow_writes(True)
        _show(self.window, 8)
        self.window._views_calendar_add.click()
        _pump()
        drawer = self.window._views_calendar_drawer
        self.assertTrue(drawer.isVisible())
        edits = self._drawer_line_edits(drawer)
        self.assertGreaterEqual(len(edits), 1)
        edits[0].setText("New review")
        create = self._drawer_button(drawer, "Create event")
        self.assertIsNotNone(create)
        create.click()
        _pump()
        created = [c for c in self._gs_calls if c[0] == "create_event"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][1], "New review")
        self.assertTrue(any("New review" in r for r in
                            self._list_texts(
                                self.window._views_month_agenda)))

    def test_create_refused_when_confirm_denied(self):
        self._allow_writes(False)
        _show(self.window, 8)
        self.window._views_calendar_add.click()
        _pump()
        drawer = self.window._views_calendar_drawer
        edits = self._drawer_line_edits(drawer)
        edits[0].setText("Never created")
        self._drawer_button(drawer, "Create event").click()
        _pump()
        self.assertEqual(
            [c for c in self._gs_calls if c[0] == "create_event"], [])
        labels = self._drawer_labels(drawer)
        self.assertTrue(any("Not created" in t for t in labels))

    def test_edit_is_create_then_cancel_by_id(self):
        self._allow_writes(True)
        _show(self.window, 8)
        event = next(e for e in self._fake_events
                     if e["id"] == "evt-1")
        self.window._views_open_event_drawer(event, "detail")
        _pump()
        drawer = self.window._views_calendar_drawer
        self._drawer_button(drawer, "Edit").click()
        _pump()
        edits = self._drawer_line_edits(drawer)
        edits[0].setText("Dentist — moved")
        self._drawer_button(drawer, "Save changes").click()
        _pump()
        kinds = [c[0] for c in self._gs_calls]
        self.assertIn("create_event", kinds)
        self.assertIn("cancel_event", kinds)
        # Save-new first, delete-old second — the old event is
        # never removed before its replacement exists.
        self.assertLess(kinds.index("create_event"),
                        kinds.index("cancel_event"))
        cancelled = [c for c in self._gs_calls
                     if c[0] == "cancel_event"]
        self.assertEqual(cancelled[0][1], "evt-1")
        self.assertFalse(any(e["id"] == "evt-1"
                             for e in self._fake_events))
        self.assertTrue(any(e["title"] == "Dentist — moved"
                            for e in self._fake_events))

    def test_reschedule_moves_by_id(self):
        self._allow_writes(True)
        _show(self.window, 8)
        event = next(e for e in self._fake_events
                     if e["id"] == "evt-2")
        self.window._views_open_event_drawer(event, "detail")
        _pump()
        drawer = self.window._views_calendar_drawer
        self._drawer_button(drawer, "Reschedule").click()
        _pump()
        save = self._drawer_button(drawer, "Move event")
        if save is None:
            save = self._drawer_button(drawer, "Reschedule event")
        self.assertIsNotNone(save)
        save.click()
        _pump()
        moved = [c for c in self._gs_calls
                 if c[0] == "reschedule_event"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0][1], "evt-2")

    def test_cancel_by_id_removes_only_that_event(self):
        self._allow_writes(True)
        _show(self.window, 8)
        # Two same-title same-day events cancel independently:
        # cancelling evt-2 leaves evt-1 (same start, other title).
        event = next(e for e in self._fake_events
                     if e["id"] == "evt-2")
        self.window._views_open_event_drawer(event, "detail")
        _pump()
        drawer = self.window._views_calendar_drawer
        self._drawer_button(drawer, "Cancel event").click()
        _pump()
        cancelled = [c for c in self._gs_calls
                     if c[0] == "cancel_event"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0][1], "evt-2")
        self.assertTrue(any(e["id"] == "evt-1"
                            for e in self._fake_events))
        self.assertFalse(any(e["id"] == "evt-2"
                             for e in self._fake_events))

    def test_not_connected_state(self):
        self._set_google_connected(False)
        _show(self.window, 8)
        _pump()
        self.assertIn("Connect Google",
                      self.window._views_calendar_sync.text())
        self.assertFalse(self.window._views_calendar_add.isVisible())
        self.assertTrue(self.window._views_calendar_connect.isVisible())
        # Render catch (2026-11-25): the offline grid was blank —
        # the empty fill wiped the day numbers. The dates come from
        # the calendar alone, so the skeleton stays up (only the
        # event dots are absent).
        table = self.window._views_month_table
        numbers = [table.item(row, col).text()
                   for row in range(6) for col in range(7)]
        self.assertIn(str(datetime.date.today().day), numbers)
        self.assertEqual(len(numbers), 42)

    def test_read_only_scope_state(self):
        self._set_google_scopes(set())
        _show(self.window, 8)
        _pump()
        self.assertFalse(self.window._views_calendar_add.isVisible())
        self.assertIn("reconnect Google",
                      self.window._views_calendar_sync.text())

    def test_week_now_line_timer_start_stop_and_position(self):
        # S-P3-04 (parent-ruled): the page-scoped 60 s timer starts
        # when the Week view becomes visible, stops on hide / view
        # switch — and the line sits at the current time.
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(1)
        _pump()
        self.assertTrue(self.window._views_now_timer.isActive())
        line = self.window._views_now_line
        self.assertTrue(line.isVisible())
        table = self.window._views_week_table
        top = table.rowViewportPosition(1)
        bottom = table.rowViewportPosition(25)
        if bottom <= top:
            bottom = top + 24 * table.rowHeight(1)
        now = datetime.datetime.now()
        frac = (now.hour * 60 + now.minute) / (24 * 60)
        expected = top + int(frac * (bottom - top))
        tolerance = max(3, (bottom - top) // (24 * 60) + 2)
        self.assertAlmostEqual(line.y(), expected, delta=tolerance)
        self.window._views_calendar_tabs.setCurrentIndex(0)
        _pump()
        self.assertFalse(self.window._views_now_timer.isActive())
        self.window._views_calendar_tabs.setCurrentIndex(1)
        _pump()
        self.assertTrue(self.window._views_now_timer.isActive())
        _show(self.window, 0)
        _pump()
        self.assertFalse(self.window._views_now_timer.isActive())

    def test_week_grid_shows_both_overlapping_events(self):
        # Look-fix (2026-11-25): Dentist and Standup both start
        # Wed 10:00, but the Week grid showed only Standup — a
        # cell holds one item, so the second write overwrote the
        # first. Same-start events now stack as lines of the one
        # cell, the row grows to show them, and the narrow hour
        # gutter gives the day columns room so titles stop
        # eliding to "...".
        self.window.resize(1280, 800)
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(1)
        _pump()
        table = self.window._views_week_table
        today = datetime.date.today()
        col = today.weekday() + 1
        cell = table.item(11, col)
        self.assertIsNotNone(cell)
        self.assertIn("Dentist", cell.text())
        self.assertIn("Standup", cell.text())
        self.assertGreater(
            table.rowHeight(11),
            table.verticalHeader().defaultSectionSize())
        tomorrow = today + datetime.timedelta(days=1)
        col_t = tomorrow.weekday() + 1
        q3 = table.item(10, col_t)
        self.assertIsNotNone(q3)
        self.assertEqual(q3.text(), "9:00 AM Tomorrow sync")
        self.assertNotIn("…", q3.text())
        allday = table.item(0, col)
        self.assertIsNotNone(allday)
        self.assertIn("All-day planning", allday.text())

    def test_week_events_render_as_filled_chips(self):
        # Look-fix (2026-11-25): Week events were bare text in
        # the cells — no filled chip, nothing that reads as an
        # event — and the grid was seven empty columns. Event
        # cells now paint as filled accent chips (the delegate
        # paints them; the fill still groups overlaps), and
        # today's column carries a faint tint as an anchor.
        self.window.resize(1280, 800)
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(1)
        _pump()
        table = self.window._views_week_table
        delegate = self.window._views_week_delegate
        self.assertIs(table.itemDelegate(), delegate)
        today = datetime.date.today()
        col = today.weekday() + 1
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
        from PySide6.QtCore import QRect

        def _paint_counts(r, c):
            index = table.model().index(r, c)
            pix = QPixmap(140, 42)
            pix.fill(QColor("#000000"))
            painter = QPainter(pix)
            try:
                opt = QStyleOptionViewItem()
                table.initViewItemOption(opt)
                opt.rect = QRect(0, 0, 140, 42)
                opt.state = QStyle.StateFlag.State_Enabled
                delegate.paint(painter, opt, index)
            finally:
                painter.end()
            img = pix.toImage()
            accent = delegate._accent
            chip = 0
            ink = 0
            for x in range(140):
                for y in range(42):
                    px = img.pixelColor(x, y)
                    if abs(px.red() - accent.red()) <= 30 \
                            and abs(px.green() - accent.green()) <= 30 \
                            and abs(px.blue() - accent.blue()) <= 30:
                        chip += 1
                    if px.red() < 40 and px.green() < 60 \
                            and px.blue() < 70:
                        ink += 1
            return chip, ink

        chip, ink = _paint_counts(11, col)
        self.assertGreater(
            chip, 1000, "event cell paints no filled chip")
        self.assertGreater(
            ink, 20, "chip carries no dark event text")
        # Control: an empty cell in another column paints no chip.
        empty_col = 5 if col != 5 else 6
        chip_e, _ink_e = _paint_counts(12, empty_col)
        self.assertEqual(chip_e, 0)

    def test_week_now_line_spans_grid_and_is_labelled(self):
        # Look-fix (2026-11-25): the now-line was a bare 2 px
        # segment in one column around 05:30 — it read as a
        # glitch. It now spans the whole day grid and carries a
        # "Now" tag in the hour gutter.
        self.window.resize(1280, 800)
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(1)
        _pump()
        table = self.window._views_week_table
        line = self.window._views_now_line
        tag = self.window._views_now_tag
        self.assertTrue(line.isVisible())
        self.assertEqual(line.x(), table.columnViewportPosition(1))
        self.assertEqual(
            line.width(),
            table.columnViewportPosition(7) + table.columnWidth(7)
            - table.columnViewportPosition(1))
        self.assertTrue(tag.isVisible())
        self.assertEqual(tag.text(), "Now")
        self.assertLessEqual(
            tag.x() + tag.width(), table.columnViewportPosition(1))

    def test_now_tag_paints_its_accent_fill(self):
        # Parent check (2026-11-25): the "Now" tag read as a
        # plain white box over the hour label — the palette's
        # Window never reached the paint. The tag paints its own
        # accent fill and dark text now, so the paint is
        # deterministic in both the render and the suite.
        self.window.resize(1280, 800)
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(1)
        _pump()
        tag = self.window._views_now_tag
        self.assertTrue(tag.isVisible())
        from PySide6.QtGui import QColor, QPixmap
        pix = QPixmap(tag.size())
        pix.fill(QColor("#000000"))
        tag.render(pix)
        img = pix.toImage()
        accent = 0
        dark = 0
        white = 0
        for x in range(pix.width()):
            for y in range(pix.height()):
                px = img.pixelColor(x, y)
                if (abs(px.red() - 0x35) < 30
                        and abs(px.green() - 0xD6) < 30
                        and abs(px.blue() - 0xFF) < 30):
                    accent += 1
                elif px.red() < 40 and px.green() < 60 \
                        and px.blue() < 70:
                    dark += 1
                elif px.red() > 240 and px.green() > 240 \
                        and px.blue() > 240:
                    white += 1
        self.assertGreater(accent, 50, "tag paints no accent fill")
        self.assertGreater(dark, 5, "tag paints no dark text")
        self.assertEqual(white, 0, "tag still paints a white box")

    def test_agenda_reads_as_a_date_and_plain_words(self):
        # Look-fix (2026-11-25): the Agenda helper was jargon
        # ("Showing the loaded window — pick a day for the Day
        # view"), the Day filter showed bare numbers ("25 11
        # 2026"), and the rows were plain lines over a void. The
        # filter now reads as a date, the helper is plain words,
        # the rows are roomy rich text, and a count line closes
        # the page.
        from PySide6.QtCore import QDate
        self.window.resize(1280, 800)
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(2)
        _pump()
        date_edit = self.window._views_agenda_date
        self.assertEqual(date_edit.displayFormat(), "ddd d MMM yyyy")
        today = datetime.date.today()
        self.assertIn(today.strftime("%b"), date_edit.text())
        label = self.window._views_agenda_filter_label.text()
        self.assertNotIn("loaded window", label)
        self.assertNotIn("Day view", label)
        self.assertIn("pick a day", label.lower())
        listing = self.window._views_agenda_list
        self.assertGreater(listing.count(), 0)
        first = listing.item(0)
        self.assertIn("<b>", first.text())
        self.assertGreaterEqual(listing.sizeHintForRow(0), 70)
        summary = self.window._views_agenda_summary.text()
        self.assertIn("4 events", summary)
        self.assertIn("3 today", summary)
        self.assertIn("1 tomorrow", summary)
        # The day-set helper is plain words too.
        tomorrow = today + datetime.timedelta(days=1)
        date_edit.setDate(
            QDate(tomorrow.year, tomorrow.month, tomorrow.day))
        _pump()
        date_edit.setDate(QDate(today.year, today.month, today.day))
        _pump()
        day_label = self.window._views_agenda_filter_label.text()
        self.assertIn("just that day", day_label)
        self.assertNotIn("Day view", day_label)

    def test_agenda_date_shows_the_full_year(self):
        # Parent check (2026-11-25): the Day filter read
        # "Wed 25 Nov 202" — the control was too narrow for its
        # own text and cut the year. Its minimum width now covers
        # the full date plus the popup button and the frame.
        self.window.resize(1280, 800)
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(2)
        _pump()
        date_edit = self.window._views_agenda_date
        today = datetime.date.today()
        self.assertIn(str(today.year), date_edit.text())
        fm = date_edit.fontMetrics()
        self.assertGreaterEqual(
            date_edit.minimumWidth(),
            fm.horizontalAdvance("Wed 25 Nov 2026") + 40)
        self.assertGreaterEqual(
            date_edit.width(),
            fm.horizontalAdvance(date_edit.text()) + 30)

    def test_calendar_view_persists(self):
        from voice_assistant.core.gui_facade import qsettings_get
        _show(self.window, 8)
        self.window._views_calendar_tabs.setCurrentIndex(1)
        _pump()
        self.assertEqual(qsettings_get("calendar/view"), "Week")
        self.window._views_calendar_tabs.setCurrentIndex(0)
        _pump()
        self.assertEqual(qsettings_get("calendar/view"), "Month")

    def test_private_mode_blanks_titles_and_hides_write(self):
        self._set_private_mode(True)
        _show(self.window, 8)
        _pump()
        rows = self._list_texts(self.window._views_month_agenda)
        self.assertTrue(rows)
        self.assertTrue(all("Private event" in r for r in rows))
        self.assertNotIn("Dentist", "\n".join(rows))
        self.assertFalse(self.window._views_calendar_add.isVisible())

    def test_month_grid_keyboard_navigation(self):
        # P3-09: the custom month grid keeps native keyboard
        # navigation — the delegate only paints.
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        _show(self.window, 8)
        table = self.window._views_month_table
        table.setFocus()
        _pump()
        table.setCurrentCell(0, 0)
        QTest.keyClick(table, Qt.Key_Down)
        _pump()
        self.assertEqual(table.currentRow(), 1)
        QTest.keyClick(table, Qt.Key_Right)
        _pump()
        self.assertEqual(table.currentColumn(), 1)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestInboxWorkspace(P3WindowBase):
    """Inbox: the primary jobs end to end."""

    def test_all_shows_messages_with_unread_marker(self):
        _show(self.window, 9)
        _pump()
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("● "))
        self.assertFalse(rows[1].startswith("● "))
        self.assertIn("Q3 numbers", rows[0])
        self.assertEqual(
            self.window._views_inbox_unread_label.text(), "1 unread")

    def test_first_unread_message_selected_on_open(self):
        # Look-fix (2026-11-25): the reading pane sat empty with
        # one line until the user clicked. The first unread
        # message opens with the page now, so the pane shows real
        # mail.
        _show(self.window, 9)
        _pump()
        self.assertEqual(self.window._views_inbox_selected_id, "m-1")
        self.assertEqual(
            self.window._views_inbox_list.currentRow(), 0)
        body = self.window._views_inbox_body.toPlainText()
        self.assertNotIn("Pick a message from the list", body)
        self.assertIn("sentence", body.lower())

    def test_rows_keep_time_on_its_own_line_and_snippet_elided(self):
        # Look-fix (2026-11-25): the unread row wrapped
        # mid-sentence with the time inline in the title. Each row
        # is two fixed lines now — who/what, then when/snippet —
        # with the snippet elided at a word boundary.
        _show(self.window, 9)
        _pump()
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[0],
            "● Ada Lovelace — Q3 numbers\n09:12 · The Q3 numbers are in.")
        self.assertEqual(
            rows[1], "Bob — Re: venue\n08:40 · Venue confirmed.")

    def test_sender_never_shows_a_bare_address(self):
        # Look-fix (2026-11-25): a sender showed as the raw
        # address ("ada@example.test"). A sender shows a name.
        _show(self.window, 9)
        _pump()
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertTrue(all("@" not in r for r in rows))
        self.assertTrue(rows[1].startswith("Bob —"))

    def test_footer_count_matches_listed_rows(self):
        # Look-fix (2026-11-25): the footer said "Showing the
        # latest 20 messages" while two were listed. It counts
        # what is actually listed now.
        _show(self.window, 9)
        _pump()
        cap = self.window._views_inbox_cap
        self.assertEqual(cap.text(), "Showing 2 messages.")
        self.window._views_inbox_tabs.setCurrentIndex(1)
        _pump()
        self.assertEqual(cap.text(), "Showing 1 message.")
        self.window._views_inbox_tabs.setCurrentIndex(2)
        _pump()
        self.assertEqual(cap.text(), "Showing 1 message.")
        self.window._views_inbox_search.setText("zzz-no-match")
        self.window._views_inbox_search_now()
        _pump()
        self.assertEqual(cap.text(), "Showing 0 messages.")

    def test_search_placeholder_is_plain_words(self):
        # Look-fix (2026-11-25): "Search mail — Top 6 matches"
        # is jargon. The cap is stated on the results label when
        # a search runs; the placeholder is plain words.
        _show(self.window, 9)
        _pump()
        self.assertEqual(
            self.window._views_inbox_search.placeholderText(),
            "Search mail")

    def test_unread_tab_filters(self):
        _show(self.window, 9)
        self.window._views_inbox_tabs.setCurrentIndex(1)
        _pump()
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertEqual(len(rows), 1)
        self.assertIn("Q3 numbers", rows[0])

    def test_snoozed_tab_lists_snoozed_mail(self):
        _show(self.window, 9)
        self.window._views_inbox_tabs.setCurrentIndex(2)
        _pump()
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertEqual(len(rows), 1)
        self.assertIn("Snoozed mail", rows[0])
        self.assertIn("Snoozed until", rows[0])

    def test_search_caps_at_six_and_states_it(self):
        _show(self.window, 9)
        self.window._views_inbox_search.setText("venue")
        self.window._views_inbox_search_now()
        _pump()
        self.assertIn("Top 6 matches for 'venue'",
                      self.window._views_inbox_results_label.text())
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertEqual(len(rows), 1)
        self.assertIn("Re: venue", rows[0])
        self.window._views_inbox_search.setText("zzz-no-match")
        self.window._views_inbox_search_now()
        _pump()
        self.assertIn("No emails found",
                      self.window._views_inbox_empty.sentence_text)

    def test_summary_first_then_full_message(self):
        _show(self.window, 9)
        self.window._views_inbox_show_message("m-1")
        _pump()
        body = self.window._views_inbox_body.toPlainText()
        # The Summary line is shown first, labelled "Summary";
        # the full body is one tap away.
        self.assertTrue(body.startswith("Summary"))
        self.assertLess(len(body), len(self._fake_body))
        summary_btn = self.window._views_inbox_summary_btn
        self.assertEqual(summary_btn.text(), "Read the full message")
        summary_btn.click()
        _pump()
        body = self.window._views_inbox_body.toPlainText()
        self.assertIn("First sentence here", body)
        self.assertIn("Third sentence closes it", body)
        self.assertEqual(summary_btn.text(), "Show summary")
        summary_btn.click()
        _pump()
        body = self.window._views_inbox_body.toPlainText()
        self.assertTrue(body.startswith("Summary"))

    def test_compose_review_then_send(self):
        self._allow_writes(True)
        _show(self.window, 9)
        self.window._views_inbox_compose.click()
        _pump()
        drawer = self.window._views_inbox_drawer
        edits = self._drawer_line_edits(drawer)
        edits[0].setText("ada@example.test")
        edits[1].setText("Q3 follow-up")
        from PySide6.QtWidgets import QTextEdit
        body_in = drawer.findChildren(QTextEdit)[0]
        body_in.setPlainText("Here are the numbers.")
        self._drawer_button(drawer, "Review and send").click()
        _pump()
        labels = self._drawer_labels(drawer)
        joined = "\n".join(labels)
        self.assertIn("ada@example.test", joined)
        self.assertIn("Q3 follow-up", joined)
        self.assertIn("Here are the numbers.", joined)
        self._drawer_button(drawer, "Send").click()
        _pump()
        sent = [c for c in self._gs_calls if c[0] == "send_email"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "ada@example.test")
        self.assertEqual(sent[0][2], "Q3 follow-up")
        self.assertEqual(sent[0][3], "Here are the numbers.")
        self.assertFalse(drawer.isVisible())

    def test_compose_back_to_edit_keeps_words(self):
        _show(self.window, 9)
        self.window._views_inbox_compose.click()
        _pump()
        drawer = self.window._views_inbox_drawer
        edits = self._drawer_line_edits(drawer)
        edits[0].setText("ada@example.test")
        edits[1].setText("Draft subject")
        from PySide6.QtWidgets import QTextEdit
        drawer.findChildren(QTextEdit)[0].setPlainText("Draft body.")
        self._drawer_button(drawer, "Review and send").click()
        _pump()
        self._drawer_button(drawer, "Back to edit").click()
        _pump()
        edits = self._drawer_line_edits(drawer)
        self.assertEqual(edits[0].text(), "ada@example.test")
        self.assertEqual(edits[1].text(), "Draft subject")
        from PySide6.QtWidgets import QTextEdit
        self.assertEqual(
            drawer.findChildren(QTextEdit)[0].toPlainText(),
            "Draft body.")

    def test_compose_refused_when_confirm_denied(self):
        self._allow_writes(False)
        _show(self.window, 9)
        self.window._views_inbox_compose.click()
        _pump()
        drawer = self.window._views_inbox_drawer
        edits = self._drawer_line_edits(drawer)
        edits[0].setText("ada@example.test")
        edits[1].setText("Never sent")
        from PySide6.QtWidgets import QTextEdit
        drawer.findChildren(QTextEdit)[0].setPlainText("Body.")
        self._drawer_button(drawer, "Review and send").click()
        _pump()
        self._drawer_button(drawer, "Send").click()
        _pump()
        self.assertEqual(
            [c for c in self._gs_calls if c[0] == "send_email"], [])

    def test_snooze_and_bring_back(self):
        import voice_assistant.gui.window_pages_views as views_mod
        _show(self.window, 9)
        self.window._views_inbox_show_message("m-1")
        _pump()

        class _FakeDialog:
            def __init__(self, parent=None):
                self.until = "tomorrow"

            def exec(self):
                from PySide6.QtWidgets import QDialog
                return QDialog.DialogCode.Accepted

        orig_dialog = views_mod._ViewsSnoozeDialog
        views_mod._ViewsSnoozeDialog = _FakeDialog
        try:
            self.window._views_inbox_snooze_now()
        finally:
            views_mod._ViewsSnoozeDialog = orig_dialog
        _pump()
        snoozed = [c for c in self._gs_calls if c[0] == "snooze_email"]
        self.assertEqual(len(snoozed), 1)
        self.assertEqual(snoozed[0][1], "m-1")
        self.assertEqual(snoozed[0][2], "tomorrow")
        self.window._views_inbox_tabs.setCurrentIndex(2)
        _pump()
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertEqual(len(rows), 2)
        # Bring back now: pick the snoozed m-1 row, then Bring back.
        from PySide6.QtCore import Qt
        for idx in range(self.window._views_inbox_list.count()):
            item = self.window._views_inbox_list.item(idx)
            if str(item.data(Qt.ItemDataRole.UserRole)).endswith("m-1"):
                self.window._views_inbox_list.setCurrentItem(item)
                self.window._on_views_inbox_picked(item)
                break
        _pump()
        self.assertEqual(
            self.window._views_inbox_snooze.text(), "Bring back now")
        self.window._views_inbox_snooze.click()
        _pump()
        brought = [c for c in self._gs_calls
                   if c[0] == "snooze_email" and c[2] == ""]
        self.assertEqual(len(brought), 1)
        self.assertEqual(brought[0][1], "m-1")

    def test_snooze_dialog_offers_only_the_three_choices(self):
        import voice_assistant.gui.window_pages_views as views_mod
        dialog = views_mod._ViewsSnoozeDialog(self.window)
        choices = [dialog._choice.itemText(i)
                   for i in range(dialog._choice.count())]
        self.assertEqual(choices, ["Tomorrow 09:00", "Pick a date",
                                   "Pick a date and time"])
        dialog._on_accept()
        self.assertEqual(dialog.until, "tomorrow")
        self.assertEqual(dialog.result(),
                         1)  # QDialog.Accepted
        dialog2 = views_mod._ViewsSnoozeDialog(self.window)
        dialog2._choice.setCurrentIndex(2)
        from PySide6.QtCore import QDateTime
        past = QDateTime.currentDateTime().addDays(-1)
        dialog2._datetime.setDateTime(past)
        dialog2._on_accept()
        self.assertEqual(dialog2.until, "")
        self.assertIn("already past", dialog2._refusal.text())

    def test_attachments_list_without_downloading(self):
        _show(self.window, 9)
        self.window._views_inbox_show_message("m-1")
        _pump()
        label = self.window._views_inbox_attach_label.text()
        self.assertIn("notes.pdf", label)
        self.assertIn("photo.png", label)
        listed = [c for c in self._gs_calls
                  if c[0] == "download_attachment"]
        # Look-fix (2026-11-25): the open itself probes once now
        # (the first unread message opens with the page), so the
        # count is no longer exactly one — the proof is that
        # EVERY attachment call used the probe filename, i.e.
        # nothing was ever downloaded for real.
        self.assertGreaterEqual(len(listed), 1)
        self.assertTrue(
            all(c[2] == self._attach_probe for c in listed),
            "an attachment call downloaded a real file")

    def test_not_connected_state(self):
        self._set_google_connected(False)
        _show(self.window, 9)
        _pump()
        # Hunt fix (c) item 5: the connect line lives ONLY in the
        # header sync slot now — the list pane shows its own empty
        # state ("No mail to show."), not a second copy of the
        # connect line.
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertFalse(any("Connect Google" in r for r in rows))
        self.assertIn("Connect Google",
                      self.window._views_inbox_sync.text())
        self.assertFalse(self.window._views_inbox_compose.isVisible())

    def test_private_mode_blanks_and_hides_compose(self):
        self._set_private_mode(True)
        _show(self.window, 9)
        _pump()
        rows = self._list_texts(self.window._views_inbox_list)
        self.assertTrue(rows)
        self.assertTrue(all(r == "Private mode is on" for r in rows))
        self.assertFalse(self.window._views_inbox_compose.isVisible())


@unittest.skipUnless(_QT, "Qt unavailable")
class TestContactsWorkspace(P3WindowBase):
    """Contacts: the primary jobs end to end."""

    def test_grid_shows_cards_with_details(self):
        _show(self.window, 10)
        _pump()
        self.assertIn("2 of 2 contacts",
                      self.window._views_contacts_count.text())
        texts = []
        from PySide6.QtWidgets import QLabel
        for lbl in self.window._views_contacts_inner.findChildren(QLabel):
            texts.append(lbl.text())
        joined = "\n".join(texts)
        self.assertIn("Ada Lovelace", joined)
        # Look-fix (2026-11-25): a card detail is a key row over
        # its value row now, never "key: value" on one line — the
        # old single line wrapped mid-token.
        self.assertIn("phone", texts)
        self.assertIn("+44 7000 000001", texts)
        self.assertIn("email", texts)
        self.assertIn("ada@example.test", texts)
        self.assertIn("Bob", joined)

    def test_first_contact_detail_pane_opens_on_load(self):
        # Look-fix (2026-11-25): two small cards over a void read
        # as unfinished. The first contact's detail pane opens
        # with the page now, so the page shows its other half.
        _show(self.window, 10)
        _pump()
        self.assertEqual(
            self.window._views_contact_name, "Ada Lovelace")
        drawer = self.window._views_contacts_drawer
        self.assertTrue(drawer.isVisible())
        from PySide6.QtWidgets import QLabel
        labels = [lbl.text() for lbl in drawer.findChildren(QLabel)]
        self.assertTrue(
            any("ada@example.test" in lbl for lbl in labels),
            "the opened drawer shows Ada's details")

    def test_single_detail_card_reads_complete(self):
        # Look-fix (2026-11-25): Bob's one-line card sat in a big
        # box and read as unfinished. Every card carries a
        # bottom-anchored footer with its detail count, so the
        # short card reads as a complete unit.
        self.window.resize(1280, 800)
        _show(self.window, 10)
        _pump()
        from PySide6.QtWidgets import QLabel
        inner = self.window._views_contacts_inner
        for name, want in (("Ada Lovelace", "3 details"),
                           ("Bob", "1 detail")):
            name_lbl = next(
                lbl for lbl in inner.findChildren(QLabel)
                if lbl.text() == name)
            card = name_lbl.parentWidget()
            footers = [lbl for lbl in card.findChildren(QLabel)
                       if lbl.objectName() == "muted"
                       and want in lbl.text()]
            self.assertEqual(len(footers), 1, name)
            foot = footers[0]
            self.assertLessEqual(
                card.height() - (foot.y() + foot.height()), 14)

    def test_contact_card_values_never_split_mid_token(self):
        # Look-fix (2026-11-25): the Ada card used to read
        # "address: 12 Analytical" / "Way", "email:" /
        # "ada@example.test", "phone: +44 7000" / "000001" — the
        # card was too narrow for label-plus-value, so values
        # wrapped mid-token. Stacked over its key, each value is
        # one unbroken label that fits its card width.
        self.window.resize(1280, 800)
        _show(self.window, 10)
        _pump()
        from PySide6.QtWidgets import QLabel
        inner = self.window._views_contacts_inner
        labels = inner.findChildren(QLabel)
        values = [lbl for lbl in labels
                  if lbl.objectName() == "bodySmall"
                  and lbl.text()
                  and not (lbl.text().startswith("+")
                           and lbl.text().endswith(" more"))]
        wanted = ["12 Analytical Way", "ada@example.test",
                  "+44 7000 000001", "+44 7000 000002"]
        for value in wanted:
            self.assertIn(
                value, [lbl.text() for lbl in values],
                f"{value!r} is not one unbroken card label")
        for lbl in values:
            self.assertFalse(
                lbl.wordWrap(),
                f"{lbl.text()!r} may wrap mid-token")
            self.assertGreaterEqual(
                lbl.width(), lbl.sizeHint().width(),
                f"{lbl.text()!r} is clipped by its card")
        keys = [lbl.text() for lbl in labels
                if lbl.objectName() == "muted"]
        self.assertIn("phone", keys)
        self.assertIn("email", keys)
        self.assertIn("address", keys)

    def test_contact_drawer_values_never_split_mid_token(self):
        # Parent check (2026-11-25): the no-split fix landed in
        # the cards but not the pane — the open drawer still read
        # "phone: +44 7000" / "000001" and "address: 12
        # Analytical" / "Way". The pane stacks key over value
        # the same way now, so each value is one unbroken label.
        self.window.resize(1280, 800)
        _show(self.window, 10)
        self._open_contact("Ada Lovelace")
        _pump()
        from PySide6.QtWidgets import QLabel
        drawer = self.window._views_contacts_drawer
        labels = drawer.findChildren(QLabel)
        wanted = ["12 Analytical Way", "ada@example.test",
                  "+44 7000 000001"]
        for value in wanted:
            found = [lbl for lbl in labels
                     if lbl.objectName() == "bodySmall"
                     and lbl.text() == value]
            self.assertEqual(
                len(found), 1,
                f"{value!r} is not one unbroken drawer label")
            lbl = found[0]
            self.assertFalse(
                lbl.wordWrap(),
                f"{value!r} may wrap mid-token")
            self.assertGreaterEqual(
                lbl.width(), lbl.sizeHint().width(),
                f"{value!r} is clipped by its pane")
        keys = [lbl.text() for lbl in labels
                if lbl.objectName() == "muted"]
        self.assertIn("phone", keys)
        self.assertIn("email", keys)
        self.assertIn("address", keys)

    def test_contact_drawer_long_values_read_whole_at_1280_and_640(self):
        # Step 2 F2: the drawer value used to share its row with
        # Copy / Edit / Delete, so at 1280 the drawer (380 px)
        # left ~126 px for the value — about 21 characters
        # before a long email clipped with no way to see the
        # rest. The buttons sit on their own row below the value
        # now, so the value has the full drawer width at 1280
        # (380 px) and at 640 (320 px) — no clipped characters,
        # no mid-token split, and the three actions stay one tap
        # away.
        from voice_assistant.core import skills_admin_schedules
        long_email = "alexandra.christopher-longname@example.test"
        self.assertGreater(len(long_email), 40)
        long_phone = "+44 7000 000001 ext. 1234"
        skills_admin_schedules.contact_card(
            "save", "Long Values", f"email: {long_email}")
        skills_admin_schedules.contact_card(
            "save", "Long Values", f"phone: {long_phone}")
        # A direct store save bypasses the GUI — refresh the page
        # so the drawer's cached contact list sees the new details.
        self.window._refresh_contacts_page()
        from PySide6.QtWidgets import QLabel
        for width in (1280, 640):
            self.window.resize(width, 800)
            _show(self.window, 10)
            self._open_contact("Long Values")
            _pump()
            drawer = self.window._views_contacts_drawer
            for value in (long_email, long_phone):
                found = [lbl for lbl in drawer.findChildren(QLabel)
                         if lbl.objectName() == "bodySmall"
                         and lbl.text() == value]
                self.assertEqual(
                    len(found), 1,
                    f"{value!r} is not one unbroken drawer label "
                    f"at {width}")
                lbl = found[0]
                self.assertFalse(
                    lbl.wordWrap(),
                    f"{value!r} may wrap mid-token at {width}")
                self.assertGreaterEqual(
                    lbl.width(), lbl.sizeHint().width(),
                    f"{value!r} is clipped by its pane at {width}")
            for action in ("Copy", "Edit", "Delete"):
                self.assertIsNotNone(
                    self._drawer_button(drawer, action),
                    f"{action} is not one tap away at {width}")

    def test_contact_drawer_shows_related_call_and_email(self):
        # Bug 21 (neighbour run, test_gui_boundary): the related-emails
        # branch imported `recent_messages` from the facade, a name the
        # facade never exported — the import always raised inside a
        # try/except, so related emails never listed. The match itself
        # was dead too: it compared a bare number/address against the
        # raw "key: value" detail string, which never equals it. §4.4
        # matches on the detail VALUE, exactly, never a substring.
        from PySide6.QtWidgets import QPushButton
        self.window.resize(1280, 800)
        _show(self.window, 10)
        # Ada's phone matches the completed call to +44 7000 000001;
        # her email matches no message (m-1's sender is a display
        # name, not a bare address) — number link only.
        self._open_contact("Ada Lovelace")
        _pump()
        drawer = self.window._views_contacts_drawer
        labels = self._drawer_labels(drawer)
        self.assertIn("Related — Linked by matching number", labels)
        calls = [b.text() for b in drawer.findChildren(QPushButton)
                 if b.text().startswith("Call — ")]
        self.assertEqual(len(calls), 1)
        self.assertIn("(completed)", calls[0])
        # Bob's email matches m-2's bare-address sender; his phone
        # matches yesterday's call to +44 7000 000002 — both links.
        from voice_assistant.core import skills_admin_schedules
        skills_admin_schedules.contact_card(
            "save", "Bob", "email: bob@example.test")
        # A direct store save bypasses the GUI — refresh the page so
        # the drawer's cached contact list sees the new detail.
        self.window._refresh_contacts_page()
        self._open_contact("Bob")
        _pump()
        drawer = self.window._views_contacts_drawer
        labels = self._drawer_labels(drawer)
        self.assertIn(
            "Related — Linked by matching number and matching email",
            labels)
        self.assertIsNotNone(
            self._drawer_button(drawer, "Email — Re: venue"))
        # A contact whose details match nothing shows no section at
        # all — §4.4: the section is absent, never an empty heading.
        skills_admin_schedules.contact_card(
            "save", "Zed", "phone: +44 7000 000777")
        skills_admin_schedules.contact_card(
            "save", "Zed", "email: zed@example.test")
        self.window._refresh_contacts_page()
        self._open_contact("Zed")
        _pump()
        drawer = self.window._views_contacts_drawer
        labels = self._drawer_labels(drawer)
        self.assertFalse(
            any("Related" in text for text in labels),
            "empty Related section shown for an unlinked contact")

    def test_related_call_button_shows_human_time_not_epoch(self):
        # Fix round (2026-11-25): the Related call button printed
        # the raw epoch float — "Call — 1795622484.1419365
        # (completed)" on the 1280/1920 renders. The bug-10 helper
        # _views_call_when formats it, but the button bypassed it.
        # Seed an epoch-float call so the test covers the exact
        # shape the render caught, beside the seeded ISO call.
        import re
        import time
        started = time.time() - 3600
        self._calls_store.record_call(
            "out", "+44 7000 000001", None,
            started, started + 600, "completed", [])
        self.window._refresh_contacts_page()
        self._open_contact("Ada Lovelace")
        _pump()
        from PySide6.QtWidgets import QPushButton
        drawer = self.window._views_contacts_drawer
        calls = [b.text() for b in drawer.findChildren(QPushButton)
                 if b.text().startswith("Call — ")]
        self.assertEqual(len(calls), 2, calls)
        for text in calls:
            self.assertNotRegex(
                text, r"\d{10}\.",
                f"raw epoch float on the Related button: {text!r}")
            self.assertIn("Nov", text, text)
            self.assertTrue(
                "AM" in text or "PM" in text,
                f"no human time on the Related button: {text!r}")
            self.assertIn("(completed)", text)

    def test_related_heading_wraps_instead_of_clipping(self):
        # Fix round (2026-11-25): the Related heading read
        # "…matching number and matching emai" — the plain QLabel
        # was wider than the drawer rail. It wraps inside the rail
        # now; the full wording stays readable, not elided. Bob
        # carries both links, so his is the longest heading.
        from voice_assistant.core import skills_admin_schedules
        skills_admin_schedules.contact_card(
            "save", "Bob", "email: bob@example.test")
        self.window._refresh_contacts_page()
        self.window.resize(1280, 800)
        _show(self.window, 10)
        self._open_contact("Bob")
        _pump()
        from PySide6.QtWidgets import QLabel
        drawer = self.window._views_contacts_drawer
        headings = [lbl for lbl in drawer.findChildren(QLabel)
                    if lbl.text().startswith("Related — ")]
        self.assertEqual(len(headings), 1)
        heading = headings[0]
        self.assertTrue(
            heading.wordWrap(),
            "Related heading does not wrap — it clips at the rail")
        self.assertTrue(
            heading.text().endswith("email"),
            f"Related heading clipped: {heading.text()!r}")
        self.assertGreaterEqual(
            heading.width(), heading.sizeHint().width(),
            f"Related heading clipped: width {heading.width()} < "
            f"sizeHint {heading.sizeHint().width()}")

    def test_views_cards_follow_light_theme(self):
        # Fix round (2026-11-25): _ViewsCard baked the dark house
        # SURFACE_1 (#0C1D28) at construction, so on Light the
        # Contacts + Calls cards were dark navy rectangles with
        # invisible Light-dark text — while the pill beside them
        # was themed. The card themes itself from the window's
        # stored resolved theme at creation, and the appearance
        # pass re-themes cards already on screen. This test asserts
        # the CARD background itself (the old pill-only test passed
        # while the cards were unreadable) and that a theme change
        # while the page is showing re-themes the same live card.
        from voice_assistant.core.config_spec import qsettings_set
        from voice_assistant.gui.style import THEMES as STYLE_THEMES
        from voice_assistant.gui.style_theme import _resolve_theme
        from voice_assistant.gui.window_pages_views import _ViewsCard
        resolved = _resolve_theme(STYLE_THEMES["Light"])
        light_surface = resolved["surface_1"]
        qsettings_set("theme", "Light", settings=self.window.settings)
        self.window._apply_appearance()
        _pump()
        self.window.resize(1280, 800)
        _show(self.window, 10)
        _pump()
        contacts_cards = self.window.findChildren(_ViewsCard)
        self.assertTrue(contacts_cards, "no Contacts cards on Light")
        for card in contacts_cards:
            self.assertIn(
                light_surface, card.styleSheet(),
                "Contacts card keeps the dark face on Light")
            self.assertNotIn(
                "#0C1D28", card.styleSheet(),
                "Contacts card still carries the dark SURFACE_1")
        # Pixel proof, not just the sheet: grab a card and count
        # light pixels — a dark card reads (12, 29, 40) inside.
        card = contacts_cards[0]
        pix = card.grab()
        img = pix.toImage()
        light = 0
        total = img.width() * img.height()
        for x in range(img.width()):
            for y in range(img.height()):
                px = img.pixelColor(x, y)
                if px.red() > 200 and px.green() > 200 \
                        and px.blue() > 200:
                    light += 1
        self.assertGreater(
            light, total // 2,
            f"Contacts card is not a light surface on Light "
            f"({light}/{total} light pixels)")
        # The Calls timeline carries the same card: same face.
        _show(self.window, 11)
        _pump()
        timeline = self.window._views_calls_timeline
        call_cards = [timeline.itemAt(i).widget()
                      for i in range(timeline.count())]
        call_cards = [c for c in call_cards if c is not None]
        self.assertTrue(call_cards, "no Calls cards on Light")
        for card in call_cards:
            self.assertIn(light_surface, card.styleSheet(),
                          "Calls card keeps the dark face on Light")
            self.assertNotIn("#0C1D28", card.styleSheet(),
                             "Calls card still carries #0C1D28")
        # Theme change while the page is showing: the SAME live
        # card re-themes (no rebuild rescues it — the appearance
        # pass re-applies in place).
        dark = _resolve_theme(STYLE_THEMES["Cyan"])
        qsettings_set("theme", "Cyan", settings=self.window.settings)
        self.window._apply_appearance()
        _pump()
        self.assertIn(
            dark["surface_1"], contacts_cards[0].styleSheet(),
            "live Contacts card did not follow the theme change")
        # Leave no theme behind for the next test's window.
        try:
            self.window.settings.remove("theme")
            self.window.settings.sync()
        except Exception:  # noqa: BLE001 — isolation best-effort
            pass

    def test_create_contact(self):
        _show(self.window, 10)
        self.window._views_contacts_add.click()
        _pump()
        drawer = self.window._views_contacts_drawer
        self.assertTrue(drawer.isVisible())
        edits = self._drawer_line_edits(drawer)
        edits[0].setText("Carol")
        edits[1].setText("phone: +44 7000 000003")
        self._drawer_button(drawer, "Save contact").click()
        _pump()
        self.assertIn("3 of 3 contacts",
                      self.window._views_contacts_count.text())
        from PySide6.QtWidgets import QLabel
        texts = [lbl.text() for lbl in
                 self.window._views_contacts_inner.findChildren(QLabel)]
        self.assertIn("Carol", texts)
        self.assertIn("+44 7000 000003", texts)
        self.assertIn("phone", texts)

    def test_edit_detail_save_new_then_delete_old(self):
        _show(self.window, 10)
        self._open_contact("Ada Lovelace")
        drawer = self.window._views_contacts_drawer
        from PySide6.QtWidgets import QPushButton, QLineEdit
        edits = [b for b in drawer.findChildren(QPushButton)
                 if b.text() == "Edit"]
        # The drawer lists details alphabetically (the store sorts
        # them) — edit the phone row, not the first row.
        from voice_assistant.core import skills_admin_schedules
        people = skills_admin_schedules.list_contacts()
        ada = next(p for p in people if p["person"] == "Ada Lovelace")
        details = list(ada["details"])
        self.assertEqual(len(edits), len(details))
        edits[details.index("phone: +44 7000 000001")].click()
        _pump()
        edit = drawer.findChild(QLineEdit, "viewsContactEditDetail")
        self.assertIsNotNone(edit)
        edit.setText("phone: +44 7000 000009")
        self._drawer_button(drawer, "Save").click()
        _pump()
        from voice_assistant.core import skills_admin_schedules
        people = skills_admin_schedules.list_contacts()
        ada = next(p for p in people if p["person"] == "Ada Lovelace")
        self.assertIn("phone: +44 7000 000009", ada["details"])
        self.assertNotIn("phone: +44 7000 000001", ada["details"])
        self.assertIn("email: ada@example.test", ada["details"])

    def test_delete_detail_with_and_without_confirm(self):
        self._allow_writes(False)
        _show(self.window, 10)
        self._open_contact("Bob")
        drawer = self.window._views_contacts_drawer
        from PySide6.QtWidgets import QPushButton
        deletes = [b for b in drawer.findChildren(QPushButton)
                   if b.text() == "Delete"]
        self.assertTrue(deletes)
        deletes[0].click()
        _pump()
        from voice_assistant.core import skills_admin_schedules
        people = skills_admin_schedules.list_contacts()
        bob = next(p for p in people if p["person"] == "Bob")
        self.assertIn("phone: +44 7000 000002", bob["details"])
        self._allow_writes(True)
        deletes[0].click()
        _pump()
        people = skills_admin_schedules.list_contacts()
        self.assertFalse(any(p["person"] == "Bob" for p in people))

    def test_copy_puts_exact_detail_on_clipboard(self):
        from PySide6.QtWidgets import QApplication
        _show(self.window, 10)
        self._open_contact("Ada Lovelace")
        drawer = self.window._views_contacts_drawer
        from PySide6.QtWidgets import QPushButton
        copies = [b for b in drawer.findChildren(QPushButton)
                  if b.text() == "Copy"]
        from voice_assistant.core import skills_admin_schedules
        people = skills_admin_schedules.list_contacts()
        ada = next(p for p in people if p["person"] == "Ada Lovelace")
        details = list(ada["details"])
        self.assertEqual(len(copies), len(details))
        wanted = "phone: +44 7000 000001"
        copies[details.index(wanted)].click()
        _pump()
        self.assertEqual(QApplication.clipboard().text(), wanted)

    def test_search_filters_by_name_and_detail(self):
        _show(self.window, 10)
        self.window._views_contacts_search.setText("bob")
        _pump()
        self.assertIn("1 of 2 contacts",
                      self.window._views_contacts_count.text())
        self.window._views_contacts_search.setText("analytical")
        _pump()
        self.assertIn("1 of 2 contacts",
                      self.window._views_contacts_count.text())
        self.window._views_contacts_search.setText("zzz")
        _pump()
        self.assertIn("No contacts match",
                      self.window._views_contacts_empty.sentence_text)

    def test_rename_moves_all_details(self):
        self._allow_writes(True)
        _show(self.window, 10)
        self._open_contact("Ada Lovelace")
        drawer = self.window._views_contacts_drawer
        from PySide6.QtWidgets import QLineEdit
        rename = drawer.findChild(QLineEdit, "viewsContactRename")
        self.assertIsNotNone(rename)
        rename.setText("Ada Byron")
        self._drawer_button(drawer, "Rename").click()
        _pump()
        from voice_assistant.core import skills_admin_schedules
        people = skills_admin_schedules.list_contacts()
        names = [p["person"] for p in people]
        self.assertIn("Ada Byron", names)
        self.assertNotIn("Ada Lovelace", names)
        byron = next(p for p in people if p["person"] == "Ada Byron")
        self.assertEqual(len(byron["details"]), 3)

    def test_private_mode_hides_names(self):
        self._set_private_mode(True)
        _show(self.window, 10)
        _pump()
        self.assertIn("Private mode is on",
                      self.window._views_contacts_count.text())
        self.assertFalse(self.window._views_contacts_scroll.isVisible())
        self.assertFalse(self.window._views_contacts_add.isVisible())

    def _open_contact(self, name):
        self.window._on_views_contact_picked(name)
        _pump()


@unittest.skipUnless(_QT, "Qt unavailable")
class TestCallsWorkspace(P3WindowBase):
    """Calls: the primary jobs end to end."""

    def test_timeline_shows_calls_with_outcomes(self):
        _show(self.window, 11)
        _pump()
        self.assertIn("Showing the latest 3 calls",
                      self.window._views_calls_status.text())
        from PySide6.QtWidgets import QLabel
        texts = [lbl.text() for lbl in
                 self.window._views_calls_inner.findChildren(QLabel)]
        joined = "\n".join(texts)
        self.assertIn("+44 7000 000001", joined)
        self.assertIn("+44 7000 000099", joined)
        self.assertIn("completed", joined)
        self.assertIn("answered", joined)

    def test_timeline_shows_human_readable_times_not_epochs(self):
        # Render catch (2026-11-25): the card meta showed the raw
        # epoch floats ("Inbound · 1795497052.9384406 – …") and the
        # raw agent id. The stamps are epochs (or ISO strings in
        # the fixture); the card must show "Tue 25 Nov, 10:00 AM"
        # style times, never a 10-digit float.
        import re
        _show(self.window, 11)
        from PySide6.QtWidgets import QLabel
        timeline = self.window._views_calls_timeline
        cards = [timeline.itemAt(i).widget()
                 for i in range(timeline.count())]
        cards = [c for c in cards if c is not None]
        self.assertEqual(len(cards), 3)
        for card in cards:
            joined = " ".join(lbl.text()
                             for lbl in card.findChildren(QLabel))
            self.assertNotRegex(joined, r"1\d{9}\.\d+")
        today = datetime.date.today().strftime("%a %d %b")
        todays = [c for c in cards
                  if "+44 7000 000001" in
                  " ".join(lbl.text()
                           for lbl in c.findChildren(QLabel))]
        self.assertEqual(len(todays), 1)
        meta = " ".join(lbl.text()
                        for lbl in todays[0].findChildren(QLabel))
        self.assertIn(f"{today}, 10:00 AM", meta)

    def test_direction_filter(self):
        _show(self.window, 11)
        self.window._views_calls_direction.setCurrentText("Outbound")
        _pump()
        self.assertIn("2 match the filters",
                      self.window._views_calls_status.text())
        self.window._views_calls_direction.setCurrentText("Inbound")
        _pump()
        self.assertIn("1 match the filters",
                      self.window._views_calls_status.text())

    def test_outcome_filter_has_no_missed(self):
        # The gate proved the vocabulary: only completed/answered
        # exist anywhere — no missed filter, no missed count.
        _show(self.window, 11)
        _pump()
        items = [self.window._views_calls_outcome.itemText(i)
                 for i in range(self.window._views_calls_outcome.count())]
        self.assertEqual(items, ["All outcomes", "answered",
                                 "completed"])
        self.assertNotIn("missed", items)
        self.window._views_calls_outcome.setCurrentText("answered")
        _pump()
        self.assertIn("1 match the filters",
                      self.window._views_calls_status.text())

    def test_detail_shows_agent_flow_transcript(self):
        _show(self.window, 11)
        first = next(r for r in self._calls_rows
                     if r["number"] == "+44 7000 000001")
        self.window._on_views_call_picked(first["id"])
        _pump()
        drawer = self.window._views_calls_drawer
        self.assertTrue(drawer.isVisible())
        labels = self._drawer_labels(drawer)
        joined = "\n".join(labels)
        self.assertIn("Agent flow transcript", joined)
        self.assertIn("say: Hello", joined)
        self.assertIn("gather: Asked for the time", joined)

    def test_detail_without_transcript_says_so(self):
        _show(self.window, 11)
        second = next(r for r in self._calls_rows
                      if r["number"] == "+44 7000 000099")
        self.window._on_views_call_picked(second["id"])
        _pump()
        drawer = self.window._views_calls_drawer
        labels = self._drawer_labels(drawer)
        self.assertIn("No transcript recorded for this call.", labels)

    def test_follow_up_sets_reminder_with_confirm(self):
        self._allow_writes(True)
        _show(self.window, 11)
        first = next(r for r in self._calls_rows
                     if r["number"] == "+44 7000 000001")
        self.window._on_views_call_picked(first["id"])
        _pump()
        drawer = self.window._views_calls_drawer
        from PySide6.QtWidgets import QLineEdit
        label = drawer.findChild(QLineEdit, "viewsCallFollowUpLabel")
        self.assertIsNotNone(label)
        label.setText("Ring Ada back")
        message = drawer.findChild(QLineEdit, "viewsCallFollowUpMessage")
        self.assertIsNotNone(message)
        message.setText("Discuss the Q3 numbers.")
        self._drawer_button(drawer, "Set reminder").click()
        _pump()
        self.assertEqual(len(self._scheduler_calls), 1)
        kind, label_text, fire_at, msg = self._scheduler_calls[0]
        self.assertEqual(kind, "add_reminder")
        self.assertEqual(label_text, "Ring Ada back")
        self.assertEqual(msg, "Discuss the Q3 numbers.")
        self.assertGreater(fire_at, datetime.datetime.now())

    def test_follow_up_refused_when_confirm_denied(self):
        self._allow_writes(False)
        _show(self.window, 11)
        first = next(r for r in self._calls_rows
                     if r["number"] == "+44 7000 000001")
        self.window._on_views_call_picked(first["id"])
        _pump()
        drawer = self.window._views_calls_drawer
        self._drawer_button(drawer, "Set reminder").click()
        _pump()
        self.assertEqual(self._scheduler_calls, [])

    def test_private_mode_hides_numbers(self):
        self._set_private_mode(True)
        _show(self.window, 11)
        _pump()
        self.assertIn("Private mode is on",
                      self.window._views_calls_status.text())
        self.assertFalse(self.window._views_calls_scroll.isVisible())


@unittest.skipUnless(_QT, "Qt unavailable")
class TestWorkspacesWiring(P3WindowBase):
    """The WORKSPACES section maps to stack 8–14 in order."""

    def test_workspaces_label_and_button_order(self):
        labels = [lbl.text()
                  for lbl in self.window._sidebar_section_labels]
        self.assertIn("WORKSPACES", labels)
        self.assertNotIn("VIEWS", labels)
        buttons = self.window.nav_buttons[7:14]
        self.assertEqual(
            [b.accessibleDescription() for b in buttons],
            ["Show the Calendar page", "Show the Inbox page",
             "Show the Contacts page", "Show the Calls page",
             "Show the Notes page — your notes, on this machine",
             "Show the Finance page — money you record yourself",
             "Show the Devices page — your home hub, on your "
             "network"])
        for index, button in enumerate(buttons):
            button.click()
            _pump()
            self.assertEqual(self.window.pages.currentIndex(), 8 + index)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestViewsPureHelpers(unittest.TestCase):
    """Pure helpers — Qt-gated because they live in the GUI module
    (window_pages_views imports PySide6 at module level), so they
    run under the Qt interpreter alongside the GUI tests."""

    def test_parse_search_results(self):
        from voice_assistant.gui.window_pages_views import (
            _views_parse_search_results)
        prose = ("• Ada Lovelace — Q3 numbers (09:12) [id: m-1]\n"
                 "• bob@example.test — Re: venue (08:40) [id: m-2]")
        rows = _views_parse_search_results(prose)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "m-1")
        self.assertEqual(rows[0]["sender"], "Ada Lovelace")
        self.assertEqual(rows[1]["id"], "m-2")
        self.assertEqual(_views_parse_search_results(""), [])
        self.assertEqual(
            _views_parse_search_results("• No id here — subject"), [])

    def test_parse_free_blocks(self):
        from voice_assistant.gui.window_pages_views import (
            _views_parse_free_blocks)
        self.assertEqual(
            _views_parse_free_blocks(
                "Free on 2026-11-27 for 30 minutes: 08:00–20:00."),
            [("08:00", "20:00")])
        self.assertEqual(_views_parse_free_blocks("No free time."), [])
        self.assertEqual(_views_parse_free_blocks(""), [])

    def test_initials(self):
        from voice_assistant.gui.window_pages_views import _views_initials
        self.assertEqual(_views_initials("Ada Lovelace"), "AL")
        self.assertEqual(_views_initials("Bob"), "B")
        self.assertEqual(_views_initials(""), "?")
        self.assertEqual(_views_initials("a b c"), "AB")

    def test_sender_address(self):
        from voice_assistant.gui.window_pages_views import (
            _views_sender_address)
        # Only a bare address matches: recent_messages strips
        # addresses to display names, so a "Name <addr>" string is
        # never treated as an address (the related-emails rule).
        self.assertEqual(
            _views_sender_address("Ada <ada@example.test>"), "")
        self.assertEqual(
            _views_sender_address("bob@example.test"),
            "bob@example.test")
        self.assertEqual(_views_sender_address("Ada Lovelace"), "")
        self.assertEqual(_views_sender_address(""), "")

    def test_sender_name_and_elide(self):
        from voice_assistant.gui.window_pages_views import (
            _views_elide, _views_sender_name)
        # Look-fix (2026-11-25): a sender showed as the raw
        # address. A sender shows a name; a display name is used
        # as-is (parseaddr takes only the first word of a bare
        # name, so the whole-string test matters).
        self.assertEqual(
            _views_sender_name("Priya Nair"), "Priya Nair")
        self.assertEqual(
            _views_sender_name("ada@example.test"), "Ada")
        self.assertEqual(
            _views_sender_name("bob.smith@example.test"), "Bob Smith")
        self.assertEqual(
            _views_sender_name("Ada Lovelace <ada@example.test>"),
            "Ada Lovelace")
        self.assertEqual(_views_sender_name(""), "Unknown sender")
        # Elision cuts at a word boundary with an ellipsis.
        self.assertEqual(_views_elide("Short text", 48), "Short text")
        elided = _views_elide(
            "Revenue is up 12% on the quarter — the deck is attached.",
            48)
        self.assertTrue(elided.endswith("…"))
        self.assertNotIn("attached", elided)
        self.assertIn("deck is", elided)

    def test_event_conflicts_same_start_only(self):
        from voice_assistant.gui.window_pages_views import (
            PagesViewsMixin)
        today = datetime.datetime(2026, 11, 25, 10, 0)
        events = [
            {"id": "a", "title": "One", "start": today},
            {"id": "b", "title": "Two", "start": today},
            {"id": "c", "title": "Three",
             "start": datetime.datetime(2026, 11, 25, 11, 0)},
            {"id": "d", "title": "All day",
             "start": datetime.datetime(2026, 11, 25, 0, 0),
             "all_day": True},
            {"id": "e", "title": "All day too",
             "start": datetime.datetime(2026, 11, 25, 0, 0),
             "all_day": True},
        ]
        conflicts = PagesViewsMixin._views_event_conflicts(None, events)
        self.assertIn("a", conflicts)
        self.assertIn("b", conflicts)
        self.assertNotIn("c", conflicts)
        self.assertNotIn("d", conflicts)
        self.assertNotIn("e", conflicts)
        self.assertIn("Two", conflicts["a"])


if __name__ == "__main__":
    unittest.main()
