"""MainWindow pages mixin (part): views pages — Calendar, Inbox, Contacts, Calls.

Stage 0 features batch 1 (Group D, items 15–18): VYUK knew the calendar,
the inbox, the people and the calls, and there was nowhere to see any
of them. These four pages are read-only views over the stores the tools
already read — calendar + mail through google_services, contacts
through skills_admin_schedules, calls through the voice_agent ledger.
Creating / cancelling / moving events, replying / snoozing mail and
group messaging stay with their own batches; nothing here writes, and
nothing here asks the voice funnel's owner gate — the owner looking at
their own screen is the owner (same "user-started = owner-proven"
boundary as the other pages).
"""

import calendar as _calendar
import datetime

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor, QFont, QPalette, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .style import (
    ACCENT,
    LINE_SUBTLE,
    RADIUS_CARD,
    SURFACE_2,
    TEXT,
    TEXT_MUTED,
    TYPE_CARD_TITLE,
)

# Stack indices (frozen 0–7 stay put; the views take 8–11).
PAGE_CALENDAR = 8
PAGE_INBOX = 9
PAGE_CONTACTS = 10
PAGE_CALLS = 11

_CONNECT_GOOGLE = (
    "Connect Google in Settings → Your world to see this here."
)


def _event_time(when: datetime.datetime) -> str:
    """"2:30 PM" — %I with the leading zero stripped (Windows-safe)."""
    return when.strftime("%I:%M %p").lstrip("0")


def _duration_text(started, ended) -> str:
    """Seconds between two epoch stamps as "3m 12s" — "—" when unknown."""
    try:
        seconds = int(float(ended) - float(started))
    except Exception:  # noqa: BLE001
        return "—"
    if seconds < 0:
        return "—"
    minutes, secs = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class PagesViewsMixin:
    # ------------------------------------------------------------------
    # Calendar (index 8)
    # ------------------------------------------------------------------
    def _refresh_views_theme(self, resolved: dict) -> None:
        """Re-apply the Views panels from a theme (batch 5, Light).

        The calendar grid, the Inbox / Contacts reading panes and
        the Calls table all carry scoped sheets / palettes with the
        house tokens — the "dark pane" precedent (a bare widget
        renders with Qt's default light palette, so the sheet
        darkened it). On Light the house tokens left black voids
        with theme-dark text: the calendar grid unreadable, the
        reading panes black. The window's appearance pass re-runs
        this from the resolved theme; each build site runs it
        once from the house tokens. Defensive: the pages are
        built lazily, so any widget may not exist yet.
        """
        try:
            surface_2 = resolved.get("surface_2", SURFACE_2)
            line = resolved.get("line_subtle", LINE_SUBTLE)
            text = resolved.get("text", TEXT)
            muted = resolved.get("text_muted", TEXT_MUTED)
            accent = resolved.get("accent", ACCENT)
            cal = getattr(self, "_views_calendar", None)
            if cal is not None:
                _weekday_format = QTextCharFormat()
                _weekday_format.setForeground(QColor(text))
                _weekday_format.setBackground(QColor(surface_2))
                for _day in (Qt.Monday, Qt.Tuesday, Qt.Wednesday,
                             Qt.Thursday, Qt.Friday, Qt.Saturday,
                             Qt.Sunday):
                    cal.setWeekdayTextFormat(_day, _weekday_format)
                cal.setStyleSheet(
                    f"QCalendarWidget QToolButton {{ color: {text};"
                    f" background: transparent; }}"
                    f"QCalendarWidget QToolButton:hover {{"
                    f" background: {surface_2}; }}"
                    f"QCalendarWidget QMenu {{ background: {surface_2};"
                    f" color: {text}; }}"
                    f"QCalendarWidget QSpinBox {{ color: {text};"
                    f" background: {surface_2}; }}"
                    f"QCalendarWidget QAbstractItemView:enabled {{"
                    f" color: {text}; background: {surface_2};"
                    f" selection-background-color: {accent}; }}"
                    f"QCalendarWidget QAbstractItemView:disabled {{"
                    f" color: {muted}; }}")
                # The palette goes LAST: setStyleSheet re-polishes
                # the widget and resets a palette set before it
                # (proven by probe P20 — the formats and the sheet
                # took the theme but the palette stayed house-dark
                # until the order flipped). The palette darkens the
                # parts the stylesheet cannot reach — the navigation
                # bar background and the weekday-name header, which
                # the internal view paints from the palette.
                _cal_palette = cal.palette()
                _cal_palette.setColor(QPalette.Window, QColor(surface_2))
                _cal_palette.setColor(QPalette.Base, QColor(surface_2))
                _cal_palette.setColor(QPalette.Text, QColor(text))
                _cal_palette.setColor(QPalette.WindowText, QColor(text))
                _cal_palette.setColor(QPalette.Button, QColor(surface_2))
                _cal_palette.setColor(QPalette.ButtonText, QColor(text))
                _cal_palette.setColor(QPalette.Highlight, QColor(accent))
                cal.setPalette(_cal_palette)
            for body in (getattr(self, "_views_inbox_body", None),
                         getattr(self, "_views_contacts_body", None)):
                if body is not None:
                    body.setStyleSheet(
                        f"QTextBrowser {{ background: {surface_2};"
                        f" color: {text};"
                        f" border: 1px solid {line};"
                        f" border-radius: {RADIUS_CARD}px; }}")
            table = getattr(self, "_views_calls_table", None)
            if table is not None:
                table.setStyleSheet(
                    f"QTableWidget {{ background: {surface_2};"
                    f" color: {text};"
                    f" gridline-color: {line}; }}"
                    f"QTableWidget::item {{ color: {text}; }}"
                    f"QTableWidget::item:selected {{"
                    f" background: {accent}; }}"
                    f"QTableWidget QHeaderView {{"
                    f" background: {surface_2}; }}"
                    f"QTableWidget QAbstractButton {{"
                    f" background: {surface_2}; }}")
            status = getattr(self, "_views_calls_status", None)
            if status is not None:
                status.setStyleSheet(f"color: {muted};")
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass

    def _build_calendar_page(self) -> QWidget:
        page, content = self._page_shell(
            "Calendar",
            "Your month at a glance — pick a day to see its events.",
        )
        top = QHBoxLayout()
        top.setSpacing(12)
        self._views_calendar = QCalendarWidget()
        self._views_calendar.setGridVisible(True)
        self._views_calendar.setVerticalHeaderFormat(
            QCalendarWidget.NoVerticalHeader)
        # Dark grid, same precedent as the mission-log detail pane:
        # a bare QCalendarWidget renders with Qt's default light
        # palette (white grid, unreadable weekday headers). The
        # stylesheet darkens the grid + nav buttons; the palette
        # darkens the parts the stylesheet cannot reach — the
        # navigation bar background and the weekday-name header,
        # which the internal view paints from the palette. Tokens
        # only, no literals; the selection colours stay Qt's
        # default, which already reads on the accent.
        # Batch 5: the palette + formats + sheet live in
        # _refresh_views_theme (re-run from the resolved theme
        # on appearance change).
        self._refresh_views_theme({
            "surface_2": SURFACE_2, "line_subtle": LINE_SUBTLE,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        self._views_calendar.clicked.connect(self._on_views_day_picked)
        self._views_calendar.currentPageChanged.connect(
            self._on_views_month_changed)
        top.addWidget(self._views_calendar, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        upcoming_title = QLabel("Coming up — next 7 days")
        upcoming_title.setStyleSheet(
            f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600; "
            f"color: {TEXT};")
        right.addWidget(upcoming_title)
        self._views_calendar_upcoming = QListWidget()
        self._views_calendar_upcoming.setSelectionMode(
            QAbstractItemView.NoSelection)
        right.addWidget(self._views_calendar_upcoming, 1)
        day_title = QLabel("This day")
        day_title.setStyleSheet(
            f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600; "
            f"color: {TEXT};")
        right.addWidget(day_title)
        self._views_calendar_day = QListWidget()
        self._views_calendar_day.setSelectionMode(
            QAbstractItemView.NoSelection)
        right.addWidget(self._views_calendar_day, 1)
        top.addLayout(right, 1)
        content.addLayout(top)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_calendar_page)
        content.addWidget(refresh)
        self._views_calendar_events: list[dict] = []
        self._views_calendar_page_date = datetime.date.today()
        return page

    def _refresh_calendar_page(self) -> None:
        from voice_assistant.core.gui_facade import google_services

        cal = getattr(self, "_views_calendar", None)
        if cal is None:
            return
        page_date = self._views_calendar_page_date
        today = datetime.date.today()
        first = page_date.replace(day=1)
        last_day = _calendar.monthrange(first.year, first.month)[1]
        last = first.replace(day=last_day)
        window_end = max(last, today + datetime.timedelta(days=7))
        try:
            events = google_services.events_in_range(first, window_end)
        except Exception:  # noqa: BLE001
            events = []
        self._views_calendar_events = events or []
        connected = False
        try:
            connected = bool(google_services.is_connected())
        except Exception:  # noqa: BLE001
            connected = False

        # Mark every day in the visible month that has an event.
        # Clear the month's marks first, so a deleted event's bold
        # day doesn't survive the refresh.
        for day in range(1, last_day + 1):
            cal.setDateTextFormat(
                QDate(first.year, first.month, day), QTextCharFormat())
        mark = QTextCharFormat()
        mark.setFontWeight(QFont.Bold)
        mark.setForeground(QColor(ACCENT))
        for event in self._views_calendar_events:
            start = event.get("start")
            if not isinstance(start, datetime.datetime):
                continue
            day = start.date()
            if day.year == first.year and day.month == first.month:
                cal.setDateTextFormat(QDate(day.year, day.month, day.day), mark)

        upcoming = self._views_calendar_upcoming
        upcoming.clear()
        day_list = self._views_calendar_day
        day_list.clear()
        if not connected:
            upcoming.addItem(_CONNECT_GOOGLE)
            day_list.addItem(_CONNECT_GOOGLE)
            return
        week_end = today + datetime.timedelta(days=7)
        for event in self._views_calendar_events:
            start = event.get("start")
            if not isinstance(start, datetime.datetime):
                continue
            if today <= start.date() < week_end:
                upcoming.addItem(self._views_event_line(event))
        if upcoming.count() == 0:
            upcoming.addItem("Nothing in the next 7 days.")
        self._fill_views_day_list()

    def _fill_views_day_list(self) -> None:
        day_list = getattr(self, "_views_calendar_day", None)
        cal = getattr(self, "_views_calendar", None)
        if day_list is None or cal is None:
            return
        selected = cal.selectedDate().toPython()
        day_list.clear()
        found = False
        for event in self._views_calendar_events:
            start = event.get("start")
            if isinstance(start, datetime.datetime) and start.date() == selected:
                day_list.addItem(self._views_event_line(event))
                found = True
        if not found:
            day_list.addItem("No events this day.")

    def _on_views_day_picked(self, _date: QDate) -> None:
        self._fill_views_day_list()

    def _on_views_month_changed(self, year: int, month: int) -> None:
        self._views_calendar_page_date = datetime.date(year, month, 1)
        self._refresh_calendar_page()

    @staticmethod
    def _views_event_line(event: dict) -> str:
        start = event.get("start")
        title = event.get("title") or "(untitled event)"
        location = event.get("location") or ""
        if isinstance(start, datetime.datetime):
            stamp = "All day" if event.get("all_day") else _event_time(start)
            stamp += f" — {start.strftime('%a %d %b')}"
        else:
            stamp = ""
        line = f"{stamp} — {title}" if stamp else title
        if location:
            line += f" · {location}"
        return line

    # ------------------------------------------------------------------
    # Inbox (index 9)
    # ------------------------------------------------------------------
    def _build_inbox_page(self) -> QWidget:
        page, content = self._page_shell(
            "Inbox",
            "Your newest mail — pick one to read it.",
        )
        split = QHBoxLayout()
        split.setSpacing(12)
        self._views_inbox_list = QListWidget()
        # Long sender/subject lines wrap instead of clipping at
        # the narrow list's edge (the connect line is long).
        self._views_inbox_list.setWordWrap(True)
        self._views_inbox_list.itemActivated.connect(
            self._on_views_inbox_picked)
        split.addWidget(self._views_inbox_list, 1)
        self._views_inbox_body = QTextBrowser()
        self._views_inbox_body.setOpenExternalLinks(False)
        # Dark pane, same precedent as the mission-log detail pane:
        # a bare QTextBrowser renders with Qt's default light
        # palette (pale text on white — the mail would be
        # unreadable). Tokens only, no literals. Batch 5: the
        # sheet lives in _refresh_views_theme.
        self._refresh_views_theme({
            "surface_2": SURFACE_2, "line_subtle": LINE_SUBTLE,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        split.addWidget(self._views_inbox_body, 2)
        content.addLayout(split)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_inbox_page)
        content.addWidget(refresh)
        self._views_inbox_messages: list[dict] = []
        return page

    def _refresh_inbox_page(self) -> None:
        from voice_assistant.core.gui_facade import google_services

        listing = getattr(self, "_views_inbox_list", None)
        body = getattr(self, "_views_inbox_body", None)
        if listing is None or body is None:
            return
        listing.clear()
        body.clear()
        connected = False
        try:
            connected = bool(google_services.is_connected())
        except Exception:  # noqa: BLE001
            connected = False
        if not connected:
            listing.addItem(_CONNECT_GOOGLE)
            return
        try:
            messages = google_services.recent_messages(20)
        except Exception:  # noqa: BLE001
            messages = []
        self._views_inbox_messages = messages or []
        if not self._views_inbox_messages:
            listing.addItem("No mail to show.")
            return
        for message in self._views_inbox_messages:
            item = QListWidgetItem(
                f"{message.get('sender') or 'Unknown sender'} — "
                f"{message.get('subject') or '(no subject)'}\n"
                f"{message.get('when') or ''} · "
                f"{message.get('snippet') or ''}")
            item.setData(Qt.UserRole, message.get("id") or "")
            listing.addItem(item)

    def _on_views_inbox_picked(self, item: QListWidgetItem) -> None:
        from voice_assistant.core.gui_facade import google_services

        body = getattr(self, "_views_inbox_body", None)
        if body is None:
            return
        message_id = item.data(Qt.UserRole) or ""
        if not message_id:
            return
        try:
            text = google_services.message_body(message_id)
        except Exception:  # noqa: BLE001
            text = ""
        body.setPlainText(
            text or "(Nothing readable in this message.)")

    # ------------------------------------------------------------------
    # Contacts (index 10)
    # ------------------------------------------------------------------
    def _build_contacts_page(self) -> QWidget:
        page, content = self._page_shell(
            "Contacts",
            "Everyone you've saved a detail for.",
        )
        split = QHBoxLayout()
        split.setSpacing(12)
        self._views_contacts_list = QListWidget()
        # Batch 5 (re-render): the empty-state item is a 70-char
        # single line and the list clipped it mid-sentence — the
        # Inbox list already wraps (see _build_inbox_page); the
        # Contacts list must too, so the empty state reads in full.
        self._views_contacts_list.setWordWrap(True)
        self._views_contacts_list.itemActivated.connect(
            self._on_views_contact_picked)
        split.addWidget(self._views_contacts_list, 1)
        self._views_contacts_body = QTextBrowser()
        # Dark pane, same precedent as the mission-log detail pane
        # (see the Inbox body above). Batch 5: the sheet lives in
        # _refresh_views_theme.
        self._refresh_views_theme({
            "surface_2": SURFACE_2, "line_subtle": LINE_SUBTLE,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        split.addWidget(self._views_contacts_body, 2)
        content.addLayout(split)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_contacts_page)
        content.addWidget(refresh)
        self._views_contacts: list[dict] = []
        return page

    def _refresh_contacts_page(self) -> None:
        from voice_assistant.core.gui_facade import skills_admin_schedules

        listing = getattr(self, "_views_contacts_list", None)
        body = getattr(self, "_views_contacts_body", None)
        if listing is None or body is None:
            return
        listing.clear()
        body.clear()
        try:
            contacts = skills_admin_schedules.list_contacts()
        except Exception as exc:  # noqa: BLE001
            listing.addItem(f"Couldn't read the contact cards: {exc}")
            return
        self._views_contacts = contacts or []
        if not self._views_contacts:
            listing.addItem(
                "No contact details stored yet — ask VYUK to save a "
                "detail for someone.")
            return
        for person in self._views_contacts:
            listing.addItem(person.get("person") or "")

    def _on_views_contact_picked(self, item: QListWidgetItem) -> None:
        body = getattr(self, "_views_contacts_body", None)
        if body is None:
            return
        name = item.text()
        for person in self._views_contacts:
            if person.get("person") == name:
                details = person.get("details") or []
                body.setPlainText(
                    f"{name}\n\n" + "\n".join(f"• {d}" for d in details))
                return
        body.setPlainText(name)

    # ------------------------------------------------------------------
    # Call history (index 11)
    # ------------------------------------------------------------------
    def _build_calls_page(self) -> QWidget:
        page, content = self._page_shell(
            "Call history",
            "Your recent voice-agent calls.",
        )
        self._views_calls_table = QTableWidget(0, 6)
        self._views_calls_table.setHorizontalHeaderLabels(
            ["When", "Direction", "Number", "Agent", "Duration", "Outcome"])
        self._views_calls_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self._views_calls_table.setSelectionBehavior(
            QAbstractItemView.SelectRows)
        # Dark body, same precedent as the mission-log detail pane:
        # a bare QTableWidget renders with Qt's default light
        # palette (pale text on white — the rows were unreadable).
        # The header sections are already dark via the global
        # QHeaderView::section rule in style_sheet.py; the header
        # view's own background (the empty area below the last
        # row number) needs the same surface here. Batch 5: the
        # sheet lives in _refresh_views_theme.
        self._refresh_views_theme({
            "surface_2": SURFACE_2, "line_subtle": LINE_SUBTLE,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        self._views_calls_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        content.addWidget(self._views_calls_table)
        self._views_calls_status = QLabel("")
        self._views_calls_status.setStyleSheet(f"color: {TEXT_MUTED};")
        content.addWidget(self._views_calls_status)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_calls_page)
        content.addWidget(refresh)
        return page

    def _refresh_calls_page(self) -> None:
        from voice_assistant.core.gui_facade import voice_agent

        table = getattr(self, "_views_calls_table", None)
        status = getattr(self, "_views_calls_status", None)
        if table is None:
            return
        table.setRowCount(0)
        try:
            store = voice_agent.VoiceAgentStore()
            calls = store.list_calls(20)
        except Exception as exc:  # noqa: BLE001
            if status is not None:
                status.setText(f"Couldn't read the call ledger: {exc}")
            return
        calls = calls or []
        if status is not None:
            status.setText(
                "" if calls else "No calls recorded yet.")
        table.setRowCount(len(calls))
        for row, call in enumerate(calls):
            started = call.get("started")
            try:
                when = datetime.datetime.fromtimestamp(
                    float(started)).strftime("%d %b, %I:%M %p").lstrip("0")
            except Exception:  # noqa: BLE001
                when = "—"
            direction = {"in": "Incoming", "out": "Outgoing"}.get(
                str(call.get("direction") or "").lower(),
                str(call.get("direction") or "—"))
            cells = [
                when,
                direction,
                str(call.get("number") or "—"),
                str(call.get("agent") or call.get("agent_id") or "—"),
                _duration_text(call.get("started"), call.get("ended")),
                str(call.get("outcome") or "—"),
            ]
            for col, text in enumerate(cells):
                table.setItem(row, col, QTableWidgetItem(text))
